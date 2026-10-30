"""Explicit, bounded broker access. No implicit credentials or autonomous research."""
from copy import deepcopy
from datetime import date
import hashlib, json, re
from pathlib import PurePosixPath
from urllib.parse import urlencode, urljoin, urlsplit
from uuid import uuid4

from research.delivery import ServiceError, scoped_path
from research.sources import extract

API='https://api.fxbaogao.com'
MAX_PDF=40*1024*1024


class BrokerError(RuntimeError):pass


def _id(value):
    if isinstance(value,bool) or not isinstance(value,int) or value<1:raise ValueError('positive report ID required')
    return value


def _bytes(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False).encode()
def _sha(raw):return hashlib.sha256(raw).hexdigest()
def _clean(value):return re.sub('<[^>]*>','',str(value)).strip()


def _url(url,host):
    try:p=urlsplit(url)
    except ValueError:raise BrokerError('Invalid source destination') from None
    try:valid=p.scheme=='https' and p.hostname==host and p.port in {None,443} and not p.username and not p.password and not p.fragment
    except ValueError:valid=False
    if not valid:raise BrokerError('Untrusted source destination')
    return url


class FxBaogao:
    def __init__(self,key,request=None,*,max_calls=6):
        if not isinstance(key,str) or not key.strip() or any(c in key for c in '\r\n'):
            raise ValueError('explicit private provider credential required')
        if isinstance(max_calls,bool) or not isinstance(max_calls,int) or not 1<=max_calls<=12:
            raise ValueError('bounded source calls required')
        if request is None:
            import requests
            request=requests.request
        self._key=key;self._request=request;self.remaining=max_calls;self.downloads_remaining=2

    def _fetch(self,method,url,*,body=None,api=True):
        _url(url,'api.fxbaogao.com' if api else 'dr.fxbaogao.com')
        if self.remaining<=0:raise BrokerError('Source request limit reached')
        self.remaining-=1
        headers={'Accept':'application/json' if api else 'application/pdf'}
        if api:headers['Authorization']='Bearer '+self._key
        kwargs={}
        if body is not None:
            headers['Content-Type']='application/json';kwargs['data']=_bytes(body)
        response=None
        try:
            response=self._request(method,url,headers=headers,allow_redirects=False,
                                   stream=True,timeout=(15,90),**kwargs)
            if response.status_code in {301,302,303,307,308}:
                if api:raise BrokerError('API redirect refused')
                return None,urljoin(url,response.headers['Location'])
            if response.status_code!=200:raise BrokerError('Source request unsuccessful; outcome may need reconciliation')
            chunks=[];size=0;limit=5*1024*1024 if api else MAX_PDF
            for chunk in response.iter_content(1024*1024):
                size+=len(chunk)
                if size>limit:raise BrokerError('Source exceeds bounded response size')
                chunks.append(chunk)
            return b''.join(chunks),None
        except BrokerError:raise
        except Exception:raise BrokerError('Source transport failed; private details omitted') from None
        finally:
            if response is not None:response.close()

    def _json(self,path,body=None):
        raw,_=self._fetch('POST' if body is not None else 'GET',API+path,body=body)
        try:r=json.loads(raw)
        except (ValueError,TypeError):raise BrokerError('Invalid source response') from None
        if not isinstance(r,dict) or type(r.get('code')) is not int or r['code']!=0 or 'data' not in r:
            raise BrokerError('Source entitlement or response unavailable')
        return r['data']

    def search(self,matrix):
        if (not isinstance(matrix,list) or not 1<=len(matrix)<=6 or any(not isinstance(x,dict)
                or any(not isinstance(x.get(k),str) or not x[k].strip() or len(x[k])>800 for k in ('question','keywords')) for x in matrix)):
            raise ValueError('bounded question and keyword matrix required')
        if len(matrix)>self.remaining:raise BrokerError('Search exceeds remaining source calls')
        out=[];seen=set();ids=set()
        for query in matrix:
            payload=dict(keywords=query['keywords'],pageNum=1,startTime='last3mon')
            data=self._json('/mofoun/agent/search',payload)
            if not isinstance(data,list) or len(data)>200:raise BrokerError('Invalid search inventory')
            for row in data:
                try:
                    rid=_id(row['reportId']);title=_clean(row['title']);org=_clean(row['orgName'])
                    published=str(row['pubTimeStr']).replace('/','-')[:10];date.fromisoformat(published)
                    if not title or not org:raise ValueError()
                except (ValueError,TypeError,KeyError):raise BrokerError('Incomplete report identity') from None
                key=(title.casefold(),org.casefold(),published)
                if rid in ids or key in seen:continue
                ids.add(rid);seen.add(key)
                out.append(dict(report_id=rid,title=title,org_name=org,published_at=published,
                    report_url='https://www.fxbaogao.com/view?id='+str(rid),identity_status='candidate',
                    research_question=query['question'],original_verified=False))
        return out

    def paragraphs(self,report_id,keyword):
        _id(report_id)
        if not isinstance(keyword,str) or not keyword.strip() or len(keyword)>200:raise ValueError('bounded paragraph query required')
        data=self._json('/mofoun/agent/paragraph',dict(reportId=report_id,keyword=keyword))
        if not isinstance(data,dict) or not isinstance(data.get('paragraphs'),list):raise BrokerError('Invalid paragraph response')
        return data

    def download(self,report_id):
        _id(report_id)
        if self.downloads_remaining<=0:raise BrokerError('Original acquisition limit reached')
        self.downloads_remaining-=1
        url=self._json('/mofoun/agent/download?'+urlencode({'reportId':report_id}))
        if not isinstance(url,str) or not url:raise BrokerError('Original download URL unavailable')
        url=urljoin('https://dr.fxbaogao.com/',url)
        for _ in range(4):
            raw,redirect=self._fetch('GET',url,api=False)
            if redirect:url=redirect;continue
            if not raw.startswith(b'%PDF-'):raise BrokerError('Original PDF unavailable')
            return raw
        raise BrokerError('Original redirect limit reached')


