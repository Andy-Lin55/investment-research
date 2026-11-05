"""Existing daily events and typed provider data feed the isolated research path."""
import json
from importlib import import_module,util
import pytest
from research.runtime import MemoryStore
from research.profiles import resolve_profile
from tests.test_research_profiles import profile
from tests.test_broker_sources import Drive,pdf


def api():
    assert util.find_spec('research.existing_data'), 'existing-data research adapter is missing'
    return import_module('research.existing_data')


def raw():
    meta=dict(ts_code='000001.SZ',end_date='20251231',ann_date='20260401',f_ann_date='20260401',report_type='1',comp_type='1')
    return {'income':{'rows':[dict(meta,revenue=100,n_income=12,n_income_attr_p=11,total_profit=15,income_tax=3,minority_gain=1)]},
            'balancesheet':{'rows':[dict(meta,total_assets=200,total_liab=80,total_hldr_eqy_inc_min_int=120,money_cap=30)]},
            'cashflow':{'rows':[dict(meta,n_cashflow_act=20,n_cashflow_inv_act=-5,n_cash_flows_fnc_act=-2,eff_fx_flu_cash=0,n_incr_cash_cash_equ=13,c_cash_equ_beg_period=17,c_cash_equ_end_period=30,net_profit=12)]},
            'daily':{'rows':[dict(ts_code='000001.SZ',trade_date='20261030',close=14.25),dict(ts_code='000001.SZ',trade_date='20261103',close=99)]}}


def test_existing_three_statements_and_price_are_archived_without_importing_daily_job():
    d=Drive();p=resolve_profile(profile(),{});calls=[]
    def query(method,**params):calls.append((method,params));return raw()[method]
    out=api().load_financial_data(p,as_of='2026-11-02',query=query,drive=d)
    assert set(out['statements'])=={'income','balancesheet','cashflow'}
    assert out['market_snapshot']['price']==14.25
    assert out['market_snapshot']['trade_date']=='2026-10-30'
    assert out['market_snapshot']['currency']=='CNY'
    assert len(calls)==4 and all(x[1]['ts_code']=='000001.SZ' for x in calls)
    assert out['archive']['verified'] and '/02_财务数据/' in out['archive']['path']
    assert out['historical_tables'][0]['periods']==['2025-12-31A']
    assert all(e['evidence_kind']=='reported_provider' for e in out['evidence_documents'])
    assert len(out['evidence_documents'])==3
    assert out['history_status']=='incomplete'  # one annual observation is not five-year coverage
    assert out['workbook']['verified'] and out['workbook']['path'].endswith('.xlsx')


def test_empty_or_cross_company_prices_never_become_current_price():
    p=resolve_profile(profile(),{})
    def query(method,**params):
        return {'rows':[dict(ts_code='OTHER',trade_date='20261030',close=10)]} if method=='daily' else raw()[method]
    out=api().load_financial_data(p,as_of='2026-11-02',query=query,drive=Drive())
    assert out['market_snapshot'] is None and 'verified_price_unavailable' in out['gaps']


def test_repeat_financial_load_reuses_identical_immutable_workbook():
    from unittest.mock import patch
    from datetime import datetime,timezone
    p=resolve_profile(profile(),{});d=Drive();query=lambda method,**kw:raw()[method]
    first=api().load_financial_data(p,as_of='2026-11-02',query=query,drive=d)
    with patch('zipfile.time.localtime',return_value=(2030,1,1,0,0,0,1,1,-1)):
        second=api().load_financial_data(p,as_of='2026-11-02',query=query,drive=d)
    assert first['workbook']['sha256']==second['workbook']['sha256']


def test_identical_provider_rows_on_later_cutoff_have_separate_immutable_package():
    p=resolve_profile(profile(),{});d=Drive();query=lambda method,**kw:raw()[method]
    a=api().load_financial_data(p,as_of='2026-11-02',query=query,drive=d)
    b=api().load_financial_data(p,as_of='2026-11-03',query=query,drive=d)
    assert a['archive']['path']!=b['archive']['path']


