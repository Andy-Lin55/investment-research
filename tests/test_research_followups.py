"""Persistent questions must survive reruns and preserve human decisions."""
from copy import deepcopy
import importlib,json
import pytest
from research.runtime import MemoryStore,Budget,enqueue,validate_report
from tests.test_research_runtime import event,holding,report
from tests.test_research_engine import setup
from research.delivery import plain


def api():
    try:return importlib.import_module('research.followups')
    except ModuleNotFoundError:pytest.fail('Persistent research follow-ups are not implemented')


class Notion:
    def __init__(self):self.rows={};self.lose=False;self.creates=0
    def __call__(self,method,path,*,body=None):
        if path.endswith('/query'):
            rows=list(self.rows.values());f=body.get('filter',{})
            if f.get('property')=='Key':rows=[r for r in rows if plain(r['properties']['Key']['title'])==f['title']['equals']]
            if f.get('property')=='Issuer':rows=[r for r in rows if plain(r['properties']['Issuer']['rich_text'])==f['rich_text']['equals']]
            return dict(results=deepcopy(rows),has_more=False)
        if method=='POST' and path=='/pages':
            self.creates+=1;key='page-'+str(self.creates)
            page=dict(id=key,url='https://notion.invalid/'+key,properties=deepcopy(body['properties']))
            self.rows[key]=page
            if self.lose:self.lose=False;raise RuntimeError('created but response lost')
            return deepcopy(page)
        if method=='GET':return deepcopy(self.rows[path.rsplit('/',1)[-1]])
        pytest.fail('Human state must not be patched by automatic follow-up delivery')


def test_lost_create_response_reconciles_and_keeps_closed_human_decision():
    http=Notion();store=api().NotionFollowups(http,'pilot-followups')
    t=enqueue(MemoryStore(),event(),holding());t['publication_key']='candidate-v1'
    item=api().items_from_report(t,report(t['card']['revision_id']))[0]
    http.lose=True
    with pytest.raises(RuntimeError):store.ensure(item,report_ref={'url':'https://notion.invalid/report'})
    receipt=store.ensure(item,report_ref={'url':'https://notion.invalid/report'},lookup_only=True)
    assert receipt['verified'] is True;assert http.creates==1
    http.rows[receipt['id']]['properties']['State']={'select':{'name':'已完成'}}
    http.rows[receipt['id']]['properties']['HumanNotes']={'rich_text':[{'text':{'content':'人工已核实'}}]}
    store.ensure(item,report_ref={'url':'https://notion.invalid/new-report'})
    assert store.unresolved(t['issuer_id'])==[]
    assert http.creates==1


def test_existing_questions_are_issuer_scoped_and_include_human_notes():
    http=Notion();store=api().NotionFollowups(http,'pilot-followups')
    t=enqueue(MemoryStore(),event(),holding());t['publication_key']='candidate-v1'
    items=api().items_from_report(t,report(t['card']['revision_id']))
    for item in items:store.ensure(item,report_ref={'url':'https://notion.invalid/report'})
    assert store.unresolved('other-issuer')==[]
    rows=store.unresolved(t['issuer_id'])
    assert rows[0]['state']=='待看'
    assert rows[0]['origin_publication']=='candidate-v1'
    assert t['issuer_id'] in store.due_issuers('2026-12-31')
    assert not store.due_issuers('2020-01-01')


def test_ambiguous_missing_followup_is_not_blindly_created_again():
    http=Notion();store=api().NotionFollowups(http,'pilot-followups')
    t=enqueue(MemoryStore(),event(),holding());t['publication_key']='candidate-v1'
    item=api().items_from_report(t,report(t['card']['revision_id']))[0]
    assert store.ensure(item,report_ref={},lookup_only=True) is None
    assert http.creates==0


def test_report_rejects_empty_or_nontext_questions_before_delivery():
    t=enqueue(MemoryStore(),event(),holding())
    for bad in [[''],[{'question':'unstructured'}],[None]]:
        r=report(t['card']['revision_id']);r['open_questions']=bad
        with pytest.raises(ValueError):validate_report(r,[t['card']],model=None)


