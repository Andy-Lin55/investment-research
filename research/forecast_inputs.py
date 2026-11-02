"""Typed reported facts and explicit research assumptions into reproducible models."""
from copy import deepcopy
from datetime import date
from financials.forecast import BASE_FIELDS,DRIVERS,digest,number
from research.model_templates import calculate,select_model_template

SCALES={'CNY':1,'USD':1,'HKD':1,'元':1,'万元':10000,'亿元':100000000,'千元':1000,'百万元':1000000,
        'shares':1,'股':1,'万股':10000,'亿股':100000000}
DIMENSIONS=('issuer_id','metric','period_end','currency','unit','accounting_basis','scope','share_basis')


def build_candidate_inputs(facts: list[dict], guidance: list[dict], broker_estimates: list[dict],
                           assumptions: list[dict], *, prior_baseline=None,as_of=None,profile=None,admitted_source_refs=None) -> dict:
    valid=[f for f in facts if f.get('kind')=='reported_fact' and f.get('scope')=='consolidated'
           and f.get('field') in BASE_FIELDS and f.get('source_ref') and f.get('source_locator')]
    for f in valid:date.fromisoformat(f['period_end'])
    profile=profile or {'sector_template':'industrial','fiscal_year_end':'12-31'}
    out=dict(status='partial',assumption_status='research_candidate',accepted_baseline=deepcopy(prior_baseline),
             gaps=[],model=None,estimates=[],admitted_source_refs=list(admitted_source_refs or []),facts=deepcopy(facts),guidance=deepcopy(guidance),assumptions=deepcopy(assumptions))
    if select_model_template(profile)['status']!='supported':
        out['gaps']=['unsupported_template'];return out
    periods=[f['period_end'] for f in valid if f['period_end'].endswith('-12-31') and (not as_of or f['period_end']<=as_of)]
    if not periods:out['gaps']=['annual_baseline_unavailable'];return out
    period=max(periods);rows=[f for f in valid if f['period_end']==period]
    if len({f['issuer_id'] for f in rows})!=1 or len({f['currency'] for f in rows})!=1:
        raise ValueError('mixed issuer or currency in baseline')
    if len({f['field'] for f in rows})!=len(rows):raise ValueError('ambiguous duplicated baseline fact')
    by={f['field']:f for f in rows};out['gaps']=['baseline:'+k for k in BASE_FIELDS if k not in by]
    if out['gaps']:return out
    currency=rows[0]['currency'];issuer=rows[0]['issuer_id']
    if profile.get('issuer_id') and issuer!=profile['issuer_id']:raise ValueError('baseline issuer differs from task profile')
    b=dict(issuer_id=issuer,currency=currency,period_end=period,revision_id=digest(rows),
        source_locator='; '.join(sorted(set(f['source_locator'] for f in rows))),cash_scope_confirmed=by['cash'].get('cash_scope_confirmed') is True,
        amount_unit=currency,amount_scale=1,share_unit='shares',share_scale=1,sector_template='industrial')
    for field,f in by.items():
        if not number(f['value']) or f.get('unit') not in SCALES:raise ValueError('finite values and explicit units required')
        if field=='shares' and f['unit'] not in {'shares','股','万股','亿股'}:raise ValueError('share unit differs')
        if field!='shares' and f['unit'] in {'shares','股','万股','亿股'}:raise ValueError('amount unit differs')
        b[field]=f['value']*SCALES[f['unit']]
    scenarios={n:{} for n in ('base','bull','bear')}
    for a in assumptions:
        bounds=a.get('range');v=a.get('value')
        if (a.get('scenario') not in scenarios or a.get('field') not in DRIVERS
                or type(a.get('year')) is not int or a.get('kind')!='research_assumption'
                or not a.get('rationale') or not isinstance(a.get('source_refs'),list) or not a['source_refs']
                or not number(v) or not isinstance(bounds,list) or len(bounds)!=2
                or not all(number(x) for x in bounds) or not bounds[0]<=v<=bounds[1]):
            raise ValueError('explicit reasoned assumption with supported range required')
        row=scenarios[a['scenario']].setdefault(a['year'],{'year':a['year'],'values':{},'sources':{}})
        if a['field'] in row['values']:raise ValueError('duplicate assumption')
        row['values'][a['field']]=v;row['sources'][a['field']]='Research assumption: '+a['rationale']+' | '+', '.join(a['source_refs'])
    s={n:[years[y] for y in sorted(years)] for n,years in scenarios.items()}
    if any(not years for years in s.values()):out['gaps']=['scenario_assumptions_missing'];return out
    model=calculate(profile,b,s,as_of=as_of or period,previous=(prior_baseline or {}).get('model'))
    out.update(status=model['status'],model=model,issuer_id=issuer,candidate_inputs=dict(baseline=b,scenarios=s,as_of=model['as_of']))
    for row in model['scenarios'].get('base',[]):
        for metric in ('revenue','parent_net_income','eps'):
            out['estimates'].append(dict(issuer_id=issuer,metric=metric,value=row['income'][metric],period_end=str(row['year'])+'-12-31',
                currency=currency,unit=currency+'/share' if metric=='eps' else currency,accounting_basis='statutory_parent' if metric!='revenue' else 'statutory',
                scope='consolidated',share_basis='weighted_average',estimate_owner='own_research',source_ref=model['model_version']))
    out['comparison']=compare_forecasts(out,broker_estimates)
    return out


def compare_forecasts(candidate: dict, comparators: list[dict]) -> dict:
    comparable=[];incomparable=[]
    for row in comparators:
        own=next((x for x in candidate.get('estimates',[]) if all(row.get(k) is not None and x.get(k)==row[k] for k in DIMENSIONS)),None)
        if (not own or not number(row.get('value')) or row.get('source_ref') not in candidate.get('admitted_source_refs',[])
                or not row.get('estimate_owner')):
            incomparable.append(dict(row,reason='unmatched_dimensions_or_provenance'));continue
        difference=own['value']-row['value']
        comparable.append(dict(row,own_value=own['value'],absolute_difference=difference,
            relative_difference=difference/abs(row['value']) if row['value'] else None,
            unexplained_difference=difference,note='Total difference; driver attribution requires comparable disclosed assumptions.'))
    return dict(comparable=comparable,incomparable=incomparable,consensus_status='not_established')


