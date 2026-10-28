import importlib,json
import pytest

def api():
    try:return importlib.import_module('research.services')
    except ModuleNotFoundError:pytest.fail('Research service configuration is not implemented')


def test_doctor_reports_configuration_gaps_without_exposing_credentials():
    r=api().doctor({'NOTION_TOKEN':'private-secret','DMXAPI_KEY':'private-key'})
    assert 'private-secret' not in json.dumps(r)
    assert 'ONEDRIVE_CLIENT_ID' in r['missing']
    assert r['budget_cny']==dict(task=2,day=10,month=100)


def test_unknown_pricing_is_not_replaced_with_invented_prices():
    p=api().APIProvider('dmx','gemini-3.8-flash','key',lambda *a,**k:None)
    assert p.input_cny_per_million is None
    assert p.output_cny_per_million is None


def test_transport_rejects_arbitrary_upload_hosts_and_credentialed_urls():
    h=api().HTTP('https://graph.microsoft.com/v1.0',{'Authorization':'Bearer private'})
    with pytest.raises(ValueError):h('PUT','https://evil.invalid/upload',body=b'a',authenticated=False)
    with pytest.raises(ValueError):h('GET','https://name:secret@graph.microsoft.com/v1.0/me')


def test_token_refresh_persists_rotated_token_and_does_not_return_refresh_secret():
    saved=[]
    def post(path,body):
        assert body['refresh_token']=='refresh-secret'
        return dict(access_token='access',refresh_token='new-refresh')
    token=api().refresh_graph('client','refresh-secret',post,saved.append)
    assert token=='access';assert saved==['new-refresh']


def test_token_refresh_requires_safe_rotation_sink():
    with pytest.raises(ValueError):api().refresh_graph('client','secret',lambda *a:None,None)


def test_api_provider_records_returned_model_and_usage_not_a_hardcoded_analyst():
    def http(method,path,**kwargs):
        return dict(model='returned-model',usage=dict(prompt_tokens=12,completion_tokens=8),
                    choices=[{'message':{'content':'{}'}}])
    p=api().APIProvider('dmx','requested-model','key',http,input_rate=1,output_rate=2)
    assert p('p',max_output_tokens=10)==dict(content='{}',actual_model='returned-model',input_tokens=12,output_tokens=8)


def test_registry_rejects_overlapping_listings_and_unconfirmed_identity():
    m=api();a=dict(issuer_id='a',tickers=['A'],company_path='投资/A',identity_confirmed=True)
    with pytest.raises(ValueError):m.validate_registry([a,dict(a,issuer_id='b')])
    with pytest.raises(ValueError):m.validate_registry([dict(a,identity_confirmed=False)])


def test_roster_matches_exact_listing_and_never_silently_drops_active_holdings():
    m=api();registry=[dict(issuer_id='a',tickers=['A'],company_path='投资/A',identity_confirmed=True)]
    r=m.match_roster(['A','B'],registry)
    assert len(r['holdings'])==1;assert r['unmapped']==['B'];assert r['complete'] is False


def test_local_authorization_cache_counts_as_present_without_printing_token(tmp_path):
    cache=tmp_path/'.research-graph-token.json';cache.write_text('{"refresh_token":"private-secret"}')
    result=api().doctor({'ONEDRIVE_TOKEN_CACHE':str(cache)})
    assert 'ONEDRIVE_REFRESH_TOKEN' not in result['missing']
    assert 'private-secret' not in json.dumps(result)
