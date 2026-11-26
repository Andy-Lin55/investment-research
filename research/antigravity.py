"""Local Antigravity host CLI (`agy`) provider with Opus-first adaptive model selection."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time

from research.model_catalog import resolve_adaptive_model

_STRIPPED_ENV_KEYS = (
    'OPENAI_API_KEY', 'CODEX_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_API_KEY',
    'DMXAPI_KEY', 'QIFU_API_KEY', 'NOTION_TOKEN', 'GOOGLE_API_KEY', 'GEMINI_API_KEY',
)

_HOOK_SCRIPT = (
    'import json,sys\n'
    'sys.stdout.write(json.dumps({"decision":"deny","reason":"Research provider is text-only"})+"\\n")\n'
)


def _extract_json_text(text: str) -> str:
    stripped = text.strip()
    fence = re.search(r'```(?:json)?\s*(\{.*\})\s*```', stripped, re.DOTALL)
    if fence:
        return fence.group(1)
    if stripped.startswith('{') and stripped.endswith('}'):
        return stripped
    start, end = stripped.find('{'), stripped.rfind('}')
    if start != -1 and end > start:
        return stripped[start:end + 1]
    return stripped


def find_executable(cli_path: str | None = None) -> str | None:
    if cli_path:
        p = Path(cli_path)
        return str(p) if p.is_file() and os.access(p, os.X_OK) else None
    candidates = [
        shutil.which('agy'),
        str(Path.home() / '.local' / 'bin' / 'agy'),
        str(Path.home() / '.local' / 'bin' / 'antigravity'),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


class AntigravityProvider:
    name = 'antigravity'
    requires_pricing = False
    input_cny_per_million = 0.0
    output_cny_per_million = 0.0

    def __init__(
        self,
        model: str = 'auto',
        *,
        cli_path: str | None = None,
        preferred_tier: str = 'high',
        prefer_opus: bool = True,
        isolate_mcp: bool = True,
        cache_path: Path | None = None,
        cache_seconds: int = 3600,
        timeout: int = 180,
    ):
        self.model = model or 'auto'
        self.cli_path = cli_path
        self.preferred_tier = preferred_tier
        self.prefer_opus = prefer_opus
        self.isolate_mcp = isolate_mcp
        self.cache_path = cache_path or (Path.home() / '.cache' / 'investment-research' / 'antigravity_models.json')
        self.cache_seconds = cache_seconds
        self.timeout = timeout

    @staticmethod
    def _host_env() -> dict[str, str]:
        env = dict(os.environ)
        for key in _STRIPPED_ENV_KEYS:
            env.pop(key, None)
        return env

    def _executable(self) -> str:
        if os.getenv('GITHUB_ACTIONS') == 'true' or os.getenv('CI', '').lower() == 'true':
            raise RuntimeError('antigravity host CLI is local only')
        binary = find_executable(self.cli_path)
        if not binary:
            raise RuntimeError('Antigravity CLI is not available')
        return binary

    def _isolate_mcp_registry(self, command: list[str]) -> list[str]:
        if not self.isolate_mcp:
            return command
        candidates = [
            Path.home() / '.gemini' / 'config' / 'mcp_config.json',
            Path.home() / '.gemini' / 'mcp.json',
            Path.home() / '.gemini' / 'antigravity' / 'mcp_config.json',
            Path.home() / '.gemini' / 'antigravity-cli' / 'mcp_config.json',
        ]
        plugins_dir = Path.home() / '.gemini' / 'config' / 'plugins'
        if plugins_dir.is_dir():
            candidates.extend(plugins_dir.glob('*/mcp_config.json'))
        registries: list[Path] = []
        seen: set[Path] = set()
        for candidate in candidates:
            if not candidate.exists():
                continue
            for variant in (candidate.resolve(), Path(os.path.abspath(candidate))):
                if variant not in seen:
                    seen.add(variant)
                    registries.append(variant)
        if not registries:
            return command
        sandbox = shutil.which('sandbox-exec')
        if not sandbox:
            return command
        clauses = [
            '(deny file-read* (literal "' + str(p).replace('\\', '\\\\').replace('"', '\\"') + '"))'
            for p in registries
        ]
        profile = '(version 1)(allow default)' + ''.join(clauses)
        return [sandbox, '-p', profile, *command]

    def _read_cache(self) -> list[str] | None:
        if self.cache_seconds <= 0 or not self.cache_path.is_file():
            return None
        try:
            payload = json.loads(self.cache_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        listed_at = payload.get('listed_at')
        models = payload.get('models')
        if not isinstance(listed_at, (int, float)) or isinstance(listed_at, bool) or not isinstance(models, list):
            return None
        if time.time() - listed_at > self.cache_seconds:
            return None
        return [m for m in models if isinstance(m, str) and m.strip()]

    def _write_cache(self, models: list[str]) -> None:
        if self.cache_seconds <= 0:
            return
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.cache_path.with_name(f'{self.cache_path.name}.{os.getpid()}.{time.time_ns()}.tmp')
            tmp.write_text(json.dumps({'listed_at': time.time(), 'models': models}, ensure_ascii=False), encoding='utf-8')
            tmp.replace(self.cache_path)
        except OSError:
            pass

    def list_models(self, refresh: bool = False) -> list[str] | None:
        if not refresh:
            cached = self._read_cache()
            if cached is not None:
                return cached
        try:
            command = self._isolate_mcp_registry([self._executable(), 'models'])
        except RuntimeError:
            return None
        try:
            process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, start_new_session=True, env=self._host_env(),
            )
            try:
                stdout, _ = process.communicate(timeout=60)
            except subprocess.TimeoutExpired:
                if os.name == 'posix':
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    process.kill()
                process.communicate()
                return None
        except OSError:
            return None
        if process.returncode != 0:
            return None
        models = [
            line.split('\t', 1)[0].strip()
            for line in stdout.splitlines()
            if line.strip() and '\t' in line
        ]
        models = [m for m in models if m]
        if not models:
            return None
        self._write_cache(models)
        return models

    def _uses_adaptive_model(self) -> bool:
        return self.model.strip().lower() in {'', 'auto', 'adaptive', 'latest'}

    def resolve_model(self, refresh: bool = False) -> str | None:
        if not self._uses_adaptive_model():
            return self.model.strip()
        roster = self.list_models(refresh=refresh)
        if roster is None:
            return None
        return resolve_adaptive_model(
            roster,
            prefer_opus=self.prefer_opus,
            preferred_tier=self.preferred_tier,
        )

    @staticmethod
    def _is_unknown_model_error(body: dict) -> bool:
        error = str(body.get('error') or '').lower()
        return any(frag in error for frag in (
            'invalid model selection', 'not recognized as a known model', 'unknown model', 'model not found',
        ))

    def _invoke(self, executable: str, model: str | None, prompt: str, cwd: Path) -> tuple[dict, int]:
        command = [executable, '--disable-slash-commands', '--output-format', 'json', '--sandbox']
        if model:
            command.extend(['--model', model])
        command = self._isolate_mcp_registry(command)
        process = subprocess.Popen(
            command, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, start_new_session=True, env=self._host_env(),
        )
        try:
            stdout, _ = process.communicate(prompt, timeout=max(1, min(self.timeout, 600)))
        except subprocess.TimeoutExpired:
            if os.name == 'posix':
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            process.communicate()
            raise RuntimeError('Antigravity request timed out') from None
        body = json.loads(stdout)
        if not isinstance(body, dict):
            raise RuntimeError('Antigravity returned a malformed envelope')
        return body, int(process.returncode or 0)

    def __call__(self, prompt: str, *, max_output_tokens: int = 12000) -> dict:
        executable = self._executable()
        model = self.resolve_model()
        with tempfile.TemporaryDirectory(prefix='holdings-antigravity-') as tmp:
            cwd = Path(tmp)
            hooks_dir = cwd / '.agents'
            hooks_dir.mkdir(parents=True, exist_ok=True)
            hook_cmd = f'{shlex.quote(sys.executable)} -c {shlex.quote(_HOOK_SCRIPT)}'
            hooks = {'hooks': {'PreToolUse': [{'matcher': '*', 'hooks': [{'type': 'command', 'command': hook_cmd, 'timeout': 5}]}]}}
            (hooks_dir / 'hooks.json').write_text(json.dumps(hooks), encoding='utf-8')
            body, returncode = self._invoke(executable, model, prompt, cwd)
            if str(body.get('status') or '').upper() != 'SUCCESS' and self._uses_adaptive_model() and self._is_unknown_model_error(body):
                refreshed = self.resolve_model(refresh=True)
                if refreshed != model:
                    model = refreshed
                    body, returncode = self._invoke(executable, model, prompt, cwd)
        if returncode != 0 or str(body.get('status') or '').upper() != 'SUCCESS':
            raise RuntimeError(f"Antigravity failed: status={body.get('status')} rc={returncode}")
        structured = body.get('structured_output')
        if isinstance(structured, dict):
            content = json.dumps(structured, ensure_ascii=False)
        else:
            raw_text = body.get('response')
            if not isinstance(raw_text, str) or not raw_text.strip():
                raise RuntimeError('Antigravity returned empty response')
            content = _extract_json_text(raw_text)
        usage = body.get('usage') if isinstance(body.get('usage'), dict) else {}
        return dict(
            content=content,
            actual_model=str(body.get('model') or model or 'host-default'),
            input_tokens=usage.get('input_tokens'),
            output_tokens=usage.get('output_tokens'),
        )
