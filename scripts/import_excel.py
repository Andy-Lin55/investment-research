"""
scripts/import_excel.py
从 TIKR 导出的 Excel 文件批量导入标的到 Notion Universe DB

用法：
    python scripts/import_excel.py
    python main.py import

文件约定：
    AL.xlsx         → Status=Active  (23只重点覆盖标的)
    我的自选股.xlsx  → Status=Watch   (51只自选股池)
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pandas as pd

# 确保从项目根目录能 import
sys.path.insert(0, str(Path(__file__).parent.parent))

from notion_sync.sync_manager import create_universe_entry  # noqa: E402

AL_PATH    = Path.home() / "Downloads" / "AL.xlsx"
WATCH_PATH = Path.home() / "Downloads" / "我的自选股.xlsx"


# ── 辅助函数 ──────────────────────────────────────────────────

def infer_market(ticker: str) -> str:
    """根据 ticker 后缀推断市场：A股 / 港股 / 美股"""
    suffix = ticker.rsplit(".", 1)[-1].upper() if "." in ticker else ""
    if suffix in ("SH", "SZ"):
        return "A股"
    if suffix == "HK":
        return "港股"
    return "美股"


def infer_market_cap(val) -> str:
    """
    根据总市值推断规模档位（使用各市场本地货币）
    >5000亿 → 大盘；500-5000亿 → 中盘；<500亿 → 小盘
    """
    try:
        v = float(val)
        if v >= 100_000_000_000:   # ≥ 1000亿（原始币值）
            return "大盘"
        if v >= 10_000_000_000:    # 100亿 - 1000亿
            return "中盘"
        return "小盘"              # < 100亿
    except Exception:
        return ""


def safe_num(val) -> float | None:
    """把 Excel 单元格值转为 float，无效值（NaN/Inf/负数）返回 None"""
    try:
        v = float(val)
        if math.isnan(v) or math.isinf(v) or v <= 0:
            return None
        return round(v, 4)
    except Exception:
        return None


def load_file(path: Path, status: str) -> list[dict]:
    """读取 Excel 文件，返回标准化后的股票列表"""
    if not path.exists():
        print(f"⚠️  文件不存在，跳过: {path}")
        return []

    df = pd.read_excel(path, engine="calamine")
    df.columns = [str(c).strip() for c in df.columns]

    rows = []
    for _, r in df.iterrows():
        ticker = str(r.get("代码", "")).strip()
        name   = str(r.get("名称", "")).strip()
        if not ticker or not name or ticker == "nan":
            continue
        rows.append({
            "ticker":     ticker,
            "name":       name,
            "market":     infer_market(ticker),
            "market_cap": infer_market_cap(r.get("总市值1")),
            "pe_ttm":     safe_num(r.get("市盈率(TTM)")),
            "pb":         safe_num(r.get("市净率(LF)")),
            "roe":        safe_num(r.get("净资产收益率(TTM)")),
            "status":     status,
        })
    return rows


# ── 主流程 ────────────────────────────────────────────────────

def run() -> None:
    active_rows = load_file(AL_PATH, "Active")
    watch_rows  = load_file(WATCH_PATH, "Watch")

    # 去重：Active 优先，Watch 中已出现的 ticker 跳过
    active_tickers = {r["ticker"] for r in active_rows}
    deduped_watch  = [r for r in watch_rows if r["ticker"] not in active_tickers]

    all_rows = active_rows + deduped_watch
    dupe_count = len(watch_rows) - len(deduped_watch)

    print(f"\n📋 导入预览")
    print(f"   AL.xlsx (Active):    {len(active_rows):>3} 只")
    print(f"   我的自选股 (Watch):   {len(watch_rows):>3} 只  (其中 {dupe_count} 只与 Active 重复，跳过)")
    print(f"   合计待写入:          {len(all_rows):>3} 只\n")

    ok = fail = skip = 0
    for row in all_rows:
        result = create_universe_entry(**row)
        if result is None:
            fail += 1
            print(f"   ❌ 失败: {row['ticker']} {row['name']}")
        else:
            # _find_universe_page_id 返回已有 page_id 时属于幂等跳过
            skip += 1 if result and len(result) == 36 and result.count("-") == 4 else 0
            ok += 1 if result and len(result) == 36 and result.count("-") == 4 else 0

    # 重新统计（sync_manager 内部日志已含详情，这里只打印汇总）
    print(f"\n完成 ✅  失败: {fail} 条")
    if fail > 0:
        print("   请检查上方日志，确认 Notion Token 和 DB 字段是否正确配置")


if __name__ == "__main__":
    run()
