"""Narrow recovery of a blank annual borrowing field from a reconciled original."""
from datetime import date
from decimal import Decimal
import re
from urllib.parse import urlsplit

from financials.forecast import number

_AMOUNT = re.compile(r'(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}')
_LABEL = re.compile(r'(?:其中:)?[\u3400-\u9fff]+')
_OPEN = re.compile(r'(?:\d+[、.])?合并资产负债表')
_CLOSE = re.compile(r'(?:\d+[、.])?(?:母公司资产负债表|(?:合并|母公司)?利润表|(?:合并|母公司)?现金流量表)')


def _amount(line):
    return Decimal(line.replace(',', ''))


def _balance_section(page, record):
    if not all(number(record['values'].get(k)) for k in ('total_ncl','total_liab')):
        return None
    # Require row-wise extraction; layouts that lose column order remain unresolved.
    lines = [re.sub(r'\s+', '', s).replace('：', ':') for s in page.splitlines() if s.strip()]
    if lines.count('非流动负债:') != 1 or lines.count('非流动负债合计') != 1:
        return None
    start, end = lines.index('非流动负债:'), lines.index('非流动负债合计')
    if not start < end or not any(lines[i:i+3]==['项目','期末余额','期初余额'] for i in range(start)):
        return None
    if (end+5 >= len(lines) or not all(_AMOUNT.fullmatch(lines[i]) for i in (end+1,end+2,end+4,end+5))
            or lines[end+3] != '负债合计'):
        return None
    if any(_amount(lines[i]) != Decimal(str(record['values'][k]))
           for i,k in ((end+1,'total_ncl'),(end+4,'total_liab'))):
        return None
    amounts = {}
    idx = start+1
    while idx < end:
        label = lines[idx]
        if not _LABEL.fullmatch(label) or label in amounts:
            return None
        idx += 1
        values = []
        while idx < end and _AMOUNT.fullmatch(lines[idx]):
            values.append(_amount(lines[idx]))
            idx += 1
        if len(values)>2 or (label in {'其中:优先股','永续债'} and values):
            return None
        amounts[label] = values
    if amounts.get('长期借款') != []:
        return None
    provider_bonds = record['values'].get('bond_payable')
    if not number(provider_bonds) or (amounts.get('应付债券') or [Decimal(0)])[0] != Decimal(str(provider_bonds)):
        return None
    # Exact match also makes a single visible prior-year value fail closed
    # unless the complete current-period subtotal can still be reconciled.
    if sum(v[0] for v in amounts.values() if v) != _amount(lines[end+1]):
        return None
    return '\n'.join(lines[start:end+6])


def long_term_borrowing_blank(documents, record, profile, *, cutoff):
    """Return only a documented zero for an explicitly blank long-term loan row."""
    period = record['period_end']
    eligible = []
    for doc in documents:
        source = doc.get('source', {})
        original = source.get('original', {})
        url = urlsplit(source.get('url') or '')
        ref = doc.get('revision_id','')
        if (doc.get('issuer_id') != profile['issuer_id'] or doc.get('ticker') != record['ticker']
                or doc.get('document_type') != '年度报告' or doc.get('period_end') != period
                or url.scheme!='https' or url.netloc!='static.cninfo.com.cn' or not url.path.startswith('/finalpage/')
                or original.get('verified') is not True or original.get('sha256') != ref
                or not re.fullmatch(r'[0-9a-f]{64}',ref)):
            continue
        try:
            day = date.fromisoformat(doc.get('published_at','')[:10])
        except (TypeError, ValueError):
            continue
        if date.fromisoformat(period) < day <= cutoff:
            eligible.append((day,doc))
    # Do not guess between multiple same-day revisions or fall back to a stale document.
    if not eligible:
        return None
    latest = max(day for day,_ in eligible)
    latest_docs = {doc['revision_id']:doc for day,doc in eligible if day==latest}
    if len(latest_docs)!=1:
        return None
    doc = next(iter(latest_docs.values()))
    header_page = None
    period_label = period[:4]+'年12月31日'
    for page in sorted(doc['pages'], key=lambda p:p['page']):
        text = page['text']
        lines = [re.sub(r'\s+', '', s).replace('：', ':') for s in text.splitlines() if s.strip()]
        for i,line in enumerate(lines):
            if _CLOSE.fullmatch(line):
                header_page = None
            if (_OPEN.fullmatch(line) and period_label in lines[i+1:i+6]
                    and any(unit in {'单位:元','单位:人民币元'} for unit in lines[i+1:i+7])):
                header_page = page['page']
        if header_page is None or not 0 <= page['page']-header_page <= 3:
            continue
        evidence = _balance_section(text, record)
        if evidence:
            return dict(source_ref=doc['revision_id'],source_page=page['page'],
                source_locator=f"{doc['document_id']} p{page['page']}" if doc.get('document_id') else f"PDF p{page['page']}",
                source_url=doc['source']['url'],value_kind='reconciled_blank',value=0,proof=evidence)
    return None
