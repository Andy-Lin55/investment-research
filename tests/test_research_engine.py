import importlib,json
import pytest
from research.runtime import MemoryStore,Budget,enqueue
from tests.test_research_runtime import event,holding,report

def api():
    try:return importlib.import_module('research.engine')
    except ModuleNotFoundError:pytest.fail('Cloud research engine is not implemented')

class Drive:
    def __init__(self):self.files={};self.fail=False
    def put_immutable(self,path,data):
        if self.fail:raise RuntimeError('upload unavailable')
        if path in self.files:assert self.files[path]==data
        self.files[path]=data;return dict(path=path,verified=True,url='https://drive.invalid/file')
    def read(self,path):
        from research.delivery import ServiceError
        raise ServiceError(404)

class Reports:
    def __init__(self):self.count=0;self.fail=True;self.rows={}
    def publish(self,task,result,files,*,candidate=True,lookup_only=False):
        key=task['publication_key']
        if key in self.rows:return self.rows[key]
        if lookup_only:return None
        self.count+=1;self.rows[key]={'id':'report-1','url':'https://notion.invalid/report'}
        if self.fail:self.fail=False;raise RuntimeError('response lost after create')
        return self.rows[key]

class Provider:
    name='dmx';model='gemini-3.8-flash';input_cny_per_million=1.;output_cny_per_million=2.
    def __init__(self):self.calls=0;self.empty=False;self.bad=False
    def __call__(self,prompt,*,max_output_tokens):
        self.calls+=1
        if self.empty:raise RuntimeError('API unavailable')
        rid=prompt.split('revision_id')[0] if False else self.rid
        content=report(rid)
        if self.bad:content['changes'][0]['evidence']=['invented']
        return dict(content=json.dumps(content,ensure_ascii=False),actual_model=self.model,
                    input_tokens=100,output_tokens=100)

def setup():
    m=api();store=MemoryStore();task=enqueue(store,event(),holding());p=Provider();p.rid=task['card']['revision_id']
    return m,store,task,Drive(),Reports(),p

def run(parts):
    m,s,t,d,r,p=parts
    return m.process(t['task_id'],store=s,drive=d,reports=r,providers=[p],budget=Budget(s),day='2026-10-27',model=None)

def test_delivery_resume_only_delivers_and_does_not_repeat_paid_analysis():
    parts=setup();a=run(parts)
    assert a['status']=='retry_delivery';assert parts[-1].calls==1
    b=run(parts)
    assert b['status']=='pending_model'
    assert parts[-1].calls==1;assert parts[-2].count==1
    assert b['checkpoints']['report']['id']=='report-1'

def test_missing_full_evidence_is_visible_and_never_calls_model():
    parts=setup();parts[2]['evidence']['content']='';parts[1].put(parts[2]);r=run(parts)
    assert r['status']=='pending_evidence';assert parts[-1].calls==0

def test_upload_failure_keeps_task_and_spends_nothing():
    parts=setup();parts[3].fail=True;r=run(parts)
    assert r['status']=='retry_delivery';assert parts[-1].calls==0;assert r['charges']==[]

def test_invalid_citations_block_quality_and_do_not_call_more_models():
    parts=setup();parts[-1].bad=True;r=run(parts)
    assert r['status']=='quality_blocked';assert parts[-1].calls==1
    run(parts);assert parts[-1].calls==1

def test_unknown_price_configuration_blocks_paid_call():
    parts=setup();parts[-1].input_cny_per_million=None;r=run(parts)
    assert r['status']=='pending_pricing';assert parts[-1].calls==0

def test_failed_call_keeps_reservation_and_stops_bounded_retries():
    parts=setup();parts[-1].empty=True;r=run(parts)
    assert r['status']=='retry_provider';assert r['charges'][0]['state']=='reserved'
    run(parts);run(parts)
    assert parts[-1].calls==2

def test_stale_model_baseline_blocks_before_api():
    parts=setup();parts[2]['baseline_version']='old';parts[1].put(parts[2])
    r=run(parts);assert r['status']=='baseline_conflict';assert parts[-1].calls==0


def test_weekly_cloud_worker_exports_request_and_never_uses_paid_fallback():
    parts=setup();parts[2]['kind']='weekly_deep';parts[1].put(parts[2]);r=run(parts)
    assert r['status']=='waiting_subscription';assert parts[-1].calls==0
    assert r['checkpoints']['request']['verified']


def test_incompatible_supplied_model_is_rejected_before_api():
    m,s,t,d,r,p=setup()
    out=m.process(t['task_id'],store=s,drive=d,reports=r,providers=[p],budget=Budget(s),day='2026-10-27',
                  model=dict(issuer_id='other',status='ready_for_review'))
    assert out['status']=='quality_blocked';assert p.calls==0


def test_received_response_updates_attempt_state_before_cost_settlement():
    parts=setup();r=run(parts)
    assert r['attempts'][0]['status']=='response_received'
