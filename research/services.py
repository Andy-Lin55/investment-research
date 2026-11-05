"""Explicit service wiring; does not load .env or contact services on import."""
import base64
import json
from pathlib import Path
from urllib.parse import urlsplit,quote

from research.delivery import ServiceError,scoped_path
from financials.forecast import number


def _anonymous_graph_host(host):
    return host in {'api.onedrive.com','onedrive.live.com','my.microsoftpersonalcontent.com'} or any(
        host.endswith(s) for s in ('.1drv.com','.storage.live.com','.sharepoint.com','.onedrive.com'))


def _curl_graph_download(url):
    """Optional TLS fallback for signed anonymous reads, never bearer requests."""
    import shutil,subprocess
    p=urlsplit(url);binary=shutil.which('curl')
    if (not binary or p.scheme!='https' or p.username or p.password or p.port not in {None,443}
            or not _anonymous_graph_host(p.hostname or '')):raise ServiceError(0)
    command=[binary,'--config','-','--silent','--show-error','--fail','--proto','=https',
        '--max-time','45','--max-filesize',str(40*1024*1024),'--http2','--write-out','%{http_code}']
    for attempt in range(3):
        try:
            result=subprocess.run(command,input=('url = '+json.dumps(url)+'\n').encode(),
                stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=50,check=False)
        except (OSError,subprocess.TimeoutExpired):raise ServiceError(0) from None
        if result.returncode not in {6,7,28,35,52,56} or attempt==2:break
    if result.returncode or len(result.stdout)<3 or len(result.stdout)>40*1024*1024+3:
        error=ServiceError(0);error.transport_code=result.returncode;raise error
    try:status=int(result.stdout[-3:])
    except ValueError:raise ServiceError(0) from None
    if not 200<=status<300:raise ServiceError(status)
    return result.stdout[:-3]


class HTTP:
    def __init__(self,base,headers,*,curl_downloads=False):
        self.base=base.rstrip('/');self.headers=dict(headers);self.curl_downloads=curl_downloads
    def __call__(self,method,path,*,body=None,headers=None,raw=False,authenticated=True):
        import requests
        absolute=path.startswith('https://')
        url=path if absolute else self.base+path
        parts=urlsplit(url);host=parts.hostname or ''
        if parts.scheme!='https' or parts.username or parts.password:raise ValueError('credential-free HTTPS required')
        if absolute:
            # Personal drives migrated by Microsoft return this upload-session host.
            allowed=_anonymous_graph_host(host) and parts.port in {None,443}
            if authenticated or not allowed:raise ValueError('untrusted upload/download destination')
        request_headers=dict(self.headers if authenticated else {});request_headers.update(headers or {})
        if body is not None and not isinstance(body,bytes):
            body=json.dumps(body,ensure_ascii=False,allow_nan=False).encode()
            request_headers.setdefault('Content-Type','application/json')
        kwargs={'data':body} if body is not None else {}
        try:response=requests.request(method,url,headers=request_headers,timeout=(15,90),allow_redirects=False,**kwargs)
        except requests.RequestException:
            if self.curl_downloads and absolute and not authenticated and not request_headers and method=='GET' and raw:
                return _curl_graph_download(url)
            raise ServiceError(0) from None
        if response.status_code in {301,302,303,307,308} and method=='GET' and raw and authenticated:
            return self('GET',response.headers['Location'],raw=True,authenticated=False)
        if not 200<=response.status_code<300:raise ServiceError(response.status_code)
        if raw:return response.content
        return response.json() if response.content else {}


def refresh_graph(client_id,refresh_token,post,persist):
    if not client_id or not refresh_token or not callable(persist):raise ValueError('client, token and safe rotation sink required')
    response=post('/consumers/oauth2/v2.0/token',{'client_id':client_id,'refresh_token':refresh_token,
                  'grant_type':'refresh_token','scope':'offline_access Files.ReadWrite User.Read'})
    if not response.get('access_token') or not response.get('refresh_token'):raise ServiceError(401)
    persist(response['refresh_token'])
    return response['access_token']


