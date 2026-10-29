from copy import deepcopy
import json
import pytest
from research.report_view import render,visible_periods,visible_rows
from research.delivery import NotionReports,DeliveryError
from tests.test_report_quality import fixture
from tests.test_report_blocks import HTTP
from tests.test_forecast_model import baseline,scenarios
from financials.forecast import project


def test_extended_forecast_splits_all_assumption_changes_without_loss():
    t,a=fixture();old=project(baseline(),scenarios(),as_of='2026-10-27');s=scenarios()
    for rows in s.values():
        rows.extend([dict(deepcopy(rows[0]),year=y) for y in (2027,2028)])
    a['model_snapshot']=project(baseline(),s,as_of='2026-10-27',previous=old)
    assert len(a['model_snapshot']['assumption_changes'])==108
    blocks=render(t,a,{})
    changes=[b['table'] for b in blocks if b['type']=='table'
             and b['table']['children'][0]['table_row']['cells'][0][0]['text']['content']=='情景／年度']
    assert sum(len(t['children'])-1 for t in changes)==108


def test_descendants_under_text_block_prevent_verified_receipt():
    t,a=fixture();http=HTTP();p=NotionReports(http,'reports');p.publish(t,a,{})
    paragraph=next(b for b in http.children['report-1'] if b['type']=='paragraph')
    paragraph['has_children']=True
    http.children[paragraph['id']]=[{'id':'unexpected','type':'paragraph','paragraph':{'rich_text':[]}}]
    with pytest.raises(DeliveryError):p.publish(t,a,{},lookup_only=True)


def test_latest_period_selection_uses_dates_instead_of_column_order():
    periods=[str(y)+'年度A' for y in range(2025,2019,-1)]
    chosen=[periods[i] for i in visible_periods(periods)]
    assert chosen==['2024年度A','2025年度A']


def test_cash_conversion_and_debt_repayment_drivers_remain_visible():
    rows=[{'label':'普通科目'+str(i)} for i in range(21)]
    drivers=[{'label':v} for v in ['经营性应收项目的减少','经营性应付项目的增加','偿还债务支付的现金']]
    assert all(x in visible_rows('现金流量表',rows+drivers) for x in drivers)


def test_weekly_schedule_is_labelled_as_schedule_not_first_hand_source():
    from tests.test_harness_evidence import weekly_card
    t,a=fixture();t['card']=weekly_card();a['report']['changes']=[];a['report']['counterevidence']=[]
    a['report']['next_actions']=[]
    text=json.dumps(render(t,a,{}),ensure_ascii=False)
    assert '任务排程记录' in text and '第一手原件' not in text


def test_retained_disclosure_in_weekly_candidate_is_still_primary_in_origin():
    from tests.test_harness_evidence import disclosure_card
    from research.harness import evidence_is_primary
    card=disclosure_card();card['source']['document_id']='weekly-existing-corpus-20261029-source'
    assert evidence_is_primary(card)


def test_large_missing_cache_inventory_is_readable_and_compacted_without_erasing_gaps():
    from research.harness import compact_context
    t,a=fixture();s=t['context'][0]['financial_snapshot']
    s['gaps']=['利润表!D'+str(i)+':missing_cached_value' for i in range(1000)]
    s['gaps']+=['利润表:missing_currency','利润表:missing_scope']
    body=json.dumps(render(t,a,{}),ensure_ascii=False)
    assert '1000' in body and '币种' in body and '合并口径' in body
    assert len(body)<50000 and '利润表!D999' not in body
    out=compact_context(t['context'])[0]['financial_snapshot']
    assert out['gap_summary']['missing_cached_value']==1000
    assert '利润表:missing_currency' in out['gaps'] and len(out['gaps'])==2
    assert len(s['gaps'])==1002


def test_failed_presentation_does_not_record_uncertain_creation_and_can_resume(monkeypatch):
    from tests.test_research_engine import setup,run
    import research.report_view as view
    parts=setup();provider=parts[-1]
    def invalid(*args,**kwargs):raise ValueError('presentation requires explicit split')
    monkeypatch.setattr(view,'render',invalid)
    first=run(parts)
    assert first['status']=='retry_delivery'
    assert not first['checkpoints'].get('report_attempted')
    assert parts[-2].count==0
    monkeypatch.setattr(view,'render',render)
    parts[-2].fail=False
    out=run(parts)
    assert out['checkpoints']['report'] and provider.calls==1
