"""Read existing services into immutable research inputs; never run daily jobs."""
from datetime import date,timedelta
from io import BytesIO
import json,re
from financials.forecast import digest,number
from financials.statements import normalize_statements,reconcile,STATEMENTS
from research.delivery import plain

FIELDS={
 'income': [('revenue','营业收入'),('total_revenue','营业总收入'),('oper_cost','营业成本'),
     ('operate_profit','营业利润'),('total_profit','利润总额'),('income_tax','所得税'),
     ('n_income','净利润'),('n_income_attr_p','归母净利润'),('basic_eps','基本EPS')],
 'balancesheet': [('money_cap','货币资金'),('accounts_receiv','应收账款'),('inventories','存货'),
     ('fix_assets','固定资产'),('total_assets','总资产'),('accounts_pay','应付账款'),
     ('st_borr','短期借款'),('lt_borr','长期借款'),('bond_payable','应付债券'),
     ('total_liab','总负债'),('total_hldr_eqy_inc_min_int','含少数股东权益'),('total_share','期末股本')],
 'cashflow': [('net_profit','净利润'),('n_cashflow_act','经营现金净流量'),
     ('n_cashflow_inv_act','投资现金净流量'),('n_cash_flows_fnc_act','筹资现金净流量'),
     ('eff_fx_flu_cash','汇率影响'),('n_incr_cash_cash_equ','现金等价物净增加'),
     ('c_cash_equ_beg_period','期初现金等价物'),('c_cash_equ_end_period','期末现金等价物')]
}
TITLES=dict(income='利润表',balancesheet='资产负债表',cashflow='现金流量表')


def tushare_query(token):
    """Lazy SDK use, shared token value, no import of the daily snapshot updater."""
    def query(method,**params):
        if not token:raise ValueError('Tushare token is not configured for research')
        import tushare
        try:df=getattr(tushare.pro_api(token),method)(**params)
        except Exception:raise ValueError('Tushare source query failed') from None
        if df is None:raise ValueError('Tushare source response missing')
        return {'rows':json.loads(df.to_json(orient='records',force_ascii=False))}
    return query


def _save(drive,path,raw):
    ref=drive.put_immutable(path,raw)
    if ref.get('verified') is not True:raise ValueError('financial archive read-back failed')
    return ref


def _json(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False).encode()


def history_tables(package):
    tables=[]
    for kind in STATEMENTS:
        records=package['statements'][kind];periods=[r['period_end']+'A' for r in records]
        tables.append(dict(title=TITLES[kind],statement=kind,currency=package['currency'],periods=periods,
            period_basis='instant' if kind=='balancesheet' else 'year_to_date',origin='reported_provider',
            rows=[dict(field=field,label=label,unit=('shares' if field=='total_share' else package['currency']+'/share' if field=='basic_eps' else package['currency']),
                values=[r['values'].get(field) for r in records],source_refs=[r['revision_id'] for r in records]) for field,label in FIELDS[kind]]))
    return tables


def _workbook(package):
    from openpyxl import Workbook
    from openpyxl.styles import Font,PatternFill
    book=Workbook();book.remove(book.active)
    for kind,records in package['statements'].items():
        sheet=book.create_sheet(TITLES[kind]);sheet.append(['历史实际数据候选／待人工核验',package['currency'],'Tushare'])
        sheet.append(['期间口径','期末' if kind=='balancesheet' else '年初至今累计；不可与单季混用'])
        sheet.append(['字段','单位',*[r['period_end']+'A' for r in records]])
        for field in sorted({k for row in records for k in row['values']}):
            unit=next((r['units'][field] for r in records if field in r['units']),package['currency'])
            sheet.append([field,unit,*[r['values'].get(field) for r in records]])
        sheet.append(['来源revision_id','',*[r['revision_id'] for r in records]])
        sheet.freeze_panes='C4';sheet.column_dimensions['A'].width=32;sheet.column_dimensions['B'].width=16
        for row in sheet.iter_rows():
            for cell in row:
                if number(cell.value):cell.number_format='#,##0.00;[Red](#,##0.00)'
        for cell in sheet[3]:cell.font=Font(color='FFFFFF',bold=True);cell.fill=PatternFill('solid',fgColor='163F35')
    checks=book.create_sheet('勾稽及缺口');checks.append(['期间','检查','状态','差额','说明'])
    for row in package['checks']:checks.append([row['period_end'],row['check'],row['status'],row.get('difference'),row.get('note','')])
    checks.append(['','覆盖状态',package['status'],None,'缺项留空；货币资金与现金等价物需调节；非已批准预测模型'])
    stream=BytesIO();book.save(stream)
    # Stable bytes are necessary for immutable paths and retry without conflicts.
    from zipfile import ZipFile,ZipInfo,ZIP_DEFLATED
    result=BytesIO()
    with ZipFile(stream) as src,ZipFile(result,'w',compression=ZIP_DEFLATED) as dst:
        for name in sorted(src.namelist()):
            data=src.read(name)
            if name=='docProps/core.xml':
                data=re.sub(rb'(<dcterms:(?:created|modified)\b[^>]*>)[^<]*(</dcterms:(?:created|modified)>)',
                    rb'\g<1>2000-01-01T00:00:00Z\g<2>',data)
            info=ZipInfo(name,date_time=(2000,1,1,0,0,0));info.compress_type=ZIP_DEFLATED
            dst.writestr(info,data)
    return result.getvalue()