def test_standard_history_does_not_require_a_validated_industry_forecast_template():
    p=resolve_profile(profile(),{'sector_template':'bank'});payload=raw()
    for kind in ('income','balancesheet','cashflow'):
        payload[kind]['rows'][0]['comp_type']='2'
    out=api().load_financial_data(p,as_of='2026-11-02',query=lambda method,**kw:payload[method],drive=Drive())
    assert len(out['historical_tables'])==3 and out['statements']['income'][0]['company_type']=='2'
    assert 'sector_specific_forecast_not_supported' in out['gaps']


def test_hk_us_history_route_does_not_mislabel_forecasts_as_reported_actuals():
    p=resolve_profile(dict(profile(ticker='0700.HK'),market='HK'),{})
    out=api().load_financial_data(p,as_of='2026-11-02',query=lambda *a,**kw:pytest.fail('CN-only data queried'),drive=Drive())
    assert out['history_route']=='broker_originals' and out['market_snapshot'] is None
    header='FY2025A HKD million consolidated annual income statement'
    doc={'revision_id':'broker-a','pages':[{'page':2,'text':header+'\n2025 revenue 100 actual; 2027 revenue 200 forecast'}]}
    rows=[dict(statement='income',field='revenue',value=100,period_end='2025-12-31',period_basis='annual',
        currency='HKD',unit='百万元',scope='consolidated',issuer_id=p['issuer_id'],kind='reported_broker',
        source_ref='broker-a',source_locator='p2',source_page=2,dimension_page=2,dimension_quote=header,quote='2025 revenue 100 actual')]
    tables=api().broker_history(rows,[doc],p,as_of='2026-11-02')
    assert tables[0]['rows'][0]['values']==[100000000]
    assert tables[0]['origin']=='broker_reported_history'
    with pytest.raises(ValueError):api().broker_history([dict(rows[0],kind='forecast')],[doc],p,as_of='2026-11-02')
    with pytest.raises(ValueError):api().broker_history([dict(rows[0],value=200)],[doc],p,as_of='2026-11-02')
    with pytest.raises(ValueError):api().broker_history([dict(rows[0],period_end='2027-12-31')],[doc],p,as_of='2026-11-02')
    for change in [{'currency':'USD'},{'unit':'亿元'},{'period_end':'2024-12-31'},{'source_page':7}]:
        with pytest.raises(ValueError):api().broker_history([dict(rows[0],**change)],[doc],p,as_of='2026-11-02')
    quarter_header='Q4 FY2025A HKD million consolidated quarterly income statement'
    quarter_quote='Q4 FY2025A revenue 100 actual'
    quarter=dict(rows[0],dimension_quote=quarter_header,quote=quarter_quote)
    with pytest.raises(ValueError):api().broker_history([quarter],[dict(doc,pages=[{'page':2,'text':quarter_header+'\n'+quarter_quote}])],p,as_of='2026-11-02')
    false_actual=dict(rows[0],quote='2025 revenue 100 forecast')
    with pytest.raises(ValueError):api().broker_history([false_actual],[dict(doc,pages=[{'page':2,'text':false_actual['quote']}])],p,as_of='2026-11-02')


def test_noncalendar_historical_broker_annual_is_supported_without_industry_forecast():
    p=resolve_profile(dict(profile(ticker='9896.HK'),market='HK'),{'fiscal_year_end':'06-30'})
    header='FY2025A USD consolidated annual income statement'
    row=dict(statement='income',field='revenue',value=100,period_end='2025-06-30',period_basis='annual',
        currency='USD',unit='USD',scope='consolidated',issuer_id=p['issuer_id'],kind='reported_broker',
        source_ref='broker-a',source_locator='p2',source_page=2,dimension_page=2,dimension_quote=header,quote='2025 revenue 100 actual')
    doc={'revision_id':'broker-a','pages':[{'page':2,'text':header+'\n'+row['quote']}]}
    assert api().broker_history([row],[doc],p,as_of='2026-11-02')[0]['periods']==['2025-06-30A']


