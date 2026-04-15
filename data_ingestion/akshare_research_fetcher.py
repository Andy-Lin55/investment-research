"""
data_ingestion/akshare_research_fetcher.py
AKShare 东方财富 — A 股卖方研报摘要/评级获取
仅适用于 A 股（.SH/.SZ/.BJ 后缀）
"""
from __future__ import annotations

import re
from typing import Optional

import akshare as ak
import pandas as pd
from loguru import logger


def _extract_pe_forecast(row: pd.Series) -> str:
    """
    从 DataFrame 行中提取盈利预测市盈率字段，汇总为可读字符串。
    例：2026-盈利预测-市盈率=7.02 + 2027-盈利预测-市盈率=6.48
        → "2026: 7.02x / 2027: 6.48x"
    """
    parts = []
    # 收集所有形如 "YYYY-盈利预测-市盈率" 的列
    pe_cols = sorted(
        [c for c in row.index if re.match(r"\d{4}-盈利预测-市盈率", c)]
    )
    for col in pe_cols:
        val = row.get(col)
        if pd.notna(val) and val not in ("", "-", "--"):
            year = col[:4]
            try:
                parts.append(f"{year}: {float(val):.2f}x")
            except (TypeError, ValueError):
                parts.append(f"{year}: {val}")
    return " / ".join(parts) if parts else ""


def get_a_share_research(ticker: str, limit: int = 5) -> list[dict]:
    """
    获取 A 股卖方研报列表（最新 limit 条）
    ticker: "601318.SH" → internally use symbol="601318"
    Returns: [
        {
            broker: str,        # 机构名
            rating: str,        # 东财评级 (买入/增持/中性/...)
            report_name: str,   # 报告名称
            pdf_url: str,       # PDF 直链
            date: str,          # YYYY-MM-DD
            pe_forecast: str,   # "2026: 7.02x / 2027: 6.48x" 格式汇总
        },
        ...
    ]
    """
    # 1. Extract 6-digit symbol (601318 from 601318.SH)
    symbol_6 = ticker.split(".")[0]
    if not symbol_6.isdigit() or len(symbol_6) != 6:
        logger.warning(f"akshare_research_fetcher: 非A股或格式错误 [{ticker}]，跳过")
        return []

    try:
        # 2. Call AKShare
        df = ak.stock_research_report_em(symbol=symbol_6)
        if df is None or df.empty:
            logger.warning(f"AKShare 研报 无数据 [{ticker}]")
            return []

        # 3. Sort by 日期 descending, take top limit
        if "日期" in df.columns:
            df = df.sort_values("日期", ascending=False)
        df = df.head(limit).reset_index(drop=True)

        # 4. Map fields
        result = []
        for _, row in df.iterrows():
            # Date normalisation — already YYYY-MM-DD as string, but ensure str
            raw_date = row.get("日期", "")
            date_str = str(raw_date)[:10] if raw_date else ""

            entry = {
                "broker":      str(row.get("机构", "") or ""),
                "rating":      str(row.get("东财评级", "") or ""),
                "report_name": str(row.get("报告名称", "") or ""),
                "pdf_url":     str(row.get("报告PDF链接", "") or ""),
                "date":        date_str,
                "pe_forecast": _extract_pe_forecast(row),
            }
            result.append(entry)

        logger.info(f"AKShare 研报 [{ticker}] → 获取 {len(result)} 条")
        return result

    except Exception as e:
        logger.warning(f"AKShare 研报获取失败 [{ticker}]: {e}")
        return []
