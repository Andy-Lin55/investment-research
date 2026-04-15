"""
notion_sync/sync_manager.py
Notion 数据写入管理器，包含幂等检查
"""
from __future__ import annotations

import io
import json
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import requests
from loguru import logger
from notion_client import APIResponseError

from config.settings import (
    NOTION_UNIVERSE_DB_ID,
    NOTION_EVENTS_DB_ID,
    NOTION_RESEARCH_DB_ID,
    FEISHU_WEBHOOK,
)
from notion_sync.notion_client_wrapper import get_client
from notion_sync.db_schema import build_notion_property
from notion_sync.notion_client_wrapper import notion_request_with_retry


# ── 工具函数 ─────────────────────────────────────────────────

def _today_iso() -> str:
    return date.today().isoformat()


def _find_universe_page_id(client, ticker: str) -> Optional[str]:
    """在 Universe DB 中查找 ticker 对应的 page_id"""
    try:
        res = client.databases.query(
            database_id=NOTION_UNIVERSE_DB_ID,
            filter={
                "property": "Ticker",
                "rich_text": {"equals": ticker},
            },
        )
        results = res.get("results", [])
        if results:
            return results[0]["id"]
        return None
    except Exception as e:
        logger.error(f"查找 Universe page 失败 [{ticker}]: {e}")
        return None


def _idempotency_check(
    client,
    db_id: str,
    ticker: str,
    event_type: str,
    event_date: str,
    source_url: str | None = None,
) -> bool:
    """
    幂等检查：返回 True 表示已存在，跳过写入。
    优先级1：同一 source_url 已存在（无论 event_type），最可靠
    优先级2：同 ticker + event_type + date（回退检查）
    """
    # ── URL 级别去重（最可靠，防止同一文件以不同 event_type 重复写入）
    # 泛域名 URL（如 https://www.cninfo.com.cn）不足以唯一标识文件，跳过
    _GENERIC_URLS = {
        "https://www.cninfo.com.cn", "http://www.cninfo.com.cn",
        "https://www.hkexnews.hk", "http://www.hkexnews.hk",
        "https://www.sec.gov", "http://www.sec.gov",
    }
    if source_url and source_url.rstrip("/") in _GENERIC_URLS:
        source_url = ""   # 降级，不走 URL 检查

    if source_url:
        try:
            res = client.databases.query(
                database_id=db_id,
                filter={
                    "and": [
                        {"property": "Ticker", "rich_text": {"equals": ticker}},
                        {"property": "SourceURL", "url": {"equals": source_url}},
                    ]
                },
            )
            if res.get("results"):
                logger.info(f"URL 幂等跳过：{ticker} SourceURL 已存在")
                return True
        except Exception:
            pass  # URL 检查失败时降级到 type+date 检查

    # ── 回退：ticker + event_type + date
    try:
        res = client.databases.query(
            database_id=db_id,
            filter={
                "and": [
                    {"property": "Ticker", "rich_text": {"equals": ticker}},
                    {"property": "EventType", "select": {"equals": event_type}},
                    {"property": "EventDate", "date": {"equals": event_date}},
                ]
            },
        )
        return len(res.get("results", [])) > 0
    except Exception:
        return False


# ── 公共幂等检查（供外部在解析前调用）────────────────────────

def event_exists(ticker: str, event_type: str, event_date: str, source_url: str = "") -> bool:
    """在 PDF 解析前检查事件是否已存在，避免重复解析历史公告"""
    client = get_client()
    return _idempotency_check(client, NOTION_EVENTS_DB_ID, ticker, event_type, event_date, source_url)


# ── 核心写入函数 ─────────────────────────────────────────────

