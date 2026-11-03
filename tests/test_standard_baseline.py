from copy import deepcopy
from importlib import import_module,util
import pytest
from financials.statements import normalize_statements
from tests.test_research_profiles import profile
from research.profiles import resolve_profile


def api():
    assert util.find_spec('research.standard_model'), 'standard baseline mapping missing'
    return import_module('research.standard_model')


def financial_data():
    meta=dict(ts_code='000001.SZ',end_date='20251231',ann_date='20260401',report_type='1',comp_type='1')
    raw={'income':[dict(meta,revenue=1000.,oper_cost=600.,sell_exp=30.,admin_exp=40.,rd_exp=30.,fin_exp=10.,
                total_profit=310.,income_tax=62.,n_income=248.,n_income_attr_p=223.2,minority_gain=24.8)],
         'balancesheet':[dict(meta,total_assets=1000.,money_cap=120.,accounts_receiv=100.,inventories=100.,fix_assets=300.,
                accounts_pay=100.,st_borr=50.,lt_borr=75.,bond_payable=50.,non_cur_liab_due_1y=25.,
                total_liab=600.,total_hldr_eqy_inc_min_int=400.,total_share=100.)],
         'cashflow':[dict(meta,c_cash_equ_beg_period=90.,n_incr_cash_cash_equ=10.,c_cash_equ_end_period=100.,depr_fa_coga_dpba=30.,n_cashflow_act=220.,
                n_cashflow_inv_act=-80.,n_cash_flows_fnc_act=-10.,c_pay_acq_const_fiolta=100.)]}
    package=normalize_statements(raw,ticker='000001.SZ',as_of='2026-11-02',currency='CNY',annual_years=1,interim_periods=1)
    return dict(issuer_id='issuer-a',statements=package['statements'],archive={'path':'投资/a/source.json','verified':True})


def test_source_categories_bridge_cash_and_residuals_without_financing_plugs():
    out=api().map_standard_baseline(financial_data(),resolve_profile(profile(),{}),as_of='2026-11-02')
    assert out['status']=='ready' and out['baseline']['cash']==100
    assert out['baseline']['other_assets']==400 and out['baseline']['debt']==200
    assert out['baseline']['other_liabilities']==300 and out['baseline']['equity']==400
    assert out['cash_bridge']['monetary_funds_less_cash_equivalents']==20
    assert out['baseline']['costs_include_depreciation'] is True
    assert out['observations']['other_pretax_income']['value']==20
    assert out['observations']['opex_ratio']['value']==.1
    assert len(out['lineage'])==11 and out['lineage']['other_assets']['terms']
    assert '租赁' in out['limitations'][0]


def test_missing_debt_is_a_specific_gap_not_a_zero_or_older_baseline():
    data=financial_data();row=data['statements']['balancesheet'][0];row['values']['lt_borr']=None
    row['revision_id']=__import__('financials.forecast',fromlist=['digest']).digest({k:v for k,v in row.items() if k!='revision_id'})
    out=api().map_standard_baseline(data,resolve_profile(profile(),{}),as_of='2026-11-02')
    assert out['status']=='partial' and 'balancesheet.lt_borr' in out['gaps']
    assert out['baseline'] is None


def test_mutated_source_and_mixed_identity_or_dates_are_rejected():
    data=financial_data();p=resolve_profile(profile(),{})
    data['statements']['balancesheet'][0]['values']['total_assets']=9999
    with pytest.raises(ValueError):api().map_standard_baseline(data,p,as_of='2026-11-02')
    data=financial_data();p['issuer_id']='another'
    with pytest.raises(ValueError):api().map_standard_baseline(data,p,as_of='2026-11-02')


def test_latest_annual_without_cashflow_is_a_gap_instead_of_using_old_cash():
    data=financial_data();data['statements']['cashflow']=[]
    out=api().map_standard_baseline(data,resolve_profile(profile(),{}),as_of='2026-11-02')
    assert out['status']=='partial' and 'cashflow:2025-12-31' in out['gaps']


def test_compact_driver_cases_expand_with_same_source_and_supported_bounds():
    rows=api().expand_driver_cases({'growth':{'rationale':'new orders','source_refs':['doc-a'],
        'cases':{n:{'2026':{'value':v,'range':[-.1,.2]}} for n,v in [('base',.1),('bull',.2),('bear',-.1)]}}})
    assert len(rows)==3 and rows[0]['kind']=='research_assumption'
    with pytest.raises(ValueError):api().expand_driver_cases({'growth':{'rationale':'','source_refs':[], 'cases':{}}})


