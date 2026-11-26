from types import SimpleNamespace
import pytest,requests
from research.services import HTTP
from research.delivery import ServiceError


def test_anonymous_graph_tls_fallback_never_passes_bearer_or_signed_url_in_process_arguments(monkeypatch):
    monkeypatch.setattr(requests,'request',lambda *a,**kw:(_ for _ in ()).throw(requests.exceptions.SSLError('TLS EOF')))
    def curl(command,**kw):
        assert all('signed_secret' not in s and 'Bearer' not in s for s in command)
        assert b'Bearer' not in kw['input'] and b'private-token' not in kw['input']
        assert b'signed_secret' in kw['input']
        assert '--location' not in command and '--insecure' not in command
        return SimpleNamespace(returncode=0,stdout=b'original bytes200')
    monkeypatch.setattr('subprocess.run',curl)
    http=HTTP('https://graph.microsoft.com/v1.0',{'Authorization':'Bearer private-token'},curl_downloads=True)
    assert http('GET','https://my.microsoftpersonalcontent.com/a?sig=signed_secret',authenticated=False,raw=True)==b'original bytes'


def test_authenticated_requests_and_untrusted_hosts_cannot_use_anonymous_fallback(monkeypatch):
    monkeypatch.setattr(requests,'request',lambda *a,**kw:(_ for _ in ()).throw(requests.exceptions.SSLError('TLS EOF')))
    monkeypatch.setattr('subprocess.run',lambda *a,**kw:pytest.fail('must not fall back'))
    http=HTTP('https://graph.microsoft.com/v1.0',{'Authorization':'Bearer private'},curl_downloads=True)
    with pytest.raises(ServiceError):http('GET','/me/drive',raw=True)
    with pytest.raises(ValueError):http('GET','https://evil.invalid/a',authenticated=False,raw=True)


def test_anonymous_read_retries_a_transient_tls_failure_without_relaxing_verification(monkeypatch):
    monkeypatch.setattr(requests,'request',lambda *a,**kw:(_ for _ in ()).throw(requests.exceptions.SSLError('TLS EOF')))
    results=[SimpleNamespace(returncode=35,stdout=b''),SimpleNamespace(returncode=0,stdout=b'bytes200')]
    monkeypatch.setattr('subprocess.run',lambda *a,**kw:results.pop(0))
    http=HTTP('https://graph.microsoft.com/v1.0',{},curl_downloads=True)
    assert http('GET','https://my.microsoftpersonalcontent.com/file',authenticated=False,raw=True)==b'bytes'
