from tests.test_forecast_model import baseline, scenarios
from financials.forecast import project
from financials.statements import normalize_statements


def real_shaped_inputs():
    meta=dict(ts_code='000001.SZ',comp_type='1',report_type='1')
    rows={
        'income':[
            dict(meta,end_date='20260630',ann_date='20260820',revenue=380,n_income_attr_p=80),
            dict(meta,end_date='20260930',ann_date='20261028',revenue=850,n_income_attr_p=230),
            dict(meta,end_date='20250930',ann_date='20251028',revenue=700,n_income_attr_p=130),
            dict(meta,end_date='20251231',ann_date='20260401',revenue=1000,n_income_attr_p=200),
        ],
        'balancesheet':[],
        'cashflow':[
            dict(meta,end_date='20260630',ann_date='20260820',n_cashflow_act=85),
            dict(meta,end_date='20260930',ann_date='20261028',n_cashflow_act=100),
            dict(meta,end_date='20250930',ann_date='20251028',n_cashflow_act=90),
            dict(meta,end_date='20251231',ann_date='20260401',n_cashflow_act=200),
        ],
    }
    data=normalize_statements(rows,ticker='000001.SZ',as_of='2026-11-02',currency='CNY')
    data['issuer_id']='issuer-a'
    model=project(baseline(),scenarios(),as_of='2026-11-02')
    return model,data


def test_bridge_exposes_implied_quarter_loss_and_same_period_prior_year_comparison():
    from research.forecast_progress import compare_with_reported_progress
    model,data=real_shaped_inputs()
    out=compare_with_reported_progress(model,data,tickers=['000001.SZ'],as_of='2026-11-02')
    assert out['actual_period']=='2026-09-30'
    profit=next(r for r in out['rows'] if r['metric']=='parent_net_income' and r['case']=='base')
    assert profit['reported_ytd']==230 and profit['implied_remainder']==-12.5
    assert profit['prior_remainder']==70 and profit['source_ref']
    assert len(out['rows'])==9


def test_bridge_does_not_use_a_stale_cashflow_or_cross_issuer_numbers():
    from research.forecast_progress import compare_with_reported_progress
    model,data=real_shaped_inputs()
    data['statements']['cashflow']=[r for r in data['statements']['cashflow'] if r['period_end']!='2026-09-30']
    out=compare_with_reported_progress(model,data,tickers=['000001.SZ'],as_of='2026-11-02')
    assert {r['metric'] for r in out['rows']}=={'revenue','parent_net_income'}
    assert compare_with_reported_progress(model,dict(data,issuer_id='other'),tickers=['000001.SZ'],as_of='2026-11-02')=={}
