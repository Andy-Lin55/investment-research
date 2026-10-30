from copy import deepcopy
import importlib,json
import pytest
from research.broker_sources import archive_report
from research.context import prepare_task_context
from research.harness import build_brief
from research.runtime import MemoryStore,enqueue,research_prompt,validate_report
from tests.test_broker_sources import Drive,pdf,REPORT
from tests.test_research_runtime import event,holding,report
from tests.test_report_quality import fixture


def archived():
    d=Drive();ref=archive_report(None,d,company_path='投资/公司A',issuer_id='issuer-a',report=REPORT,identity_confirmed=True,cached_pdf=pdf())
    return d,ref


def prepared():
    d,ref=archived();store=MemoryStore();t=enqueue(store,event(),holding())
    h=dict(holding(),broker_report_refs=[ref],research_question='现金是否支持增长？')
    prepare_task_context(t,store,d,None,h)
    return store.get(t['task_id']),d,store,h


def valid_report(t):
    c=t['context'][0];r=report(t['card']['revision_id']);r.update(
        research_question=t['research_question'],data_requests=[],consensus_status='sampled_views_only',
        independent_hypothesis={'claim':'增长质量取决于现金回款','mechanism':'应收增长占用现金',
            'falsifier':'回款改善且现金流持续超过利润','evidence':[t['card']['revision_id']]},
        broker_comparisons=[{'evidence':[c['revision_id']],'pages':[1],'broker_view':'盈利增长预测',
            'own_view':'尚缺一手验证','model_implication':'需验证回款及营运资金假设','verification_needed':'查阅现金流量表原文'}])
    return r


def test_context_loads_original_broker_material_and_preserves_core_question():
    t,d,s,h=prepared();assert len(t['context'])==1
    assert t['context'][0]['evidence_kind']=='sell_side_report'
    assert t['research_brief']['version']=='holdings-broker-v1'
    assert t['research_brief']['question']=='现金是否支持增长？'
    assert 'primary_disclosure' in t['research_brief']['missing']
    assert 'Broker estimate' in research_prompt([t['card']],None,context=t['context'],brief=t['research_brief'])


@pytest.mark.parametrize('change',['hash','scope','issuer'])
def test_bad_broker_manifest_is_visible_gap_not_usable_evidence(change):
    d,ref=archived();s=MemoryStore();t=enqueue(s,event(),holding());h=dict(holding(),broker_report_refs=[deepcopy(ref)])
    if change=='hash':h['broker_report_refs'][0]['sha256']='0'*64
    elif change=='scope':h['company_path']='投资/other'
    else:h['issuer_id']='wrong'
    prepare_task_context(t,s,d,None,h)
    out=s.get(t['task_id']);assert out['context']==[]
    assert any(g.startswith('broker_report_unreadable:') for g in out['context_gaps'])


def test_broker_limit_rejects_before_any_read():
    s=MemoryStore();t=enqueue(s,event(),holding());h=dict(holding(),broker_report_refs=[{}]*3)
    with pytest.raises(ValueError):prepare_task_context(t,s,None,None,h)


@pytest.mark.parametrize('change',['missing_comparison','fake_page','unknown_source','consensus','no_falsifier'])
def test_broker_quality_contract_blocks_invented_or_untraceable_output(change):
    t,*_=prepared();r=valid_report(t)
    if change=='missing_comparison':r.pop('broker_comparisons')
    elif change=='fake_page':r['broker_comparisons'][0]['pages']=[99]
    elif change=='unknown_source':r['broker_comparisons'][0]['evidence']=[t['card']['revision_id']]
    elif change=='consensus':r['consensus_status']='market_consensus'
    else:r['independent_hypothesis']['falsifier']=''
    with pytest.raises(ValueError):validate_report(r,[t['card'],*t['context']],model=None,brief=t['research_brief'])


def test_valid_broker_output_remains_pending_model_and_manually_reviewed():
    t,*_=prepared();r=valid_report(t)
    out=validate_report(r,[t['card'],*t['context']],model=None,brief=t['research_brief'])
    assert out['status']=='pending_model' and out['human_review']=='pending'
    prompt=json.loads(research_prompt([t['card']],None,context=t['context'],brief=t['research_brief']).split('\n',1)[1])
    assert 'broker_comparisons' in prompt['schema'] and 'independent_hypothesis' in prompt['schema']


def test_report_shows_dated_broker_pages_independent_view_and_not_consensus():
    t,*_=prepared();r=valid_report(t);t.update(presentation_version=2)
    out={'report':r,'actual_model':'unreported','provider':'local_codex_subscription','model_snapshot':None}
    text=json.dumps(importlib.import_module('research.report_view').render(t,out,{}),ensure_ascii=False)
    assert 'Broker A' in text and '2026-10-01' in text and '第 1 页' in text
    assert '卖方观点' in text and '不代表市场共识' in text and '回款改善且现金流持续超过利润' in text