def load_financial_data(profile,*,as_of,query,drive):
    out=dict(issuer_id=profile['issuer_id'],statements={},historical_tables=[],evidence_documents=[],
        market_snapshot=None,gaps=[],history_route='tushare' if profile['market']=='CN' else 'broker_originals')
    if profile['market']!='CN':
        out['gaps']=['broker_original_historical_statements_required','verified_price_unavailable'];return out
    ticker=next(t for t in profile.get('active_tickers',profile['tickers']) if t.endswith(('.SZ','.SH','.BJ')))
    end=date.fromisoformat(as_of);raw={}
    # Fetch an explicit finite window. Never silently truncate provider pages.
    for kind in (*STATEMENTS,'daily'):
        start=(end-timedelta(days=15)).strftime('%Y%m%d') if kind=='daily' else str(end.year-6)+'0101'
        raw[kind]=query(kind,ts_code=ticker,start_date=start,end_date=end.strftime('%Y%m%d'),limit=1000)
        if not isinstance(raw[kind],dict) or not isinstance(raw[kind].get('rows'),list):raise ValueError('explicit provider rows required')
        if len(raw[kind]['rows'])>=1000:raise ValueError('provider result may be truncated')
    package=normalize_statements(raw,ticker=ticker,as_of=as_of,currency='CNY',allow_sector_history=True)
    package['checks']=reconcile(package)
    version=digest({'raw':raw,'as_of':as_of,'normalization_version':2});root=profile['company_path']+'/02_财务数据/v17/'+version
    out['archive']=_save(drive,root+'/source-and-normalized.json',_json({'raw':raw,'normalized':package}))
    out['workbook']=_save(drive,root+'/历史三表.xlsx',_workbook(package))
    out.update(statements=package['statements'],historical_tables=history_tables(package),
        history_status=package['status'],checks=package['checks'],coverage_target=package['coverage_target'])
    if package['status']!='ready_for_review':out['gaps'].append('historical_statement_coverage_incomplete')
    if any(r['company_type']!='1' for records in package['statements'].values() for r in records):
        out['gaps'].append('sector_specific_forecast_not_supported')
    if any(c['status']=='fail' for c in package['checks']):out['gaps'].append('financial_reconciliation_failed')
    prices=[]
    for row in raw['daily']['rows']:
        if row.get('ts_code')!=ticker or not number(row.get('close')) or row['close']<=0:continue
        day=date.fromisoformat(str(row['trade_date']))
        if day<=end:prices.append((day,row))
    if prices:
        day,row=max(prices,key=lambda r:r[0])
        if (end-day).days<=7:
            out['market_snapshot']=dict(ticker=ticker,price=row['close'],trade_date=day.isoformat(),currency='CNY',
                price_basis='unadjusted_close',provider='Tushare',source_ref=version,
                revision_id=digest(row),source_locator='daily(ts_code='+ticker+') / '+day.isoformat(),intraday=False)
    if not out['market_snapshot']:out['gaps'].append('verified_price_unavailable')
    for kind,records in package['statements'].items():
        for record in records:
            text='\n'.join(field+'='+str(record['values'][field]) for field,_ in FIELDS[kind] if record['values'].get(field) is not None)
            out['evidence_documents'].append(dict(issuer_id=profile['issuer_id'],revision_id=record['revision_id'],
                document_id=record['source_locator'],evidence_kind='reported_provider',published_at=record['published_at'],
                period_end=record['period_end'],source={'url':'https://tushare.pro/document/2','original':out['archive']},
                pages=[{'page':1,'text':text}],currency='CNY',scope='consolidated',period_basis=record['period_basis']))
    if out['market_snapshot']:
        price=out['market_snapshot'];price['source']={'url':'https://tushare.pro/document/2','original':out['archive']}
    return out


