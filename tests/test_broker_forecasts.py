from copy import deepcopy
import json
import pytest

from tests.test_forecast_inputs import facts, assumptions
from tests.test_research_profiles import profile
from tests.test_research_report_contract import report
from tests.test_research_workflow import event
from research.profiles import resolve_profile
from research.runtime import MemoryStore
from research.workflow import plan_task
from research.v17 import build_adapters
from research.report_v17 import render


TABLE = """盈利预测与财务指标
项目/年度
2025A
2026E
2027E
营业收入（百万元）
332,344
408,862
488,978
归属母公司股东净利润（百万元）
16,600
18,968
30,600
每股收益（元）
2.14
2.45
3.95
资料来源：iFind，甲证券研究所预测"""


def original(text=TABLE, **kwargs):
    return dict(dict(issuer_id='issuer-a', revision_id='broker-1', org_name='甲证券',
        published_at='2026-09-30', original_verified=True, source_quality='original', original_sha256='digest',
        source={'url':'https://example.invalid/original.pdf'}, pages=[{'page': 2, 'text': text}]), **kwargs)


def extract(*documents):
    from research.broker_forecasts import broker_forecasts
    return broker_forecasts(documents, resolve_profile(profile(), {}), as_of='2026-11-02')


def test_extracts_annual_columns_and_normalizes_original_units():
    rows, gaps = extract(original())
    profit = next(r for r in rows if r['metric']=='parent_net_income' and r['period_end']=='2026-12-31')
    assert (profit['value'], profit['reported_value'], profit['unit']) == (18_968_000_000, 18_968, 'CNY')
    assert profit['source_page']==2 and profit['estimate_owner']=='甲证券' and '18,968' in profit['quote']
    assert len(rows)==4  # 2025A and EPS with no basic/diluted label are excluded.
    assert not gaps


@pytest.mark.parametrize('text,changes', [
    (TABLE, {'issuer_id':'other'}),
    (TABLE, {'published_at':'2026-11-03'}),
    (TABLE, {'original_verified':False}),
    (TABLE.replace('甲证券研究所预测','市场一致预期'), {}),
    (TABLE.replace('项目/年度','项目/季度'), {}),
    (TABLE.replace('2025A\n2026E\n2027E','2025A\n2026E\n2026E'), {}),
    (TABLE.replace('百万元','亿元/百万元'), {}),
])
def test_unverified_nonannual_ambiguous_or_consensus_data_does_not_enter_comparison(text, changes):
    rows, gaps = extract(original(text, **changes))
    assert not rows and gaps


def test_newer_revision_supersedes_older_and_same_date_conflicts_are_visible():
    old = original()
    newer = original(TABLE.replace('18,968','19,000'), revision_id='broker-2', published_at='2026-10-30')
    rows, _ = extract(old, newer)
    assert len(rows)==4 and {r['source_ref'] for r in rows}=={'broker-2'}
    conflict = deepcopy(newer)
    conflict['published_at'] = old['published_at']
    rows, gaps = extract(old, conflict)
    assert not any(r['metric']=='parent_net_income' and r['period_end']=='2026-12-31' for r in rows)
    assert any(g['reason']=='same_date_conflicting_estimates' for g in gaps)


def test_original_values_flow_through_model_and_report_without_using_generated_broker_figures():
    p=resolve_profile(profile(), {})
    task=plan_task(event(), p, {}, as_of='2026-11-02')
    f=facts()
    for row in f:
        row['quote']=row['field']+'='+str(row['value'])
    annual={'revision_id':'annual-p10','pages':[{'page':10,'text':'\n'.join(row['quote'] for row in f)}]}
    a=assumptions()
    def infer(*args):
        return {'facts': f, 'assumptions': a, 'broker_estimates':[
            {'issuer_id':'issuer-a','source_ref':'broker-1','quote':'18,968','value':999}]}
    source=original()
    adapters=build_adapters(store=MemoryStore(),drive=None,reports=None,env={},
        documents=lambda t:[],reasoner=infer)
    model=adapters['model']({'coverage':{'documents':[annual]},'retrieval':{'reports':[source]}},task)
    rows=model['comparison']['comparable']
    assert rows and 999 not in [r['value'] for r in rows]
    assert all(r['absolute_difference']==r['own_value']-r['value'] for r in rows)
    blocks=render(task,{'report':report(),'actual_model':'fixture','provider':'offline',
        'forecast_comparison':model['comparison']}, {})
    text=json.dumps(blocks,ensure_ascii=False)
    assert '2026-09-30' in text and 'p2' in text and 'https://example.invalid/original.pdf' in text
    assert '不代表市场共识' in text


def test_english_table_keeps_broker_net_revenue_and_rejects_adjusted_profit_and_consensus():
    text = """Fiscal Year Ending
12/25 12/26e 12/27e
EPS (Rmb)**
2.29
2.65
3.14
EPS (Rmb)§
-
2.92
3.99
Revenue, net (Rmb mn) 332,344 411,810 476,649
ModelWare net inc
(Rmb mn)
16,600
19,371
22,995
§ = Consensus data is provided by Refinitiv Estimates
e = Broker A Research estimates"""
    rows, _ = extract(original(text, org_name='Broker A'))
    assert [(r['metric'],r['value']) for r in rows]==[('revenue',411_810_000_000),('revenue',476_649_000_000)]


def test_writer_cannot_label_broker_gap_comparable_without_a_program_matched_source():
    from research.contract_v17 import validate_report
    from tests.test_research_report_contract import payload
    p=resolve_profile(profile(), {})
    task=plan_task(event(), p, {}, as_of='2026-11-02')
    r=report()
    r['earnings_differences'][0]['status']='comparable'
    with pytest.raises(ValueError, match='computed comparison'):
        validate_report(r,payload(),task)


def test_profit_gap_bridge_uses_same_source_revenue_and_margin_and_reconciles():
    from research.forecast_inputs import compare_forecasts
    estimates, _ = extract(original())
    sample = [r for r in estimates if r['period_end']=='2026-12-31']
    own = [dict(r,value={'revenue':415e9,'parent_net_income':20e9}[r['metric']]) for r in sample]
    result = compare_forecasts({'estimates':own,'admitted_source_refs':['broker-1']},sample)
    bridge, = result['profit_bridges']
    assert bridge['revenue_effect'] > 0 and bridge['margin_effect'] > 0
    assert bridge['revenue_effect']+bridge['margin_effect']==pytest.approx(1.032e9)
    assert bridge['own_margin']==pytest.approx(20/415)
    sample[0]['source_ref']='broker-other'
    assert not compare_forecasts({'estimates':own,'admitted_source_refs':['broker-1','broker-other']},sample)['profit_bridges']
