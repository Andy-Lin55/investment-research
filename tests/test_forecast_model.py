from copy import deepcopy
import importlib
import pytest


def model():
    try: return importlib.import_module('financials.forecast')
    except ModuleNotFoundError: pytest.fail('Linked forecast model is not implemented')


def baseline():
    return dict(issuer_id='issuer-a', currency='CNY', period_end='2025-12-31', revision_id='financial-v1',
                source_locator='annual report p10', cash_scope_confirmed=True,
                revenue=1000., cash=100., receivables=100., inventory=100., ppe=300., other_assets=100.,
                payables=100., debt=200., other_liabilities=100., equity=300., shares=100.)


def scenarios():
    values=dict(growth=.1,cogs_ratio=.6,opex_ratio=.1,tax_rate=.25,dep_rate=.1,capex_ratio=.1,
                receivable_days=36.5,inventory_days=60.,payable_days=60.,interest_rate=.05,
                dividend_payout=.3,debt_change=0.,equity_issuance=0.,share_change=0.,fx_cash=0.,
                other_equity_change=0.,minority_ratio=0.,pe_multiple=12.)
    return {name:[dict(year=2026,values=deepcopy(values),sources={k:'explicit synthetic assumption' for k in values})]
            for name in ('base','bull','bear')}


def test_linked_statements_balance_and_changes_flow_through_cash_and_equity():
    result=model().project(baseline(),scenarios(),as_of='2026-10-27')
    assert result['status']=='ready_for_review'
    row=result['scenarios']['base'][0]
    assert row['income']['revenue']==1100
    assert row['income']['net_income']==217.5
    assert row['cashflow']['dividends']==65.25
    assert row['balance']['equity']==452.25
    assert abs(row['checks']['balance_difference'])<1e-8
    assert abs(row['checks']['cash_difference'])<1e-8
    assert row['valuation']['price']==pytest.approx(26.1)
    assert row['value_kind']=='forecast_assumption'


def test_funding_shortfall_is_visible_and_never_creates_plug_debt():
    s=scenarios();s['base'][0]['values']['capex_ratio']=2
    r=model().project(baseline(),s,as_of='2026-10-27')['scenarios']['base'][0]
    assert r['funding_shortfall']>0
    assert r['balance']['debt']==200
    assert r['balance']['cash']<0
    assert r['status']=='funding_required'


def test_missing_assumptions_produce_pending_state_without_partial_valuation():
    s=scenarios();s['base'][0]['values']['growth']=None
    r=model().project(baseline(),s,as_of='2026-10-27')
    assert r['status']=='pending_assumptions'
    assert r['missing']
    assert r['scenarios']=={}


@pytest.mark.parametrize('change',['unbalanced','cash_scope','source','future','nonfinite','bank'])
def test_invalid_baseline_never_produces_valid_forecast(change):
    b=baseline()
    if change=='unbalanced':b['equity']=200
    if change=='cash_scope':b['cash_scope_confirmed']=False
    if change=='source':b['source_locator']=''
    if change=='future':b['period_end']='2026-12-31'
    if change=='nonfinite':b['cash']=float('nan')
    if change=='bank':b['sector_template']='bank'
    with pytest.raises(ValueError):model().project(b,scenarios(),as_of='2026-10-27')


def test_assumption_sources_and_consecutive_years_are_required():
    s=scenarios();s['bull'][0]['sources']['growth']=''
    assert model().project(baseline(),s,as_of='2026-10-27')['status']=='pending_assumptions'
    s=scenarios();s['bear'][0]['year']=2028
    with pytest.raises(ValueError):model().project(baseline(),s,as_of='2026-10-27')


def test_model_changes_are_explicit_and_do_not_modify_baseline():
    b=baseline();s=scenarios();original=deepcopy(b)
    first=model().project(b,s,as_of='2026-10-27')
    s['base'][0]['values']['growth']=.2
    second=model().project(b,s,as_of='2026-10-27',previous=first)
    assert second['model_version']!=first['model_version']
    assert second['assumption_changes'][0]['field']=='growth'
    assert b==original


def test_eps_uses_parent_profit_not_total_group_profit():
    s=scenarios();s['base'][0]['values']['minority_ratio']=.2
    row=model().project(baseline(),s,as_of='2026-10-27')['scenarios']['base'][0]
    assert row['income']['parent_net_income']==pytest.approx(row['income']['net_income']*.8)
    assert row['income']['eps']==pytest.approx(row['income']['parent_net_income']/100)