def get_active_tickers() -> list[str]:
    """从 Universe DB 获取所有 Status=Active 的股票列表（用于每日公告任务）"""
    client = get_client()
    tickers = []
    cursor = None
    try:
        while True:
            kwargs: dict = {
                "database_id": NOTION_UNIVERSE_DB_ID,
                "filter": {"property": "Status", "select": {"equals": "Active"}},
                "page_size": 100,
            }
            if cursor:
                kwargs["start_cursor"] = cursor
            res = notion_request_with_retry(client.databases.query, **kwargs)
            for page in res.get("results", []):
                props = page.get("properties", {})
                ticker_prop = props.get("Ticker", {}).get("rich_text", [])
                if ticker_prop:
                    tickers.append(ticker_prop[0]["plain_text"])
            if res.get("has_more"):
                cursor = res["next_cursor"]
            else:
                break
        logger.info(f"获取到 {len(tickers)} 只 Active 股票")
    except Exception as e:
        logger.error(f"获取 Active tickers 失败: {e}")
    return tickers


def get_daily_tickers() -> list[str]:
    """从 Universe DB 获取 Status=Active 或 Watch 的股票（每日公告/新闻 + Research Pipeline 用）"""
    client = get_client()
    tickers = []
    cursor = None
    try:
        while True:
            kwargs: dict = {
                "database_id": NOTION_UNIVERSE_DB_ID,
                "filter": {
                    "or": [
                        {"property": "Status", "select": {"equals": "Active"}},
                        {"property": "Status", "select": {"equals": "Watch"}},
                    ]
                },
                "page_size": 100,
            }
            if cursor:
                kwargs["start_cursor"] = cursor
            res = notion_request_with_retry(client.databases.query, **kwargs)
            for page in res.get("results", []):
                props = page.get("properties", {})
                ticker_prop = props.get("Ticker", {}).get("rich_text", [])
                if ticker_prop:
                    tickers.append(ticker_prop[0]["plain_text"])
            if res.get("has_more"):
                cursor = res["next_cursor"]
            else:
                break
        logger.info(f"获取到 {len(tickers)} 只股票（Active + Watch）")
    except Exception as e:
        logger.error(f"获取每日 tickers 失败: {e}")
    return tickers


def get_all_tickers() -> list[str]:
    """从 Universe DB 获取所有股票（Coverage + Active + Watch）用于周度财务快照"""
    client = get_client()
    tickers = []
    cursor = None
    try:
        while True:
            kwargs: dict = {
                "database_id": NOTION_UNIVERSE_DB_ID,
                "filter": {
                    "or": [
                        {"property": "Status", "select": {"equals": "Coverage"}},
                        {"property": "Status", "select": {"equals": "Active"}},
                        {"property": "Status", "select": {"equals": "Watch"}},
                    ]
                },
                "page_size": 100,
            }
            if cursor:
                kwargs["start_cursor"] = cursor
            res = notion_request_with_retry(client.databases.query, **kwargs)
            for page in res.get("results", []):
                props = page.get("properties", {})
                ticker_prop = props.get("Ticker", {}).get("rich_text", [])
                if ticker_prop:
                    tickers.append(ticker_prop[0]["plain_text"])
            if res.get("has_more"):
                cursor = res["next_cursor"]
            else:
                break
        logger.info(f"获取到 {len(tickers)} 只股票（Coverage + Active + Watch）")
    except Exception as e:
        logger.error(f"获取全量 tickers 失败: {e}")
    return tickers


