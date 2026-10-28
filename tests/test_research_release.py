import importlib
import pytest

def api():
    try:return importlib.import_module('research.release')
    except ModuleNotFoundError:pytest.fail('Acceptance and rollback gates are not implemented')

def test_two_week_trial_cannot_be_claimed_complete_from_synthetic_or_one_day_runs():
    rows=[dict(day='2026-10-27',issuer_id=str(i),environment='synthetic',delivery_verified=True,kind='earnings') for i in range(3)]
    r=api().trial_readiness(rows,as_of='2026-10-27')
    assert r['ready'] is False;assert r['real_issuers']==0

def test_two_week_trial_requires_verified_deliveries_three_issuers_and_all_task_classes():
    rows=[]
    for i,kind in enumerate(['earnings','incremental','no_change']):
        for day in ['2026-10-01','2026-10-16']:
            rows.append(dict(day=day,issuer_id=str(i),environment='pilot_live',delivery_verified=True,kind=kind))
    assert api().trial_readiness(rows,as_of='2026-10-27')['ready'] is True
    rows[-1]['delivery_verified']=False
    assert api().trial_readiness(rows,as_of='2026-10-27')['ready'] is False

def test_formal_release_rejects_unaccepted_or_unverified_candidate_before_side_effects():
    with pytest.raises(ValueError):api().release_candidate({},None,None,None,enabled=False,accepted_model_version='v')

def test_rollback_rejects_wrong_version_and_never_deletes_reports_or_models():
    class Drive:
        def read(self,path):return b'{"model_version":"new-human-version"}',{'eTag':'new'}
        def update_pointer(self,*a,**k):raise AssertionError('must not write')
    with pytest.raises(api().VersionConflict):api().rollback_pointer(Drive(),'投资/A',
         {'model_version':'old'},expected_current='candidate')


def test_release_resumes_lost_notion_response_and_keeps_old_pointer_snapshot():
    import json
    from research.runtime import MemoryStore
    from research.delivery import ServiceError
    class Drive:
        def __init__(self):self.files={'投资/A/research-current.json':json.dumps({'model_version':'old'}).encode()};self.serial=1
        def read(self,path):
            if path not in self.files:raise ServiceError(404)
            return self.files[path],{'eTag':str(self.serial)}
        def put_immutable(self,path,data):
            assert path not in self.files or self.files[path]==data
            self.files[path]=data;return dict(verified=True)
        def update_pointer(self,path,value,*,expected_version,expected_etag):
            assert json.loads(self.files[path])['model_version']==expected_version
            assert expected_etag==str(self.serial)
            self.files[path]=json.dumps(value).encode();self.serial+=1
    class Reports:
        def __init__(self):self.created=False;self.count=0
        def publish(self,*a,**kw):
            if not self.created:self.created=True;self.count+=1;raise RuntimeError('response lost')
            return dict(id='report',url='https://notion.invalid/report',verified=True)
    t=dict(task_id='t',status='completed',human_review='已确认',company_path='投资/A',baseline_version='old',publication_key='pub',
           analysis=dict(validation={'status':'ready_for_review'},model_version='new'),
           checkpoints={'source':{'verified':True},'result':{'verified':True}})
    d=Drive();r=Reports();s=MemoryStore();s.put(t)
    with pytest.raises(RuntimeError):api().release_candidate(t,s,d,r,enabled=True,accepted_model_version='new')
    out=api().release_candidate(s.get('t'),s,d,r,enabled=True,accepted_model_version='new')
    assert out['status']=='published';assert r.count==1
    assert json.loads(d.files['投资/A/research-current.json'])['publication_status']=='complete'
    assert json.loads(d.files['投资/A/05_历史版本/old/pointer.json'])['model_version']=='old'
