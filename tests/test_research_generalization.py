"""Migration/failure cases supplement the single-company regression sample."""
from copy import deepcopy
from importlib import import_module
import json
import pytest
from research.runtime import MemoryStore
from research.workflow import plan_task,advance_task,company_state
from research.profiles import resolve_profile,forecast_years
from research.v17 import build_adapters
from tests.test_research_profiles import profile
from tests.test_research_workflow import event
from tests.test_broker_sources import Drive
from tests.test_research_report_contract import report,payload
from tests.test_research_v17_runner import api


def test_weekly_no_change_is_recorded_without_repeating_reasoning_or_fabricating_report():
    p=resolve_profile(profile(),{});s=MemoryStore();t=plan_task(event(kind='weekly_screen'),p,{},as_of='2026-11-02');s.put(t)
    pdoc={'issuer_id':'issuer-a','document_id':'d1','revision_id':'sha1','document_type':'公告',
          'published_at':'2026-11-01','pages':[{'page':8,'text':'unchanged original'}]}
    previous={'source_revisions':{'d1':'sha1'},'open_questions':[]}
    t['previous_state']=previous;s.put(t)
    def forbidden(*a):pytest.fail('unchanged weekly screen cannot call AI')
    adapters=build_adapters(store=s,drive=Drive(),reports=None,env={},documents=lambda t:[pdoc],reasoner=forbidden)
    out=advance_task(t['task_id'],store=s,adapters=adapters,now='2026-11-02')
    assert out['status']=='no_change' and out.get('screened_at')=='2026-11-02'
    assert not out.get('publication_files')


def test_due_question_prevents_no_change_shortcut_and_changes_weekly_task_to_subscription():
    p=resolve_profile(profile(),{});s=MemoryStore()
    t=plan_task(event(kind='weekly_screen'),p,{'source_revisions':{'d1':'sha1'},
        'open_questions':[{'id':'q1','need':'核验回款','due':'2026-11-01'}]},as_of='2026-11-02');s.put(t)
    doc={'issuer_id':'issuer-a','document_id':'d1','revision_id':'sha1','document_type':'公告',
         'published_at':'2026-11-01','pages':[{'page':1,'text':'unchanged'}]}
    adapters=build_adapters(store=s,drive=Drive(),reports=None,env={},documents=lambda t:[doc])
    out=advance_task(t['task_id'],store=s,adapters=adapters,now='2026-11-02')
    assert out['status']=='waiting_subscription' and out['kind']=='weekly_deep'
    assert out['checkpoints']['request']['verified'] is True


def test_automatic_state_keeps_source_versions_and_confirmed_human_baseline():
    from research.workflow import build_next_state
    p={'accepted_baseline':{'version':'manual'},'open_questions':[{'id':'old','need':'待核验'}]}
    r={'issuer_id':'issuer-b','source_revisions':{'filing-2':'newsha'},'questions':[]}
    out=build_next_state(p,r,manifest={'verified':True,'id':'report','input_digest':'digest','issuer_id':'issuer-b'})
    assert out['source_revisions']=={'filing-2':'newsha'} and out['accepted_baseline']=={'version':'manual'}
    assert out['open_questions'][0]['id']=='old'


def test_paid_calls_are_checkpointed_before_response_and_survive_reloaded_store():
    s=MemoryStore();t=plan_task(event(),resolve_profile(profile(),{}),{},as_of='2026-11-02');s.put(t)
    class Provider:
        name='paid';model='requested';input_cny_per_million=.1;output_cny_per_million=.2
        def __call__(self,prompt,**kwargs):
            fresh=s.get(t['task_id']);assert fresh['charges'][0]['state']=='reserved'
            return dict(content='{}',actual_model='actual-returned',input_tokens=10,output_tokens=10)
    out=api().reason('judgment',{},t,store=s,drive=Drive(),providers=[Provider()],now='2026-11-02')
    assert out=={} and s.get(t['task_id'])['charges'][0]['state']=='charged'
    assert s.get(t['task_id'])['inference'][0]['actual_model']=='actual-returned'


def test_candidate_fact_cannot_claim_a_different_value_than_its_quoted_original():
    from tests.test_forecast_inputs import facts,assumptions
    p=resolve_profile(profile(),{});s=MemoryStore();t=plan_task(event(),p,{},as_of='2026-11-02');s.put(t)
    f=facts()
    for row in f:row.update(source_ref='doc-a',quote=row['field']+'='+str(row['value']))
    text='\n'.join(x['quote'] for x in f);f[0]['value']=9000
    doc=dict(issuer_id='issuer-a',document_id='d1',revision_id='doc-a',document_type='公告',
             published_at='2026-11-01',pages=[{'page':1,'text':text}])
    a=assumptions()
    for row in a:row['source_refs']=['doc-a']
    def infer(stage,payload,t):return {'facts':f,'guidance':[],'broker_estimates':[],'assumptions':a}
    adapters=build_adapters(store=s,drive=Drive(),reports=None,env={},documents=lambda t:[doc],reasoner=infer)
    out=advance_task(t['task_id'],store=s,adapters=adapters,now='2026-11-02')
    assert out['status']=='quality_blocked' and not out.get('publication_files')