def _optional(drive,path):
    try:return drive.read(path)
    except ServiceError as error:
        if error.status==404:return None
        raise


def _write(drive,path,raw):
    ref=drive.put_immutable(path,raw)
    if ref.get('verified') is not True or ref.get('sha256')!=_sha(raw):raise ValueError('immutable source receipt not verified')
    return ref


def _meta(report):
    rid=_id(report['report_id'])
    for key in ('title','org_name','published_at'):
        if not isinstance(report.get(key),str) or not report[key].strip() or len(report[key])>1000:
            raise ValueError('complete report identity required')
    date.fromisoformat(report['published_at'])
    return {k:report[k] for k in ('report_id','title','org_name','published_at')}


def _under(path,root):
    path=scoped_path(path)
    if not path.startswith(root+'/'):raise ValueError('broker source outside company scope')
    return path


def archive_report(client,drive,*,company_path,issuer_id,report,identity_confirmed,cached_pdf=None):
    root=scoped_path(company_path)
    if identity_confirmed is not True or not isinstance(issuer_id,str) or not issuer_id.strip():
        raise ValueError('explicit issuer match confirmation required')
    report=_meta(report);base=root+'/01_原始资料/卖方研报/fxbaogao/'+str(report['report_id'])
    identity=dict(issuer_id=issuer_id,report=report)
    manifest_path=base+'/manifest.json';existing=_optional(drive,manifest_path)
    if existing:
        raw,_=existing;manifest=json.loads(raw)
        if manifest.get('issuer_id')!=issuer_id or manifest.get('report')!=report:raise ValueError('archived broker identity differs')
        ref=dict(path=manifest_path,sha256=_sha(raw),verified=True)
        load_report(drive,ref,company_path=root,issuer_id=issuer_id)
        return ref
    original_path=base+'/original.pdf';original=_optional(drive,original_path)
    attempt_path=base+'/acquisition-attempt.json'
    if original:raw=original[0]
    elif cached_pdf is not None:raw=cached_pdf
    else:
        if _optional(drive,attempt_path):raise BrokerError('Original acquisition uncertain; reconcile before retry')
        _write(drive,attempt_path,_bytes(dict(identity,attempt_id=uuid4().hex)))
        try:raw=client.download(report['report_id'])
        except Exception:raise BrokerError('Original acquisition uncertain; reconcile before retry') from None
    if not isinstance(raw,bytes) or not raw.startswith(b'%PDF-') or len(raw)>MAX_PDF:
        raise BrokerError('Invalid original PDF')
    parsed=extract(raw,kind='pdf',max_chars=120000)
    if not parsed['text'].strip():raise BrokerError('Original has no readable text; OCR review required')
    original_ref=_write(drive,original_path,raw)
    parsed_ref=_write(drive,base+'/extraction.json',_bytes(parsed))
    manifest=dict(version='broker-original-v1',issuer_id=issuer_id,report=report,
        report_url='https://www.fxbaogao.com/view?id='+str(report['report_id']),
        original=original_ref,extraction=parsed_ref,human_review='pending',
        identity_basis='explicitly confirmed report-to-issuer mapping; PDF facts still require review')
    ref=_write(drive,manifest_path,_bytes(manifest))
    return dict(path=ref['path'],sha256=ref['sha256'],verified=True)


def load_report(drive,ref,*,company_path,issuer_id):
    root=scoped_path(company_path)
    if not isinstance(ref,dict) or ref.get('verified') is not True or not re.fullmatch('[0-9a-f]{64}',str(ref.get('sha256',''))):
        raise ValueError('verified broker manifest digest required')
    raw,_=drive.read(_under(ref['path'],root))
    if _sha(raw)!=ref['sha256']:raise ValueError('broker manifest digest differs')
    manifest=json.loads(raw)
    if manifest.get('version')!='broker-original-v1' or manifest.get('issuer_id')!=issuer_id:
        raise ValueError('broker issuer or schema differs')
    report=_meta(manifest['report']);expected_url='https://www.fxbaogao.com/view?id='+str(report['report_id'])
    if manifest['report_url']!=expected_url:raise ValueError('report locator differs')
    original=manifest['original'];data,meta=drive.read(_under(original['path'],root))
    if 'remoteItem' in meta or original.get('verified') is not True or _sha(data)!=original.get('sha256'):
        raise ValueError('broker original digest differs')
    # Re-extract original bytes: even a changed JSON cache cannot inject a summary.
    parsed=extract(data,kind='pdf',max_chars=120000)
    if not data.startswith(b'%PDF-') or not parsed['text'].strip():raise ValueError('broker original unreadable')
    return dict(revision_id=_sha(_bytes([issuer_id,report,_sha(data)])),evidence_kind='sell_side_report',
        source=dict(url=expected_url,locator=report['org_name']+'｜'+report['published_at']+'｜'+report['title'],
                    published_at=report['published_at'],original=original),
        broker=dict(report,original_sha256=_sha(data),total_pages=parsed['total_pages'],
                    human_review='pending',consensus_status='single_broker_only'),
        source_text=parsed['text'],partial=parsed['partial'],missing=[],
        note='Dated broker analysis and forecasts, not first-party facts, approved model inputs or market consensus. PDF page markers refer to the retained original.')
