"""
data_ingestion/cninfo_fetcher.py
A股公告抓取 — 通过巨潮资讯 /new/hisAnnouncement/query 接口
（Tushare anns_d 需较高积分，改回 CNINFO 直连）
"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
from loguru import logger

from config.settings import (
    ANNOUNCEMENT_KEYWORDS,
    PERIODIC_REPORT_KEYWORDS,
    TMP_ANNOUNCEMENTS,
    REQUEST_INTERVAL_SECONDS,
)

_CNINFO_QUERY = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
_CNINFO_PDF_BASE = "http://static.cninfo.com.cn/"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Referer": "http://www.cninfo.com.cn/",
    "X-Requested-With": "XMLHttpRequest",
}


def _is_a_share(ticker: str) -> bool:
    """判断是否为A股（沪深北交所）"""
    return ticker.endswith(".SH") or ticker.endswith(".SZ") or ticker.endswith(".BJ")


def _parse_stock_code(ticker: str) -> str:
    """601318.SH → 601318"""
    return ticker.split(".")[0]


def _ts_to_date(ts_ms) -> str:
    """毫秒时间戳 → YYYY-MM-DD"""
    try:
        return datetime.fromtimestamp(int(ts_ms) / 1000).strftime("%Y-%m-%d")
    except Exception:
        return ""


def _download_pdf(pdf_url: str, save_path: Path) -> bool:
    if save_path.exists():
        logger.debug(f"PDF 已存在，跳过下载: {save_path.name}")
        return True
    for attempt in range(3):
        try:
            resp = requests.get(pdf_url, headers={"User-Agent": _HEADERS["User-Agent"]}, timeout=30)
            resp.raise_for_status()
            save_path.write_bytes(resp.content)
            logger.info(f"PDF 下载成功: {save_path.name}")
            return True
        except Exception as e:
            if attempt < 2:
                logger.warning(f"PDF 下载失败（第{attempt + 1}次），5s 后重试 [{pdf_url}]: {e}")
                time.sleep(5)
            else:
                logger.error(f"PDF 下载失败（已重试3次），放弃 [{pdf_url}]: {e}")
    return False


def fetch_announcements(
    ticker: str,
    days_back: int = 365,
    download_pdf: bool = True,
    keywords: list[str] | None = None,
) -> list[dict]:
    """
    从巨潮资讯拉取指定 A股股票公告。
    非A股 ticker 直接返回空列表。
    keywords: 关键词列表（各 keyword 单独搜索后合并去重），None 则用 ANNOUNCEMENT_KEYWORDS。

    返回：[{ticker, title, date, pdf_path, url}, ...]
    """
    if not _is_a_share(ticker):
        logger.debug(f"[{ticker}] 非A股，跳过公告抓取")
        return []

    kws = keywords if keywords is not None else ANNOUNCEMENT_KEYWORDS
    code = _parse_stock_code(ticker)

    # 日期范围
    from datetime import date, timedelta
    end_date = date.today().strftime("%Y-%m-%d")
    start_date = (date.today() - timedelta(days=days_back)).strftime("%Y-%m-%d")

    seen_ids: set[str] = set()
    results: list[dict] = []

    for kw in kws:
        logger.info(f"抓取公告 [{ticker}] 关键词={kw}")
        time.sleep(REQUEST_INTERVAL_SECONDS)

        try:
            resp = requests.post(
                _CNINFO_QUERY,
                headers=_HEADERS,
                data={
                    "pageNum": 1,
                    "pageSize": 20,
                    "tabName": "fulltext",
                    "searchkey": f"{code} {kw}",
                    "seDate": f"{start_date}~{end_date}",
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error(f"CNINFO 请求失败 [{ticker}] kw={kw}: {e}")
            continue

        for ann in (data.get("announcements") or []):
            ann_id = str(ann.get("announcementId", ""))
            sec_code = str(ann.get("secCode", ""))
            # 精确匹配股票代码（防止子公司公告混入）
            if sec_code != code or ann_id in seen_ids:
                continue
            seen_ids.add(ann_id)

            title = ann.get("announcementTitle", "")
            ann_date = _ts_to_date(ann.get("announcementTime", 0))
            adjunct_url = ann.get("adjunctUrl", "")
            pdf_url = (_CNINFO_PDF_BASE + adjunct_url) if adjunct_url else ""

            pdf_path = None
            if download_pdf and pdf_url:
                filename = f"{ticker}_{ann_date}_{title[:30].replace('/', '_')}.pdf"
                save_path = TMP_ANNOUNCEMENTS / filename
                if _download_pdf(pdf_url, save_path):
                    pdf_path = str(save_path)

            results.append({
                "ticker": ticker,
                "title": title,
                "date": ann_date,
                "pdf_path": pdf_path,
                "url": pdf_url,
            })

    logger.info(f"[{ticker}] 共抓取到 {len(results)} 条公告")
    return results


def fetch_periodic_reports(ticker: str, days_back: int = 365) -> list[dict]:
    """专门获取年报/季报/业绩会材料/新闻稿（使用 PERIODIC_REPORT_KEYWORDS）"""
    return fetch_announcements(ticker, days_back=days_back, keywords=PERIODIC_REPORT_KEYWORDS)


def batch_fetch(tickers: list[str], days_back: int = 3) -> dict[str, list[dict]]:
    """批量抓取多只股票公告（并行5线程，自动跳过非A股）"""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    result: dict[str, list[dict]] = {}

    def _fetch_one(ticker: str) -> tuple[str, list[dict]]:
        try:
            return ticker, fetch_announcements(ticker, days_back=days_back)
        except Exception as e:
            logger.error(f"批量抓取失败 [{ticker}]: {e}")
            return ticker, []

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(_fetch_one, t): t for t in tickers}
        for future in as_completed(futures):
            ticker, ann_list = future.result()
            result[ticker] = ann_list

    return result
