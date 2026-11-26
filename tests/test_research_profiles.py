from importlib import import_module, util
import pytest


def api():
    assert util.find_spec('research.profiles'), 'company profile routing is not implemented'
    return import_module('research.profiles')


def profile(issuer='issuer-a', ticker='000001.SZ'):
    return dict(issuer_id=issuer, identity_confirmed=True, status='Active', tickers=[ticker],
                company_path='投资/测试/'+issuer, research_question='增长是否产生可持续回报？',
                market='CN', sector_template='industrial', fiscal_year_end='12-31', sources=['official'])


def test_same_workflow_resolves_distinct_companies_without_sharing_question_or_directory():
    a=api().resolve_profile(profile(), {})
    b=api().resolve_profile(profile('issuer-b','000002.SZ'), {'research_question':'现金回报是否改善？'})
    assert (a['issuer_id'],b['issuer_id'])==('issuer-a','issuer-b')
    assert b['research_question']=='现金回报是否改善？'
    assert a['company_path']!=b['company_path']
    assert api().forecast_years(a, '2027-01-02')==[2027,2028,2029]


@pytest.mark.parametrize('change',[{'identity_confirmed':False},{'status':'Watch'},
    {'company_path':'投资/../other'}, {'tickers':[]}, {'fiscal_year_end':'02-31'}])
def test_unconfirmed_identity_and_unsafe_or_ambiguous_profile_cannot_run(change):
    with pytest.raises(ValueError):api().resolve_profile(dict(profile(),**change),{})


def test_non_calendar_fiscal_year_and_unsupported_model_are_explicit():
    p=api().resolve_profile(profile(),{'sector_template':'bank','fiscal_year_end':'06-30'})
    assert api().forecast_years(p,'2026-11-02')==[2027,2028,2029]
    assert p['model_support']=='unsupported_template'
