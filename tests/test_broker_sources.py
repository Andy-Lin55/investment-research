"""Original evidence, entitlement replay and credential boundaries are observable contracts."""
from copy import deepcopy
import hashlib, importlib, json
import pytest
from research.delivery import ServiceError


def api():
    try:return importlib.import_module('research.broker_sources')
    except ModuleNotFoundError:pytest.fail('Broker source integration not implemented')


def pdf():
    import fitz
    doc=fitz.open();p=doc.new_page();p.insert_text((72,72),'Broker estimate FY2027 revenue 100; not an actual.')
    raw=doc.tobytes();doc.close();return raw


class Drive:
    def __init__(self):self.files={}
    def read(self,path):
        if path not in self.files:raise ServiceError(404)
        return self.files[path],{'webUrl':'https://drive.invalid/original','id':path}
    def put_immutable(self,path,data):
        if path in self.files:assert self.files[path]==data
        self.files[path]=data
        return {'path':path,'sha256':hashlib.sha256(data).hexdigest(),'verified':True,'url':'https://drive.invalid/original'}


REPORT={'report_id':123,'title':'Company earnings','org_name':'Broker A','published_at':'2026-10-01'}
MATRIX=[{'question':'Does cash support growth?','keywords':'Company cash flow'}]


class Response:
    def __init__(self,data,status=200,headers=None):
        self.data=data if isinstance(data,bytes) else json.dumps(data).encode()
        self.status_code=status;self.headers=headers or {};self.closed=False
    def iter_content(self,size):yield self.data
    def close(self):self.closed=True


def test_search_deduplicates_and_retains_candidate_identity_status():
    row={'reportId':123,'title':'<em>Company</em> earnings','orgName':'Broker A','pubTimeStr':'2026/10/01','pageNum':1}
    def request(method,url,**kw):return Response({'code':0,'data':[row,row,dict(row,reportId=124)]})
    result=api().FxBaogao('private-placeholder',request=request).search(MATRIX)
    assert len(result)==1 and result[0]['report_id']==123
    assert result[0]['identity_status']=='candidate' and result[0]['title']=='Company earnings'
    assert result[0]['report_url']=='https://www.fxbaogao.com/view?id=123'


@pytest.mark.parametrize('matrix',[[],[{'keywords':'broad topic'}],MATRIX*7])
def test_search_requires_bounded_question_matrix_before_network(matrix):
    def forbidden(*a,**k):pytest.fail('Invalid plan must not contact provider')
    with pytest.raises(ValueError):api().FxBaogao('placeholder',request=forbidden).search(matrix)


@pytest.mark.parametrize('response',[{'code':403,'msg':'SECRET'}, {'code':0,'data':{}}, {'code':0,'data':[{}]}])
def test_bad_provider_reply_is_not_empty_success_or_secret_leak(response):
    with pytest.raises(api().BrokerError) as error:
        api().FxBaogao('SECRET',request=lambda *a,**kw:Response(response)).search(MATRIX)
    assert 'SECRET' not in str(error.value)


def test_key_goes_only_to_api_and_download_redirect_has_no_authorization():
    calls=[];original=pdf()
    def request(method,url,**kw):
        calls.append((url,kw['headers']))
        if 'api.fxbaogao.com' in url:return Response({'code':0,'data':'https://dr.fxbaogao.com/a.pdf?signature=hidden'})
        if len(calls)==2:return Response(b'',302,{'Location':'/b.pdf'})
        return Response(original)
    client=api().FxBaogao('SECRET',request=request)
    assert client.download(123)==original
    assert calls[0][1]['Authorization']=='Bearer SECRET'
    assert all('Authorization' not in h for _,h in calls[1:])


@pytest.mark.parametrize('url',['https://evil.invalid/report.pdf','https://dr.fxbaogao.com.evil.invalid/a',
                              'http://dr.fxbaogao.com/a','https://u:p@dr.fxbaogao.com/a','https://dr.fxbaogao.com:444/a'])