def standard_scenarios():
    from tests.test_forecast_model import scenarios
    out=scenarios()
    for rows in out.values():
        row=rows[0];row['values'].update(growth=0.,cogs_ratio=.6,opex_ratio=.1,tax_rate=.2,dep_rate=.1,capex_ratio=.1,
            receivable_days=36.5,inventory_days=100/600*365,payable_days=100/600*365,interest_rate=.05,minority_ratio=.1,
            other_pretax_income=20.,other_income_operating_cash_ratio=.25,other_income_investing_cash_ratio=.5,
            weighted_average_shares=100.,fx_cash=5.,other_equity_change=7.)
        row['sources']={k:'explicit synthetic assumption' for k in row['values']}
    return out


def test_reported_costs_do_not_deduct_depreciation_twice_and_other_income_has_cash_and_asset_counterparts():
    from financials.forecast import project
    b=api().map_standard_baseline(financial_data(),resolve_profile(profile(),{}),as_of='2026-11-02')['baseline']
    model=project(b,standard_scenarios(),as_of='2026-11-02');row=model['scenarios']['base'][0]
    assert model['status']=='ready_for_review'
    assert row['income']['pretax']==pytest.approx(310.)
    assert row['income']['parent_net_income']==pytest.approx(223.2)
    assert row['income']['eps']==pytest.approx(2.232)
    assert row['cashflow']['operating']==pytest.approx(263.)
    assert row['cashflow']['investing']==pytest.approx(-90.)
    assert row['balance']['other_assets']==pytest.approx(412.)
    assert abs(row['checks']['balance_difference'])<=.01
    assert row['income']['weighted_average_shares']==100.


def test_other_income_cash_allocation_cannot_exceed_the_total_and_shares_must_be_positive():
    from financials.forecast import project
    b=api().map_standard_baseline(financial_data(),resolve_profile(profile(),{}),as_of='2026-11-02')['baseline']
    s=standard_scenarios();s['base'][0]['values']['other_income_operating_cash_ratio']=.8
    with pytest.raises(ValueError):project(b,s,as_of='2026-11-02')
    s=standard_scenarios();s['base'][0]['values']['weighted_average_shares']=0
    with pytest.raises(ValueError):project(b,s,as_of='2026-11-02')


def test_mapping_drives_real_candidate_without_ai_retyping_or_guessing_derived_totals():
    from research.forecast_inputs import build_candidate_inputs
    p=resolve_profile(profile(),{});mapping=api().map_standard_baseline(financial_data(),p,as_of='2026-11-02')
    a=[dict(scenario=n,year=r['year'],field=k,value=v,kind='research_assumption',rationale='explicit synthetic',
            range=[v,v],source_refs=['doc-a']) for n,rows in standard_scenarios().items() for r in rows for k,v in r['values'].items()]
    out=build_candidate_inputs([],[],[],a,profile=p,as_of='2026-11-02',mapped_baseline=mapping)
    assert out['model']['baseline']['other_assets']==400.
    assert out['model']['scenarios']['base'][0]['income']['parent_net_income']==pytest.approx(223.2)


def test_standard_workbook_formulas_and_caches_match_the_program_for_all_three_tables():
    from financials.forecast import project
    from financials.workbook import export_model
    from io import BytesIO
    from openpyxl import load_workbook
    b=api().map_standard_baseline(financial_data(),resolve_profile(profile(),{}),as_of='2026-11-02')['baseline']
    model=project(b,standard_scenarios(),as_of='2026-11-02');raw=export_model(model)
    values=load_workbook(BytesIO(raw),data_only=True);formulas=load_workbook(BytesIO(raw),data_only=False)
    assert values['Base']['C10'].value==pytest.approx(310.)
    assert values['Base']['C29'].value==pytest.approx(263.)
    assert values['Base']['C21'].value==pytest.approx(412.)
    assert 'C60' in formulas['Base']['C10'].value
    assert 'C8' not in formulas['Base']['C10'].value
    assert 'weighted_average_shares' in [c.value for c in values['Assumptions']['C']]


