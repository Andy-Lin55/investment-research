from importlib import import_module,util
from copy import deepcopy
import pytest
from tests.test_forecast_model import baseline,scenarios
from financials.forecast import BASE_FIELDS


def api():
    assert util.find_spec('research.forecast_inputs'), 'candidate forecast inputs not implemented'
    return import_module('research.forecast_inputs')


def facts():
    b=baseline()
    return [dict(field=k,value=b[k],issuer_id=b['issuer_id'],period_end=b['period_end'],currency='CNY',
        unit='shares' if k=='shares' else 'CNY',scope='consolidated',kind='reported_fact',
        source_ref='annual-p10',source_locator='annual report p10',cash_scope_confirmed=True) for k in BASE_FIELDS]


def assumptions():
    return [dict(scenario=n,year=r['year'],field=k,value=v,kind='research_assumption',
        rationale='Synthetic candidate, not company guidance',range=[v,v],source_refs=['annual-p10'])
        for n,rows in scenarios().items() for r in rows for k,v in r['values'].items()]


def test_reasoned_candidate_without_accepted_baseline_produces_reconciled_model():
    out=api().build_candidate_inputs(facts(),[],[],assumptions())
    assert out['status']=='ready_for_review' and out['accepted_baseline'] is None
    row=out['model']['scenarios']['base'][0]
    assert row['income']['parent_net_income']==217.5
    assert row['valuation']['price']==pytest.approx(26.1)
    assert row['checks']['balance_difference']==pytest.approx(0)
    assert out['assumption_status']=='research_candidate'


def test_missing_data_is_partial_and_cannot_be_plugged_by_management_guidance():
    out=api().build_candidate_inputs(facts()[:-1],[dict(field='shares',value=100,kind='guidance')],[],assumptions())
    assert out['status']=='partial' and 'baseline:shares' in out['gaps']
    assert out.get('model') is None


def test_raw_units_are_normalized_but_mixed_currency_and_unsupported_templates_do_not_sneak_through():
    f=facts()
    for x in f:
        if x['field']!='shares':x['value']/=10000;x['unit']='万元'
    assert api().build_candidate_inputs(f,[],[],assumptions())['model']['baseline']['revenue']==1000
    f[0]['currency']='USD'
    with pytest.raises(ValueError):api().build_candidate_inputs(f,[],[],assumptions())
    assert import_module('research.model_templates').select_model_template({'sector_template':'bank'})['status']=='unsupported_template'


def test_assumptions_without_rationale_or_outside_supported_range_are_rejected():
    a=assumptions();a[0]['rationale']=''
    with pytest.raises(ValueError):api().build_candidate_inputs(facts(),[],[],a)
    a=assumptions();a[0]['range']=[.2,.3]
    with pytest.raises(ValueError):api().build_candidate_inputs(facts(),[],[],a)


def test_different_profit_bases_and_consensus_owners_are_not_merged():
    own=dict(issuer_id='issuer-a',metric='parent_net_income',period_end='2027-12-31',currency='CNY',unit='CNY',
        accounting_basis='statutory_parent',scope='consolidated',share_basis='weighted_average',value=300,
        estimate_owner='own',source_ref='candidate')
    broker=dict(own,value=250,estimate_owner='broker',source_ref='broker-p2')
    incompatible=dict(broker,accounting_basis='ModelWare',value=220)
    consensus=dict(broker,value=270,estimate_owner='Refinitiv_consensus')
    out=api().compare_forecasts({'estimates':[own],'admitted_source_refs':['broker-p2']},[broker,incompatible,consensus])
    assert len(out['comparable'])==2 and len(out['incomparable'])==1
    assert out['comparable'][0]['absolute_difference']==50
    assert out['comparable'][1]['estimate_owner']=='Refinitiv_consensus'
    assert out['consensus_status']=='not_established'
