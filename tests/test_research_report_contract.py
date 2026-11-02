from copy import deepcopy
from importlib import import_module,util
import pytest
from tests.test_research_profiles import profile
from tests.test_research_workflow import event
from research.profiles import resolve_profile
from research.workflow import plan_task,advance_task,STAGES
from research.runtime import MemoryStore


def api():
    assert util.find_spec('research.contract_v17'), 'v17 research/report contract not implemented'
    return import_module('research.contract_v17')


def report():
    return dict(research_question='增长是否产生可持续回报？',research_status='partial',
        thesis='现金转换是持有逻辑候选',conclusion='现金证据不足，保留条件式判断',
        holding_logic=[dict(id='logic-a',claim='回报可持续',change='weakened',reason='现金弱于利润',
                           falsifier='连续两期现金转换未改善',evidence=['doc-a'])],
        key_disagreement='增长质量与外部乐观预期存在分歧',
        earnings_differences=[dict(metric='parent_net_income',reason='可比机构预测尚未取得',status='unavailable',evidence=['doc-a'])],
        valuation_status='pending',valuation_reason='关键盈利假设待取证',
        changes=[dict(text='现金未同步改善',evidence=['doc-a'])],
        counterevidence=[dict(text='季节性可能解释',evidence=['doc-a'])],
        next_actions=[dict(condition='取得回款数据',action='复算营运资金',due='2026-11-20',evidence=['doc-a'])],
        open_questions=['回款是否延后？'],data_requests=[],
        questions=[dict(id='q1',need='回款是否延后？',due='2026-11-20')])


def payload():
    return dict(coverage={'documents':[{'revision_id':'doc-a','pages':[{'page':1,'text':'cash flow'}]}]},
                retrieval={'reports':[]},model={'status':'partial','model':None})


def test_company_question_is_preserved_and_missing_research_core_cannot_publish():
    p=resolve_profile(profile(),{});task=plan_task(event(),p,{},as_of='2026-11-02')
    assert api().validate_report(report(),payload(),task)['research_status']=='partial'
    for field in ('holding_logic','key_disagreement','earnings_differences','valuation_reason'):
        broken=report();broken.pop(field)
        with pytest.raises(ValueError):api().validate_report(broken,payload(),task)
    broken=report();broken['research_question']='另一家公司问题'
    with pytest.raises(ValueError):api().validate_report(broken,payload(),task)


def test_unknown_evidence_and_uncomputed_complete_valuation_are_rejected():
    task=plan_task(event(),resolve_profile(profile(),{}),{},as_of='2026-11-02')
    broken=report();broken['holding_logic'][0]['evidence']=['not-read']
    with pytest.raises(ValueError):api().validate_report(broken,payload(),task)
    broken=report();broken['valuation_status']='complete'
    with pytest.raises(ValueError):api().validate_report(broken,payload(),task)


def test_stage_prompt_allows_reasoned_candidate_and_uses_company_question_not_a_global_one():
    task=plan_task(event(),resolve_profile(profile(),{}),{},as_of='2026-11-02')
    prompt=api().stage_prompt('model',payload(),task)
    assert '增长是否产生可持续回报？' in prompt and 'research_assumption' in prompt
    assert '不给当前买卖建议' not in prompt
    assert api().CONTRACT_VERSION=='holdings-v17'


def test_requested_data_is_actually_retrieved_before_final_report_with_bounded_rounds():
    task=plan_task(event(),resolve_profile(profile(),{}),{},as_of='2026-11-02');s=MemoryStore();s.put(task)
    calls=[]
    def stage(data,t):
        calls.append(t['running_stage'])
        result={'issuer_id':'issuer-a','status':'ready'}
        if t['running_stage']=='judgment':result['data_requests']=[dict(kind='sell_side_report',need='可比预测',why='量化盈利差异')]
        return result
    adapters={x:stage for x in STAGES};adapters['baseline']=lambda t:None
    adapters['publish']=lambda data,t:{'id':'report','verified':True,'input_digest':t['input_digest']}
    out=advance_task(task['task_id'],store=s,adapters=adapters,now='2026-11-02')
    assert calls.count('retrieval')==3  # initial plus at most two actual follow-up rounds
    assert out['unresolved_data_requests'] and out['research_status']=='partial'
    assert out['status']=='partial'


def test_long_originals_are_indexed_but_all_required_financial_pages_reach_model():
    task=plan_task(event(),resolve_profile(profile(),{}),{},as_of='2026-11-02')
    p=payload();doc=p['coverage']['documents'][0]
    doc['pages']=[{'page':1,'text':'cover'}, {'page':2,'text':'unrelated boilerplate '*20000},
                  {'page':3,'text':'consolidated assets and liabilities full table'},
                  {'page':4,'text':'consolidated income full table'}, {'page':5,'text':'consolidated cash full table'}]
    p['coverage']['coverage']=[{'document_id':None,'statements':{'balance':{'pages':[3]},'income':{'pages':[4]},'cashflow':{'pages':[5]}}}]
    prompt=api().stage_prompt('model',p,task)
    assert len(prompt)<160000
    assert 'consolidated assets and liabilities full table' in prompt
    assert 'consolidated income full table' in prompt and 'consolidated cash full table' in prompt
    assert 'receivable_days' in prompt and 'other_liabilities' in prompt
    assert 'omitted_pages' in prompt


def test_historical_corpus_does_not_crowd_out_current_filing_and_latest_annual_baseline():
    task=plan_task(event(),resolve_profile(profile(),{}),{},as_of='2026-11-02');p=payload()
    docs=[];checks=[]
    for year in (2022,2023,2024,2025):
        docs.append({'document_id':str(year),'revision_id':str(year),'document_type':'年度报告',
            'published_at':str(year+1)+'-03-01','pages':[{'page':1,'text':str(year)+' complete annual table '+'A'*20000}]})
        checks.append({'document_id':str(year),'statements':{'balance':{'pages':[1]}}})
    docs.append({'document_id':'current','revision_id':'current','document_type':'季度报告',
        'published_at':'2026-11-01','pages':[{'page':8,'text':'CURRENT full financial tables'+'B'*15000}]})
    checks.append({'document_id':'current','statements':{'balance':{'pages':[8]}}})
    p['coverage'].update(documents=docs,coverage=checks)
    prompt=api().stage_prompt('model',p,task)
    assert '2025 complete annual table' in prompt and 'CURRENT full financial tables' in prompt
    assert len(prompt)<160000 and '2022 complete annual table' not in prompt