def test_live_stage_adapter_uses_mapped_baseline_and_compact_cases_and_publishes_forecast_tables():
    from research.v17 import build_adapters
    from research.workflow import plan_task,advance_task
    from research.runtime import MemoryStore
    from tests.test_research_workflow import event
    from tests.test_broker_sources import Drive
    from tests.test_research_report_contract import report
    from research.report_v17 import render
    import json
    p=resolve_profile(profile(),{});s=MemoryStore();d=Drive();task=plan_task(event(),p,{},as_of='2026-11-02');s.put(task)
    doc=dict(issuer_id=p['issuer_id'],document_id='d',revision_id='doc-a',document_type='公告',
        period_end='2026-09-30',published_at='2026-10-30',pages=[{'page':1,'text':'company disclosure'}])
    def reason(stage,payload,t):
        if stage=='model':
            assert payload['coverage']['financial_data']['baseline_mapping']['status']=='ready'
            scenarios=standard_scenarios()
            cases={f:dict(rationale='explicit synthetic',source_refs=['doc-a'],cases={n:{str(r['year']):dict(value=r['values'][f],range=[r['values'][f],r['values'][f]]) for r in rows} for n,rows in scenarios.items()}) for f in scenarios['base'][0]['values']}
            return dict(facts=[],guidance=[],broker_estimates=[],driver_cases=cases)
        if stage=='judgment':return {'data_requests':[]}
        return report()
    class Reports:
        def publish(self,t,result,files,**kw):
            text=json.dumps(render(t,result,files),ensure_ascii=False)
            assert '预测利润表' in text and '预测资产负债表' in text and '预测现金流量表' in text
            assert '223.20' in text and files['model_workbook']['verified']
            return {'id':'report','verified':True}
    a=build_adapters(store=s,drive=d,reports=Reports(),env={},documents=lambda t:[doc],financial_data=lambda t:financial_data(),reasoner=reason,baseline=lambda t:None)
    out=advance_task(task['task_id'],store=s,adapters=a,now='2026-11-02')
    assert out['status']=='partial' and out['next_state']['last_published_research']['id']=='report'


def test_weighted_shares_cannot_create_silent_dilution_outside_cash_share_bridge():
    from financials.forecast import project
    b=api().map_standard_baseline(financial_data(),resolve_profile(profile(),{}),as_of='2026-11-02')['baseline']
    s=standard_scenarios()
    s['base'][0]['values'].update(weighted_average_shares=112.,share_change=10.,equity_issuance=100.)
    with pytest.raises(ValueError,match='weighted average'):
        project(b,s,as_of='2026-11-02')
    s['base'][0]['values'].update(weighted_average_shares=105.,equity_issuance=-100.)
    with pytest.raises(ValueError,match='same direction'):
        project(b,s,as_of='2026-11-02')


def test_mismatched_cash_rollforward_cannot_seed_forecast():
    from financials.forecast import digest
    data=financial_data();row=data['statements']['cashflow'][0];row['values']['c_cash_equ_beg_period']=50.
    row['revision_id']=digest({k:v for k,v in row.items() if k!='revision_id'})
    out=api().map_standard_baseline(data,resolve_profile(profile(),{}),as_of='2026-11-02')
    assert out['status']=='partial' and 'annual_reconciliation:cash_rollforward' in out['gaps']


def test_funding_or_negative_other_assets_suppress_valuation_in_model_report_and_workbook():
    from financials.forecast import project
    from financials.workbook import export_model
    from research.report_v17 import standard_forecast_tables
    from openpyxl import load_workbook
    from io import BytesIO
    b=api().map_standard_baseline(financial_data(),resolve_profile(profile(),{}),as_of='2026-11-02')['baseline']
    s=standard_scenarios();s['base'][0]['values']['capex_ratio']=2
    s['bear'][0]['values']['other_equity_change']=-500.
    m=project(b,s,as_of='2026-11-02')
    assert m['status']=='needs_model_review'
    assert m['scenarios']['base'][0]['status']=='funding_required'
    assert m['scenarios']['bear'][0]['status']=='invalid_asset_balance'
    for n in ('base','bear'):
        rows=m['scenarios'][n]
        assert rows[0]['valuation']['price'] is None
        assert '—' in str(standard_forecast_tables(rows,m,{'currency':'CNY','price':10.})[-1])
    raw=export_model(m)
    for n in ('Base','Bear'):
        assert load_workbook(BytesIO(raw),data_only=True)[n]['C44'].value in (None,'')
        assert 'C40=0' in load_workbook(BytesIO(raw))[n]['C44'].value
