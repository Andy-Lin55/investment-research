"""Offline-only regression boundary, installed before test module collection."""
import smtplib
import socket
import subprocess

import dotenv
import pytest
import requests

_guard = pytest.MonkeyPatch()
_attempts = []


def _deny_external(*args, **kwargs):
    _attempts.append(True)
    raise AssertionError("Offline regression forbids real network, SMTP and child processes; use a boundary fixture")


def pytest_configure(config):
    # settings.py normally loads .env with override=True. Never let collection
    # import real credentials, even in the operator's normal checkout.
    _guard.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)
    for name in (
        "NOTION_TOKEN", "TUSHARE_TOKEN", "FINNHUB_KEY", "DMXAPI_KEY", "GEMINI_API_KEY",
        "OPENAI_API_KEY", "CODEX_API_KEY", "SMTP_USER", "SMTP_PASS",
    ):
        _guard.setenv(name, "offline-test-placeholder")
    for name in (
        "NOTION_UNIVERSE_DB_ID", "NOTION_EVENTS_DB_ID", "NOTION_RESEARCH_DB_ID",
        "NOTION_RESEARCH_HISTORY_DB_ID",
    ):
        _guard.setenv(name, "00000000-0000-0000-0000-000000000001")
    _guard.setenv("FEISHU_WEBHOOK", "")
    _guard.setenv("BRIEFING_TO_EMAIL", "regression@example.invalid")
    _guard.setenv("BRIEFING_FROM_EMAIL", "regression@example.invalid")
    _guard.setenv("SMTP_HOST", "smtp.example.invalid")
    _guard.setenv("CODEX_SUBSCRIPTION_ENABLED", "false")
    _guard.setenv("CODEX_CLI_PATH", "/nonexistent/offline-codex")
    _guard.setattr(socket.socket, "connect", _deny_external)
    _guard.setattr(socket.socket, "connect_ex", _deny_external)
    _guard.setattr(socket, "create_connection", _deny_external)
    for name in ("getaddrinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr"):
        _guard.setattr(socket, name, _deny_external)
    _guard.setattr(requests.Session, "request", _deny_external)
    _guard.setattr(smtplib.SMTP, "connect", _deny_external)
    _guard.setattr(subprocess, "Popen", _deny_external)


def pytest_unconfigure(config):
    _guard.undo()


def pytest_collection_finish(session):
    if _attempts:
        raise pytest.UsageError("Test imports attempted external access during collection")


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_teardown(item, nextitem):
    result = yield
    if _attempts:
        _attempts.clear()
        pytest.fail("External access was attempted, even if application code caught the offline guard")
    return result


def pytest_sessionfinish(session, exitstatus):
    if _attempts:
        _attempts.clear()
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
