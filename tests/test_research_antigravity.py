import json
import subprocess
import pytest

from research.antigravity import AntigravityProvider

ROSTER = (
    'gemini-4-hydrogen-high\tGemini 4 Hydrogen (High)\n'
    'gemini-pro-orchestrator-g1-pro\tOrchestrator (Argon)\n'
    'claude-opus-5-5-high\tClaude Opus 5.5 (High)\n'
    'claude-sonnet-5-5-high\tClaude Sonnet 5.5 (High)\n'
)


def test_antigravity_auto_selects_opus_and_isolates_secrets(tmp_path, monkeypatch):
    monkeypatch.delenv('CI', raising=False)
    monkeypatch.delenv('GITHUB_ACTIONS', raising=False)
    monkeypatch.setenv('DMXAPI_KEY', 'secret-dmx')
    monkeypatch.setenv('GEMINI_API_KEY', 'secret-gemini')
    monkeypatch.setenv('NOTION_TOKEN', 'secret-notion')
    agy = tmp_path / 'agy'
    agy.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
    agy.chmod(0o755)

    calls = []
    body = json.dumps({
        'status': 'SUCCESS',
        'model': 'claude-opus-5-5-high',
        'response': '```json\n{"ok": true}\n```',
        'usage': {'input_tokens': 50, 'output_tokens': 12},
    })

    def fake_popen(command, **kwargs):
        calls.append((command, kwargs))
        stdout = ROSTER if len(command) >= 2 and command[1] == 'models' else body
        return type('Proc', (), {
            'returncode': 0,
            'pid': 99999,
            'communicate': lambda self, *a, **kw: (stdout, ''),
        })()

    monkeypatch.setattr(subprocess, 'Popen', fake_popen)
    p = AntigravityProvider(cli_path=str(agy), cache_path=tmp_path / 'cache.json', isolate_mcp=False)
    out = p('analyze', max_output_tokens=4000)
    assert json.loads(out['content']) == {'ok': True}
    assert out['actual_model'] == 'claude-opus-5-5-high'
    assert out['input_tokens'] == 50 and out['output_tokens'] == 12
    invoke_cmd, invoke_kw = calls[1]
    assert '--model' in invoke_cmd and invoke_cmd[invoke_cmd.index('--model') + 1] == 'claude-opus-5-5-high'
    assert '--sandbox' in invoke_cmd
    for key in ('DMXAPI_KEY', 'GEMINI_API_KEY', 'NOTION_TOKEN'):
        assert key not in invoke_kw['env']


def test_antigravity_refuses_ci_execution(tmp_path, monkeypatch):
    monkeypatch.setenv('GITHUB_ACTIONS', 'true')
    p = AntigravityProvider(cli_path=str(tmp_path / 'agy'), cache_path=tmp_path / 'cache.json')
    with pytest.raises(RuntimeError, match='local only'):
        p('analyze')
