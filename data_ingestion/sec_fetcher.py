"""
data_ingestion/sec_fetcher.py
美股 SEC EDGAR Submissions API 公告抓取
支持 20-F / 10-K / 10-Q / 6-K / 8-K 等 filing 类型
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Optional

import requests
from loguru import logger

from config.settings import TMP_ANNOUNCEMENTS, REQUEST_INTERVAL_SECONDS, MAX_RETRIES, SEC_USER_AGENT

_EDGAR_BASE = "https://data.sec.gov"
_CIK_CACHE_PATH = Path("/tmp/sec_cik_cache.json")

_HEADERS = {
    "User-Agent": SEC_USER_AGENT,
    "Accept-Encoding": "gzip, deflate",
}

# 中国 ADR 通常用 20-F / 6-K；美国本土用 10-K / 10-Q / 8-K
_ADR_TICKERS_HINT = {".O", ".N", ".A"}   # NASDAQ / NYSE / AMEX
_ANNUAL_FORMS_ADR = ["20-F"]
_ANNUAL_FORMS_US = ["10-K"]
_EARNINGS_FORMS_ADR = ["6-K"]
_EARNINGS_FORMS_US = ["8-K"]
_QUARTERLY_FORMS_US = ["10-Q"]


# ── CIK 查询 ──────────────────────────────────────────────────────────────────

def _load_cik_cache() -> dict:
    if _CIK_CACHE_PATH.exists():
        try:
            return json.loads(_CIK_CACHE_PATH.read_text())
        except Exception:
            pass
    return {}


def _save_cik_cache(cache: dict) -> None:
    try:
        _CIK_CACHE_PATH.write_text(json.dumps(cache))
    except Exception as e:
        logger.warning(f"CIK 缓存写入失败: {e}")


def get_cik(ticker: str) -> Optional[str]:
    """
    通过 ticker 查询 SEC CIK（10位补零），结果缓存到 /tmp/sec_cik_cache.json。
    先查公司 tickers 映射文件（最可靠），失败则 fallback 到 EDGAR 搜索。
    """
    cache = _load_cik_cache()

    # 去掉交易所后缀（.O/.N/.A），保留股票代码（BRK.B 保持不变，QFIN.O → QFIN）
    _EXCHANGE_SFXS = {".O", ".N", ".A", ".NQ", ".NY"}
    raw = ticker.upper()
    for sfx in _EXCHANGE_SFXS:
        if raw.endswith(sfx):
            raw = raw[: -len(sfx)]
            break
    key = raw  # "QFIN" / "BRK.B" / "SPG"

    if key in cache:
        return cache[key]

    # 额外尝试的变体：BRK.B → BRK-B（SEC 用连字符表示股票类别）
    key_variant = key.replace(".", "-") if "." in key else None

    # 方法1：company_tickers.json（官方 ticker→CIK 映射，约 1MB）
    try:
        time.sleep(REQUEST_INTERVAL_SECONDS)
        resp = requests.get(
            "https://www.sec.gov/files/company_tickers.json",
            headers=_HEADERS,
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        for entry in data.values():
            sec_ticker = entry.get("ticker", "").upper()
            if sec_ticker == key or (key_variant and sec_ticker == key_variant):
                cik = str(entry["cik_str"]).zfill(10)
                cache[key] = cik
                _save_cik_cache(cache)
                logger.info(f"CIK 查询成功 [{ticker}]: {cik}")
                return cik
    except Exception as e:
        logger.warning(f"company_tickers.json 查询失败: {e}")

    # 方法2：EDGAR browse-edgar fallback
    try:
        time.sleep(REQUEST_INTERVAL_SECONDS)
        resp = requests.get(
            "https://www.sec.gov/cgi-bin/browse-edgar",
            params={
                "company": "",
                "CIK": key,
                "type": "10-K",
                "dateb": "",
                "owner": "include",
                "count": 1,
                "action": "getcompany",
                "output": "atom",
            },
            headers=_HEADERS,
            timeout=15,
        )
        match = re.search(r"CIK=(\d+)", resp.url)
        if match:
            cik = match.group(1).zfill(10)
            if cik == "0000000000":   # SEC 对未知 ticker 返回 CIK=0，跳过
                raise ValueError(f"无效 CIK=0 [{ticker}]")
            cache[key] = cik
            _save_cik_cache(cache)
            logger.info(f"CIK fallback 查询成功 [{ticker}]: {cik}")
            return cik
    except Exception as e:
        logger.error(f"CIK fallback 查询失败 [{ticker}]: {e}")

    logger.error(f"无法查询到 CIK [{ticker}]")
    return None


# ── Exhibit 枚举 ──────────────────────────────────────────────────────────────

def _get_exhibit_pdfs(cik: str, accession_no: str) -> list[str]:
    """
    从 EDGAR filing index 枚举所有 EX-99.x PDF exhibit 的 URL。
    用于 8-K / 6-K filings 中附带的 investor presentation slides。
    """
    clean = accession_no.replace("-", "")
    index_url = (
        f"https://data.sec.gov/Archives/edgar/data/{int(cik)}/{clean}/index.json"
    )
    try:
        time.sleep(REQUEST_INTERVAL_SECONDS)
        resp = requests.get(index_url, headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        items = resp.json().get("directory", {}).get("item", [])
        base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{clean}/"
        return [
            base + it["name"]
            for it in items
            if it.get("type", "").startswith("EX-99")
            and it["name"].lower().endswith(".pdf")
        ]
    except Exception as e:
        logger.warning(f"EDGAR exhibit 枚举失败 [{accession_no}]: {e}")
        return []


# ── Submissions API ───────────────────────────────────────────────────────────

def fetch_sec_filings(
    ticker: str,
    form_types: list[str],
    max_items: int = 5,
    since_date: str = "2023-01-01",
) -> list[dict]:
    """
    通过 SEC EDGAR Submissions API 抓取指定 filing 类型。

    返回：[{ticker, title, form_type, fiscal_period, date, url, pdf_path}, ...]
    """
    cik = get_cik(ticker)
    if not cik:
        return []

    logger.info(f"抓取 SEC filings [{ticker}] forms={form_types}")
    time.sleep(REQUEST_INTERVAL_SECONDS)

    try:
        url = f"{_EDGAR_BASE}/submissions/CIK{cik}.json"
        resp = requests.get(url, headers=_HEADERS, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.error(f"Submissions API 请求失败 [{ticker}]: {e}")
        return []

    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    docs = recent.get("primaryDocument", [])
    accessions = recent.get("accessionNumber", [])
    periods = recent.get("reportDate", [])

    results = []
    for i, form in enumerate(forms):
        if form not in form_types:
            continue
        filing_date = dates[i] if i < len(dates) else ""
        if filing_date < since_date:
            continue

        accession = accessions[i] if i < len(accessions) else ""
        accession_clean = accession.replace("-", "")
        primary_doc = docs[i] if i < len(docs) else ""
        period = periods[i] if i < len(periods) else ""

        doc_url = (
            f"https://www.sec.gov/Archives/edgar/data/{int(cik)}"
            f"/{accession_clean}/{primary_doc}"
        ) if primary_doc else f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}"

        # 8-K / 6-K: 额外枚举 EX-99.x PDF exhibits（investor presentation slides）
        exhibit_urls: list[str] = []
        if form in ("8-K", "6-K") and accession:
            exhibit_urls = _get_exhibit_pdfs(cik, accession)

        results.append({
            "ticker": ticker,
            "title": f"{ticker} {form} {period or filing_date}",
            "form_type": form,
            "fiscal_period": period,
            "date": filing_date,
            "url": doc_url,
            "pdf_path": None,
            "exhibit_urls": exhibit_urls,
        })

        if len(results) >= max_items:
            break

    logger.info(f"[{ticker}] 找到 {len(results)} 条 {form_types} filings")
    return results


# ── 便捷函数 ──────────────────────────────────────────────────────────────────

def _detect_filing_type(cik: str) -> str:
    """
    通过 Submissions API 检测公司 filing 类型：
    - "foreign"：有 20-F / 6-K → 外国公司 / 中国 ADR
    - "domestic"：有 10-K / 10-Q → 美国本土公司
    结果缓存到 CIK 缓存，key 为 "type_{cik}"
    """
    cache = _load_cik_cache()
    cache_key = f"type_{cik}"
    if cache_key in cache:
        return cache[cache_key]

    try:
        resp = requests.get(
            f"{_EDGAR_BASE}/submissions/CIK{cik}.json",
            headers=_HEADERS, timeout=15,
        )
        forms = resp.json().get("filings", {}).get("recent", {}).get("form", [])
        has_20f = any(f in ("20-F", "6-K") for f in forms)
        result = "foreign" if has_20f else "domestic"
        cache[cache_key] = result
        _save_cik_cache(cache)
        return result
    except Exception:
        return "domestic"  # 保守默认


def fetch_annual_report(ticker: str, max_items: int = 3) -> list[dict]:
    """自动检测公司类型：外国公司→20-F，美国本土→10-K"""
    cik = get_cik(ticker)
    if cik and _detect_filing_type(cik) == "foreign":
        return fetch_sec_filings(ticker, ["20-F"], max_items=max_items)
    return fetch_sec_filings(ticker, ["10-K"], max_items=max_items)


def fetch_earnings_releases(ticker: str, max_items: int = 5) -> list[dict]:
    """自动检测公司类型：外国公司→6-K，美国本土→8-K"""
    cik = get_cik(ticker)
    if cik and _detect_filing_type(cik) == "foreign":
        return fetch_sec_filings(ticker, ["6-K"], max_items=max_items)
    return fetch_sec_filings(ticker, ["8-K"], max_items=max_items)


def fetch_quarterly_reports(ticker: str, max_items: int = 4) -> list[dict]:
    """季报：外国公司→6-K，美国本土→10-Q"""
    cik = get_cik(ticker)
    if cik and _detect_filing_type(cik) == "foreign":
        return fetch_sec_filings(ticker, ["6-K"], max_items=max_items)
    return fetch_sec_filings(ticker, ["10-Q"], max_items=max_items)


# ── 向后兼容 ──────────────────────────────────────────────────────────────────

def fetch_8k(ticker: str, max_items: int = 5) -> list[dict]:
    """保留旧接口（向后兼容）"""
    return fetch_sec_filings(ticker, ["8-K"], max_items=max_items)
