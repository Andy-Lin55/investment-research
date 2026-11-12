from copy import deepcopy
from importlib import import_module, util
import pytest
from research.runtime import MemoryStore
from tests.test_research_profiles import profile


def api(name='workflow'):
    assert util.find_spec('research.'+name), 'v17 '+name+' is not implemented'
    return import_module('research.'+name)


def event(revision='v1',kind='earnings'):
    return dict(issuer_id='issuer-a',event_id='source-1',revision_id=revision,
                kind=kind,published_at='2026-11-01',category='fundamentals')


def test_duplicate_events_and_revised_source_are_distinct_durable_tasks():
    p=api('profiles').resolve_profile(profile(),{});s=MemoryStore()
    t=api().plan_task(event(),p,{},as_of='2026-11-02');s.put(t)
    assert api().plan_task(event(),p,{},as_of='2026-11-03')['task_id']==t['task_id']
    assert api().plan_task(event('v2'),p,{},as_of='2026-11-03')['task_id']!=t['task_id']
    with pytest.raises(ValueError):api().plan_task(dict(event(),issuer_id='other'),p,{},as_of='2026-11-02')


def test_source_failure_preserves_watermark_and_retry_discovers_late_revision():
    p=api('profiles').resolve_profile(profile(),{});s=MemoryStore();windows=[]
    def source(p,start,end):
        windows.append((start,end))
        return {'events':[event()], 'complete':True}
    out=api('discovery').discover_events([p],store=s,adapters={'official':source},as_of='2026-11-02')
    assert out['queued']==1 and out['gaps']==[]
    def fail(*args):raise OSError('source unavailable')
    failed=api('discovery').discover_events([p],store=s,adapters={'official':fail},as_of='2026-11-04')
    assert failed['gaps'] and failed['status']!='no_change'
    api('discovery').discover_events([p],store=s,adapters={'official':source},as_of='2026-11-05')
    assert windows[-1][0]<='2026-11-02'
    tasks=[t for t in s.list() if t.get('record_type')=='research_task']
    assert len(tasks)==1


def test_delivery_failure_resumes_checkpoints_without_repeating_research_and_preserves_accepted_baseline():
    p=api('profiles').resolve_profile(profile(),{});s=MemoryStore()
    task=api().plan_task(event(),p,{'accepted_baseline':{'version':'human-1'}},as_of='2026-11-02');s.put(task)
    calls=[];lost=[True]
    def stage(payload, task):
        calls.append(task['running_stage']);return {'status':'ready','issuer_id':'issuer-a','questions':[{'id':'q1','need':'现金改善可持续吗？'}]}
    def publish(payload,task):
        if lost.pop() if lost else False:raise OSError('lost delivery response')
        return {'verified':True,'id':'report-1','input_digest':task['input_digest']}
    adapters={name:stage for name in api().STAGES};adapters['publish']=publish
    adapters['baseline']=lambda task:{'version':'human-1'}
    out=api().advance_task(task['task_id'],store=s,adapters=adapters,now='2026-11-02')
    assert out['status']=='retryable_failure'
    out=api().advance_task(task['task_id'],store=s,adapters=adapters,now='2026-11-02')
    assert out['status']=='completed' and len(calls)==len(api().STAGES)
    state=out['next_state'];assert state['accepted_baseline']=={'version':'human-1'}
    assert state['last_published_research']['review_status']=='unadopted'
    assert state['open_questions'][0]['id']=='q1'


def test_unknown_pricing_and_baseline_conflict_do_not_publish_or_advance_state():
    p=api('profiles').resolve_profile(profile(),{});s=MemoryStore()
    task=api().plan_task(event(),p,{'accepted_baseline':{'version':'human-1'}},as_of='2026-11-02');s.put(task)
    def blocked(payload,task):raise api().StageWait('waiting_budget',['pricing_unknown'])
    out=api().advance_task(task['task_id'],store=s,adapters={api().STAGES[0]:blocked},now='2026-11-02')
    assert out['status']=='waiting_budget' and not out.get('next_state')
    def stage(payload,task):return {'issuer_id':'issuer-a','status':'ready'}
    adapters={x:stage for x in api().STAGES};adapters['baseline']=lambda task:{'version':'human-2'}
    out=api().advance_task(task['task_id'],store=s,adapters=adapters,now='2026-11-02')
    assert out['status']=='baseline_conflict' and not out.get('next_state')


def test_no_change_weekly_screen_and_rejected_hypotheses_do_not_disappear():
    p=api('profiles').resolve_profile(profile(),{})
    previous={'accepted_baseline':{'version':'human-1'},'open_questions':[{'id':'old','need':'待核对'}],
              'rejected_hypothesis_ids':['bad']}
    t=api().plan_task(event(kind='weekly_screen'),p,previous,as_of='2026-11-02');s=MemoryStore();s.put(t)
    def screen(payload,task):return {'status':'no_change','issuer_id':'issuer-a'}
    out=api().advance_task(t['task_id'],store=s,adapters={'coverage':screen},now='2026-11-02')
    assert out['status']=='no_change' and 'last_published_research' not in out.get('next_state',{})
    state=api().build_next_state(previous,{'issuer_id':'issuer-a','questions':[]},
       manifest={'verified':True,'id':'report','input_digest':'x','issuer_id':'issuer-a'})
    assert state['open_questions']==previous['open_questions']
    assert state['rejected_hypothesis_ids']==['bad']