def test_daily_events_paginates_filters_exact_ticker_and_fetches_original_not_ai_summary():
    p=resolve_profile(profile(),{});calls=[]
    def page(identifier,ticker):
        return {'id':identifier,'properties':{'Name':{'title':[{'plain_text':'2026年三季度报告'}]},
            'Ticker':{'rich_text':[{'plain_text':ticker}]},'EventType':{'select':{'name':'季度报告'}},
            'EventDate':{'date':{'start':'2026-10-29'}},'ProcessedAt':{'date':{'start':'2026-11-02'}},
            'SourceURL':{'url':'http://static.cninfo.com.cn/finalpage/'+identifier+'.PDF'},
            'AI Summary':{'rich_text':[{'plain_text':'summary is not original'}]}}}
    def http(method,path,*,body):
        calls.append((method,path,body))
        return {'results':[page('event2','000001.SZ')], 'has_more':False} if body.get('start_cursor') else {
            'results':[page('event1','000002.SZ')],'has_more':True,'next_cursor':'next'}
    from research.v17_live import OfficialSource,read_ref
    urls=[]
    def download(url):urls.append(url);return pdf()
    d=Drive();s=MemoryStore();official=OfficialSource(s,d,collectors={},downloader=download)
    source=api().DailyEventsSource(http,'daily-source',official)
    out=source.discover(p,'2026-10-30','2026-11-02')
    assert out['complete'] and len(out['events'])==1 and len(calls)==2
    event=out['events'][0]
    assert event['kind']=='earnings' and event['ticker']=='000001.SZ'
    assert urls==['https://static.cninfo.com.cn/finalpage/event2.PDF']
    assert event['published_at']=='2026-10-29'
    doc=read_ref(d,event['documents'][0]);assert 'summary is not original' not in json.dumps(doc)
    assert doc['period_end']=='2026-09-30'
    assert calls[0][2]['filter']['and'][0]['rich_text']['equals']=='000001.SZ'


def test_same_original_from_daily_and_official_listing_does_not_collide_or_duplicate():
    from research.v17_live import OfficialSource
    d=Drive();s=MemoryStore();p=resolve_profile(profile(),{});raw_pdf=pdf();src=OfficialSource(s,d,downloader=lambda url:raw_pdf)
    row=dict(issuer_id=p['issuer_id'],ticker='000001.SZ',title='2026年三季度报告',url='https://static.cninfo.com.cn/a.pdf',
        document_type='季度报告',published_at='2026-10-29',metadata_revision='daily-meta',document_id='daily:page')
    a,doc=src.original(p,row)
    b,other=src.original(p,dict(row,metadata_revision='official-meta',document_id='official-id'))
    assert doc['revision_id']==other['revision_id'] and a['path']!=b['path']


def test_generic_daily_annual_name_uses_original_header_year_not_publication_year():
    import fitz
    from research.v17_live import OfficialSource
    book=fitz.open();page=book.new_page();page.insert_text((70,70),'2025 Annual report consolidated statements');raw_pdf=book.tobytes();book.close()
    src=OfficialSource(MemoryStore(),Drive(),downloader=lambda url:raw_pdf);p=resolve_profile(profile(),{})
    _,doc=src.original(p,dict(issuer_id=p['issuer_id'],ticker='000001.SZ',title='000001.SZ 年度报告 2026-04-01',
        document_id='daily:page',document_type='年度报告',published_at='2026-04-01',
        url='https://static.cninfo.com.cn/a.pdf',metadata_revision='meta',daily_event=True))
    assert doc['period_end']=='2025-12-31'


