from copy import deepcopy
import importlib,json,hashlib
import pytest
from research.runtime import MemoryStore,enqueue,Budget
from research.delivery import NotionTaskStore,NotionReports,ServiceError,DeliveryError
from research.engine import process
from tests.test_research_runtime import event,holding,report
from tests.test_research_engine import setup,run
from tests.test_forecast_model import baseline,scenarios


def test_archived_pdf_whitespace_hydrates_same_normalized_revision():
    e=event();e['content']='  Report revenue 100.\n'
    t=enqueue(MemoryStore(),e,holding());t['evidence']['text_ref']={'path':'投资/A/text.json','verified':True}
    t['evidence']['content']='';t['content_external']=True
    page={'id':'p','properties':{'Checkpoint':{'rich_text':[{'plain_text':json.dumps(t)}]}}}
    http=lambda *a,**k:dict(results=[page],has_more=False)
    store=NotionTaskStore(http,'ds',content_loader=lambda _:json.dumps({'text':e['content']}).encode())
    assert store.get(t['task_id'])['evidence']['content']=='Report revenue 100.'


def test_selection_skips_blocked_inactive_and_release_states_before_limit():
    from research import cli
    rows=[dict(task_id=str(i),issuer_id='a',status='quality_blocked',priority=0,created_at=str(i)) for i in range(3)]
    rows += [dict(task_id='valid',issuer_id='a',status='pending',priority=0,created_at='4'),
             dict(task_id='inactive',issuer_id='b',status='pending',priority=0,created_at='0'),
             dict(task_id='release',issuer_id='a',status='retry_release',priority=0,created_at='0')]
    assert [x['task_id'] for x in cli.select_tasks(rows,{'a'},limit=3,day='2026-10-28')]==['valid']


def test_crash_after_reservation_preserves_attempt_and_does_not_repay_or_wedge():
    parts=setup();m,s,t,d,r,p=parts
    original=s.put
    class Crash(BaseException):pass
    def crash(row):
        original(row)
        if row['charges'] and row['charges'][0]['state']=='reserved':raise Crash()
    s.put=crash
    with pytest.raises(Crash):run(parts)
    s.put=original
    assert s.get(t['task_id'])['attempts']
    out=run(parts)
    assert out['status']=='pending_attempt_confirmation';assert p.calls==0


def test_same_document_id_is_revalidated_for_changed_content(monkeypatch):
    from research import cli,sources
    bodies=iter([b'<p>old facts</p>',b'<p>new facts</p>']);downloads=[]
    row=dict(title='重大合同',url='https://static.cninfo.com.cn/a.html',document_id='same',published_at='2026-10-26T00:00:00+00:00')
    monkeypatch.setattr(sources,'cninfo',lambda *a:[row])
    monkeypatch.setattr(sources,'download_primary',lambda _:downloads.append(1) or next(bodies))
    class Drive:
        def put_immutable(self,path,data):return dict(path=path,verified=True,sha256=hashlib.sha256(data).hexdigest())
    s=MemoryStore();roster={'holdings':[dict(holding(),active_tickers=['000001.SZ'])],'unmapped':[]}
    cli.scan(roster,s,Drive(),start='2026-10-01',end='2026-10-28')
    cli.scan(roster,s,Drive(),start='2026-10-01',end='2026-10-28')
    resolved=[x for x in s.list() if x['evidence']['content']]
    assert len(downloads)==2;assert len(resolved)==2


def test_recovered_notion_report_rejects_wrong_body_even_with_matching_key():
    t=enqueue(MemoryStore(),event(),holding());t['publication_key']='version'
    a=dict(report=report(t['card']['revision_id']),actual_model='model',provider='test',validation={'status':'pending_model'})
    def http(method,path,**kw):
        if path.endswith('/query'):return dict(results=[{'id':'report'}],has_more=False)
        if path=='/pages/report':return {'properties':{'TriggerEventID':{'rich_text':[{'plain_text':'version'}]}}}
        return dict(results=[{'type':'paragraph','paragraph':{'rich_text':[{'plain_text':'Unrelated report'}]}}],has_more=False)
    with pytest.raises(DeliveryError):NotionReports(http,'ds').publish(t,a,{},lookup_only=True)


def test_ordinary_processing_never_mutates_partial_release_or_published_task():
    parts=setup();parts[2]['status']='retry_release';parts[1].put(parts[2])
    assert run(parts)['status']=='retry_release';assert parts[-1].calls==0
    parts[2]['status']='published';parts[1].put(parts[2]);assert run(parts)['status']=='published'


