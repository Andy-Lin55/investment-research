"""Read only unambiguous annual estimate cells from verified broker originals."""
from collections import defaultdict
from datetime import date
import re
from unicodedata import normalize

from financials.forecast import number

_CN_TITLE = re.compile(r'盈利预测(?:与财务指标|及财务指标)?')
_CN_HEADER = re.compile(r'(?:项目|指标)\s*/\s*年度')
_EN_HEADER = re.compile(r'Fiscal Year Ending', re.I)
_YEAR = re.compile(r'(?:FY\s*)?(20\d{2})([AEF]?)', re.I)
_YEAR_SHORT = re.compile(r'12/(\d{2})([AEF]?)', re.I)
_NUM = re.compile(r'[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?')
_CN_METRIC = {
    '营业收入': 'revenue',
    '归母净利润': 'parent_net_income',
    '归属母公司股东净利润': 'parent_net_income',
    '归属于母公司股东的净利润': 'parent_net_income',
    '归属于母公司股东净利润': 'parent_net_income',
    '基本每股收益': 'eps',
}
_EN_METRIC = {'revenue': 'revenue', 'revenue, net': 'revenue', 'net income attributable to parent': 'parent_net_income',
              'basic eps': 'eps'}
_SCALE = {'元': 1, '千元': 1e3, '万元': 1e4, '百万元': 1e6, '亿元': 1e8,
          'thousand': 1e3, 'million': 1e6, 'billion': 1e9, 'mn': 1e6, 'bn': 1e9}


def _label(label, *, chinese, market):
    match = re.fullmatch(r'([^()]+)\(([^()]+)\)', label.strip())
    if not match:
        return None
    name, unit = (part.strip().casefold() for part in match.groups())
    metric = (_CN_METRIC if chinese else _EN_METRIC).get(name)
    if not metric:
        return None
    if chinese:
        currency = 'CNY'
        prefix = re.match(r'人民币|港元|美元|rmb|cny|hkd|usd', unit)
        if prefix:
            currency = {'港元':'HKD', 'hkd':'HKD', '美元':'USD', 'usd':'USD'}.get(prefix[0], 'CNY')
            unit = unit[prefix.end():].strip()
            # 港元/美元 are already unscaled units when there is no suffix.
            if not unit:
                unit = '元'
        elif market != 'CN':
            return None
        if unit.endswith('/股') and metric != 'eps':
            return None
        unit = unit.removesuffix('/股')
        scale = _SCALE.get(unit)
    else:
        if unit.endswith('/share') and metric != 'eps':
            return None
        parts = unit.removesuffix('/share').split()
        currency = {'rmb':'CNY', 'cny':'CNY', 'usd':'USD', 'hkd':'HKD'}.get(parts[0]) if parts else None
        scale = (1 if len(parts)==1 else _SCALE.get(parts[1]) if len(parts)==2 else None)
    if not currency or scale is None or (metric == 'eps' and scale != 1):
        return None
    return metric, currency, scale, match[2]


def _owner(text):
    return re.sub(r'\s+', '', text).casefold()