def create_event(ticker: str, parsed_data: dict) -> Optional[str]:
    """
    在 Events DB 创建一条事件记录
    parsed_data 结构见 pdf_parser.py 的返回格式
    返回新建页面的 page_id，若跳过返回 None
    """
    client = get_client()
    event_date = parsed_data.get("event_date", _today_iso())
    event_type = parsed_data.get("event_type", "公告")

    # 幂等检查（先 URL 级别，再 type+date 级别）
    source_url = parsed_data.get("source_url") or ""
    if _idempotency_check(client, NOTION_EVENTS_DB_ID, ticker, event_type, event_date, source_url):
        logger.info(f"幂等跳过：{ticker} {event_type} {event_date} 已存在")
        return None

    properties: dict = {
        "Name": build_notion_property("Name", {"type": "title"},
                                      f"{ticker} {event_type} {event_date}"),
        "Ticker": build_notion_property("Ticker", {"type": "rich_text"}, ticker),
        "EventType": build_notion_property("EventType", {"type": "select"}, event_type),
        "Importance": build_notion_property("Importance", {"type": "select"},
                                            parsed_data.get("importance", "Medium")),
        "Signal": build_notion_property("Signal", {"type": "select"},
                                        parsed_data.get("investment_signal", "中性")),
        "EventDate": build_notion_property("EventDate", {"type": "date"}, event_date),
        "ProcessedAt": build_notion_property("ProcessedAt", {"type": "date"}, _today_iso()),
        "AI Summary": build_notion_property("AI Summary", {"type": "rich_text"},
                                           parsed_data.get("detailed_analysis", "")),
        "KeyNumbers": build_notion_property("KeyNumbers", {"type": "rich_text"},
                                            json.dumps(parsed_data.get("key_numbers", {}),
                                                       ensure_ascii=False)),
        "Status": build_notion_property("Status", {"type": "select"}, "New"),
    }

    if parsed_data.get("source_url"):
        properties["SourceURL"] = build_notion_property(
            "SourceURL", {"type": "url"}, parsed_data["source_url"]
        )
    if parsed_data.get("fiscal_period"):
        properties["FiscalPeriod"] = build_notion_property(
            "FiscalPeriod", {"type": "rich_text"}, parsed_data["fiscal_period"]
        )

    try:
        page = notion_request_with_retry(
            client.pages.create,
            parent={"database_id": NOTION_EVENTS_DB_ID},
            properties=properties,
        )
        page_id = page["id"]
        logger.info(f"✅ Events 写入成功: {ticker} {event_type} → {page_id}")

        # 高重要性事件触发飞书预警
        if parsed_data.get("importance") == "High":
            _send_feishu_alert(ticker, parsed_data, page_id)

        return page_id
    except APIResponseError as e:
        logger.error(f"Notion API 错误 create_event [{ticker}]: {e}")
        return None


def create_research(ticker: str, parsed_data: dict) -> Optional[str]:
    """在 Research DB 创建一条研报记录"""
    client = get_client()
    report_date = parsed_data.get("report_date", _today_iso())
    doc_type = parsed_data.get("doc_type", "卖方研报")

    universe_page_id = _find_universe_page_id(client, ticker)

    properties: dict = {
        "Name": build_notion_property("Name", {"type": "title"},
                                      f"{ticker} {doc_type} {report_date}"),
        "DocType": build_notion_property("DocType", {"type": "select"}, doc_type),
        "Broker": build_notion_property("Broker", {"type": "rich_text"},
                                        parsed_data.get("broker", "")),
        "Analyst": build_notion_property("Analyst", {"type": "rich_text"},
                                         parsed_data.get("analyst", "")),
        "CoreThesis": build_notion_property("CoreThesis", {"type": "rich_text"},
                                            parsed_data.get("core_thesis", "")),
        "Catalysts": build_notion_property("Catalysts", {"type": "rich_text"},
                                           parsed_data.get("catalysts", "")),
        "ReportDate": build_notion_property("ReportDate", {"type": "date"}, report_date),
        "ParsedAt": build_notion_property("ParsedAt", {"type": "date"}, _today_iso()),
    }

    if parsed_data.get("rating"):
        properties["Rating"] = build_notion_property(
            "Rating", {"type": "select"}, parsed_data["rating"]
        )
    if parsed_data.get("target_price") is not None:
        properties["TargetPrice"] = build_notion_property(
            "TargetPrice", {"type": "number"}, parsed_data["target_price"]
        )
    if parsed_data.get("upside") is not None:
        properties["Upside"] = build_notion_property(
            "Upside", {"type": "number"}, parsed_data["upside"]
        )
    # Research DB 的 Ticker 是 rich_text（Notion API 不支持跨 DB relation）
    properties["Ticker"] = build_notion_property(
        "Ticker", {"type": "rich_text"}, ticker
    )
    if parsed_data.get("risk_factors"):
        properties["RiskFactors"] = build_notion_property(
            "RiskFactors", {"type": "rich_text"}, parsed_data["risk_factors"]
        )
    if parsed_data.get("valuation_view") in ("低估", "合理", "高估"):
        properties["ValuationView"] = build_notion_property(
            "ValuationView", {"type": "select"}, parsed_data["valuation_view"]
        )
    if parsed_data.get("confidence") in ("High", "Medium", "Low"):
        properties["Confidence"] = build_notion_property(
            "Confidence", {"type": "select"}, parsed_data["confidence"]
        )
    if parsed_data.get("debate_summary"):
        properties["DebateSummary"] = build_notion_property(
            "DebateSummary", {"type": "rich_text"}, parsed_data["debate_summary"]
        )
    if parsed_data.get("trigger_event_id"):
        properties["TriggerEventID"] = build_notion_property(
            "TriggerEventID", {"type": "rich_text"}, parsed_data["trigger_event_id"]
        )
    if parsed_data.get("data_sources"):
        properties["DataSources"] = build_notion_property(
            "DataSources", {"type": "rich_text"}, parsed_data["data_sources"]
        )

    try:
        page = client.pages.create(
            parent={"database_id": NOTION_RESEARCH_DB_ID},
            properties=properties,
        )
        page_id = page["id"]
        logger.info(f"✅ Research 写入成功: {ticker} {doc_type} → {page_id}")
        return page_id
    except APIResponseError as e:
        logger.error(f"Notion API 错误 create_research [{ticker}]: {e}")
        return None