def test_blank_pdf_locator_is_not_substantive_source_content():
    import fitz
    from research.sources import extract
    with fitz.open() as d:
        d.new_page();parsed=extract(d.tobytes(),kind='pdf')
    assert parsed['text']=='';assert parsed['substantive_characters']==0
    parts=setup();parts[2]['evidence']['content']='[PDF page 1]\n';parts[1].put(parts[2])
    assert run(parts)['status']=='pending_evidence';assert parts[-1].calls==0


def test_forecast_requires_raw_currency_amounts_and_share_counts():
    from financials.forecast import project
    b=baseline();b['amount_unit']='CNY_millions'
    with pytest.raises(ValueError,match='unit'):project(b,scenarios(),as_of='2026-10-27')
    b=baseline();b.pop('share_unit',None)
    with pytest.raises(ValueError,match='unit'):project(b,scenarios(),as_of='2026-10-27')


def test_local_backend_uses_no_legacy_settings_or_dotenv(monkeypatch):
    import dotenv
    called=[];monkeypatch.setattr(dotenv,'load_dotenv',lambda *a,**k:called.append(1))
    try:backend=importlib.import_module('research.subscription')
    except ModuleNotFoundError:pytest.fail('Isolated subscription backend is missing')
    monkeypatch.setenv('CI','true')
    assert backend.call_subscription('evidence')=='';assert called==[]


def test_local_poll_reuses_existing_candidate_without_calling_subscription(monkeypatch):
    from research.local import run_local_queue
    monkeypatch.delenv('CI',raising=False);monkeypatch.delenv('GITHUB_ACTIONS',raising=False)
    s=MemoryStore();t=enqueue(s,event(),holding(),kind='weekly_deep',week='2026-10-24')
    t['input_fingerprint']='fp';t['checkpoints']['request']={'path':'投资/A/subscription-request.json'};s.put(t)
    class Drive:
        def __init__(self):self.files={}
        def read(self,path):
            if path.endswith('subscription-request.json'):
                return json.dumps(dict(task_id=t['task_id'],input_fingerprint='fp',prompt='e',response_path='投资/A/subscription-response.json')).encode(),{}
            if path not in self.files:raise ServiceError(404)
            return self.files[path],{}
        def put_immutable(self,path,data):self.files[path]=data;return dict(verified=True)
    d=Drive();calls=[]
    for _ in range(2):run_local_queue(s,d,limit=1,subscription=lambda _:calls.append(1) or '{}')
    assert len(calls)==1


def test_execution_compares_precise_prior_model_assumptions():
    from research.context import prepare_task_context
    from financials.forecast import project
    old=project(baseline(),scenarios(),as_of='2026-10-27');new=scenarios();new['base'][0]['values']['growth']=.2
    h=dict(holding(),baseline_version=old['model_version'],forecast_inputs_path='投资/公司A/inputs.json')
    s=MemoryStore();t=enqueue(s,event(),h)
    class Drive:
        def read(self,path):
            if path.endswith('research-current.json'):data=dict(model_version=old['model_version'],model={'path':'投资/公司A/old.json'})
            elif path.endswith('old.json'):data=old
            elif path.endswith('inputs.json'):data=dict(baseline=baseline(),scenarios=new,as_of='2026-10-27')
            else:raise ServiceError(404)
            return json.dumps(data).encode(),{}
    model=prepare_task_context(t,s,Drive(),None,h)
    assert model['assumption_changes'];assert any(x['field']=='growth' for x in model['assumption_changes'])


def test_model_cutoff_before_trigger_requires_refresh_before_paid_research():
    from financials.forecast import project
    m,s,t,d,r,p=setup()
    stale=project(baseline(),scenarios(),as_of='2026-01-01')
    out=m.process(t['task_id'],store=s,drive=d,reports=r,providers=[p],budget=Budget(s),day='2026-10-28',model=stale)
    assert out['status']=='pending_model_refresh';assert p.calls==0


def test_pilot_schema_upgrade_only_adds_missing_columns():
    from research.cli import ensure_pilot_schema
    calls=[]
    def http(method,path,**kw):
        calls.append((method,kw))
        return {'properties':{'HumanReview':{'select':{'options':[{'name':'custom-human-state'}]}}}}
    ensure_pilot_schema(http,'pilot',{'HumanReview':{'select':{}},'RetryRequested':{'checkbox':{}}})
    assert calls[-1][0]=='PATCH'
    assert calls[-1][1]['body']['properties']=={'RetryRequested':{'checkbox':{}}}