def test_download_rejects_other_hosts_and_credentialed_urls(url):
    def request(method,target,**kw):
        assert target.startswith('https://api.fxbaogao.com/')
        return Response({'code':0,'data':url})
    with pytest.raises(api().BrokerError):api().FxBaogao('SECRET',request=request).download(123)


def test_invalid_pdf_and_provider_timeout_never_leak_response_or_key():
    def request(method,url,**kw):
        if 'api.fxbaogao.com' in url:return Response({'code':0,'data':'https://dr.fxbaogao.com/a.pdf'})
        return Response(b'<html>SECRET quota exhausted</html>')
    with pytest.raises(api().BrokerError) as error:api().FxBaogao('SECRET',request=request).download(123)
    assert 'SECRET' not in str(error.value)
    def fail(*a,**kw):raise RuntimeError('SECRET signed-url')
    with pytest.raises(api().BrokerError) as error:api().FxBaogao('SECRET',request=fail).search(MATRIX)
    assert 'SECRET' not in str(error.value)


def test_api_call_limit_prevents_another_request():
    calls=[]
    def request(*a,**kw):calls.append(True);return Response({'code':0,'data':[]})
    c=api().FxBaogao('x',request=request,max_calls=1)
    c.search(MATRIX)
    with pytest.raises(api().BrokerError):c.search(MATRIX)
    assert len(calls)==1


def test_original_archive_replay_and_typed_context_exclude_provider_summary():
    drive=Drive()
    class Client:
        calls=0
        def download(self,rid):self.calls+=1;return pdf()
    client=Client();kwargs=dict(company_path='投资/公司A',issuer_id='issuer-a',report=REPORT,identity_confirmed=True)
    ref=api().archive_report(client,drive,**kwargs)
    assert api().archive_report(client,drive,**kwargs)==ref and client.calls==1
    entry=api().load_report(drive,ref,company_path='投资/公司A',issuer_id='issuer-a')
    assert entry['evidence_kind']=='sell_side_report'
    assert '[PDF page 1]' in entry['source_text'] and 'FY2027' in entry['source_text']
    assert entry['broker']['published_at']=='2026-10-01' and entry['broker']['total_pages']==1
    assert entry['broker']['consensus_status']=='single_broker_only'
    assert entry['broker']['human_review']=='pending'
    assert entry['source']['url']=='https://www.fxbaogao.com/view?id=123'


def test_unknown_download_remains_reconciliation_blocked_after_failure():
    drive=Drive()
    class Client:
        calls=0
        def download(self,rid):self.calls+=1;raise RuntimeError('uncertain')
    c=Client();kw=dict(company_path='投资/公司A',issuer_id='issuer-a',report=REPORT,identity_confirmed=True)
    with pytest.raises(api().BrokerError):api().archive_report(c,drive,**kw)
    with pytest.raises(api().BrokerError):api().archive_report(c,drive,**kw)
    assert c.calls==1


def test_cached_pdf_uses_no_download_and_rejects_conflicting_issuer_and_tampered_bytes():
    d=Drive();kw=dict(company_path='投资/公司A',issuer_id='issuer-a',report=REPORT,identity_confirmed=True,cached_pdf=pdf())
    ref=api().archive_report(None,d,**kw)
    for changes in [{'issuer_id':'wrong'},{'company_path':'投资/公司B'}]:
        with pytest.raises(ValueError):api().load_report(d,ref,company_path=changes.get('company_path','投资/公司A'),issuer_id=changes.get('issuer_id','issuer-a'))
    manifest=json.loads(d.files[ref['path']]);d.files[manifest['original']['path']]=pdf()
    with pytest.raises(ValueError):api().load_report(d,ref,company_path='投资/公司A',issuer_id='issuer-a')


def test_unconfirmed_identity_is_rejected_before_archive():
    d=Drive()
    with pytest.raises(ValueError):api().archive_report(None,d,company_path='投资/公司A',issuer_id='issuer-a',report=REPORT,identity_confirmed=False,cached_pdf=pdf())
    assert d.files=={}