def get_universe_snapshot(ticker: str) -> dict:
    """
    返回 Universe DB 中该股的估值快照
    Returns: {pe_ttm, pb, roe, revenue_growth, net_profit_growth, latest_signal} or {}
    """
    client = get_client()
    try:
        res = client.databases.query(
            database_id=NOTION_UNIVERSE_DB_ID,
            filter={"property": "Ticker", "rich_text": {"equals": ticker}},
        )
        results = res.get("results", [])
        if not results:
            return {}
        props = results[0].get("properties", {})
        def _num(key):
            v = props.get(key, {}).get("number")
            return v
        def _sel(key):
            s = props.get(key, {}).get("select")
            return s["name"] if s else ""
        name_rt = props.get("Name", {}).get("title", [])
        company_name = name_rt[0]["plain_text"] if name_rt else ""
        return {
            "company_name":     company_name,
            "pe_ttm":           _num("PE_TTM"),
            "pb":               _num("PB"),
            "roe":              _num("ROE"),
            "revenue_growth":   _num("RevenueGrowth"),
            "net_profit_growth":_num("NetProfitGrowth"),
            "dividend_yield":   _num("DividendYield"),
            "latest_signal":    _sel("LatestSignal"),
        }
    except Exception as e:
        logger.error(f"get_universe_snapshot [{ticker}]: {e}")
        return {}


def get_recent_events(ticker: str, limit: int = 5) -> list[dict]:
    """
    返回 Events DB 最近 N 条事件（按 EventDate 降序）
    Returns: [{event_type, date, summary, signal, importance}, ...]
    """
    client = get_client()
    try:
        res = client.databases.query(
            database_id=NOTION_EVENTS_DB_ID,
            filter={"property": "Ticker", "rich_text": {"equals": ticker}},
            sorts=[{"property": "EventDate", "direction": "descending"}],
            page_size=limit,
        )
        out = []
        for page in res.get("results", []):
            props = page.get("properties", {})
            def _rt(key):
                rt = props.get(key, {}).get("rich_text", [])
                return rt[0]["plain_text"] if rt else ""
            def _sel(key):
                s = props.get(key, {}).get("select")
                return s["name"] if s else ""
            def _date(key):
                d = props.get(key, {}).get("date")
                return d["start"] if d else ""
            out.append({
                "event_type": _sel("EventType"),
                "date":        _date("EventDate"),
                "summary":     _rt("AI Summary"),
                "signal":      _sel("Signal"),
                "importance":  _sel("Importance"),
                "source_url":  props.get("SourceURL", {}).get("url") or "",
            })
        return out
    except Exception as e:
        logger.error(f"get_recent_events [{ticker}]: {e}")
        return []


