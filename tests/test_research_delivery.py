import importlib,json
import pytest
from tests.test_research_runtime import event,holding

def api():
    try:return importlib.import_module('research.delivery')
    except ModuleNotFoundError:pytest.fail('Cloud delivery adapters are not implemented')


class FakeHTTP:
    def __init__(self):self.calls=[];self.files={};self.uploads={};self.fail_readback=False
    def __call__(self,method,path,*,body=None,headers=None,raw=False,authenticated=True):
        self.calls.append((method,path,headers))
        m=api()
        if method=='GET' and path.endswith(':/content'):
            key=path[:-9]
            if key not in self.files:raise m.ServiceError(404)
            return b'wrong' if self.fail_readback else self.files[key]['body']
        if method=='GET':
            if not path.endswith(('.json','.xlsx')):return dict(id='folder',folder={})
            if path not in self.files:raise m.ServiceError(404)
            return {k:v for k,v in self.files[path].items() if k!='body'}
        if method=='POST' and path.endswith(':/createUploadSession'):
            key=path[:-21];url='https://upload.invalid/'+str(len(self.uploads))
            self.uploads[url]=key;return {'uploadUrl':url}
        if method=='PUT' and path.startswith('https://upload.invalid/'):
            key=self.uploads[path];self.files[key]=dict(id='id-'+key,eTag='etag-1',webUrl='https://drive.invalid/file',body=body)
            return {'id':'id-'+key,'eTag':'etag-1','webUrl':'https://drive.invalid/file'}
        raise AssertionError((method,path))


def test_graph_candidate_is_immutable_idempotent_and_readback_verified():
    h=FakeHTTP();drive=api().GraphDrive(h)
    a=drive.put_immutable('投资/公司A/04_研究底稿/task/evidence.json',b'abc')
    assert a['verified']
    count=len(h.calls);drive.put_immutable('投资/公司A/04_研究底稿/task/evidence.json',b'abc')
    assert len(h.calls)==count+2
    with pytest.raises(api().VersionConflict):drive.put_immutable('投资/公司A/04_研究底稿/task/evidence.json',b'changed')


def test_graph_never_marks_upload_verified_if_readback_differs():
    h=FakeHTTP();h.fail_readback=True
    with pytest.raises(api().DeliveryError):api().GraphDrive(h).put_immutable('投资/A/test.json',b'abc')


@pytest.mark.parametrize('path',['../outside.json','/投资/A.json','投资/../secret','other/file.json'])
def test_graph_rejects_unscoped_or_traversing_paths(path):
    with pytest.raises(ValueError):api().GraphDrive(FakeHTTP()).put_immutable(path,b'abc')


def test_upload_resume_does_not_rewrite_existing_human_file():
    h=FakeHTTP();drive=api().GraphDrive(h);drive.put_immutable('投资/A/model.xlsx',b'human')
    with pytest.raises(api().VersionConflict):drive.put_immutable('投资/A/model.xlsx',b'candidate')


def test_notion_task_store_paginates_and_preserves_manual_review():
    m=api();calls=[]
    checkpoint=json.dumps(dict(task_id='abc',status='pending',human_review='pending',charges=[]))
    page=dict(id='page-1',properties={'Checkpoint':{'rich_text':[{'plain_text':checkpoint}]},'HumanReview':{'select':{'name':'有疑问'}}})
    def http(method,path,**kwargs):
        calls.append((method,path,kwargs))
        if path.endswith('/query'):
            if kwargs.get('body',{}).get('start_cursor'):return dict(results=[page],has_more=False)
            return dict(results=[],has_more=True,next_cursor='next')
        return page
    store=m.NotionTaskStore(http,'data-source')
    rows=store.list();assert len(rows)==1;assert rows[0]['human_review']=='有疑问'
    store.put(rows[0]);patch=calls[-1][2]['body']['properties']
    assert 'HumanReview' not in patch


def test_backup_restore_checks_digest_and_never_overwrites_newer_files(tmp_path):
    m=api();source=tmp_path/'company';source.mkdir();(source/'a.json').write_bytes(b'original')
    snapshot=tmp_path/'snapshot.zip';m.backup(source,snapshot)
    restored=tmp_path/'restored';m.restore(snapshot,restored)
    assert (restored/'a.json').read_bytes()==b'original'
    (restored/'a.json').write_bytes(b'newer-human-edit')
    with pytest.raises(FileExistsError):m.restore(snapshot,restored)
    assert (restored/'a.json').read_bytes()==b'newer-human-edit'


def test_durable_checkpoint_uses_verified_text_reference_instead_of_large_inline_payload():
    from research.runtime import enqueue,MemoryStore
    task=enqueue(MemoryStore(),event(),holding());task['evidence']['text_ref']={'path':'投资/A/text.json','verified':True}
    saved=[]
    def http(method,path,**kw):
        if path.endswith('/query'):return dict(results=[],has_more=False)
        saved.append(kw['body']);return dict(id='new')
    store=api().NotionTaskStore(http,'source');store.put(task)
    encoded=api().plain(saved[0]['properties']['Checkpoint']['rich_text']);checkpoint=json.loads(encoded)
    assert checkpoint['evidence']['content']==''
    assert checkpoint['content_external'] is True


def test_notion_report_requires_content_and_key_readback_before_delivery_receipt():
    from research.runtime import enqueue,MemoryStore,validate_report
    from tests.test_research_runtime import report
    t=enqueue(MemoryStore(),event(),holding());t['publication_key']='publication-v1'
    r=report(t['card']['revision_id']);a=dict(report=r,validation=validate_report(r,[t['card']],model=None),actual_model='model',provider='test')
    def http(method,path,**kw):
        if path.endswith('/query'):return dict(results=[],has_more=False)
        if path=='/pages':return dict(id='created',url='https://notion.invalid/report')
        if path=='/pages/created':return {'properties':{'TriggerEventID':{'rich_text':[{'plain_text':'wrong-key'}]}}}
        raise AssertionError('must verify key before content')
    with pytest.raises(api().DeliveryError):api().NotionReports(http,'source').publish(t,a,{})
