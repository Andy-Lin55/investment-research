"""
data_ingestion/cninfo_fetcher.py
A股公告抓取 — 通过巨潮资讯 /new/hisAnnouncement/query 接口
（Tushare anns_d 需较高积分，改回 CNINFO 直连）
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import requests
from loguru import logger
from data_ingestion.disclosure_health import PaginationError, SourceHealth, beijing_date

from config.settings import (
    ANNOUNCEMENT_KEYWORDS,
    PERIODIC_REPORT_KEYWORDS,
    TMP_ANNOUNCEMENTS,
    REQUEST_INTERVAL_SECONDS,
)

_CNINFO_QUERY = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
_CNINFO_PDF_BASE = "http://static.cninfo.com.cn/"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Referer": "https://www.cninfo.com.cn/",
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
        return datetime.fromtimestamp(int(ts_ms) / 1000, ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
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


def _post_query_page(form: dict, record: dict) -> requests.Response:
    """Repeat a transient read of the same page without restarting pagination."""
    for attempt in range(1, 4):
        record["attempts"] = attempt
        try:
            response = requests.post(_CNINFO_QUERY, headers=_HEADERS, data=form, timeout=15)
            response.raise_for_status()
            return response
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as error:
            response = getattr(error, "response", None)
            status = response.status_code if response is not None else None
            if attempt == 3 or (isinstance(error, requests.HTTPError)
                                and status not in {429, 500, 502, 503, 504}):
                raise
            delay = max(REQUEST_INTERVAL_SECONDS, 2 ** attempt)
            retry_after = response.headers.get("Retry-After", "") if response is not None else ""
            if retry_after:
                # Avoid retrying earlier than requested or blocking the daily job indefinitely.
                if not retry_after.isdecimal() or int(retry_after) > 10:
                    raise
                delay = max(delay, int(retry_after))
            logger.warning(f"CNINFO 查询重试：第 {form['pageNum']} 页，"
                           f"{status or type(error).__name__}，等待 {delay}s")
            time.sleep(delay)


def fetch_announcements(
    ticker: str,
    days_back: int = 365,
    download_pdf: bool = True,
    keywords: list[str] | None = None,
    *,
    company_name: str | None = None,
    health: SourceHealth | None = None,
    strict: bool = False,
    as_of: date | None = None,
    max_pages: int = 100,
) -> list[dict]:
    """
    从巨潮资讯拉取指定 A股股票公告。
    非A股 ticker 直接返回空列表。
    keywords: 关键词列表（各 keyword 单独搜索后合并去重），None 则用 ANNOUNCEMENT_KEYWORDS。
    company_name: 调用方核实的公司名；与代码查询结果合并，补足部分索引。
    keywords=[""]: 按代码和公司名查询整个日期窗口的公告库存。
    health/strict: 可观测部分成功、来源失败或分页截断；strict 时显式抛错。

    返回：[{ticker, title, date, pdf_path, url}, ...]
    """
    health = health if health is not None else SourceHealth("CNINFO", ticker)
    if not _is_a_share(ticker):
        logger.debug(f"[{ticker}] 非A股，跳过公告抓取")
        health.finish(0)
        return []
    if max_pages < 1 or days_back < 0:
        raise ValueError("max_pages must be positive and days_back nonnegative")

    kws = keywords if keywords is not None else ANNOUNCEMENT_KEYWORDS
    code = _parse_stock_code(ticker)
    name = (company_name or "").strip()

    # 日期范围
    today = as_of or beijing_date()
    end_date = today.isoformat()
    start_date = (today - timedelta(days=days_back)).isoformat()

    def query(searchkey: str):
        fingerprints = set()
        identities = set()
        returned = 0
        for page in range(1, max_pages + 1):
            record = {"searchkey": searchkey, "page": page, "window": f"{start_date}~{end_date}"}
            health.requests.append(record)
            time.sleep(REQUEST_INTERVAL_SECONDS)
            response = _post_query_page(
                {"pageNum": page, "pageSize": 20, "tabName": "fulltext",
                 "searchkey": searchkey, "seDate": record["window"]}, record,
            )
            data = response.json()
            if not isinstance(data, dict) or "announcements" not in data:
                raise ValueError("CNINFO missing announcements")
            rows = data["announcements"] if data["announcements"] is not None else []
            if not isinstance(rows, list) or any(not isinstance(a, dict) for a in rows):
                raise ValueError("CNINFO invalid announcements")
            total = int(data.get("totalAnnouncement") or data.get("totalRecordNum") or 0)
            if not rows and (data.get("hasMore") or total > returned):
                raise PaginationError("CNINFO empty page before inventory end")
            fingerprint = tuple((str(a.get("announcementId")), a.get("secCode"), a.get("adjunctUrl")) for a in rows)
            if rows and fingerprint in fingerprints:
                raise PaginationError("CNINFO repeated page")
            fingerprints.add(fingerprint)
            identities.update(fingerprint)
            record.update(returned=len(rows), exact_hits=sum(str(a.get("secCode", "")) == code for a in rows), total=total)
            record["exact_ids_in_window"] = [str(a.get("announcementId") or a.get("adjunctUrl") or "") for a in rows
                                             if str(a.get("secCode", "")) == code
                                             and start_date <= _ts_to_date(a.get("announcementTime")) <= end_date]
            health.successful_requests += 1
            returned += len(rows)
            yield rows
            # totalpages is inconsistent on the live endpoint; hasMore + count take precedence.
            more = bool(data.get("hasMore")) or total > returned
            if "hasMore" not in data and not total:
                more = len(rows) == 20
            if not more:
                if total > len(identities):
                    raise PaginationError("CNINFO overlapping pages did not cover reported total")
                return
        raise PaginationError("CNINFO page budget exhausted")

    seen_ids: set[str] = set()
    results: list[dict] = []

    def add_announcement(ann: dict) -> None:
        ann_id = str(ann.get("announcementId") or ann.get("adjunctUrl") or "")
        if str(ann.get("secCode", "")) != code or ann_id in seen_ids:
            return
        if not ann_id:
            raise ValueError("CNINFO missing announcement identity")
        ann_date = _ts_to_date(ann.get("announcementTime"))
        if not ann_date:
            raise ValueError("CNINFO invalid publication date")
        if not start_date <= ann_date <= end_date:
            return
        title = ann.get("announcementTitle", "")
        adjunct_url = ann.get("adjunctUrl", "")
        if not adjunct_url:
            raise ValueError("CNINFO document URL missing")
        seen_ids.add(ann_id)
        pdf_url = (_CNINFO_PDF_BASE + adjunct_url) if adjunct_url else ""
        pdf_path = None
        if download_pdf and pdf_url:
            filename = f"{ticker}_{ann_date}_{title[:30].replace('/', '_')}.pdf"
            save_path = TMP_ANNOUNCEMENTS / filename
            if _download_pdf(pdf_url, save_path):
                pdf_path = str(save_path)
            else:
                health.fail(pdf_url, RuntimeError("PDF download failed"))
        results.append({"ticker": ticker, "title": title, "date": ann_date,
                        "pdf_path": pdf_path, "url": pdf_url})

    for kw in kws:
        logger.info(f"抓取公告 [{ticker}] 关键词={kw}")
        for search_name in dict.fromkeys([code] + ([name] if name and name != code else [])):
            searchkey = f"{search_name} {kw}".strip()
            try:
                for announcements in query(searchkey):
                    for ann in announcements:
                        try:
                            add_announcement(ann)
                        except Exception as e:
                            health.fail("announcement_row", e)
            except Exception as e:
                health.fail(searchkey, e)
                failure = health.errors[-1]
                status = f" HTTP {failure['http_status']}" if "http_status" in failure else ""
                logger.error(f"CNINFO 抓取不完整 [{ticker}] kw={kw}: {type(e).__name__}{status}")

    logger.info(f"[{ticker}] 共抓取到 {len(results)} 条公告")
    health.finish(len(results), strict)
    return results


def fetch_periodic_reports(
    ticker: str, days_back: int = 365, *, company_name: str | None = None,
    health: SourceHealth | None = None, strict: bool = False, as_of: date | None = None,
) -> list[dict]:
    """专门获取年报/季报/业绩会材料/新闻稿（使用 PERIODIC_REPORT_KEYWORDS）"""
    return fetch_announcements(
        ticker, days_back=days_back, keywords=PERIODIC_REPORT_KEYWORDS, company_name=company_name,
        health=health, strict=strict, as_of=as_of,
    )


def batch_fetch(
    tickers: list[str], days_back: int = 3, *, company_names: dict[str, str] | None = None,
    health: dict[str, SourceHealth] | None = None, strict: bool = False, as_of: date | None = None,
) -> dict[str, list[dict]]:
    """批量抓取多只股票公告（并行5线程，自动跳过非A股）"""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    result: dict[str, list[dict]] = {}
    statuses = {t: SourceHealth("CNINFO", t) for t in tickers}
    if health is not None:
        health.update(statuses)

    def _fetch_one(ticker: str) -> tuple[str, list[dict]]:
        try:
            return ticker, fetch_announcements(
                ticker, days_back=days_back, company_name=(company_names or {}).get(ticker),
                health=statuses[ticker], as_of=as_of,
            )
        except Exception as e:
            statuses[ticker].fail("batch", e)
            logger.error(f"批量抓取失败 [{ticker}]: {e}")
            return ticker, []

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(_fetch_one, t): t for t in tickers}
        for future in as_completed(futures):
            ticker, ann_list = future.result()
            result[ticker] = ann_list

    if strict:
        for ticker, status in statuses.items():
            status.finish(len(result.get(ticker, [])), strict=True)
    return result
