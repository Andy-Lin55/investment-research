from importlib import import_module,util
from copy import deepcopy
import json
from research.runtime import MemoryStore
from tests.test_research_profiles import profile
from research.profiles import resolve_profile
from research.workflow import plan_task,advance_task
from tests.test_research_workflow import event
from tests.test_broker_sources import Drive
from tests.test_forecast_inputs import facts,assumptions
from tests.test_research_report_contract import report


def api():
    assert util.find_spec('research.v17'), 'production v17 entry is not connected'
    return import_module('research.v17')


def test_disabled_v17_commands_do_not_read_credentials_connect_or_write(capsys):
    assert api().main(['scan','--write'],env={})==2
    assert api().main(['worker'],env={})==0
    assert api().main(['doctor'],env={})==0
    assert 'false' in capsys.readouterr().out.lower()


def test_discovery_to_research_uses_real_model_and_publication_readback_then_resumes_without_ai_repayment():
    p=resolve_profile(profile(),{});s=MemoryStore();drive=Drive();t=plan_task(event(),p,{},as_of='2026-11-02');s.put(t)
    text='\n'.join(str(x['field'])+'='+str(x['value']) for x in facts())
    document=dict(issuer_id='issuer-a',document_id='d1',revision_id='doc-a',document_type='公告',
        period_end='2025-12-31',published_at='2026-11-01',pages=[{'page':1,'text':text}],source={'url':'https://static.cninfo.com.cn/test.pdf'})
    calls=[];fail=[True]
    def reason(stage,payload,task):
        calls.append(stage)
        if stage=='model':
            f=facts()
            for row in f:
                row['source_ref']='doc-a';row['quote']=row['field']+'='+str(row['value'])
            a=assumptions()
            for row in a:row['source_refs']=['doc-a']
            return {'facts':f,'guidance':[],'broker_estimates':[],'assumptions':a}
        if stage=='judgment':return {'data_requests':[]}
        return report()
    class Reports:
        def publish(self,task,result,files,**kw):
            if fail.pop() if fail else False:raise OSError('lost response')
            assert result['model_snapshot']['scenarios']['base'][0]['income']['parent_net_income']==217.5
            assert files['model_workbook']['verified']
            return {'id':'notion-report','url':'https://notion.invalid/report','verified':True}
    adapters=api().build_adapters(store=s,drive=drive,reports=Reports(),env={},
        documents=lambda task:[document],reasoner=reason,baseline=lambda task:None)
    first=advance_task(t['task_id'],store=s,adapters=adapters,now='2026-11-02')
    assert first['status']=='retryable_failure'
    second=advance_task(t['task_id'],store=s,adapters=adapters,now='2026-11-02')
    assert second['status']=='partial' and calls==['model','judgment','report']
    assert second['next_state']['open_questions'][0]['id']=='q1'
    assert any(path.endswith('.xlsx') for path in drive.files)


def test_paid_reasoning_with_unknown_price_does_not_call_provider():
    p=resolve_profile(profile(),{});s=MemoryStore();t=plan_task(event(),p,{},as_of='2026-11-02');s.put(t)
    class Provider:
        name='provider';model='requested';input_cny_per_million=None;output_cny_per_million=None
        def __call__(self,*a,**kw):raise AssertionError('unpriced model must not be called')
    from research.workflow import StageWait
    import pytest
    with pytest.raises(StageWait) as error:
        api().reason('model',{},t,store=s,drive=Drive(),providers=[Provider()],now='2026-11-02')
    assert error.value.status=='waiting_budget' and not s.get(t['task_id'])['charges']


def test_v17_native_report_displays_logic_and_computed_forecasts():
    from research.report_view import render
    from financials.forecast import project
    from tests.test_forecast_model import baseline,scenarios
    task=plan_task(event(),resolve_profile(profile(),{}),{},as_of='2026-11-02')
    result={'report':report(),'actual_model':'SYNTHETIC','provider':'offline',
            'model_snapshot':project(baseline(),scenarios(),as_of='2026-11-02'),
            'validation':{'status':'ready_for_review'}}
    blocks=render(task,result,{})
    text=json.dumps(blocks,ensure_ascii=False)
    assert '回报可持续' in text and '217.50' in text and '26.10' in text
    assert 'AI研究更新' in text


def test_report_body_displays_program_compared_broker_values_and_unexplained_difference():
    from research.report_view import render
    task=plan_task(event(),resolve_profile(profile(),{}),{},as_of='2026-11-02')
    result={'report':report(),'actual_model':'SYNTHETIC','provider':'offline','model_snapshot':None,
        'forecast_comparison':{'comparable':[{'metric':'parent_net_income','period_end':'2027-12-31',
          'currency':'CNY','estimate_owner':'Broker A','value':250,'own_value':300,'absolute_difference':50,
          'unexplained_difference':50,'source_ref':'broker-p2'}],'incomparable':[{'reason':'ModelWare basis unmatched'}]}}
    text=json.dumps(render(task,result,{}),ensure_ascii=False)
    assert '250.00' in text and '300.00' in text and '50.00' in text
    assert 'Broker A' in text and '未解释' in text and 'ModelWare basis unmatched' in text
