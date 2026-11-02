"""Bounded retrieval with durable unknown-cost markers and original admission."""
from copy import deepcopy
from financials.forecast import digest
from research.workflow import control


def plan_retrieval(brief: dict, inventory: list[dict], *, limits: dict) -> dict:
    if not brief.get('issuer_id') or not brief.get('question'):raise ValueError('issuer and research question required')
    bounds=dict(max_reports=5,max_queries=6,max_rounds=2);bounds.update(limits)
    for key,ceiling in [('max_reports',6),('max_queries',6),('max_rounds',2)]:
        v=bounds[key]
        if type(v) is not int or not 1<=v<=ceiling:raise ValueError('bounded retrieval limits required')
    queries=brief.get('queries',[])
    if not isinstance(queries,list) or any(not isinstance(x,str) or not x.strip() or len(x)>800 for x in queries):
        raise ValueError('explicit bounded queries required')
    return dict(brief=deepcopy(brief),inventory=deepcopy(inventory),limits=bounds,
                plan_id=digest([brief,bounds]))


def run_retrieval(plan: dict, adapters: dict, journal) -> dict:
    issuer=plan['brief']['issuer_id'];reports=[];rejected=[];gaps=[];seen=set()
    cap=plan['limits']['max_reports'];counts=dict(searches=0,acquisitions=0,reused=0)
    def admit(row):
        if (row.get('issuer_id')!=issuer or row.get('original_verified') is not True
                or row.get('source_quality')!='original' or not row.get('original_sha256')
                or not row.get('org_name') or not row.get('published_at') or not row.get('pages')):
            rejected.append(dict(report_id=row.get('report_id'),reason='identity_or_original_quality'));return
        key=row['original_sha256']
        if key not in seen and len(reports)<cap:seen.add(key);reports.append(deepcopy(row))
    def operation(kind,request):
        identity=[issuer,kind,request]+([plan['plan_id']] if kind=='search' else [])
        key='v17:source-operation:'+digest(identity);cached=journal.get(key)
        if cached:
            if cached['operation_status']=='complete':counts['reused']+=1;return cached['result']
            gaps.append(kind+':unknown_result_no_retry');return None
        journal.put(control(key,issuer,operation_status='inflight',operation_kind=kind))
        try:
            if kind not in adapters:raise ValueError('source adapter unavailable')
            counts['searches' if kind=='search' else 'acquisitions']+=1
            result=adapters[kind](request)
            journal.put(control(key,issuer,operation_status='complete',operation_kind=kind,result=result))
            return result
        except Exception as error:
            journal.put(control(key,issuer,operation_status='unknown',operation_kind=kind,error_type=type(error).__name__))
            gaps.append(kind+':unknown_result_no_retry');return None
    for row in plan['inventory']:admit(row)
    limit=plan['limits']['max_queries']*plan['limits']['max_rounds']
    for query in list(dict.fromkeys(plan['brief'].get('queries',[])))[:limit]:
        if len(reports)>=cap:break
        candidates=operation('search',query)
        if candidates is None:continue
        if not isinstance(candidates,list) or len(candidates)>200:
            gaps.append('invalid_source_inventory');continue
        for candidate in candidates:
            if len(reports)>=cap:break
            if not candidate.get('report_id'):gaps.append('report_identity_missing');continue
            cached=next((r for r in reports if r.get('report_id')==candidate['report_id']),None)
            if cached:continue
            # Original acquisition is separately charged; a search hit cannot self-admit.
            original=operation('acquire',candidate)
            if original is not None:admit(original)
    if len(reports)<3:gaps.append('limited_original_coverage')
    return dict(issuer_id=issuer,status='ready',reports=reports,rejected=rejected,gaps=list(dict.fromkeys(gaps)),
        counts=counts,consensus_status='not_established',coverage_status='limited_institution_sample',
        institutions=sorted(set(r['org_name'] for r in reports)),
        note='Dated original institutional sample; not a claim of market consensus or post-event coverage.')