def test_provider_history_reaches_prompt_and_native_report_even_without_forecast():
    from research.v17 import build_adapters
    from research.workflow import plan_task,advance_task
    from tests.test_research_workflow import event
    from tests.test_research_report_contract import report
    from research.contract_v17 import stage_prompt
    p=resolve_profile(profile(),{});s=MemoryStore();d=Drive();t=plan_task(event(),p,{},as_of='2026-11-02');s.put(t)
    data=api().load_financial_data(p,as_of='2026-11-02',query=lambda method,**kw:raw()[method],drive=d)
    doc=dict(issuer_id=p['issuer_id'],document_id='d',revision_id='doc-a',document_type='公告',period_end='2026-09-30',pages=[{'page':1,'text':'company disclosure'}])
    def reason(stage,payload,task):
        assert payload['coverage']['financial_data']['market_snapshot']['price']==14.25
        if stage=='model':
            assert '14.25' in stage_prompt(stage,payload,task)
            return {'facts':[],'guidance':[],'broker_estimates':[],'assumptions':[]}
        if stage=='judgment':return {'data_requests':[]}
        return report()
    class Reports:
        def publish(self,task,result,files,**kw):
            from research.report_v17 import render
            text=json.dumps(render(task,result,files),ensure_ascii=False)
            assert '历史三张表' in text and '利润表' in text and '资产负债表' in text and '现金流量表' in text
            assert '14.25' in text and '2025-12-31A' in text and files['financial_workbook']['verified']
            return {'id':'report','verified':True}
    adapters=build_adapters(store=s,drive=d,reports=Reports(),env={},documents=lambda t:[doc],reasoner=reason,
        financial_data=lambda task:data,baseline=lambda t:None)
    out=advance_task(t['task_id'],store=s,adapters=adapters,now='2026-11-02')
    assert out['status']=='partial' and out['next_state']['last_published_research']['verified']


def test_judgment_prompt_lists_actual_request_enum_instead_of_a_free_text_placeholder():
    from research.contract_v17 import stage_prompt,REQUEST_KINDS
    from research.workflow import plan_task
    from tests.test_research_workflow import event
    p=resolve_profile(profile(),{});task=plan_task(event(),p,{},as_of='2026-11-02')
    prompt=stage_prompt('judgment',{},task)
    assert all(kind in prompt for kind in REQUEST_KINDS)
    assert '已授权资料类别' not in prompt


def test_model_prompt_lists_standard_history_fields_for_broker_extraction():
    from research.contract_v17 import stage_prompt
    from research.workflow import plan_task
    from tests.test_research_workflow import event
    task=plan_task(event(),resolve_profile(profile(),{}),{},as_of='2026-11-02')
    prompt=stage_prompt('model',{},task)
    assert 'historical_statement_fields' in prompt
    assert 'total_hldr_eqy_inc_min_int' in prompt and 'c_cash_equ_end_period' in prompt


def test_resuming_json_checkpoint_keeps_identical_prompt_for_identical_inputs():
    from research.contract_v17 import stage_prompt
    from research.workflow import plan_task
    from tests.test_research_workflow import event
    p=resolve_profile(profile(),{});task=plan_task(event(),p,{},as_of='2026-11-02')
    payload={'coverage':{'documents':[]},'retrieval':{'reports':[]},'previous':{'z':1,'a':2}}
    resumed=json.loads(json.dumps(payload,sort_keys=True))
    assert stage_prompt('report',payload,task)==stage_prompt('report',resumed,task)


def test_unavailable_comparison_does_not_require_an_invented_source_but_claims_still_do():
    from research.contract_v17 import validate_report
    from research.workflow import plan_task
    from tests.test_research_workflow import event
    from tests.test_research_report_contract import report,payload
    p=resolve_profile(profile(),{});task=plan_task(event(),p,{},as_of='2026-11-02');r=report()
    r['earnings_differences'][0]['evidence']=[]
    assert validate_report(r,payload(),task)['research_status']=='partial'
    r['changes'][0]['evidence']=[]
    with pytest.raises(ValueError):validate_report(r,payload(),task)


