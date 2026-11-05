from importlib import import_module,util
from tests.test_research_profiles import profile
from research import sources


def api():
    assert util.find_spec('research.financial_coverage'), 'disclosure coverage is not implemented'
    return import_module('research.financial_coverage')


def pages():
    return [dict(page=8,text='合并资产负债表 单位：元 币种：人民币 资产总计 100',included=False),
            dict(page=9,text='负债和所有者权益总计 100'),
            dict(page=12,text='合并利润表 单位：元 币种：人民币 营业收入 50 净利润 10 每股收益 1'),
            dict(page=17,text='合并现金流量表 单位：元 币种：人民币 经营活动产生的现金流量净额 10 投资活动产生的现金流量净额 -5 筹资活动产生的现金流量净额 0 期末现金及现金等价物余额 15')]


def test_original_complete_but_omitted_statement_start_is_an_input_gap_not_missing_disclosure():
    out=api().build_coverage(pages(),profile=profile(),document_type='年度报告',period_end='2025-12-31',document_id='doc')
    assert out['original_complete'] is True and out['input_complete'] is False
    assert out['statements']['balance']['pages']==[8,9]
    assert 'balance:input_pages_missing' in out['gaps']
    assert out['status']=='input_incomplete'


def test_complete_statements_move_across_pages_and_year_without_special_cases():
    data=pages()
    for p in data:p.update(page=p['page']+100,included=True)
    out=api().build_coverage(data,profile=profile(),document_type='年度报告',period_end='2027-12-31',document_id='other')
    assert out['input_complete'] is True
    assert out['statements']['balance']['pages']==[108,109]


def test_parent_only_tables_cannot_satisfy_consolidated_coverage_and_unknown_unit_stays_gap():
    data=pages()
    for p in data:p['text']=p['text'].replace('合并','母公司').replace('单位：元','')
    out=api().build_coverage(data,profile=profile(),document_type='年度报告',period_end='2025-12-31',document_id='doc')
    assert out['original_complete'] is False
    assert any('statement_missing' in x for x in out['gaps'])


def test_guidance_is_not_an_incomplete_annual_report():
    assert sources.document_type('2026年度业绩预告')=='业绩预告'
    out=api().build_coverage([dict(page=1,text='全年净利润预告')],profile=profile(),
        document_type='业绩预告',period_end='2026-12-31',document_id='guide')
    assert out['required_statements']==[] and out['status']=='not_applicable'


def test_source_failure_and_revision_discovery_cannot_be_called_fresh():
    known=[dict(issuer_id='issuer-a',document_id='h1',revision_id='a')]
    new=[dict(issuer_id='issuer-a',document_id='q3',revision_id='b',published_at='2026-10-30',signed_at='2026-10-29')]
    out=api().check_freshness(known,new,profile=profile(),as_of='2026-11-02')
    assert out['status']=='stale' and out['new_documents'][0]['document_id']=='q3'
    out=api().check_freshness(known,{'complete':False,'documents':[]},profile=profile(),as_of='2026-11-02')
    assert out['status']=='unknown'
    revision=[dict(known[0],revision_id='corrected')]
    assert api().check_freshness(known,revision,profile=profile(),as_of='2026-11-02')['revisions']


def test_previous_statement_tail_above_next_heading_is_kept_and_shareholder_aliases_match():
    data=pages()
    data[0]['included']=True
    data[1]['text']='负债和所有者权益（或股东权益）总计 100\n母公司资产负债表 单位：元 资产总计 50'
    data[2]['text']+='\n母公司利润表 净利润 2'
    out=api().build_coverage(data,profile=profile(),document_type='年度报告',period_end='2025-12-31',document_id='changed-layout')
    assert out['original_complete'] is True and out['input_complete'] is True
    assert out['statements']['balance']['pages']==[8,9]