def test_half_year_and_quarter_titles_are_not_misclassified_as_annual_reports():
    from research.sources import document_type
    from research.v17_live import period_end
    assert document_type('2026年半年度报告')=='中期报告'
    assert period_end('2026年半年度报告','中期报告',profile())=='2026-06-30'


def test_broker_acquisition_bound_is_configurable_without_creating_unlimited_downloads():
    from research.broker_sources import FxBaogao,BrokerError
    from tests.test_broker_sources import Response,pdf
    charged=[]
    def request(method,url,**kwargs):
        if 'api.fxbaogao.com' in url:
            charged.append(url);return Response({'code':0,'data':'https://dr.fxbaogao.com/a.pdf'})
        return Response(pdf())
    client=FxBaogao('placeholder',request=request,max_calls=12,max_downloads=4)
    for i in range(4):assert client.download(i+1).startswith(b'%PDF')
    with pytest.raises(BrokerError):client.download(5)
    assert len(charged)==4


@pytest.mark.parametrize('issuer,year,ticker',[('migration-a',2026,'000001.SZ'),('migration-b',2028,'09999.HK')])
def test_different_company_and_year_complete_through_real_durable_adapters_after_lost_response(issuer,year,ticker):
    from tests.test_research_end_to_end import Notion
    from tests.test_research_delivery import FakeHTTP
    from research.delivery import GraphDrive,NotionReports,NotionTaskStore
    from tests.test_forecast_inputs import facts,assumptions
    http=Notion();drive=GraphDrive(FakeHTTP());store=NotionTaskStore(http,'tasks')
    p=resolve_profile(profile(issuer,ticker),{})
    as_of=f'{year}-11-02';ev=dict(event(),issuer_id=issuer,published_at=f'{year}-10-30')
    task=plan_task(ev,p,{},as_of=as_of);store.put(task)
    f=facts()
    for row in f:
        row.update(issuer_id=issuer,period_end=f'{year-1}-12-31',source_ref='source-'+issuer,
                   source_locator='original p77',quote=row['field']+'='+str(row['value']))
    a=assumptions()
    for row in a:row.update(year=year,source_refs=['source-'+issuer])
    document=dict(issuer_id=issuer,document_id='filing-'+issuer,revision_id='source-'+issuer,
        document_type='公告',published_at=f'{year}-10-30',period_end=f'{year-1}-12-31',
        source={'url':'https://static.cninfo.com.cn/synthetic.pdf'},
        pages=[{'page':77,'text':'\n'.join(x['quote'] for x in f)}])
    calls=[]
    def infer(stage,payload,t):
        calls.append(stage)
        if stage=='model':return {'facts':f,'assumptions':a,'guidance':[],'broker_estimates':[]}
        if stage=='judgment':return {'data_requests':[]}
        r=report()
        for key in ('holding_logic','earnings_differences','changes','counterevidence','next_actions'):
            for item in r[key]:item['evidence']=['source-'+issuer]
        r['next_actions'][0]['due']=f'{year}-11-20';r['questions'][0]['due']=f'{year}-11-20'
        return r
    adapters=build_adapters(store=store,drive=drive,reports=NotionReports(http,'reports'),env={},
        documents=lambda t:[document],reasoner=infer,baseline=lambda t:None)
    first=advance_task(task['task_id'],store=store,adapters=adapters,now=as_of)
    assert first['status']=='retryable_failure'
    # Rebuild the durable store and stage adapter: no in-memory analysis survives.
    cold=NotionTaskStore(http,'tasks');adapters=build_adapters(store=cold,drive=drive,
        reports=NotionReports(http,'reports'),env={},documents=lambda t:[document],reasoner=infer,baseline=lambda t:None)
    out=advance_task(task['task_id'],store=cold,adapters=adapters,now=as_of)
    assert out['status']=='partial' and calls==['model','judgment','report']
    saved=company_state(cold,issuer)
    assert saved['last_published_research']['verified'] is True
    assert saved['source_revisions']=={'filing-'+issuer:'source-'+issuer}
    assert len([x for x in http.rows.values() if x['parent']['data_source_id']=='reports'])==1