def test_pending_status_cannot_hide_uncomputed_numeric_target_or_upside():
    from research.contract_v17 import validate_report
    from research.workflow import plan_task
    from tests.test_research_workflow import event
    from tests.test_research_report_contract import report,payload
    from tests.test_forecast_model import baseline,scenarios
    from financials.forecast import project
    task=plan_task(event(),resolve_profile(profile(),{}),{},as_of='2026-11-02');r=report();p=payload()
    r['valuation_reason']='目标价50元，上涨空间40%'
    with pytest.raises(ValueError):validate_report(r,p,task)
    p['model']['model']=project(baseline(),scenarios(),as_of='2026-11-02')
    r['valuation_reason']='目标价26.10元，上涨空间40%'
    with pytest.raises(ValueError):validate_report(r,p,task)
    p['coverage']['financial_data']={'market_snapshot':{'price':20,'currency':'CNY'}}
    r['valuation_reason']='目标价26.10元，上涨空间30.50%'
    assert validate_report(r,p,task)


def test_followup_without_new_originals_does_not_recompute_model_or_judgment():
    from research.workflow import plan_task,advance_task,STAGES
    from tests.test_research_workflow import event
    p=resolve_profile(profile(),{});s=MemoryStore();task=plan_task(event(),p,{},as_of='2026-11-02');s.put(task);calls=[]
    def stage(payload,t):
        name=t['running_stage'];calls.append(name);out=dict(issuer_id=p['issuer_id'],status='ready')
        if name=='retrieval':out['reports']=[]
        if name=='judgment':out['data_requests']=[dict(kind='sell_side_report',need='可比预测',why='量化预期差')]
        return out
    adapters={name:stage for name in STAGES};adapters.update(baseline=lambda t:None,
        publish=lambda payload,t:dict(id='report',verified=True,input_digest=t['input_digest']))
    out=advance_task(task['task_id'],store=s,adapters=adapters,now='2026-11-02')
    assert calls.count('retrieval')==2 and calls.count('model')==1 and calls.count('judgment')==1
    assert out['status']=='partial' and out['unresolved_data_requests']


def test_independent_primary_history_uses_verified_company_name_when_code_query_is_empty():
    from research.sources import cninfo
    calls=[]
    def request(method,url,**kw):
        key=kw['data']['searchkey'];calls.append(key)
        if key=='000001':return {'announcements':None,'hasMore':False,'totalAnnouncement':0}
        return {'announcements':[dict(secCode='000001',announcementId='annual',announcementTitle='2025年度报告',
            announcementTime=1774998000000,adjunctUrl='a.pdf')], 'hasMore':False}
    rows=cninfo('000001.SZ','2026-03-01','2026-05-01',request,company_name='已核实公司名')
    assert calls==['000001','已核实公司名'] and rows[0]['document_id']=='annual'


def test_independent_history_merges_name_results_even_if_code_index_has_unrelated_filings():
    from research.sources import cninfo
    def request(method,url,**kw):
        code_query=kw['data']['searchkey']=='000001'
        return {'announcements':[dict(secCode='000001',announcementId='contract' if code_query else 'quarter',
            announcementTitle='重大合同' if code_query else '2026三季度报告',announcementTime=1793232000000,adjunctUrl='a.pdf')], 'hasMore':False}
    rows=cninfo('000001.SZ','2026-10-01','2026-11-02',request,company_name='已核实公司名')
    assert {r['document_id'] for r in rows}=={'contract','quarter'}


def test_research_roster_reuses_names_from_exact_active_listing_without_changing_identity(monkeypatch):
    from research import cli
    monkeypatch.setattr(cli,'active_records',lambda *a:[{'ticker':'000001.SZ','name':'已核实公司名'}])
    registry=[dict(profile(),status='Active')]
    out=cli.active_roster(None,{},registry)
    assert out['holdings'][0]['company_aliases']==['已核实公司名']
    assert out['holdings'][0]['issuer_id']=='issuer-a'
