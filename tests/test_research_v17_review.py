"""Reproductions of the final whole-branch review findings."""
from copy import deepcopy
import json
from research.runtime import MemoryStore
from research.profiles import resolve_profile
from research.workflow import plan_task,advance_task,STAGES
from research.v17 import build_adapters
from tests.test_research_profiles import profile
from tests.test_research_workflow import event
from tests.test_broker_sources import Drive


def make_task(store,revision='v1'):
    task=plan_task(event(revision),resolve_profile(profile(),{}),{},as_of='2026-11-02');store.put(task);return task


def test_summary_alongside_full_report_does_not_require_another_complete_three_statements():
    from research.sources import document_type
    from tests.test_financial_coverage import pages
    assert document_type('2026年年度报告摘要')=='年度报告摘要'
    s=MemoryStore();t=make_task(s);p=pages()
    for row in p:row['included']=True
    docs=[{'issuer_id':'issuer-a','document_id':'full','revision_id':'full-sha','document_type':'年度报告',
        'period_end':'2025-12-31','pages':p},
        {'issuer_id':'issuer-a','document_id':'summary','revision_id':'summary-sha','document_type':'年度报告摘要',
         'period_end':'2025-12-31','pages':[{'page':1,'text':'summary only'}]}]
    a=build_adapters(store=s,drive=Drive(),reports=None,env={},documents=lambda t:docs)
    out=a['coverage']({},t)
    assert out['status']=='ready' and len(out['documents'])==2


def test_source_revision_invalidates_stages_and_resumes_with_a_new_frozen_input_generation():
    from research import workflow
    assert hasattr(workflow,'InputsChanged'), 'newly discovered inputs need an explicit invalidation path'
    s=MemoryStore();t=make_task(s);calls=[];changed=[True]
    def stage(data,task):calls.append(task['running_stage']);return {'issuer_id':'issuer-a','status':'ready'}
    def check(data,task):
        if changed.pop() if changed else False:raise workflow.InputsChanged('new official revision')
    adapters={k:stage for k in STAGES};adapters.update(baseline=lambda t:None,prepublish=check,
        publish=lambda data,t:{'verified':True,'id':'r','input_digest':t['input_digest']})
    first=advance_task(t['task_id'],store=s,adapters=adapters,now='2026-11-02')
    assert first['status']=='waiting_source' and not first['stage_refs']
    out=advance_task(t['task_id'],store=s,adapters=adapters,now='2026-11-02')
    assert out['status']=='completed' and calls.count('coverage')==2
    assert out['input_generation']==1 and out['stage_history']


def test_two_queued_events_rebase_to_current_research_before_analysis_and_publication():
    s=MemoryStore();a=make_task(s,'v1');b=make_task(s,'v2');publications=[];previous=[]
    def stage(data,t):
        if t['running_stage']=='coverage':previous.append(data['previous'].get('last_published_research',{}).get('id'))
        return {'issuer_id':'issuer-a','status':'ready'}
    def publish(data,t):
        publications.append(t['task_id']);return {'id':'report-'+str(len(publications)),'verified':True,'input_digest':t['input_digest']}
    adapters={k:stage for k in STAGES};adapters.update(baseline=lambda t:None,publish=publish)
    assert advance_task(a['task_id'],store=s,adapters=adapters,now='2026-11-02')['status']=='completed'
    out=advance_task(b['task_id'],store=s,adapters=adapters,now='2026-11-02')
    assert out['status']=='completed' and previous==[None,'report-1']
    assert out['next_state']['last_published_research']['id']=='report-2'


def test_metadata_change_cannot_retry_unknown_download_of_same_report_id():
    from research.retrieval import plan_retrieval,run_retrieval
    j=MemoryStore();attempts=[]
    def acquire(row):attempts.append(row['report_id']);raise OSError('unknown download result')
    for title in ('old title','updated platform title'):
        plan=plan_retrieval({'issuer_id':'issuer-a','question':'核心问题','as_of':title,'queries':['query']},[],limits={})
        run_retrieval(plan,{'search':lambda q:[{'report_id':42,'title':title}],'acquire':acquire},j)
    assert attempts==[42]