def _table_rows(page, doc, profile):
    original = [s for s in page['text'].splitlines() if s.strip()]
    lines = [normalize('NFKC', s).strip() for s in original]
    owner = _owner(doc['org_name'])
    for index, heading in enumerate(lines):
        chinese = bool(_CN_HEADER.fullmatch(heading))
        if chinese:
            if not index or not _CN_TITLE.fullmatch(lines[index-1]):
                continue
        elif not _EN_HEADER.fullmatch(heading):
            continue
        cursor = index+1
        years = []
        while cursor < len(lines):
            fields = lines[cursor].split()
            matches = [_YEAR.fullmatch(f) or (not chinese and _YEAR_SHORT.fullmatch(f)) for f in fields]
            if not matches or not all(matches):
                break
            years.extend((int(m[1])+(2000 if len(m[1])==2 else 0), m[2].upper()) for m in matches)
            cursor += 1
        if not 2 <= len(years) <= 8 or not any(mark in {'E','F'} for _, mark in years):
            continue
        years_only = [year for year, _ in years]
        if years_only != sorted(set(years_only)):
            continue
        end = min(len(lines), cursor+100)
        footnote = None
        for i in range(cursor, end):
            s = lines[i]
            if _CN_HEADER.fullmatch(s) or _EN_HEADER.fullmatch(s) or _CN_TITLE.fullmatch(s):
                break
            if chinese and re.match(r'资料来源[:：]', s):
                source = _owner(s)
                if (re.search(re.escape(owner)+r'(?:研究所|研究部|研究团队)?(?:预测|测算)', source)
                        and not re.search(r'一致预期|彭博预测|万得预测', source)):
                    footnote = i
                break
            if not chinese and re.fullmatch(r'[ef]\s*=\s*.+?\s+estimates?\.?', s, re.I):
                if re.fullmatch(r'[ef]=(?:'+re.escape(owner)+r')(?:research)?estimates?\.?', _owner(s)):
                    footnote = i
                break
        if footnote is None:
            continue
        for i in range(cursor, footnote):
            # A cell may be on the label line or on its own PDF extraction line.
            m = re.fullmatch(r'(.+?\))\s*(.*)', lines[i])
            label = _label(m[1], chinese=chinese, market=profile['market']) if m else None
            if not label:
                continue
            cells = m[2].split()
            j = i+1
            while j < footnote and all(_NUM.fullmatch(t) or t in {'-', '—', '–'} for t in lines[j].split()):
                cells.extend(lines[j].split())
                j += 1
            if len(cells) != len(years) or not all(_NUM.fullmatch(t) or t in {'-', '—', '–'} for t in cells):
                continue
            metric, currency, scale, reported_unit = label
            for (year, mark), cell in zip(years, cells):
                if mark not in {'E','F'} or not _NUM.fullmatch(cell):
                    continue
                if not chinese and mark.casefold() != lines[footnote][0].casefold():
                    continue
                raw = float(cell.replace(',', ''))
                if not number(raw*scale):
                    continue
                yield dict(issuer_id=doc['issuer_id'], metric=metric, period_end=f'{year}-12-31',
                    currency=currency, unit=currency+'/share' if metric=='eps' else currency,
                    scope='consolidated', accounting_basis='statutory' if metric=='revenue' else 'statutory_parent',
                    share_basis='weighted_average', estimate_owner=doc['org_name'], value=raw*scale,
                    reported_value=raw, reported_unit=reported_unit, source_ref=doc['revision_id'],
                    source_page=page['page'], published_at=doc['published_at'][:10],
                    source_url=doc.get('source',{}).get('url'), quote='\n'.join(original[i:j]),
                    header_quote='\n'.join(original[index:cursor]), ownership_quote=original[footnote])


def broker_forecasts(documents, profile, *, as_of):
    """An absent comparable cell is a coverage gap, not a failed model or a zero."""
    rows = defaultdict(list)
    gaps = []
    cutoff = date.fromisoformat(as_of)
    for doc in documents:
        name = doc.get('org_name')
        gap = dict(estimate_owner=name, source_ref=doc.get('revision_id'))
        try:
            published = date.fromisoformat(doc.get('published_at', '')[:10])
        except (TypeError, ValueError):
            published = None
        if (doc.get('issuer_id') != profile['issuer_id'] or not name or not doc.get('revision_id')
                or not published or published > cutoff or doc.get('original_verified') is not True
                or doc.get('source_quality') != 'original' or not doc.get('original_sha256')):
            gaps.append(dict(gap, reason='broker_original_or_as_of_unverified'))
            continue
        extracted = []
        if profile.get('fiscal_year_end', '12-31') == '12-31':
            extracted = [r for p in doc.get('pages', []) if type(p.get('page')) is int and p['page']>0
                         for r in _table_rows(p, doc, profile) if r['period_end'] >= published.isoformat()]
        if not extracted:
            gaps.append(dict(gap, reason='no_unambiguous_annual_forecast_cells'))
        for row in extracted:
            key = (_owner(name), row['metric'], row['period_end'])
            rows[key].append(row)
    selected = []
    for key, versions in sorted(rows.items()):
        latest = max(r['published_at'] for r in versions)
        current = [r for r in versions if r['published_at']==latest]
        if len({(r['currency'], r['value']) for r in current}) != 1:
            gaps.append(dict(estimate_owner=current[0]['estimate_owner'], metric=key[1],
                period_end=key[2], reason='same_date_conflicting_estimates'))
        else:
            selected.append(min(current, key=lambda r: (r['source_ref'], r['source_page'])))
    return selected, gaps
