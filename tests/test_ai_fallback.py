import json
import subprocess
import sys
import types
from unittest.mock import Mock

import pytest

from data_ingestion import ai_client, codex_client


def stream(message="订阅结果", complete=True):
    events = [{"type": "item.completed", "item": {"type": "agent_message", "text": message}}]
    if complete:
        events.append({"type": "turn.completed"})
    return "\n".join(json.dumps(event, ensure_ascii=False) for event in events)


@pytest.fixture
def routing(monkeypatch):
    monkeypatch.setattr(ai_client, "CODEX_SUBSCRIPTION_ENABLED", True)
    monkeypatch.setattr(ai_client, "DMXAPI_KEY", "test-dmx")
    monkeypatch.setattr(ai_client, "GEMINI_API_KEY", "test-google")
    codex = Mock(return_value="订阅结果")
    api = Mock(return_value="API 结果")
    monkeypatch.setattr(codex_client, "call_codex", codex)
    monkeypatch.setattr(ai_client, "_call_api", api)
    return codex, api


def test_subscription_success_avoids_paid_api(routing):
    codex, api = routing
    assert ai_client.call_ai("公告") == "订阅结果"
    codex.assert_called_once()
    api.assert_not_called()


@pytest.mark.parametrize("codex_failure", ["", RuntimeError("unavailable")])
def test_subscription_failure_falls_back_to_current_dmx(routing, codex_failure):
    codex, api = routing
    if isinstance(codex_failure, Exception):
        codex.side_effect = codex_failure
    else:
        codex.return_value = codex_failure
    assert ai_client.call_ai("公告") == "API 结果"
    assert api.call_args.kwargs["api_key"] == "test-dmx"
    assert api.call_args.kwargs["model"] == "gemini-3.8-flash"


def test_subscription_and_dmx_failure_fall_back_to_google(routing):
    codex, api = routing
    codex.return_value = ""
    api.side_effect = [RuntimeError("dmx down"), "Google 结果"]
    assert ai_client.call_ai("公告") == "Google 结果"
    assert [call.kwargs["api_key"] for call in api.call_args_list] == ["test-dmx", "test-google"]


def test_all_backends_unavailable_return_empty(routing):
    codex, api = routing
    codex.return_value = ""
    api.side_effect = RuntimeError("down")
    assert ai_client.call_ai("公告") == ""


def test_disabled_subscription_preserves_api_only_behavior(routing, monkeypatch):
    codex, api = routing
    monkeypatch.setattr(ai_client, "CODEX_SUBSCRIPTION_ENABLED", False)
    assert ai_client.call_ai("公告") == "API 结果"
    codex.assert_not_called()
    api.assert_called_once()


def test_api_timeout_does_not_add_sdk_retries(monkeypatch):
    client = Mock()
    client.chat.completions.create.return_value.choices = [types.SimpleNamespace(message=types.SimpleNamespace(content=" result "))]
    factory = Mock(return_value=client)
    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=factory))
    assert ai_client._call_api("prompt", api_key="test", base_url="https://example.com/v1", model="test-model", timeout=9) == "result"
    factory.assert_called_once_with(api_key="test", base_url="https://example.com/v1", max_retries=0)
    assert client.chat.completions.create.call_args.kwargs["timeout"] == 9


def test_final_message_preserves_json_without_cli_diagnostics():
    content = '{"revenue": 100, "profit": 8}'
    assert codex_client._final_message("diagnostic\n" + stream(content)) == content


@pytest.mark.parametrize("output", [
    stream("partial", complete=False),
    stream() + '\n{"type": "turn.failed"}',
    stream() + '\n{"type": "item.completed", "item": {"type": "command_execution"}}',
    '"unexpected"',
    '{"type":"item.completed","item":null}',
])
def test_incomplete_failed_or_tool_using_output_is_rejected(output):
    assert codex_client._final_message(output) == ""


def test_child_environment_does_not_receive_application_secrets(monkeypatch):
    for name in ["DMXAPI_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY", "CODEX_API_KEY", "NOTION_TOKEN", "SMTP_PASS"]:
        monkeypatch.setenv(name, "test-secret")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:8080")
    environment = codex_client._subscription_environment()
    assert environment["HTTPS_PROXY"] == "http://proxy.example:8080"
    assert not any(name in environment for name in ["DMXAPI_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY", "CODEX_API_KEY", "NOTION_TOKEN", "SMTP_PASS"])


@pytest.mark.parametrize("variable", ["CI", "GITHUB_ACTIONS"])
def test_ci_never_starts_subscription_cli(monkeypatch, variable):
    monkeypatch.setenv(variable, "true")
    process = Mock()
    monkeypatch.setattr(codex_client.subprocess, "Popen", process)
    assert codex_client.call_codex("公告") == ""
    process.assert_not_called()


def prepare_process(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(codex_client.shutil, "which", Mock(return_value="/mock/codex"))
    process = Mock(returncode=0, pid=12345)
    factory = Mock(return_value=process)
    monkeypatch.setattr(codex_client.subprocess, "Popen", factory)
    return process, factory


def test_subscription_runs_without_tools_and_passes_prompt_on_stdin(monkeypatch):
    process, factory = prepare_process(monkeypatch)
    process.communicate.return_value = (stream(), "")
    assert codex_client.call_codex("private prompt") == "订阅结果"
    args = factory.call_args.args[0]
    assert "private prompt" not in args
    assert 'forced_login_method="chatgpt"' in args
    assert "features.shell_tool=false" in args
    assert "features.view_image=false" in args
    assert "--ignore-user-config" in args
    assert "private prompt" in process.communicate.call_args.args[0]


def test_timeout_kills_and_reaps_process_before_fallback(monkeypatch):
    process, _ = prepare_process(monkeypatch)
    process.communicate.side_effect = [subprocess.TimeoutExpired("codex", 1), ("", "")]
    kill = Mock()
    monkeypatch.setattr(codex_client.os, "killpg", kill)
    assert codex_client.call_codex("公告", timeout=1) == ""
    kill.assert_called_once_with(12345, codex_client.signal.SIGKILL)
    assert process.communicate.call_count == 2


def test_missing_cli_and_nonzero_exit_are_unavailable(monkeypatch):
    process, _ = prepare_process(monkeypatch)
    process.returncode = 1
    process.communicate.return_value = (stream(), "auth expired")
    assert codex_client.call_codex("公告") == ""
    monkeypatch.setattr(codex_client.shutil, "which", Mock(return_value=None))
    assert codex_client.call_codex("公告") == ""