def broker_history(rows,documents,profile,*,as_of):
    """Historical figures quoted by brokers remain secondary, with explicit A/F labels."""
    grouped={};seen=set();cutoff=date.fromisoformat(as_of)
    for row in rows:
        doc=next((d for d in documents if d['revision_id']==row.get('source_ref')),None)
        quote=row.get('quote','');field=row.get('field');kind=row.get('statement');period=row.get('period_end','')
        if (row.get('kind')!='reported_broker' or row.get('issuer_id')!=profile['issuer_id'] or kind not in STATEMENTS
                or field not in dict(FIELDS[kind]) or row.get('scope')!='consolidated'
                or row.get('currency') not in {'CNY','HKD','USD'} or not row.get('source_locator')
                or not doc or not quote or not any(quote in p['text'] for p in doc.get('pages',[]))
                or date.fromisoformat(period)>cutoff or not number(row.get('value'))):raise ValueError('verified historical broker figure required')
        nums=[float(n) for n in re.findall(r'[-+]?\d+(?:\.\d+)?',quote.replace(',',''))]
        if row['value'] not in nums:raise ValueError('historical figure differs from quote')
        source_page=row.get('source_page');dimension_page=row.get('dimension_page');context=row.get('dimension_quote','')
        numeric_page=next((p for p in doc['pages'] if p['page']==source_page),None)
        header_page=next((p for p in doc['pages'] if p['page']==dimension_page),None)
        if (type(source_page) is not int or type(dimension_page) is not int or not numeric_page or not header_page
                or quote not in numeric_page['text'] or not context or context not in header_page['text']
                or not re.search(r'(?:p(?:age)?\s*|第\s*)'+str(source_page)+r'(?!\d)',row['source_locator'],re.I)):
            raise ValueError('broker numeric and dimension quotes need exact pages')
        currency_patterns={'CNY':r'\bCNY\b|\bRMB\b|人民币','HKD':r'\bHKD\b|HK\$|港元','USD':r'\bUSD\b|US\$|美元'}
        if not re.search(currency_patterns[row['currency']],context,re.I) or not re.search(r'consolidated|合并|綜合',context,re.I):
            raise ValueError('broker currency and consolidated scope must be quoted')
        if not re.search(r'\bactuals?\b|实际|已公布|历史|20\d{2}\s*A\b',quote,re.I):
            raise ValueError('historical actual label must appear in evidence')
        if re.search(r'\bforecast\b|预测|20\d{2}\s*[EF]\b',quote,re.I):raise ValueError('forecast cannot enter actual history')
        expected='instant' if kind=='balancesheet' else 'annual' if period.endswith(profile.get('fiscal_year_end','12-31')) else 'year_to_date'
        if row.get('period_basis')!=expected:raise ValueError('broker period basis differs')
        year=period[:4]
        annual_label=bool(re.search(r'\bFY\s*'+year+r'\s*A\b|\b'+year+r'\s*A\b',context,re.I))
        exact_end=period in context or period.replace('-','/') in context
        if expected in {'annual','instant'} and period.endswith(profile.get('fiscal_year_end','12-31')):
            if not annual_label and not exact_end:raise ValueError('broker annual reporting period must be quoted')
        elif not exact_end:raise ValueError('broker interim reporting end must be quoted')
        if expected=='annual':
            duration=context+' '+quote
            if (not re.search(r'\bannual\b|full.year|twelve.months|12.months|year.ended|全年|年度|十二个月',context,re.I)
                    or re.search(r'\bQ[1-4]\b|quarter|half.year|six.months|nine.months|半年|季度|[上下]半年',duration,re.I)):
                raise ValueError('annual flow needs annual-duration evidence, not a fiscal-year label on a quarter')
        if expected=='year_to_date' and not re.search(r'YTD|year.to.date|累计|累計|年初至',context,re.I):
            raise ValueError('broker cumulative basis must be quoted')
        unit=row.get('unit');scale={'CNY':1,'HKD':1,'USD':1,'万元':10000,'百万元':1000000,'亿元':100000000,'千元':1000,
            'shares':1,'股':1,'万股':10000,'亿股':100000000,'CNY/share':1,'HKD/share':1,'USD/share':1}.get(unit)
        if scale is None or (field=='total_share')!=(unit in {'shares','股','万股','亿股'}):raise ValueError('explicit consistent units required')
        if unit in {'CNY','HKD','USD'} and unit!=row['currency']:raise ValueError('currency unit mismatch')
        if field=='basic_eps' and unit!=row['currency']+'/share':raise ValueError('explicit EPS unit required')
        if field!='basic_eps' and unit.endswith('/share'):raise ValueError('per-share unit on total amount')
        scale_patterns={1000:r'thousand|千',10000:r'万元|万股',1000000:r'million|\bmn\b|百万',100000000:r'亿元|亿股'}
        if scale>1 and not re.search(scale_patterns[scale],context,re.I):raise ValueError('broker unit scale must be quoted')
        if scale==1 and field!='basic_eps' and re.search(r'thousand|million|billion|\bmn\b|千元|万元|百万元|亿元|万股|亿股',context,re.I):
            raise ValueError('unscaled value conflicts with broker header')
        identity=(kind,field,period,row['currency'],row['period_basis'])
        if identity in seen:raise ValueError('ambiguous broker historical figure')
        seen.add(identity);key=(kind,row['currency'],expected);grouped.setdefault(key,[]).append(dict(row,normalized_value=row['value']*scale))
    tables=[]
    for (kind,currency,basis),items in grouped.items():
        periods=sorted({r['period_end'] for r in items});fields=sorted({r['field'] for r in items})
        tables.append(dict(title=TITLES[kind],statement=kind,currency=currency,periods=[p+'A' for p in periods],
            period_basis=basis,origin='broker_reported_history',rows=[dict(field=f,label=dict(FIELDS[kind])[f],unit='shares' if f=='total_share' else currency+'/share' if f=='basic_eps' else currency,
            values=[next((r['normalized_value'] for r in items if r['field']==f and r['period_end']==p),None) for p in periods],
            source_refs=[r['source_ref'] for r in items if r['field']==f]) for f in fields]))
    return tables