def update_company_signal(ticker: str, signal: str) -> bool:
    """更新 Universe DB 中指定 ticker 的 LatestSignal"""
    client = get_client()
    page_id = _find_universe_page_id(client, ticker)
    if not page_id:
        logger.warning(f"Universe 中未找到 {ticker}，无法更新 signal")
        return False
    try:
        client.pages.update(
            page_id=page_id,
            properties={
                "LatestSignal": build_notion_property(
                    "LatestSignal", {"type": "select"}, signal
                ),
                "LastUpdated": build_notion_property(
                    "LastUpdated", {"type": "date"}, _today_iso()
                ),
            },
        )
        logger.info(f"✅ {ticker} LatestSignal 更新为 {signal}")
        return True
    except APIResponseError as e:
        logger.error(f"更新 signal 失败 [{ticker}]: {e}")
        return False


# ── Universe 名单管理 ──────────────────────────────────────────

def create_universe_entry(
    ticker: str,
    name: str,
    market: str,
    market_cap: str = "",
    status: str = "Active",
    pe_ttm=None,
    pb=None,
    roe=None,
) -> Optional[str]:
    """
    在 Universe DB 创建一条标的记录（幂等：已存在则跳过）
    返回 page_id；已存在时返回已有 page_id；失败返回 None
    """
    client = get_client()
    existing = _find_universe_page_id(client, ticker)
    if existing:
        logger.info(f"幂等跳过：{ticker} 已在 Universe DB")
        return existing

    properties: dict = {
        "Name":        build_notion_property("Name",        {"type": "title"},     name),
        "Ticker":      build_notion_property("Ticker",      {"type": "rich_text"}, ticker),
        "Market":      build_notion_property("Market",      {"type": "select"},    market),
        "Status":      build_notion_property("Status",      {"type": "select"},    status),
        "LastUpdated": build_notion_property("LastUpdated", {"type": "date"},      _today_iso()),
    }
    if market_cap:
        properties["MarketCap"] = build_notion_property("MarketCap", {"type": "select"}, market_cap)
    for field, val in [("PE_TTM", pe_ttm), ("PB", pb), ("ROE", roe)]:
        if val is not None and val == val:   # 排除 NaN
            properties[field] = build_notion_property(field, {"type": "number"}, val)

    try:
        page = client.pages.create(
            parent={"database_id": NOTION_UNIVERSE_DB_ID},
            properties=properties,
        )
        page_id = page["id"]
        logger.info(f"✅ Universe 写入: {ticker} {name} [{status}]")
        return page_id
    except APIResponseError as e:
        logger.error(f"Universe 写入失败 [{ticker}]: {e}")
        return None


def set_universe_status(ticker: str, status: str) -> bool:
    """
    软删除 / 状态切换：把 Status 改为 Active / Watch / Archived
    推荐日常用于管理覆盖名单，条目保留在 DB 中。
    """
    client = get_client()
    page_id = _find_universe_page_id(client, ticker)
    if not page_id:
        logger.warning(f"set_universe_status: 未找到 {ticker}")
        return False
    try:
        client.pages.update(
            page_id=page_id,
            properties={
                "Status":      build_notion_property("Status",      {"type": "select"}, status),
                "LastUpdated": build_notion_property("LastUpdated", {"type": "date"},   _today_iso()),
            },
        )
        logger.info(f"✅ {ticker} Status → {status}")
        return True
    except APIResponseError as e:
        logger.error(f"set_universe_status 失败 [{ticker}]: {e}")
        return False


