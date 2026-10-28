import importlib,json
import pytest
from research.runtime import MemoryStore,enqueue
from tests.test_research_runtime import event,holding,report

def api():
    try:return importlib.import_module('research.local')
    except ModuleNotFoundError:pytest.fail('Subscription-only queue worker is not implemented')

def test_local_worker_writes_candidate_only_and_defers_exhausted_subscription(monkeypatch):
    # This test models a local machine using a stub subscription. Keep the
    # separate CI prohibition test active under the real runner environment.
    monkeypatch.delenv('CI',raising=False)
    monkeypatch.delenv('GITHUB_ACTIONS',raising=False)
    store=MemoryStore();t=enqueue(store,event(),holding(),kind='weekly_deep',week='2026-10-24')
    t['input_fingerprint']='fp';t['checkpoints']['request']={'path':'投资/A/request.json'};store.put(t)
    class Drive:
        def __init__(self):self.writes=[]
        def read(self,path):
            from research.delivery import ServiceError
            if not path.endswith('request.json'):raise ServiceError(404)
            return json.dumps(dict(task_id=t['task_id'],input_fingerprint='fp',prompt='evidence',response_path='投资/A/subscription-response.json')).encode(),{}
        def put_immutable(self,path,data):self.writes.append(path);return dict(verified=True,path=path)
    d=Drive();r=api().run_local_queue(store,d,limit=1,subscription=lambda _:'')
    assert r['waiting_subscription']==1;assert d.writes==[]
    r=api().run_local_queue(store,d,limit=1,subscription=lambda _:json.dumps(report(t['card']['revision_id'])))
    assert r['candidates']==1;assert d.writes==['投资/A/subscription-response.json']
    assert store.get(t['task_id'])['status']=='pending'

def test_local_worker_cannot_run_inside_github_cloud(monkeypatch):
    monkeypatch.setenv('GITHUB_ACTIONS','true')
    with pytest.raises(ValueError):api().run_local_queue(MemoryStore(),None,limit=1,subscription=lambda _:'')
