"""Structural disclosure/input coverage, not a claim of audited financial facts."""
from datetime import date
import re

TABLES={
    'balance':(r'资产负债表|STATEMENTS?OF(?:FINANCIALPOSITION|BALANCESHEETS?)|BALANCESHEETS?',
               [('资产总计','TOTALASSETS'),('负债和所有者权益总计','负债及所有者权益总计','TOTALLIABILITIESANDEQUITY')]),
    'income':(r'利润表|STATEMENTS?OF(?:INCOME|PROFITORLOSS|OPERATIONS)',
              [('营业收入','营业总收入','REVENUE','NETSALES'),('净利润','NETINCOME','PROFITFORTHE'),('每股收益','EARNINGSPERSHARE')]),
    'cashflow':(r'现金流量表|STATEMENTS?OFCASHFLOWS?',
                [('经营活动产生的现金流量净额','OPERATINGACTIVITIES'),('投资活动产生的现金流量净额','INVESTINGACTIVITIES'),
                 ('筹资活动产生的现金流量净额','FINANCINGACTIVITIES'),('期末现金及现金等价物余额','CASHANDCASHEQUIVALENTSATEND')])}
FULL={'年度报告','半年度报告','中期报告','季度报告','第一季度报告','第三季度报告','10-K','10-Q','20-F','10-K/A','10-Q/A','20-F/A'}


def required_statements(profile,document_type):
    rules=profile.get('disclosure_rules',{})
    if document_type in rules:return list(rules[document_type])
    return list(TABLES) if document_type in FULL else []


def build_coverage(pages: list[dict], *, profile: dict, document_type: str, period_end: str, document_id: str) -> dict:
    date.fromisoformat(period_end)
    required=required_statements(profile,document_type)
    if any(s not in TABLES for s in required):raise ValueError('unsupported statement requirement')
    sections={s:[] for s in TABLES};active=None;scope=None
    for page in sorted(pages,key=lambda x:x['page']):
        text=re.sub(r'\s+','',page['text']).upper()
        headings=[]
        for kind,(pattern,_) in TABLES.items():
            for match in re.finditer(pattern,text):headings.append((match.start(),kind))
        if not headings:
            if active:sections[active].append((page,text,scope))
            continue
        headings.sort()
        for index,(offset,kind) in enumerate(headings):
            prefix=text[max(0,offset-30):offset]
            scope='parent' if '母公司' in prefix or 'PARENT' in prefix else 'consolidated' if '合并' in prefix or 'CONSOLIDATED' in prefix else page.get('scope')
            active=kind
            end=headings[index+1][0] if index+1<len(headings) else len(text)
            sections[kind].append((page,text[max(0,offset-30):end],scope))
    gaps=[];statements={}
    for kind in required:
        entries=[x for x in sections[kind] if x[2]=='consolidated']
        combined=''.join(x[1] for x in entries)
        complete=bool(entries) and all(any(marker in combined for marker in choices) for choices in TABLES[kind][1])
        omitted=[p['page'] for p,_,_ in entries if p.get('included',True) is not True]
        unit=bool(re.search(r'单位[:：].{0,12}(?:元|万元|千元)|IN(?:MILLIONS|THOUSANDS)|RMBMILLION',combined))
        currency=bool(re.search(r'人民币|港元|美元|\b(?:CNY|HKD|USD)|RMB',combined))
        statements[kind]=dict(scope='consolidated',pages=sorted(set(x[0]['page'] for x in entries)),
            original_complete=complete,input_complete=complete and not omitted,unit_confirmed=unit,currency_confirmed=currency)
        if not complete:gaps.append(kind+':statement_missing_or_incomplete')
        if omitted:gaps.append(kind+':input_pages_missing')
        if not unit:gaps.append(kind+':unit_unconfirmed')
        if not currency:gaps.append(kind+':currency_unconfirmed')
    original=all(s['original_complete'] for s in statements.values())
    included=all(s['input_complete'] for s in statements.values())
    return dict(issuer_id=profile['issuer_id'],document_id=document_id,period_end=period_end,
        required_statements=required,statements=statements,gaps=gaps,original_complete=original,input_complete=included,
        status='not_applicable' if not required else 'original_incomplete' if not original else
               'input_incomplete' if not included else 'metadata_pending' if gaps else 'complete')


def check_freshness(known_documents: list[dict], discovered_documents, *, profile: dict, as_of: str) -> dict:
    date.fromisoformat(as_of)
    complete=not isinstance(discovered_documents,dict) or discovered_documents.get('complete') is True
    rows=discovered_documents.get('documents',[]) if isinstance(discovered_documents,dict) else discovered_documents
    old={x['document_id']:x for x in known_documents};new=[];revisions=[];gaps=[]
    for row in rows:
        if row.get('issuer_id')!=profile['issuer_id']:gaps.append('issuer_mismatch');continue
        if row.get('published_at','')[:10]>as_of:continue
        before=old.get(row['document_id'])
        if before is None:new.append(row)
        elif before.get('revision_id')!=row.get('revision_id'):revisions.append(row)
    if not complete:gaps.append('source_scan_incomplete')
    return dict(issuer_id=profile['issuer_id'],checked_at=as_of,new_documents=new,revisions=revisions,
        latest_known=max((x.get('published_at','') for x in rows),default=None),gaps=gaps,
        status='unknown' if gaps else 'stale' if new or revisions else 'fresh')
