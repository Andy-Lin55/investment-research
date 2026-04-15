"""
data_ingestion/hkex_fetcher.py
港股公告抓取 — 通过 HKEX titleSearchServlet.do JSON 接口
支持年报/中报/业绩公告/全部类型，stockId 从 HKEX activestock 列表动态查询
"""
from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path
from typing import Optional

import requests
from loguru import logger

from config.settings import TMP_ANNOUNCEMENTS, REQUEST_INTERVAL_SECONDS

_SEARCH_API = "https://www1.hkexnews.hk/search/titleSearchServlet.do"
_STOCK_LIST_URL = "https://www1.hkexnews.hk/ncms/script/eds/activestock_sehk_e.json"
_DOC_BASE = "https://www1.hkexnews.hk"

# stockId 索引缓存（code → index）
_STOCK_IDX_CACHE: dict[str, int] = {}

# t2code → (t1code, label)
_T2_META: dict[int, tuple[int, str]] = {
    40100: (40000, "年度报告"),    # Annual Report
    40200: (40000, "中期报告"),    # Interim/Half-Year Report
    40300: (40000, "季度报告"),    # Quarterly Report
    13300: (10000, "业绩公告"),    # Final Results
    13400: (10000, "业绩公告"),    # Interim Results
}

_DOC_TYPE_T2_MAP: dict[str, list[int]] = {
    "annual":    [40100],
    "interim":   [40200, 13400],
    "quarterly": [40300],
    "earnings":  [13300, 13400],
    "all":       [40100, 40200, 13300, 13400],
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}


def _get_stock_index(stock_code_5: str) -> Optional[int]:
    """
    返回 HKEX titleSearchServlet 要求的内部 stockId 索引。
    stock_code_5: "00700" (5位补零格式)
    """
    global _STOCK_IDX_CACHE
    if not _STOCK_IDX_CACHE:
        try:
            resp = requests.get(_STOCK_LIST_URL, headers=_HEADERS, timeout=15)
            resp.raise_for_status()
            for entry in resp.json():
                code = str(entry.get("c", "")).zfill(5)
                idx = entry.get("i")
                if code and idx is not None:
                    _STOCK_IDX_CACHE[code] = int(idx)
        except Exception as e:
            logger.warning(f"HKEX activestock 列表获取失败: {e}")
    return _STOCK_IDX_CACHE.get(stock_code_5)


def _parse_hk_ticker(ticker: str) -> Optional[str]:
    """
    "0700.HK" / "700.HK" → "00700"（5 位补零格式）
    """
    code = ticker.split(".")[0]
    return code.zfill(5) if code.isdigit() else None


def _parse_datetime(dt_str: str) -> str:
    """
    "23/03/2026 17:43" → "2026-03-23"
    """
    s = dt_str.strip()
    if len(s) >= 10 and s[2] == "/" and s[5] == "/":
        # DD/MM/YYYY HH:MM
        return f"{s[6:10]}-{s[3:5]}-{s[0:2]}"
    return s[:10]


def _download_pdf(pdf_url: str, save_path: Path) -> bool:
    if save_path.exists():
        logger.debug(f"PDF 已存在，跳过: {save_path.name}")
        return True
    try:
        resp = requests.get(pdf_url, headers=_HEADERS, timeout=30)
        resp.raise_for_status()
        save_path.write_bytes(resp.content)
        logger.info(f"PDF 下载成功: {save_path.name}")
        return True
    except Exception as e:
        logger.error(f"PDF 下载失败 [{pdf_url}]: {e}")
        return False


