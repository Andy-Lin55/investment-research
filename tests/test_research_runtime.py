from copy import deepcopy
import importlib
import pytest
from tests.test_evidence_cards import payload


def runtime():
    try:return importlib.import_module('research.runtime')
    except ModuleNotFoundError:pytest.fail('Durable research runtime is not implemented')


def event():
    e=payload();e.update(document_type='重大公告',content='Company disclosed a material acquisition.',issuer_id='issuer-a',ticker='000001.SZ',facts=[])
    return e


def holding():return dict(issuer_id='issuer-a',status='Active',tickers=['000001.SZ'],company_path='投资/公司A')


def test_event_tasks_are_idempotent_and_amendments_and_late_ingestion_are_retained():
    m=runtime();store=m.MemoryStore();a=m.enqueue(store,event(),holding())
    b=m.enqueue(store,event(),holding());assert a['task_id']==b['task_id']
    e=event();e['content']+=' Amendment';c=m.enqueue(store,e,holding())
    assert c['task_id']!=a['task_id']
    assert len(store.list())==2


def test_non_active_and_unknown_issuer_never_enter_holdings_queue():
    m=runtime();store=m.MemoryStore();h=holding();h['status']='Watch'
    with pytest.raises(ValueError):m.enqueue(store,event(),h)
    h=holding();h['issuer_id']='other'
    with pytest.raises(ValueError):m.enqueue(store,event(),h)


def test_budget_reservations_count_uncertain_and_failed_calls_and_enforce_all_caps():
    m=runtime();store=m.MemoryStore();t=m.enqueue(store,event(),holding())
    b=m.Budget(store,task_limit=2,day_limit=10,month_limit=100)
    b.reserve(t,1.2,'2026-10-27','dmx-1')
    with pytest.raises(m.BudgetBlocked):b.reserve(store.get(t['task_id']),1,'2026-10-27','dmx-2')
    assert store.get(t['task_id'])['charges'][0]['state']=='reserved'
    with pytest.raises(ValueError):b.reserve(t,float('nan'),'2026-10-27','bad')


def test_global_budget_uses_durable_history_across_workers():
    m=runtime();store=m.MemoryStore()
    for i in range(5):
        e=event();e['document_id']=str(i)
        t=m.enqueue(store,e,holding());m.Budget(store).reserve(t,2,'2026-10-27',str(i))
    e=event();e['document_id']='six';t=m.enqueue(store,e,holding())
    with pytest.raises(m.BudgetBlocked):m.Budget(store).reserve(t,.01,'2026-10-27','six')


def test_weekly_checks_every_active_issuer_and_prioritizes_changes_and_due_questions():
    m=runtime();hs=[dict(holding(),issuer_id=str(i),changed=i==2,due_questions=i==1,last_review='2026-10-01') for i in range(6)]
    hs.append(dict(holding(),issuer_id='watch',status='Watch'))
    out=m.weekly_plan(hs,as_of='2026-10-27',deep_slots=2)
    assert len(out['screened'])==6
    assert set(out['deep'])=={'1','2'}
    assert len(out['deferred'])==4


def test_optional_subscription_failure_defers_without_paid_fallback():
    m=runtime();store=m.MemoryStore();t=m.enqueue(store,event(),holding(),kind='weekly_deep',week='2026-10-24')
    called=[]
    r=m.run_local(t,store,lambda p:called.append(p) or '',prompt='evidence',online=True)
    assert r['status']=='waiting_subscription'
    assert r['charges']==[]
    assert called


def test_major_event_never_uses_subscription_only_worker():
    m=runtime();store=m.MemoryStore();t=m.enqueue(store,event(),holding())
    with pytest.raises(ValueError):m.run_local(t,store,lambda _: 'text',prompt='evidence',online=True)


def test_current_baseline_conflict_never_overwrites_formal_pointer():
    m=runtime()
    with pytest.raises(m.VersionConflict):m.assert_baseline('model-v1','model-v2')


def report(evidence_id):
    return dict(thesis='现金流需要跟踪',changes=[dict(text='公司公告重大收购',evidence=[evidence_id])],
                counterevidence=[dict(text='尚未披露资金安排',evidence=[evidence_id])],
                next_actions=[dict(condition='公布融资方案后',action='复核债务与现金',due='2026-11-15',evidence=[evidence_id])],
                open_questions=['收购融资成本未明'],conclusion='等待财务假设核验',valuation_status='pending')


def test_report_requires_real_evidence_countercase_and_conditional_actions():
    m=runtime();card=m.build_card(event());r=report(card['revision_id'])
    assert m.validate_report(r,[card],model=None)['status']=='pending_model'
    r['changes'][0]['evidence']=['invented']
    with pytest.raises(ValueError):m.validate_report(r,[card],model=None)


def test_unverified_model_cannot_receive_complete_valuation_conclusion():
    m=runtime();card=m.build_card(event());r=report(card['revision_id']);r['valuation_status']='complete'
    with pytest.raises(ValueError):m.validate_report(r,[card],model={'status':'pending_assumptions'})
