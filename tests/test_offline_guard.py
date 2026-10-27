from pathlib import Path

import pytest

pytest_plugins = ["pytester"]


@pytest.mark.parametrize("scope", ["function", "session"])
def test_caught_external_attempt_in_fixture_finalizer_fails_run(pytester, scope):
    pytester.makeconftest(Path(__file__).with_name("conftest.py").read_text())
    pytester.makepyfile(f'''
import socket
import pytest
@pytest.fixture(scope={scope!r})
def resource():
    yield
    try:
        socket.create_connection(("example.invalid", 443))
    except AssertionError:
        pass
def test_finalizer(resource):
    pass
''')
    result = pytester.runpytest_inprocess("-q")
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    assert "External access was attempted" in result.stdout.str()


def test_caught_http_dns_error_cannot_hide_unmocked_request(pytester):
    pytester.makeconftest(Path(__file__).with_name("conftest.py").read_text())
    pytester.makepyfile('''
import socket
import requests
def test_request(monkeypatch):
    def failed_resolution(*args, **kwargs):
        raise socket.gaierror("synthetic DNS failure; no network")
    monkeypatch.setattr(socket, "getaddrinfo", failed_resolution)
    try:
        with requests.Session() as session:
            session.trust_env = False
            session.get("https://example.invalid/", timeout=1)
    except (requests.RequestException, AssertionError):
        pass
''')
    result = pytester.runpytest_inprocess("-q")
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    assert "External access was attempted" in result.stdout.str()
