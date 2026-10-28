import importlib,json
import pytest
from research.runtime import MemoryStore,enqueue
from tests.test_research_runtime import event,holding

def api():
    try:return importlib.import_module('research.context')
    except ModuleNotFoundError:pytest.fail('Existing research context ingestion is not implemented')

def test_context_preserves_old_research_as_interpretation_and_records_content_hash():
    store=MemoryStore();t=enqueue(store,event(),holding())
    class Drive:
        def read(self,path):return b'Old human research, not current financial fact',{'webUrl':'https://drive.invalid/old','lastModifiedDateTime':'2024-01-01T00:00:00Z'}
        def put_immutable(self,path,data):return dict(path=path,verified=True)
    h=dict(holding(),context_files=[{'path':'投资/公司A/old.md','role':'prior_research'}])
    assert api().prepare_task_context(t,store,Drive(),None,h) is None
    out=store.get(t['task_id']);assert out['context'][0]['evidence_kind']=='prior_research'
    assert out['context'][0]['source_text'];assert out['context_digest']
    assert 'forecast_inputs_not_configured' in out['context_gaps']

def test_context_cannot_read_outside_company_scope():
    store=MemoryStore();t=enqueue(store,event(),holding())
    h=dict(holding(),context_files=[{'path':'投资/other/private.md','role':'prior_research'}])
    with pytest.raises(ValueError):api().prepare_task_context(t,store,None,None,h)

def test_failed_source_read_is_visible_and_not_claimed_ingested():
    store=MemoryStore();t=enqueue(store,event(),holding())
    class Drive:
        def read(self,path):raise RuntimeError('unavailable')
    h=dict(holding(),context_files=[{'path':'投资/公司A/old.md','role':'prior_research'}])
    api().prepare_task_context(t,store,Drive(),None,h)
    out=store.get(t['task_id']);assert out['context']==[];assert out['context_gaps']