def archive_universe_entry(ticker: str) -> bool:
    """
    硬删除：将 Universe 页面设为 archived=True，移入 Notion 回收站。
    页面从所有视图消失，可在回收站找回。用于彻底移除错误条目。
    """
    client = get_client()
    page_id = _find_universe_page_id(client, ticker)
    if not page_id:
        logger.warning(f"archive_universe_entry: 未找到 {ticker}")
        return False
    try:
        client.pages.update(page_id=page_id, archived=True)
        logger.info(f"🗑 Universe archived: {ticker}")
        return True
    except APIResponseError as e:
        logger.error(f"archive_universe_entry 失败 [{ticker}]: {e}")
        return False


# ── 飞书预警 ─────────────────────────────────────────────────

def _send_feishu_alert(ticker: str, parsed_data: dict, page_id: str) -> None:
    """发送飞书 Webhook 预警消息"""
    if not FEISHU_WEBHOOK:
        return
    notion_link = f"https://notion.so/{page_id.replace('-', '')}"
    msg = (
        f"🚨 [{ticker}] {parsed_data.get('event_type', '事件')}\n"
        f"📋 {parsed_data.get('one_line_summary', '')}\n"
        f"📊 信号：{parsed_data.get('investment_signal', '中性')}\n"
        f"🔗 {notion_link}"
    )
    payload = {"msg_type": "text", "content": {"text": msg}}
    try:
        resp = requests.post(FEISHU_WEBHOOK, json=payload, timeout=10)
        resp.raise_for_status()
        logger.info(f"飞书预警发送成功: {ticker}")
    except Exception as e:
        logger.error(f"飞书预警发送失败: {e}")


# ── 文件内容提取 + Notion 子页面 ──────────────────────────────