def test_legacy_extra_broker_fields_cannot_block_existing_delivery():
    from tests.test_research_engine import setup,run
    m,s,t,d,reports,provider=setup();t['presentation_version']=2;s.put(t)
    call=provider.__class__.__call__
    class Extra(provider.__class__):
        def __call__(self,*a,**kw):
            r=call(self,*a,**kw);content=json.loads(r['content'])
            content['independent_hypothesis']={'claim':'Additional legacy analysis'}
            content['broker_comparisons']=[{'unvalidated':'legacy extra field'}]
            r['content']=json.dumps(content);return r
    provider.__class__=Extra
    run((m,s,t,d,reports,provider));out=run((m,s,t,d,reports,provider))
    assert out['checkpoints'].get('report') and reports.count==1
    assert provider.calls==1


def test_large_real_corpus_keeps_all_sources_in_native_reference_table():
    t,*_=prepared();r=valid_report(t)
    historical,_=fixture();t['context']+=historical['context']
    t['context'] += [{'revision_id':'prior-'+str(i),'evidence_kind':'prior_research',
                     'source':{'url':'https://drive.invalid/prior-'+str(i),'locator':'Prior source '+str(i)},
                     'source_text':'old analysis','missing':[]} for i in range(16)]
    r['open_questions']=['Question '+str(i) for i in range(7)]
    r['data_requests']=[{'kind':'primary_disclosure','need':'Data '+str(i),'why':'Resolve gap'} for i in range(5)]
    result={'report':r,'model_snapshot':None,'provider':'local_codex_subscription','actual_model':'unreported'}
    from research.report_view import render
    blocks=render(t,result,{})
    assert len(blocks)<=95
    heading=next(i for i,b in enumerate(blocks) if b['type']=='heading_2'
                 and b['heading_2']['rich_text'][0]['text']['content']=='资料与模型口径')
    appendix=blocks[heading+1]['table']
    assert len(appendix['children'])==len(t['context'])+2
    text=json.dumps(appendix,ensure_ascii=False)
    assert 'Prior source 15' in text and 'https://drive.invalid/prior-15' in text
    assert 'https://www.fxbaogao.com/view?id=123' in text


def test_broker_prompt_excerpts_keep_explicit_original_page_boundaries():
    from research.harness import compact_context
    text='[PDF page 1]\n'+('revenue background '*1000)+'\n[PDF page 3]\nChina operations remain resilient.\n[PDF page 4]\nEurope cash flow integration remains unverified.'
    item=compact_context([{'evidence_kind':'sell_side_report','source_text':text,'revision_id':'broker'}])[0]
    assert '[PDF page 4 excerpt]' in item['source_text']
    for span in item['prompt_excerpts']:
        assert item['source_text'][span['prompt_start']:span['prompt_end']]==text[span['source_start']:span['source_end']]
        if 'Europe' in text[span['source_start']:span['source_end']]:assert span['pdf_page']==4
    assert len(item['source_text'])<=12000


def test_short_broker_pages_are_labelled_without_becoming_primary_evidence():
    from research.harness import compact_context,evidence_is_primary
    entry={'evidence_kind':'sell_side_report','source_text':'[PDF page 3]\nChina\n[PDF page 4]\nEurope','revision_id':'broker'}
    result=compact_context([entry])[0]
    assert '[PDF page 3 excerpt]' in result['source_text'] and '[PDF page 4 excerpt]' in result['source_text']
    assert {x['pdf_page'] for x in result['prompt_excerpts']}=={3,4}
    assert evidence_is_primary(result) is False


def test_notion_merged_adjacent_citations_preserve_semantic_readback():
    from research.notion_report_verify import canonical_rich
    from research.report_view import rich
    url='https://www.fxbaogao.com/view?id=123'
    expected=rich('[1] ',url=url)+rich('[2] ',url=url)
    actual=rich('[1] [2] ',url=url)
    assert canonical_rich(actual)==canonical_rich(expected)
    assert canonical_rich(actual)!=canonical_rich(rich('[1] [3] ',url=url))
    assert canonical_rich(actual)!=canonical_rich(rich('[1] [2] ',url=url+'&changed=1'))


def test_readback_never_merges_different_link_or_bold_semantics():
    from research.notion_report_verify import canonical_rich
    from research.report_view import rich
    parts=rich('first',url='https://source.invalid/a')+rich('second',url='https://source.invalid/b')
    assert canonical_rich(parts)!=canonical_rich(rich('firstsecond',url='https://source.invalid/a'))
    assert canonical_rich(rich('first')+rich('second',bold=True))!=canonical_rich(rich('firstsecond'))