def test_model_issuer_and_forecast_source_identity_are_not_masked_by_outer_task():
    from research.forecast_inputs import compare_forecasts
    from tests.test_forecast_inputs import facts,assumptions
    s=MemoryStore();t=make_task(s);f=facts()
    for row in f:row.update(issuer_id='different',source_ref='doc-a',quote=row['field']+'='+str(row['value']))
    a=assumptions()
    for row in a:row['source_refs']=['doc-a']
    d={'issuer_id':'issuer-a','document_id':'d','revision_id':'doc-a','document_type':'公告',
       'published_at':'2026-11-01','pages':[{'page':1,'text':'\n'.join(x['quote'] for x in f)}]}
    adapters=build_adapters(store=s,drive=Drive(),reports=None,env={},documents=lambda t:[d],
        reasoner=lambda *args:{'facts':f,'assumptions':a,'broker_estimates':[],'guidance':[]})
    out=advance_task(t['task_id'],store=s,adapters=adapters,now='2026-11-02')
    assert out['status']=='quality_blocked'
    own={'issuer_id':'issuer-a','metric':'eps','value':3,'currency':'CNY','unit':'CNY/share','period_end':'2027-12-31',
         'scope':'consolidated','accounting_basis':'statutory_parent','share_basis':'weighted_average'}
    other=dict(own,issuer_id='different',value=2,source_ref='unread',estimate_owner='broker')
    assert not compare_forecasts({'estimates':[own]},[other])['comparable']


def test_terminal_partial_and_already_satisfied_requests_do_not_starve_local_queue(monkeypatch):
    from research.local import run_local_queue
    monkeypatch.delenv('CI',raising=False);monkeypatch.delenv('GITHUB_ACTIONS',raising=False)
    s=MemoryStore();drive=Drive()
    for i in range(3):
        t=make_task(s,'v'+str(i));t.update(kind='weekly_deep',input_fingerprint='fp'+str(i))
        request={'task_id':t['task_id'],'input_fingerprint':t['input_fingerprint'],'prompt':'question '+str(i),
                 'response_path':'投资/A/'+str(i)+'/subscription-response.json'}
        t['checkpoints']['request']=drive.put_immutable('投资/A/'+str(i)+'/subscription-request.json',json.dumps(request).encode())
        if i==0:t.update(status='partial',next_state={'published':True})
        else:t['status']='waiting_subscription'
        if i==1:drive.put_immutable(request['response_path'],json.dumps(dict(request,content='existing')).encode())
        s.put(t)
    calls=[]
    result=run_local_queue(s,drive,limit=1,subscription=lambda p:calls.append(p) or '{}',workflow_contract='holdings-v17')
    assert calls==['question 2'] and result['candidates']==1


def test_cloud_worker_exits_incomplete_when_model_is_waiting_for_price(monkeypatch,capsys):
    from types import SimpleNamespace
    from research import v17_live,cli
    s=MemoryStore();t=make_task(s)
    monkeypatch.setattr(cli,'notion_http',lambda env:object())
    monkeypatch.setattr(cli,'graph_drive',lambda env:Drive())
    monkeypatch.setattr(cli,'registry',lambda *args:[])
    monkeypatch.setattr(cli,'active_roster',lambda *args:{'holdings':[t['profile']],'unmapped':[]})
    monkeypatch.setattr(v17_live,'NotionTaskStore',lambda *args,**kw:s)
    monkeypatch.setattr(v17_live,'NotionReports',lambda *args:None)
    monkeypatch.setattr(v17_live.OfficialSource,'documents',lambda *args:[{'issuer_id':'issuer-a',
        'document_id':'d','revision_id':'doc','document_type':'公告','published_at':'2026-11-01',
        'pages':[{'page':1,'text':'official original'}]}])
    monkeypatch.setattr(v17_live,'load_prior_context',lambda *args:{'items':[],'gaps':[]})
    args=SimpleNamespace(mode='worker',limit=3)
    result=v17_live.run(args,{'RESEARCH_NOTION_TASKS_SOURCE_ID':'tasks','RESEARCH_NOTION_REPORTS_SOURCE_ID':'reports'})
    assert s.get(t['task_id'])['status']=='waiting_budget'
    assert result==1 and 'waiting_budget' in capsys.readouterr().out