class DailyEventsSource:
    """Read existing Events; summaries schedule work but never replace originals."""
    def __init__(self,http,source_id,official):self.http=http;self.source_id=source_id;self.official=official
    def discover(self,profile,start,end):
        from research.cli import pages
        from research.v17_live import period_end
        from research import sources
        events=[];complete=True;seen=set()
        for ticker in profile.get('active_tickers',profile['tickers']):
            body={'filter':{'and':[{'property':'Ticker','rich_text':{'equals':ticker}},
                {'or':[{'and':[{'property':field,'date':{'on_or_after':start}},
                              {'property':field,'date':{'on_or_before':end}}]} for field in ('EventDate','ProcessedAt')]}]}}
            for page in pages(self.http,self.source_id,body):
                props=page['properties'];actual=plain(props.get('Ticker',{}).get('rich_text',[])).strip()
                if actual!=ticker:continue
                kind=(props.get('EventType',{}).get('select') or {}).get('name','')
                if kind in {'新闻资讯','新闻'}:continue
                title=plain(props.get('Name',{}).get('title',[]))
                url=props.get('SourceURL',{}).get('url');day=(props.get('EventDate',{}).get('date') or {}).get('start','')[:10]
                if not url or not day:complete=False;continue
                from urllib.parse import urlsplit
                parsed=urlsplit(url)
                if parsed.scheme=='http' and parsed.netloc.lower()=='static.cninfo.com.cn':
                    url='https://static.cninfo.com.cn'+parsed.path+('?' + parsed.query if parsed.query else '')
                if url in seen:continue
                seen.add(url);dtype=sources.document_type(title,kind)
                row=dict(issuer_id=profile['issuer_id'],ticker=ticker,title=title,url=url,document_type=dtype,
                    document_id='daily:'+page['id'],published_at=day,publication_precision='day',daily_event=True,
                    title_is_scheduling_metadata=title.startswith(ticker) and bool(re.search(r'20\d{2}-\d{2}-\d{2}',title)),
                    metadata_revision=digest([ticker,title,url,day]))
                try:
                    ref,doc=self.official.original(profile,row)
                    events.append(dict(issuer_id=profile['issuer_id'],ticker=ticker,event_id='daily:'+page['id'],
                        revision_id=doc['revision_id'],kind='earnings' if dtype in {'年度报告','中期报告','季度报告','10-K','10-Q','20-F'} else 'material',
                        published_at=row['published_at'],documents=[ref],category='fundamentals'))
                except Exception:complete=False
        return {'events':events,'complete':complete}
