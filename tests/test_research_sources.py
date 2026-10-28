import importlib,json
import pytest

def api():
    try:return importlib.import_module('research.sources')
    except ModuleNotFoundError:pytest.fail('Full-source collectors are not implemented')

def test_cninfo_pagination_and_exact_issuer_filter():
    calls=[]
    def request(method,url,**kw):
        page=kw['data']['pageNum'];calls.append(page)
        return {'announcements':[{'secCode':'002475','announcementId':str(page),'announcementTitle':'三季度报告',
                                  'announcementTime':1793016000000,'adjunctUrl':'filing.pdf'},
                                 {'secCode':'000001','announcementId':'other','announcementTitle':'Other'}],
                'hasMore':page==1}
    rows=api().cninfo('002475.SZ','2026-10-01','2026-10-27',request)
    assert calls==[1,2];assert len(rows)==2


def test_hk_broad_search_includes_quarterly_and_material_announcements_and_splits_full_window():
    calls=[]
    def request(method,url,**kw):
        p=kw.get('params',{});calls.append(p)
        if 'activestock' in url:return [{'c':'00700','i':123}]
        full=p['fromDate']!=p['toDate']
        return dict(hasNextRow=full,recordCnt=1001 if full else 1,
                    result=json.dumps([{'FILE_LINK':'/a.pdf','TITLE':'Acquisition','DATE_TIME':'27/10/2026 16:30'}]))
    out=api().hkex('0700.HK','2026-10-26','2026-10-27',request)
    assert out;assert all(x['t1code']==-2 and x['t2code']==-2 for x in calls if 't1code' in x)


def test_capped_same_day_hk_source_is_visible_failure_not_no_news():
    def request(method,url,**kw):
        if 'activestock' in url:return [{'c':'00700','i':123}]
        return dict(hasNextRow=True,recordCnt=2000,result='[]')
    with pytest.raises(api().CoverageError):api().hkex('0700.HK','2026-10-27','2026-10-27',request)


def test_report_text_keeps_page_locators_and_discloses_partial_extraction():
    import fitz
    doc=fitz.open()
    for i in range(3):p=doc.new_page();p.insert_text((72,72),'Revenue 100 cash flow '+str(i))
    out=api().extract(doc.tobytes(),kind='pdf',max_chars=40)
    assert out['partial'] is True;assert '[PDF page 1]' in out['text'];assert out['total_pages']==3
    doc.close()


def test_material_classification_is_rules_based_and_not_ai_filtered():
    m=api()
    assert m.document_type('2026年第三季度报告')=='季度报告'
    assert m.document_type('Major acquisition of subsidiary')=='重大公告'
    assert m.document_type('股东大会决议')=='重大公告'


def test_non_official_download_hosts_are_rejected_before_request():
    with pytest.raises(ValueError):api().download_primary('https://127.0.0.1/secret')


@pytest.mark.parametrize('listing,symbol',[('QFIN.O','QFIN'),('SPG.N','SPG'),('BRK.B','BRK-B')])
def test_sec_resolves_venue_suffixes_and_class_shares_without_changing_listing(listing,symbol,monkeypatch):
    monkeypatch.setenv('SEC_USER_AGENT','Research owner@example.org')
    def request(method,url,**kw):
        if url.endswith('company_tickers.json'):return {'0':dict(cik_str=123,ticker=symbol)}
        assert url.endswith('CIK0000000123.json')
        return {'filings':{'recent':dict(filingDate=['2026-10-27'],form=['10-Q'],accessionNumber=['000123-26-001'],primaryDocument=['q.htm'])}}
    rows=api().sec(listing,'2026-10-01','2026-10-28',request)
    assert rows[0]['title'].startswith(listing+' ')
    assert '/123/' in rows[0]['url']


def test_sec_aliases_that_match_different_issuers_remain_unresolved(monkeypatch):
    monkeypatch.setenv('SEC_USER_AGENT','Research owner@example.org')
    def request(method,url,**kw):
        assert url.endswith('company_tickers.json')
        return {'0':dict(cik_str=123,ticker='BRK.B'),'1':dict(cik_str=456,ticker='BRK-B')}
    with pytest.raises(api().CoverageError):api().sec('BRK.B','2026-10-01','2026-10-28',request)
