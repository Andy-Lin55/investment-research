"""Read a specifically mapped historical workbook, not arbitrary human forecasts."""
from datetime import date
from io import BytesIO
from zipfile import ZipFile
import hashlib,re
from financials.forecast import number

SHEETS=('利润表','资产负债表','现金流量表')
_AMOUNT_SCALES={'元':1,'千元':1000,'万元':10_000,'百万元':1_000_000,'亿元':100_000_000}
_SHARE_SCALES={'股':1,'万股':10_000,'亿股':100_000_000}
_PER_SHARE_SCALES={'元/股':1,'分/股':.01}
_METADATA_KEYS={'币种':'currency','合并口径':'scope','报表口径':'scope','口径':'scope',
                '原始金额单位':'raw_amount_unit','原始单位':'raw_amount_unit',
                '原始股数单位':'raw_share_unit','原始每股单位':'raw_per_share_unit',
                '历史数据来源':'historical_source'}
_CURRENCIES={'CNY':'CNY','人民币':'CNY','RMB':'CNY','USD':'USD','美元':'USD',
             'HKD':'HKD','港元':'HKD','EUR':'EUR','GBP':'GBP','JPY':'JPY'}
_SCOPES={'合并':'consolidated','合并报表':'consolidated','consolidated':'consolidated',
         '母公司':'parent','母公司报表':'parent','parent':'parent'}


def _percent_scale(fmt,value):
    """Count operators in the selected numeric section, never literal glyphs."""
    sections=[dict(percents=0,condition=None)]
    for token in re.findall(r'"[^"]*"|\\.|_.|\*.|\[[^\]]*\]|;|.',fmt):
        if token==';':sections.append(dict(percents=0,condition=None))
        elif token=='%':sections[-1]['percents']+=1
        elif token.startswith('['):
            condition=re.fullmatch(r'\[(<=|>=|<>|=|<|>)(-?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?)\]',token)
            if condition:sections[-1]['condition']=condition.groups()
    numeric=sections[:3]
    if any(s['condition'] for s in numeric):
        selected=None
        for section in numeric:
            if section['condition'] is None:
                selected=section;break
            op,bound=section['condition'];bound=float(bound)
            if {'<':value<bound,'>':value>bound,'<=':value<=bound,'>=':value>=bound,
                '=':value==bound,'<>':value!=bound}[op]:
                selected=section;break
        return 100**selected['percents'] if selected else 1
    index=1 if value<0 and len(numeric)>1 else 2 if value==0 and len(numeric)>2 else 0
    return 100**numeric[index]['percents']


def _metadata(grid,name,gaps):
    """Only explicit tagged header fields establish dimensions, never the title.

    Rows 1-4 accept label/adjacent-value pairs or colon-delimited fields joined
    by ｜. Old historical headers without these fields remain gap-marked.
    """
    found={key:[] for key in _METADATA_KEYS.values()}
    for row in grid[:4]:
        for index,cell in enumerate(row):
            if not isinstance(cell.value,str):continue
            for part in re.split(r'[｜|；;\n]',cell.value):
                pair=re.split(r'[:：]',part,maxsplit=1);key=pair[0].strip()
                if key not in _METADATA_KEYS:continue
                target=cell
                if len(pair)==2:value=pair[1].strip()
                else:
                    if index+1>=len(row):continue
                    target=row[index+1];value=target.value
                if value is not None and str(value).strip():
                    found[_METADATA_KEYS[key]].append((str(value).strip(),name+'!'+target.coordinate))
    metadata={};locators={};raw={}
    mappings={'currency':_CURRENCIES,'scope':_SCOPES,
              'raw_amount_unit':{u:u for u in _AMOUNT_SCALES},
              'raw_share_unit':{u:u for u in _SHARE_SCALES},
              'raw_per_share_unit':{u:u for u in _PER_SHARE_SCALES}}
    for key,entries in found.items():
        raw[key]=[value for value,_ in entries];locators[key]=[loc for _,loc in entries]
        values={mappings[key].get(value,value) if key in mappings else value for value,_ in entries}
        if len(values)>1:raise ValueError(name+': conflicting '+key+' metadata')
        value=next(iter(values),None)
        if key in mappings and value not in mappings[key].values():
            if value is not None:gaps.append(name+':unknown_'+key)
            value=None
        if value is None and key not in {'raw_share_unit','raw_per_share_unit'}:
            gaps.append(name+':missing_'+key)
        metadata[key]=value
    return metadata,locators,raw


def _dimensions(unit,metadata):
    currency=metadata['currency'] if unit in {'亿元','元/股'} else None
    if unit=='亿元':
        raw_unit=metadata['raw_amount_unit'];raw_scale=_AMOUNT_SCALES.get(raw_unit)
    elif unit=='亿股':
        raw_unit=metadata['raw_share_unit'];raw_scale=_SHARE_SCALES.get(raw_unit)
    elif unit=='元/股':
        # Display and total-amount units cannot establish the source EPS scale.
        raw_unit=metadata['raw_per_share_unit'];raw_scale=_PER_SHARE_SCALES.get(raw_unit)
    else:raw_unit=None;raw_scale=None
    return dict(currency=currency,scope=metadata['scope'],raw_unit=raw_unit,raw_scale=raw_scale,
                display_unit=unit,display_scale=100_000_000 if unit in {'亿元','亿股'} else 1)


