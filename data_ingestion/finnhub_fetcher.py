"""
data_ingestion/finnhub_fetcher.py
Finnhub 免费 API — US/HK 股票分析师共识、EPS 超预期、公司新闻
免费额度: 60 calls/min，无 Transcripts
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests
from loguru import logger

from config.settings import FINNHUB_KEY

_BASE_URL = "https://finnhub.io/api/v1"
_US_EXCHANGE_SUFFIXES = (".O", ".N", ".A", ".NQ", ".NY")


# ── 工具函数 ───────────────────────────────────────────────────

def _normalize_ticker(ticker: str) -> str:
    """Strip exchange suffixes: QFIN.O → QFIN"""
    for suffix in _US_EXCHANGE_SUFFIXES:
        if ticker.upper().endswith(suffix.upper()):
            return ticker[: -len(suffix)]
    return ticker


def _get(endpoint: str, params: dict) -> Optional[dict | list]:
    """GET request to Finnhub, returns parsed JSON or None on failure."""
    params["token"] = FINNHUB_KEY
    url = _BASE_URL + endpoint
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning(f"Finnhub request failed [{endpoint}]: {e}")
        return None


# ── 公开接口 ───────────────────────────────────────────────────

def get_analyst_consensus(ticker: str) -> Optional[dict]:
    """
    分析师评级共识（最新一期）
    ticker: "QFIN" or "QFIN.O" (suffix auto-stripped)
    Returns: {buy, hold, sell, strongBuy, strongSell, period} or None
    """
    symbol = _normalize_ticker(ticker)
    data = _get("/stock/recommendation", {"symbol": symbol})
    if not data or not isinstance(data, list):
        logger.warning(f"Finnhub analyst consensus 无数据 [{ticker}]")
        return None

    latest = data[0]
    return {
        "buy":        latest.get("buy"),
        "hold":       latest.get("hold"),
        "sell":       latest.get("sell"),
        "strongBuy":  latest.get("strongBuy"),
        "strongSell": latest.get("strongSell"),
        "period":     latest.get("period"),
    }


def get_earnings_surprises(ticker: str, limit: int = 4) -> list[dict]:
    """
    EPS 超预期历史
    Returns: [{actual, estimate, surprise, surprisePercent, period, year, quarter}, ...]
    """
    symbol = _normalize_ticker(ticker)
    data = _get("/stock/earnings", {"symbol": symbol})
    if not data or not isinstance(data, list):
        logger.warning(f"Finnhub earnings surprises 无数据 [{ticker}]")
        return []

    result = []
    for item in data[:limit]:
        result.append({
            "actual":          item.get("actual"),
            "estimate":        item.get("estimate"),
            "surprise":        item.get("surprise"),
            "surprisePercent": item.get("surprisePercent"),
            "period":          item.get("period"),
            "year":            item.get("year"),
            "quarter":         item.get("quarter"),
        })
    return result


def get_company_news(ticker: str, days: int = 7) -> list[dict]:
    """
    公司新闻（最近 days 天）
    Returns: [{headline, summary, datetime (ISO string), url, source}, ...]
    """
    symbol = _normalize_ticker(ticker)
    now = datetime.now(timezone.utc)
    date_from = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    date_to = now.strftime("%Y-%m-%d")

    data = _get("/company-news", {"symbol": symbol, "from": date_from, "to": date_to})
    if not data or not isinstance(data, list):
        logger.warning(f"Finnhub company news 无数据 [{ticker}]")
        return []

    result = []
    for item in data:
        ts = item.get("datetime")
        iso_dt = None
        if ts:
            try:
                iso_dt = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
            except Exception:
                iso_dt = str(ts)
        result.append({
            "headline": item.get("headline"),
            "summary":  item.get("summary"),
            "datetime": iso_dt,
            "url":      item.get("url"),
            "source":   item.get("source"),
        })
    return result


def get_basic_financials(ticker: str) -> dict:
    """
    基础财务（PE/PB/ROE 等，用于补充 AKShare 缺失的 US 数据）
    Returns: {pe_normalized, pb_quarterly, roe_rfy, revenue_growth_ttm_yoy}
    """
    symbol = _normalize_ticker(ticker)
    data = _get("/stock/metric", {"symbol": symbol, "metric": "all"})
    if not data or not isinstance(data, dict):
        logger.warning(f"Finnhub basic financials 无数据 [{ticker}]")
        return {}

    metric = data.get("metric", {})
    result = {}

    pe = metric.get("peNormalizedAnnual") or metric.get("peTTM")
    if pe is not None:
        result["pe_normalized"] = pe

    pb = metric.get("pbQuarterly") or metric.get("pbAnnual")
    if pb is not None:
        result["pb_quarterly"] = pb

    roe = metric.get("roeRfy") or metric.get("roeTTM")
    if roe is not None:
        result["roe_rfy"] = roe

    rev_growth = metric.get("revenueGrowthTTMYoy")
    if rev_growth is not None:
        result["revenue_growth_ttm_yoy"] = rev_growth

    logger.info(f"Finnhub basic financials [{ticker}] → {result}")
    return result