def test_followup_retry_never_repeats_paid_analysis_or_report_creation():
    m,s,t,d,r,p=setup();r.fail=False;http=Notion();http.lose=True
    followups=api().NotionFollowups(http,'pilot-followups')
    def run():return m.process(t['task_id'],store=s,drive=d,reports=r,providers=[p],budget=Budget(s),day='2026-10-27',followups=followups)
    out=run();assert out['status']=='retry_delivery';assert p.calls==1;assert r.count==1
    out=run();assert out['status']=='pending_model'
    assert p.calls==1;assert r.count==1
    assert len(out['checkpoints']['followups'])==len(api().items_from_report(out,out['analysis']['report']))


def test_unresolved_items_enter_next_context_as_prior_analysis():
    from research.context import prepare_task_context
    s=MemoryStore();t=enqueue(s,event(),holding())
    class Drive:
        def put_immutable(self,path,data):return dict(path=path,verified=True)
    class Followups:
        def unresolved(self,issuer):return [dict(key='open-1',question='复核现金流',state='有疑问',due='2026-10-29')]
    prepare_task_context(t,s,Drive(),None,holding(),followups=Followups())
    context=s.get(t['task_id'])['context']
    assert context[0]['evidence_kind']=='prior_research'
    assert '复核现金流' in context[0]['source_text']
    assert 'not primary facts' in context[0]['note']


def test_current_delivery_cannot_change_its_own_input_context_on_retry():
    from research.context import prepare_task_context
    s=MemoryStore();t=enqueue(s,event(),holding())
    class Followups:
        def unresolved(self,issuer):return [dict(origin_task=t['task_id'],key='own-question',question='自己的交付')]
    prepare_task_context(t,s,None,None,holding(),followups=Followups())
    assert s.get(t['task_id'])['context']==[]


def test_weekly_review_prioritizes_due_questions_over_issuer_alphabetical_order():
    from research.cli import weekly
    s=MemoryStore()
    a=dict(holding(),issuer_id='a-issuer',active_tickers=['A.SZ'],tickers=['A.SZ'])
    b=dict(holding(),issuer_id='b-issuer',active_tickers=['B.SZ'],tickers=['B.SZ'])
    class Followups:
        def due_issuers(self,day):return {'b-issuer'}
    result=weekly({'holdings':[a,b],'unmapped':[]},s,day='2026-10-28',slots=1,followups=Followups())
    assert result['screened']==2
    assert [t['issuer_id'] for t in s.list()]==['b-issuer']


def test_human_summary_corrections_enter_context_without_rewriting_identity():
    http=Notion();f=api().NotionFollowups(http,'pilot-followups')
    s=MemoryStore();t=enqueue(s,event(),holding());t['publication_key']='old-publication'
    item=api().items_from_report(t,report(t['card']['revision_id']))[0]
    receipt=f.ensure(item,report_ref={})
    http.rows[receipt['id']]['properties']['Summary']={'rich_text':[{'text':{'content':'人工纠正：先复核收购资金来源'}}]}
    out=f.unresolved(t['issuer_id'])[0]
    assert out['human_summary']=='人工纠正：先复核收购资金来源'
    assert out['key']==item['key']


def test_incomplete_delivery_reuses_frozen_context_until_channel_reconciles():
    from research.context import prepare_task_context
    m,s,t,d,r,p=setup()
    out=m.process(t['task_id'],store=s,drive=d,reports=r,providers=[p],budget=Budget(s),day='2026-10-27')
    assert out['status']=='retry_delivery';frozen=out.get('context_digest')
    class Followups:
        def unresolved(self,issuer):return [dict(key='prior-item',human_notes='new human edit',origin_task='earlier')]
    prepare_task_context(out,s,d,None,holding(),followups=Followups())
    assert s.get(t['task_id']).get('context_digest')==frozen
    final=m.process(t['task_id'],store=s,drive=d,reports=r,providers=[p],budget=Budget(s),day='2026-10-27')
    assert final['status']=='pending_model';assert p.calls==1;assert r.count==1
