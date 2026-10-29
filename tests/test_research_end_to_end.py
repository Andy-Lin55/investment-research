"""Exercise real durable adapters across model/report/follow-up lost responses."""
from copy import deepcopy
import json,hashlib
from financials.forecast import project
from research.delivery import GraphDrive,NotionReports,NotionTaskStore,plain,ServiceError
from research.followups import NotionFollowups
from research.runtime import Budget,enqueue
from research.engine import process,encoded
from research.context import prepare_task_context
from tests.test_research_delivery import FakeHTTP
from tests.test_research_runtime import event,holding,report
from tests.test_forecast_model import baseline,scenarios


class Notion:
    def __init__(self):self.rows={};self.lost=set();self.blocks={};self.serial=0
    def save_blocks(self,blocks):
        result=[]
        for b in deepcopy(blocks):
            self.serial+=1;b['id']='block-'+str(self.serial);kind=b['type'];kids=b[kind].pop('children',[])
            b['has_children']=bool(kids)
            if kids:self.blocks[b['id']]=self.save_blocks(kids)
            result.append(b)
        return result
    def __call__(self,method,path,*,body=None):
        if path.endswith('/query'):
            source=path.split('/')[2];rows=[r for r in self.rows.values() if r['parent']['data_source_id']==source]
            f=body.get('filter')
            if f:
                prop=f['property'];kind='title' if 'title' in f else 'rich_text';expected=f[kind]['equals']
                rows=[r for r in rows if plain(r['properties'].get(prop,{}).get(kind,[]))==expected]
            return dict(results=deepcopy(rows),has_more=False)
        if method=='POST' and path=='/pages':
            key='page-'+str(len(self.rows)+1)
            page=dict(deepcopy(body),id=key,url='https://notion.invalid/'+key)
            self.blocks[key]=self.save_blocks(page.pop('children',[]))
            self.rows[key]=page;source=page['parent']['data_source_id']
            if source in {'reports','followups'} and source not in self.lost:
                self.lost.add(source);raise ServiceError(0)
            return deepcopy(page)
        if path.startswith('/pages/'):
            row=self.rows[path.split('/')[2]]
            if method=='PATCH':row['properties'].update(deepcopy(body['properties']))
            return deepcopy(row)
        if path.startswith('/blocks/'):
            return dict(results=deepcopy(self.blocks[path.split('/')[2]]),has_more=False)
        raise AssertionError((method,path))


def test_complete_research_resumes_across_real_durable_adapters_without_paid_calls():
    http=Notion();drive=GraphDrive(FakeHTTP())
    store=NotionTaskStore(http,'tasks',content_loader=lambda ref:drive.read(ref['path'])[0])
    reports=NotionReports(http,'reports');followups=NotionFollowups(http,'followups')
    task=enqueue(store,event(),holding(),kind='weekly_deep',week='2026-10-27')
    model=project(baseline(),scenarios(),as_of='2026-10-27')
    def run():return process(task['task_id'],store=store,drive=drive,reports=reports,providers=[],
                             budget=Budget(store),day='2026-10-27',model=model,followups=followups)
    out=run();assert out['status']=='waiting_subscription'
    request=json.loads(drive.read(out['checkpoints']['request']['path'])[0])
    r=report(out['card']['revision_id']);r['valuation_status']='complete'
    response=dict(task_id=out['task_id'],input_fingerprint=out['input_fingerprint'],actual_model='SYNTHETIC/no AI',
                  model_provenance='synthetic_test_only',content=json.dumps(r))
    drive.put_immutable(request['response_path'],encoded(response))
    assert run()['status']=='retry_delivery'  # report created, response lost
    out=run();assert out['status']=='retry_delivery'  # follow-up created, response lost
    rebuilt=prepare_task_context(out,store,drive,http,holding(),followups=followups)
    assert rebuilt==model
    out=run();assert out['status']=='completed'
    assert store.get(task['task_id'])['status']=='completed'
    assert out['charges']==[];assert out['attempts']==[]
    assert len(out['checkpoints']['followups'])==2
    sources=[row['parent']['data_source_id'] for row in http.rows.values()]
    assert sources.count('reports')==1;assert sources.count('followups')==2
    raw=drive.read(out['checkpoints']['model_workbook']['path'])[0]
    assert hashlib.sha256(raw).hexdigest()==out['checkpoints']['model_workbook']['sha256']
    assert run()['publication_key']==out['publication_key']