_DOC_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/pdf,*/*;q=0.9",
    "Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.hkexnews.hk/",
    "Connection": "keep-alive",
}
_MAX_TEXT_CHARS = 80_000   # 约 40 页正文，避免 Notion 页面过大
_BLOCK_CHAR_LIMIT = 1800   # Notion 每个 rich_text 上限 2000，留缓冲


def _extract_text_from_pdf_bytes(data: bytes, timeout: int = 60) -> str:
    """从 PDF 字节流提取纯文本（使用 pdfplumber，带超时保护避免大 PDF 挂起）"""
    import concurrent.futures

    def _parse() -> str:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            pages_text = []
            total = 0
            for page in pdf.pages:
                t = page.extract_text() or ""
                pages_text.append(t)
                total += len(t)
                if total >= _MAX_TEXT_CHARS:
                    break
            return "\n\n".join(pages_text)[:_MAX_TEXT_CHARS]

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(_parse)
        try:
            return fut.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            logger.warning(f"PDF 解析超时（>{timeout}s），返回空文本")
            return ""
        except Exception as e:
            logger.warning(f"PDF 文本提取失败: {e}")
            return ""


def _extract_text_from_html(content: bytes, url: str = "") -> str:
    """从 HTML 字节流提取正文纯文本"""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(content, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        return text[:_MAX_TEXT_CHARS]
    except Exception as e:
        logger.warning(f"HTML 文本提取失败: {e}")
        return ""


def fetch_document_text(url: str, pdf_path: str | None = None) -> str:
    """
    从本地 PDF 路径或远程 URL 获取文档纯文本。
    优先使用已下载的本地 PDF；否则下载 URL 内容并提取。
    """
    # 优先读取本地 PDF
    if pdf_path:
        p = Path(pdf_path)
        if p.exists():
            try:
                return _extract_text_from_pdf_bytes(p.read_bytes())
            except Exception as e:
                logger.warning(f"读取本地 PDF 失败 {pdf_path}: {e}")

    if not url:
        return ""

    # 下载远程文件（最多重试 3 次，间隔 5s）
    import time as _time
    for attempt in range(3):
        try:
            resp = requests.get(url, headers=_DOC_HEADERS, timeout=120)
            resp.raise_for_status()
            content_type = resp.headers.get("Content-Type", "")
            if "pdf" in content_type or url.lower().endswith(".pdf"):
                return _extract_text_from_pdf_bytes(resp.content)
            else:
                return _extract_text_from_html(resp.content, url)
        except Exception as e:
            if attempt < 2:
                logger.warning(f"文件下载失败（第{attempt+1}次），5s 后重试 [{url}]: {e}")
                _time.sleep(5)
            else:
                logger.warning(f"文件下载失败（已重试3次），跳过 [{url}]: {e}")
    return ""


def _text_to_blocks(text: str) -> list[dict]:
    """将纯文本切割为 Notion paragraph blocks（每块 ≤ 1800 字符）"""
    blocks = []
    lines = text.split("\n")
    buf = []
    buf_len = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if buf_len + len(line) + 1 > _BLOCK_CHAR_LIMIT:
            if buf:
                blocks.append({
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"type": "text", "text": {"content": "\n".join(buf)}}]
                    },
                })
            buf = [line]
            buf_len = len(line)
        else:
            buf.append(line)
            buf_len += len(line) + 1
    if buf:
        blocks.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": "\n".join(buf)}}]
            },
        })
    return blocks


def _detect_market(ticker: str) -> str:
    upper = ticker.upper()
    if upper.endswith(".SH") or upper.endswith(".SZ") or upper.endswith(".BJ"):
        return "A股"
    if upper.endswith(".HK"):
        return "港股"
    return "美股"


def _attach_analysis_subpage(client, page_id: str, ticker: str, week_date: str, result: dict) -> None:
    """在 ResearchHistory 条目下创建子页面，含 Step 1/2/3 全文分析"""
    step1 = result.get("_step1_text", "")
    step2 = result.get("_step2_text", "")
    step3 = result.get("_step3_raw", "")

    if not any([step1, step2, step3]):
        return

    def _h2(text: str) -> dict:
        return {
            "object": "block",
            "type": "heading_2",
            "heading_2": {"rich_text": [{"type": "text", "text": {"content": text}}]},
        }

    blocks: list[dict] = []
    if step1:
        blocks.append(_h2("综合分析（多维视角）"))
        blocks.extend(_text_to_blocks(step1))
    if step2:
        blocks.append(_h2("风险与反驳"))
        blocks.extend(_text_to_blocks(step2))
    if step3:
        blocks.append(_h2("最终判断（原文）"))
        blocks.extend(_text_to_blocks(step3))

    if not blocks:
        return

    title_text = ticker + " 分析详情 " + week_date
    first_batch = blocks[:95]
    rest_batches = [blocks[i:i + 95] for i in range(95, len(blocks), 95)]

    try:
        child = client.pages.create(
            parent={"page_id": page_id},
            properties={
                "title": {"title": [{"type": "text", "text": {"content": title_text[:255]}}]}
            },
            children=first_batch,
        )
        child_id = child["id"]
        for batch in rest_batches:
            client.blocks.children.append(block_id=child_id, children=batch)
        logger.info("  📄 分析子页面创建成功: " + title_text[:40])
    except Exception as e:
        logger.warning("分析子页面创建失败 [" + ticker + "]: " + str(e))


def create_research_history(ticker: str, result: dict, week_date: str) -> Optional[str]:
    """
    写入 ResearchHistory DB，并在条目下创建子页面存储 Step1/2/3 完整分析。
    统一入口：手动 research 命令和周度自动化都调用此函数。
    """
    from config.settings import NOTION_RESEARCH_HISTORY_DB_ID
    if not NOTION_RESEARCH_HISTORY_DB_ID:
        logger.error("NOTION_RESEARCH_HISTORY_DB_ID 未配置")
        return None

    client = get_client()
    market = _detect_market(ticker)
    name_text = ticker + " " + week_date

    # Signal: pipeline returns English (Strong Buy/Buy/Hold/Sell/Strong Sell)
    # Store as-is since ResearchHistory Signal options are in English
    signal = result.get("rating", "")
    # rating in result is already Chinese (买入/增持/中性/减持/卖出) from pipeline
    # Map back to English for ResearchHistory Signal select
    _rating_to_signal = {
        "买入": "Strong Buy",
        "增持": "Buy",
        "中性": "Hold",
        "减持": "Sell",
        "卖出": "Strong Sell",
    }
    signal_en = _rating_to_signal.get(signal, "Hold")

    properties: dict = {
        "Name":     build_notion_property("Name", {"type": "title"}, name_text),
        "Ticker":   build_notion_property("Ticker", {"type": "rich_text"}, ticker),
        "WeekDate": build_notion_property("WeekDate", {"type": "date"}, week_date),
        "Market":   build_notion_property("Market", {"type": "select"}, market),
        "Signal":   build_notion_property("Signal", {"type": "select"}, signal_en),
    }

    valuation = result.get("valuation_view", "")
    if valuation in ("低估", "合理", "高估"):
        properties["ValuationView"] = build_notion_property("ValuationView", {"type": "select"}, valuation)

    confidence = result.get("confidence", "")
    if confidence in ("High", "Medium", "Low"):
        properties["Confidence"] = build_notion_property("Confidence", {"type": "select"}, confidence)

    core_thesis = result.get("core_thesis", "")
    if core_thesis:
        properties["CoreThesis"] = build_notion_property("CoreThesis", {"type": "rich_text"}, core_thesis[:2000])

    risk_factors = result.get("risk_factors", "")
    if risk_factors:
        properties["RiskFactors"] = build_notion_property("RiskFactors", {"type": "rich_text"}, risk_factors[:2000])

    debate = result.get("debate_summary", "")
    if debate:
        properties["DebateSummary"] = build_notion_property("DebateSummary", {"type": "rich_text"}, debate[:500])

    sources = result.get("data_sources", "")
    if sources:
        properties["DataSources"] = build_notion_property("DataSources", {"type": "rich_text"}, sources)

    try:
        page = client.pages.create(
            parent={"database_id": NOTION_RESEARCH_HISTORY_DB_ID},
            properties=properties,
        )
        page_id = page["id"]
        logger.info("✅ ResearchHistory 写入成功: " + ticker + " " + week_date + " → " + page_id)

        # Create analysis sub-page with full Step 1/2/3 content
        _attach_analysis_subpage(client, page_id, ticker, week_date, result)
        return page_id
    except APIResponseError as e:
        logger.error("Notion API 错误 create_research_history [" + ticker + "]: " + str(e))
        return None


def attach_document_subpage(
    page_id: str,
    title: str,
    url: str = "",
    pdf_path: str | None = None,
) -> Optional[str]:
    """
    在 Events 页面（page_id）下创建子页面，内容为对应文件的提取文本。
    返回子页面 page_id；提取失败时返回 None。
    """
    text = fetch_document_text(url, pdf_path)
    if not text.strip():
        logger.debug(f"文档内容为空，跳过子页面创建: {title}")
        return None

    blocks = _text_to_blocks(text)
    if not blocks:
        return None

    client = get_client()
    # 每次最多创建 95 个 block（Notion 限制 100）
    first_batch = blocks[:95]
    rest_batches = [blocks[i:i+95] for i in range(95, len(blocks), 95)]

    try:
        child_page = client.pages.create(
            parent={"page_id": page_id},
            properties={
                "title": {
                    "title": [{"type": "text", "text": {"content": title[:255]}}]
                }
            },
            children=first_batch,
        )
        child_id = child_page["id"]

        for batch in rest_batches:
            client.blocks.children.append(block_id=child_id, children=batch)

        logger.info(f"  📄 子页面创建成功: {title[:40]} ({len(blocks)} blocks)")
        return child_id
    except Exception as e:
        logger.warning(f"子页面创建失败 [{title[:40]}]: {e}")
        return None
