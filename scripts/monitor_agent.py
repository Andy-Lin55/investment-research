"""
scripts/monitor_agent.py
监控 investment_research.log，检测 ERROR/CRITICAL/Traceback，
自动调用 Claude Code 子 Agent 介入排查并修复 bug。

用法（通常由 start_scheduler() 自动启动，也可手动运行）：
  python scripts/monitor_agent.py
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = PROJECT_ROOT / "investment_research.log"

# Resolve claude CLI path at import time (launchd has minimal PATH)
_CLAUDE_CANDIDATES = [
    # nvm node path (most common)
    str(Path.home() / ".nvm/versions/node/v24.14.0/bin/claude"),
    # fallback: search PATH extended with common locations
]


def _find_claude() -> str:
    """Return absolute path to claude CLI, or 'claude' as last resort."""
    # Try candidates first (avoids nvm version churn by checking glob)
    nvm_bin = Path.home() / ".nvm/versions/node"
    if nvm_bin.exists():
        for version_dir in sorted(nvm_bin.iterdir(), reverse=True):
            candidate = version_dir / "bin" / "claude"
            if candidate.exists():
                return str(candidate)
    # Try shutil.which with extended PATH
    import os
    extended_env = os.environ.copy()
    extra = ":".join([
        str(Path.home() / ".nvm/versions/node/v24.14.0/bin"),
        "/usr/local/bin",
        "/opt/homebrew/bin",
    ])
    extended_env["PATH"] = extra + ":" + extended_env.get("PATH", "")
    found = shutil.which("claude", path=extended_env["PATH"])
    return found if found else "claude"


_CLAUDE_CMD = _find_claude()

# 每个独立错误最多触发一次 debug session 的冷却时间（秒）
COOLDOWN_SECONDS = 600

# 连续读取之间的间隔（秒）
POLL_INTERVAL = 60

# 触发时抓取的日志上下文行数
CONTEXT_LINES = 120


def _read_tail(path: Path, n: int) -> list[str]:
    """读取文件末尾 n 行"""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.readlines()[-n:]


def _extract_error_blocks(lines: list[str]) -> list[str]:
    """从日志行中提取包含 ERROR / CRITICAL / Traceback 的片段"""
    error_lines = []
    in_traceback = False
    for line in lines:
        if "Traceback" in line:
            in_traceback = True
        if in_traceback or "| ERROR" in line or "| CRITICAL" in line:
            error_lines.append(line)
        if in_traceback and line.strip() and not line.startswith(" ") and "Traceback" not in line:
            # Traceback 块结束（非缩进行）
            in_traceback = False
    return error_lines


def _fingerprint(error_lines: list[str]) -> str:
    """对错误块生成去重指纹，取末尾 5 行计算 MD5"""
    key = "".join(error_lines[-5:])
    return hashlib.md5(key.encode()).hexdigest()


def _spawn_debug_agent(error_lines: list[str], context_lines: list[str]) -> None:
    """启动 Claude Code 子 Agent 自动 debug"""
    error_text = "".join(error_lines[-20:])
    context_text = "".join(context_lines[-80:])
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    prompt = f"""你是投研自动化系统（investment_research）的 debug agent，在 {timestamp} 被自动触发。

## 系统概述
项目路径：{PROJECT_ROOT}
功能：每日 07:30 自动拉取 A股/港股/美股公告 → AI 解析（DMXAPI gpt-5.4）→ 写入 Notion Events DB
主要文件：scheduler/jobs.py, data_ingestion/pdf_parser.py, notion_sync/sync_manager.py

## 检测到的错误
```
{error_text}
```

## 相关日志上下文（最近 80 行）
```
{context_text}
```

## 任务
1. 分析错误的根本原因（不要猜测，读相关源文件）
2. 定位到具体代码位置（文件 + 行号）
3. 修复 bug，确保修改不影响其他功能
4. 用一句话描述你做了什么，写入 {PROJECT_ROOT}/monitor_fix.log（追加模式，带时间戳）

注意：
- 只修复本次错误，不做额外重构
- 如果错误是外部服务（网络/API）临时故障，在 monitor_fix.log 中记录原因，不修改代码
- 修复后不需要重启调度器
"""

    subprocess.Popen(
        [
            _CLAUDE_CMD,
            "--print",
            "--dangerously-skip-permissions",
            prompt,
        ],
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def run_monitor() -> None:
    """主监控循环（阻塞），通常在 daemon 线程中运行"""
    print(f"[monitor] 启动，监控 {LOG_PATH}，间隔 {POLL_INTERVAL}s")
    seen: dict[str, datetime] = {}  # fingerprint → 最近触发时间

    while True:
        try:
            if LOG_PATH.exists():
                lines = _read_tail(LOG_PATH, CONTEXT_LINES)
                error_lines = _extract_error_blocks(lines)

                if error_lines:
                    fp = _fingerprint(error_lines)
                    last = seen.get(fp)
                    now = datetime.now()

                    if last is None or (now - last).total_seconds() > COOLDOWN_SECONDS:
                        seen[fp] = now
                        print(f"[monitor] {now.strftime('%H:%M:%S')} 检测到新错误，启动 debug agent")
                        _spawn_debug_agent(error_lines, lines)
        except Exception as e:
            print(f"[monitor] 监控循环异常（不影响调度器）: {e}")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run_monitor()