def fetch_hk_filings(
    ticker: str,
    doc_type: str = "all",
    max_items: int = 10,
    since_date: str = "2023-01-01",
    download_pdf: bool = False,
    **_kwargs,  # 兼容旧调用方的 max_pages 等参数
) -> list[dict]:
    """
    通过 HKEX titleSearchServlet.do 查询港股文件。

    ticker: "0700.HK" 格式
    doc_type: "annual" / "interim" / "quarterly" / "earnings" / "all"
    返回：[{ticker, title, doc_type, fiscal_period, date, url, pdf_path}, ...]
    """
    if not ticker.upper().endswith(".HK"):
        logger.debug(f"[{ticker}] 非港股，跳过 HKEX 抓取")
        return []

    stock_code_5 = _parse_hk_ticker(ticker)
    if not stock_code_5:
        logger.error(f"[{ticker}] 无法解析股票代码")
        return []

    time.sleep(REQUEST_INTERVAL_SECONDS)
    stock_id = _get_stock_index(stock_code_5)
    if stock_id is None:
        logger.error(f"[{ticker}] 未在 HKEX 活跃股票列表中找到 {stock_code_5}")
        return []

    t2codes = _DOC_TYPE_T2_MAP.get(doc_type, _DOC_TYPE_T2_MAP["all"])
    logger.info(f"抓取港股文件 [{ticker}] doc_type={doc_type} stockId={stock_id}")

    raw: list[dict] = []

    for t2code in t2codes:
        t1code, label = _T2_META.get(t2code, (-2, "公告"))
        time.sleep(REQUEST_INTERVAL_SECONDS)
        params = {
            "lang": "en",
            "market": "SEHK",
            "stockId": stock_id,
            "category": 0,
            "documentType": -1,
            "fromDate": since_date.replace("-", ""),
            "toDate": date.today().strftime("%Y%m%d"),
            "title": "",
            "searchType": 0,
            "t1code": t1code,
            "t2Gcode": -2,
            "t2code": t2code,
            "rowRange": max_items,
            "sortByOptions": "DateTime",
            "sortDir": 0,
        }
        try:
            resp = requests.get(_SEARCH_API, params=params, headers=_HEADERS, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            items = json.loads(data.get("result", "[]"))
        except Exception as e:
            logger.warning(f"HKEX titleSearchServlet 请求失败 [{ticker}] t2code={t2code}: {e}")
            continue

        for item in items:
            file_link = item.get("FILE_LINK", "")
            doc_url = (_DOC_BASE + file_link) if file_link else ""
            doc_date = _parse_datetime(item.get("DATE_TIME", ""))

            pdf_path = None
            if download_pdf and doc_url.lower().endswith(".pdf"):
                title_safe = item.get("TITLE", "")[:30].replace("/", "_").replace(" ", "_")
                filename = f"{ticker}_{doc_date}_{title_safe}.pdf"
                save_path = TMP_ANNOUNCEMENTS / filename
                if _download_pdf(doc_url, save_path):
                    pdf_path = str(save_path)

            raw.append({
                "ticker": ticker,
                "title": item.get("TITLE", ""),
                "doc_type": label,
                "fiscal_period": "",
                "date": doc_date,
                "url": doc_url,
                "pdf_path": pdf_path,
            })

    # 按日期排序 + 去重 + 截断
    seen: set[tuple] = set()
    results: list[dict] = []
    for r in sorted(raw, key=lambda x: x["date"], reverse=True):
        key = (r["title"], r["date"])
        if key not in seen:
            seen.add(key)
            results.append(r)
        if len(results) >= max_items:
            break

    logger.info(f"[{ticker}] 找到 {len(results)} 条港股文件")
    return results


def fetch_hk_annual_report(ticker: str, max_items: int = 3) -> list[dict]:
    return fetch_hk_filings(ticker, doc_type="annual", max_items=max_items)


def fetch_hk_interim_report(ticker: str, max_items: int = 3) -> list[dict]:
    return fetch_hk_filings(ticker, doc_type="interim", max_items=max_items)


def fetch_hk_results(ticker: str, max_items: int = 5) -> list[dict]:
    return fetch_hk_filings(ticker, doc_type="earnings", max_items=max_items)


def fetch_hk_news(ticker: str, max_items: int = 5) -> list[dict]:
    return fetch_hk_filings(ticker, doc_type="all", max_items=max_items)
