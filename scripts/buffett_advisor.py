#!/usr/bin/env python3
"""
巴菲特投资顾问 Agent
基于巴菲特1956-2025年81封股东信（35个核心投资概念）构建的交互式投资分析助手

用法：
  python scripts/buffett_advisor.py              # 纯对话模式
  python scripts/buffett_advisor.py 601318.SH    # 带股票数据启动

对话中可用命令：
  /ticker 601318.SH   — 加载某只股票的当前数据
  /clear              — 清空对话历史
  /exit               — 退出
"""
from __future__ import annotations

import os
import sys
import json
import textwrap
from pathlib import Path

# ── 路径设置 ─────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from openai import OpenAI
from config.settings import DMXAPI_KEY, DMXAPI_BASE_URL, DMXAPI_MODEL

# ── 知识库 ───────────────────────────────────────────────────
_PHILOSOPHY_PATH = _ROOT / "docs" / "buffett_philosophy.md"

_SYSTEM_PROMPT_TEMPLATE = """\
你是「巴菲特价值投资顾问」，深刻内化了沃伦·巴菲特1956-2025年81封股东信的核心投资哲学。

## 你的身份与行为准则
- 你用巴菲特的框架思考，而不是预测市场
- 你始终问：「如果市场关闭10年，我仍会持有吗？」
- 你不预测股价走势，不做短期判断
- 你会坦诚说「这超出了我的能力圈，我无法判断」
- 每个结论必须有数据支撑或逻辑推导，不讲废话
- 引用巴菲特原话时注明年份（如「1988年信」）
- 语言简洁直接，使用中文回答

## 六步投资检验框架（每次分析股票时贯穿始终）
1. **能力圈**：10年后的盈利能力是否可以合理估计？
2. **护城河**：竞争者测试——资金充裕的理性竞争者愿意进入吗？喜诗糖果标准？
3. **财务质量**：ROE连续>15%？一美元测试？摇椅测试？商业模式三检验？
4. **管理层**：所有者心态？资本配置记录？诚信信号？
5. **安全边际**：≥30%内在价值折扣？PE×PB<22.5？
6. **10年持有**：市场关闭10年，仍会持有吗？

## 巴菲特核心引语（分析时可引用）
- 「用合理价格买入优秀公司，远好过以便宜价格买入平庸公司。」（1989年信）
- 「时间是优秀企业的朋友，是平庸企业的敌人。」
- 「真正的护城河必须能抵御通货膨胀：公司必须有定价权。」
- 「我们衡量管理层的标准只有一条：资本配置的成绩单。」
- 「永久性本金损失——而非波动——才是真正的风险。」
- 「不确定的时候，答案就是不。」
- 「市场先生是你的仆人，不是你的向导。」（1987年信）
- 「我们从未对市场方向有任何想法——从来没有。」（1986年信）
- 「每$1留存收益，应为股东创造至少$1市值。」（1983年信）
- 「利润增长靠堆砌资本，连摇椅都能做到。」（1985年信）

## 完整知识库
{philosophy_content}
"""


def _load_philosophy() -> str:
    if not _PHILOSOPHY_PATH.exists():
        return "（知识库文件未找到，请先运行内容抓取）"
    return _PHILOSOPHY_PATH.read_text(encoding="utf-8")


def _load_ticker_context(ticker: str) -> str:
    """尝试从投资研究系统加载股票当前数据"""
    try:
        from notion_sync.sync_manager import get_universe_snapshot
        data = get_universe_snapshot(ticker)
        if not data:
            return f"未找到 {ticker} 的数据（请确认该股票在 Universe DB 中）"

        lines = [f"## {ticker} 当前数据快照"]
        for k, v in data.items():
            if v is not None:
                lines.append(f"- {k}: {v}")
        return "\n".join(lines)
    except Exception as e:
        return f"加载 {ticker} 数据失败: {e}"


def _print_wrapped(text: str, prefix: str = "", width: int = 80) -> None:
    """带前缀的自动换行打印"""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        p = prefix if i == 0 else " " * len(prefix)
        if len(line) <= width - len(p):
            print(p + line)
        else:
            wrapped = textwrap.fill(line, width=width - len(p),
                                    initial_indent=p,
                                    subsequent_indent=" " * len(p))
            print(wrapped)


def _banner() -> None:
    print("\n" + "═" * 60)
    print("  📚 巴菲特价值投资顾问")
    print("  基于81封股东信 · 35个核心投资概念")
    print("═" * 60)
    print("  命令: /ticker <代码>  /clear  /exit")
    print("  提示: 可直接提问，或输入股票代码获取分析")
    print("═" * 60 + "\n")


def main() -> None:
    _banner()

    philosophy = _load_philosophy()
    system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(philosophy_content=philosophy)

    client = OpenAI(api_key=DMXAPI_KEY, base_url=DMXAPI_BASE_URL)
    messages: list[dict] = []

    # 命令行参数：直接带 ticker 启动
    initial_ticker = sys.argv[1] if len(sys.argv) > 1 else None
    if initial_ticker:
        ctx = _load_ticker_context(initial_ticker)
        messages.append({"role": "user",
                         "content": f"请用巴菲特六步框架分析 {initial_ticker}：\n\n{ctx}"})
        print(f"⏳ 正在分析 {initial_ticker}...\n")

        resp = client.chat.completions.create(
            model=DMXAPI_MODEL,
            messages=[{"role": "system", "content": system_prompt}] + messages,
            timeout=120,
        )
        reply = resp.choices[0].message.content or ""
        messages.append({"role": "assistant", "content": reply})
        _print_wrapped(reply, prefix="顾问: ")
        print()

    # 主对话循环
    while True:
        try:
            user_input = input("你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break

        if not user_input:
            continue

        # ── 命令处理 ─────────────────────────────────────────
        if user_input.lower() in ("/exit", "/quit", "退出"):
            print("再见！")
            break

        if user_input.lower() in ("/clear", "清空"):
            messages = []
            print("✅ 对话历史已清空\n")
            continue

        if user_input.lower().startswith("/ticker "):
            ticker = user_input.split(maxsplit=1)[1].strip().upper()
            ctx = _load_ticker_context(ticker)
            user_input = f"请用巴菲特六步框架分析 {ticker}：\n\n{ctx}"
            print(f"⏳ 加载 {ticker} 数据并分析...\n")

        # ── AI 调用 ──────────────────────────────────────────
        messages.append({"role": "user", "content": user_input})

        try:
            resp = client.chat.completions.create(
                model=DMXAPI_MODEL,
                messages=[{"role": "system", "content": system_prompt}] + messages,
                timeout=120,
            )
            reply = resp.choices[0].message.content or "（无回复）"
        except Exception as e:
            reply = f"（调用失败: {e}）"

        messages.append({"role": "assistant", "content": reply})
        print()
        _print_wrapped(reply, prefix="顾问: ")
        print()


if __name__ == "__main__":
    main()
