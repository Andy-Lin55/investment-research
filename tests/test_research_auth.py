import importlib
import pytest

def api():
    try:return importlib.import_module('research.auth')
    except ModuleNotFoundError:pytest.fail('OneDrive authorization helper is not implemented')

def test_device_auth_is_started_only_on_explicit_call_and_persists_secret_privately(tmp_path):
    calls=[];shown=[];saved=[]
    def post(path,body):
        calls.append(path)
        if path.endswith('/devicecode'):return dict(user_code='ABCD',verification_uri='https://microsoft.com/devicelogin',
            device_code='private-device-code',interval=1,expires_in=60)
        return dict(refresh_token='private-refresh',access_token='private-access')
    api().authorize('00000000-0000-0000-0000-000000000001',post,shown.append,saved.append,sleep=lambda _:None)
    assert saved==['private-refresh']
    assert all('private-' not in str(x) for x in shown)
    assert len(calls)==2

def test_device_auth_requires_owned_application_id():
    with pytest.raises(ValueError):api().authorize('',None,None,None)
