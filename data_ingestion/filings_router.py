"""
data_ingestion/filings_router.py
三市场公开文件路由 — 根据 ticker 后缀自动分发到对应数据源
"""
from __future__ import annotations

from loguru import logger

from data_ingestion.cninfo_fetcher import fetch_periodic_reports
from data_ingestion.hkex_fetcher import (
    fetch_hk_annual_report,
    fetch_hk_filings,
    fetch_hk_interim_report,
    fetch_hk_results,
)
from data_ingestion.sec_fetcher import (
    fetch_annual_report,
    fetch_earnings_releases,
    fetch_quarterly_reports,
    fetch_sec_filings,
)


def _infer_market(ticker: str) -> str:
    """根据 ticker 后缀推断市场"""
    t = ticker.upper()
    if t.endswith(".SH") or t.endswith(".SZ") or t.endswith(".BJ"):
        return "A"
    if t.endswith(".HK"):
        return "HK"
    return "US"   # .O / .N / .A 等


def fetch_filings(
    ticker: str,
    doc_type: str = "all",
    max_items: int = 5,
) -> list[dict]:
    """
    统一入口：根据 ticker 路由到对应市场数据源。

    doc_type:
        "annual"    — 年报 (A: 年度报告, HK: 年报, US: 10-K/20-F)
        "interim"   — 中期报告 (A: 半年度报告, HK: 中报, US: 10-Q/6-K)
        "earnings"  — 业绩公告/新闻稿 (A: 业绩说明会+新闻稿, HK: 业绩公告+新闻稿, US: 8-K/6-K)
        "quarterly" — 季报 (A: 季度报告, HK: 同 interim, US: 10-Q/6-K)
        "all"       — 全部以上类型

    返回统一格式：[{ticker, title, doc_type, fiscal_period, date, url, pdf_path, form_type?}, ...]
    """
    market = _infer_market(ticker)
    logger.info(f"filings_router: [{ticker}] market={market} doc_type={doc_type}")

    results: list[dict] = []

    if market == "A":
        items = fetch_periodic_reports(ticker)
        for item in items:
            results.append({
                "ticker": item["ticker"],
                "title": item["title"],
                "doc_type": _classify_a_share(item["title"]),
                "fiscal_period": "",
                "date": item["date"],
                "url": item.get("url", ""),
                "pdf_path": item.get("pdf_path"),
                "form_type": "",
            })

    elif market == "HK":
        # "all" 用一次 fetch_hk_filings 扫全类型，避免重复扫描 NCMS 分页
        if doc_type == "all":
            for item in fetch_hk_filings(ticker, doc_type="all", max_items=max_items):
                results.append(_normalize_hk(item))
        else:
            for item in fetch_hk_filings(ticker, doc_type=doc_type, max_items=max_items):
                results.append(_normalize_hk(item))

    else:  # US
        if doc_type in ("annual", "all"):
            for item in fetch_annual_report(ticker, max_items=max_items):
                results.append(_normalize_us(item))
        if doc_type in ("quarterly", "interim", "all"):
            for item in fetch_quarterly_reports(ticker, max_items=max_items):
                results.append(_normalize_us(item))
        if doc_type in ("earnings", "all"):
            for item in fetch_earnings_releases(ticker, max_items=max_items):
                results.append(_normalize_us(item))

    logger.info(f"[{ticker}] filings_router 共返回 {len(results)} 条")
    return results


def _classify_a_share(title: str) -> str:
    if "年度" in title:
        return "年度报告"
    if "半年" in title:
        return "中期报告"
    if "季度" in title or "季报" in title:
        return "季度报告"
    if "说明会" in title or "发布会" in title or "投资者" in title:
        return "业绩会材料"
    if "新闻" in title or "媒体" in title:
        return "新闻稿"
    return "公告"


def _normalize_hk(item: dict) -> dict:
    return {
        "ticker": item["ticker"],
        "title": item["title"],
        "doc_type": item.get("doc_type", "公告"),
        "fiscal_period": item.get("fiscal_period", ""),
        "date": item["date"],
        "url": item.get("url", ""),
        "pdf_path": item.get("pdf_path"),
        "form_type": "",
    }


def _normalize_us(item: dict) -> dict:
    form = item.get("form_type", "")
    doc_type_map = {
        "20-F": "年度报告", "10-K": "年度报告",
        "10-Q": "季度报告", "6-K": "新闻稿",
        "8-K": "8-K",
    }
    return {
        "ticker": item["ticker"],
        "title": item["title"],
        "doc_type": doc_type_map.get(form, form or "公告"),
        "fiscal_period": item.get("fiscal_period", ""),
        "date": item["date"],
        "url": item.get("url", ""),
        "pdf_path": item.get("pdf_path"),
        "form_type": form,
    }