class APIProvider:
    def __init__(self,name,model,key,http,*,input_rate=None,output_rate=None):
        self.name=name;self.model=model;self.http=http
        self.input_cny_per_million=input_rate;self.output_cny_per_million=output_rate
    def __call__(self,prompt,*,max_output_tokens):
        response=self.http('POST','/chat/completions',body=dict(model=self.model,
                    messages=[{'role':'user','content':prompt}],max_tokens=max_output_tokens))
        return dict(content=response['choices'][0]['message']['content'],actual_model=response.get('model'),
                    input_tokens=response.get('usage',{}).get('prompt_tokens'),
                    output_tokens=response.get('usage',{}).get('completion_tokens'))


def configured_providers(env):
    providers=[]
    for name,key,base,prefix in [('dmx','DMXAPI_KEY','https://www.dmxapi.cn/v1','DMX'),
                                 ('gemini','GEMINI_API_KEY','https://generativelanguage.googleapis.com/v1beta/openai','GEMINI')]:
        if not env.get(key):continue
        rates=[]
        for suffix in ('INPUT_CNY_PER_MILLION','OUTPUT_CNY_PER_MILLION'):
            value=env.get('RESEARCH_'+prefix+'_'+suffix)
            try:rates.append(float(value) if value is not None else None)
            except ValueError:rates.append(None)
        model=env.get('RESEARCH_'+prefix+'_MODEL','gemini-3.8-flash')
        providers.append(APIProvider(name,model,env[key],HTTP(base,{'Authorization':'Bearer '+env[key]}),
                                     input_rate=rates[0],output_rate=rates[1]))
    return providers


def doctor(env):
    required=['NOTION_TOKEN','RESEARCH_UNIVERSE_SOURCE_ID','RESEARCH_NOTION_TASKS_SOURCE_ID',
              'RESEARCH_NOTION_REPORTS_SOURCE_ID','RESEARCH_NOTION_FOLLOWUPS_SOURCE_ID','ONEDRIVE_CLIENT_ID','ONEDRIVE_REFRESH_TOKEN',
              'DMXAPI_KEY','RESEARCH_DMX_INPUT_CNY_PER_MILLION','RESEARCH_DMX_OUTPUT_CNY_PER_MILLION']
    if env.get('GITHUB_ACTIONS')=='true':required.append('RESEARCH_GITHUB_TOKEN')
    else:
        required.append('ONEDRIVE_TOKEN_CACHE')
        if env.get('ONEDRIVE_TOKEN_CACHE') and Path(env['ONEDRIVE_TOKEN_CACHE']).is_file():
            required.remove('ONEDRIVE_REFRESH_TOKEN')
    return dict(enabled=env.get('HOLDINGS_RESEARCH_ENABLED')=='true',missing=[k for k in required if not env.get(k)],
                budget_cny=dict(task=2,day=10,month=100),
                production_active=False,note='Presence checks only; authorization and read-back are separate tests.')


def validate_registry(registry):
    if not isinstance(registry,list):raise ValueError('issuer registry must be a list')
    seen=set();issuers=set()
    for h in registry:
        if not h.get('issuer_id') or h['issuer_id'] in issuers or h.get('identity_confirmed') is not True:
            raise ValueError('unique confirmed issuer identities required')
        issuers.add(h['issuer_id']);scoped_path(h['company_path'])
        if not isinstance(h.get('tickers'),list) or not h['tickers']:raise ValueError('listing mapping required')
        for ticker in h['tickers']:
            if not isinstance(ticker,str) or not ticker or ticker in seen:raise ValueError('ambiguous/duplicate listing')
            seen.add(ticker)
    return registry


def match_roster(active_tickers,registry):
    validate_registry(registry);active=set(active_tickers);out=[];mapped=set()
    for h in registry:
        selected=active.intersection(h['tickers'])
        if selected:out.append(dict(h,status='Active',active_tickers=sorted(selected)));mapped.update(selected)
    return dict(holdings=out,unmapped=sorted(active-mapped),complete=active==mapped)


def github_secret_writer(token,*,repository='Andy-Lin55/investment-research'):
    if repository!='Andy-Lin55/investment-research':raise ValueError('repository outside scope')
    def persist(value):
        from nacl.public import PublicKey,SealedBox
        http=HTTP('https://api.github.com',{'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json'})
        key=http('GET',f'/repos/{repository}/actions/secrets/public-key')
        encrypted=SealedBox(PublicKey(base64.b64decode(key['key']))).encrypt(value.encode())
        http('PUT',f'/repos/{repository}/actions/secrets/ONEDRIVE_REFRESH_TOKEN',
             body={'encrypted_value':base64.b64encode(encrypted).decode(),'key_id':key['key_id']})
    return persist