def from_workbook(data,*,issuer_id,source,as_of,expected_company):
    from openpyxl import load_workbook
    from openpyxl.utils.cell import get_column_letter
    if not issuer_id or not expected_company:raise ValueError('confirmed issuer and company title required')
    if source.get('sha256')!=hashlib.sha256(data).hexdigest():raise ValueError('workbook checksum differs')
    cutoff=date.fromisoformat(as_of)
    if len(data)>40_000_000:raise ValueError('bounded workbook required')
    with ZipFile(BytesIO(data)) as z:
        if sum(i.file_size for i in z.infolist())>100_000_000:raise ValueError('expanded workbook too large')
        if any('externalLinks/' in i.filename or 'vbaProject' in i.filename for i in z.infolist()):
            raise ValueError('external-linked/macro workbook needs separate review')
    book=load_workbook(BytesIO(data),data_only=True,read_only=True,keep_links=False)
    tables=[];gaps=[]
    try:
        for name in SHEETS:
            s=book[name]
            if s.max_row is None or s.max_column is None:s.calculate_dimension(force=True)
            if s.max_row>200 or s.max_column>25:raise ValueError('bounded historical table required')
            grid=list(s.iter_rows(min_row=1,max_row=max(5,s.max_row),max_col=max(3,s.max_column)))
            def cell(r,c):return grid[r-1][c-1]
            if (not str(cell(2,2).value or '').startswith(expected_company+'｜')
                    or cell(5,2).value!='科目' or cell(5,3).value!='展示单位'):
                raise ValueError('explicit historical-workpaper template required')
            cols=[c for c in range(4,s.max_column+1) if cell(5,c).value is not None]
            headers=[str(cell(5,c).value) for c in cols]
            if not headers or len(headers)!=len(set(headers)) or any(not re.fullmatch(r'\d{4}(?:年度|-\d{2}-\d{2}(?:累计|期末))A',h) for h in headers):
                raise ValueError('explicit unique actual-period labels required')
            periods=[]
            for h in headers:
                annual='年度A' in h;stock=name=='资产负债表'
                if not annual and ('期末A' in h)!=stock:
                    raise ValueError(name+': incompatible period basis')
                end=date.fromisoformat(h[:4]+'-12-31' if annual else h[:10])
                if end>cutoff:raise ValueError('future financial period labelled actual')
                periods.append(dict(label=h,period_start=None if stock else h[:4]+'-01-01',
                                    period_end=end.isoformat(),period_basis='instant' if stock else 'year_to_date'))
            metadata,locators,raw_metadata=_metadata(grid,name,gaps)
            for dimension in ('currency','scope'):
                known={t[dimension] for t in tables if t[dimension] is not None}
                if metadata[dimension] is not None and known and metadata[dimension] not in known:
                    raise ValueError('incompatible statement '+dimension)
            rows=[]
            for r in range(6,s.max_row+1):
                label=cell(r,2).value;unit=cell(r,3).value
                if label is None:continue
                if not isinstance(label,str) or unit not in {'亿元','元/股','亿股','%','倍','天'}:
                    raise ValueError('explicit supported display units required')
                optional_key={'亿股':'raw_share_unit','元/股':'raw_per_share_unit'}.get(unit)
                if optional_key and metadata[optional_key] is None:
                    gap=name+':missing_'+optional_key
                    if gap not in gaps:gaps.append(gap)
                values=[];raw_values=[];cells=[];formats=[];scales=[]
                for c in cols:
                    source_cell=cell(r,c);value=source_cell.value;coordinate=get_column_letter(c)+str(r)
                    if value is None:gaps.append(name+'!'+coordinate+':missing_cached_value')
                    elif not number(value):
                        gaps.append(name+'!'+coordinate+':invalid_cached_value');value=None
                    raw_values.append(value)
                    scale=_percent_scale(source_cell.number_format,value) if value is not None and unit=='%' else 1
                    values.append(value*scale if value is not None else None)
                    scales.append(scale);formats.append(source_cell.number_format);cells.append(name+'!'+coordinate)
                rows.append(dict(label=label,unit=unit,values=values,cells=cells,raw_values=raw_values,
                                 number_formats=formats,display_multipliers=scales,dimensions=_dimensions(unit,metadata)))
            tables.append(dict(title=name,periods=headers,period_metadata=periods,rows=rows,
                               **metadata,metadata_cells=locators,raw_metadata=raw_metadata))
    finally:book.close()
    all_periods={p['period_end'] for table in tables for p in table['period_metadata']}
    for table in tables:
        present={p['period_end'] for p in table['period_metadata']}
        gaps.extend(table['title']+':missing_period:'+p for p in sorted(all_periods-present))
    return dict(schema_version=1,issuer_id=issuer_id,as_of=as_of,source=dict(source),tables=tables,gaps=gaps,
                human_review='pending',value_kind='historical_cached_workbook',
                note='Historical workbook display values only; raw_values are unscaled Excel caches, source scales are metadata only. '
                     'Missing currency/scope/source dimensions remain gaps; no currency inferred from 元. '
                     'No approved forecast inferred. 货币资金与现金等价物不混同。')
