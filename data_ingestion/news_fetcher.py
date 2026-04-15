"""
data_ingestion/news_fetcher.py
每日个股新闻抓取，写入 Events DB（EventType = "新闻资讯"）

数据源：
- A股：AKShare stock_news_em（东方财富）
- 美股：Finnhub get_company_news（days=1）
- 港股：暂无稳定源，跳过
"""
from __future__ import annotations

from datetime import date, datetime, timezone, timedelta

from loguru import logger

# 关键词 → Importance = High
_HIGH_KEYWORDS = [
    "年报", "业绩", "增速", "净利", "营收", "收购", "并购", "重组",
    "监管", "处罚", "立案", "调查", "停牌", "退市", "ST",
    "分红", "派息", "回购", "定增", "配股",
]


def _is_high_importance(headline: str) -> bool:
    return any(kw in headline for kw in _HIGH_KEYWORDS)


def _detect_market(ticker: str) -> str:
    upper = ticker.upper()
    if upper.endswith(".SH") or upper.endswith(".SZ") or upper.endswith(".BJ"):
        return "A股"
    if upper.endswith(".HK"):
        return "港股"
    return "美股"


def _to_parsed_data(
    headline: str,
    content: str,
    pub_date: str,
    url: str,
) -> dict:
    """将新闻字段转换为 create_event() 接受的 parsed_data 格式"""
    importance = "High" if _is_high_importance(headline) else "Medium"
    summary = (headline + "\n\n" + content).strip()
    return {
        "event_type":        "新闻资讯",
        "event_date":        pub_date,
        "source_url":        url,
        "importance":        importance,
        "investment_signal": "中性",
        "detailed_analysis": summary[:2000],
        "key_numbers":       {},
        "fiscal_period":     "",
    }


# ── A股新闻（AKShare 东方财富）────────────────────────────────

def fetch_a_share_news(ticker: str) -> list[dict]:
    """
    拉取 A 股个股新闻（全量，不限条数）
    ticker: "601318.SH" 格式
    返回: list of parsed_data dict
    """
    import akshare as ak

    symbol_6 = ticker.split(".")[0]
    today = date.today().isoformat()

    try:
        df = ak.stock_news_em(symbol=symbol_6)
    except Exception as e:
        logger.warning(f"AKShare stock_news_em 失败 [{ticker}]: {e}")
        return []

    if df is None or df.empty:
        return []

    # 只保留近2天的新闻（AKShare 返回全量历史，无法通过参数过滤）
    cutoff = (date.today() - timedelta(days=2)).isoformat()
    seen_urls: set[str] = set()
    results = []
    for _, row in df.iterrows():
        title   = str(row.get("新闻标题", "") or "").strip()
        content = str(row.get("新闻内容", "") or "").strip()
        url     = str(row.get("新闻链接", "") or "").strip()
        pub_raw = str(row.get("发布时间", "") or "").strip()

        if not title or not url:
            continue

        # 日期提取：取前10位
        pub_date = pub_raw[:10] if pub_raw else today

        # 过滤历史新闻 + 批内 URL 去重
        if pub_date < cutoff:
            continue
        if url in seen_urls:
            continue
        seen_urls.add(url)

        results.append(_to_parsed_data(title, content, pub_date, url))

    logger.info(f"AKShare 新闻 [{ticker}] → {len(results)} 条（截止 {cutoff}）")
    return results


# ── 美股新闻相关性过滤（call_ai with DMXAPI→Gemini fallback）──

def _batch_relevance_filter(
    headlines: list[str], company_name: str, base_ticker: str
) -> list[bool]:
    """
    批量判断 headlines 是否与公司相关（1 次 LLM 调用/ticker）。
    返回与 headlines 等长的 bool 列表；失败时全部 True（fail-open）。
    """
    import json as _json
    import re as _re
    from data_ingestion.ai_client import call_ai as _call_ai_inner

    if not headlines:
        return []

    numbered = "\n".join(f"{i + 1}. {h}" for i, h in enumerate(headlines))
    prompt = (
        f'For each headline below, is it directly relevant to {company_name} ({base_ticker})? '
        f'Reply with a JSON array of "YES" or "NO", one per headline, in order. '
        f'Example for 3 headlines: ["YES","NO","YES"]\n\n{numbered}'
    )

    try:
        raw = _call_ai_inner(prompt, timeout=30).strip()
        m = _re.search(r"\[.*?\]", raw, _re.DOTALL)
        answers = _json.loads(m.group(0) if m else raw)
        if isinstance(answers, list) and len(answers) == len(headlines):
            return [str(a).upper() != "NO" for a in answers]
        logger.warning(f"LLM 返回格式异常，保留全部: {raw[:80]}")
    except Exception as e:
        logger.warning(f"LLM 相关性过滤失败，fail-open 保留全部: {e}")

    return [True] * len(headlines)


# ── 美股新闻（Finnhub + LLM 批量相关性过滤）─────────────────

def fetch_us_news(ticker: str) -> list[dict]:
    """
    拉取美股个股新闻（今天，不限条数）。
    用 glm-5.1-free 批量过滤无关新闻（1 次 LLM 调用/ticker）。
    ticker: "AAPL.O" / "BRK.B" 格式
    """
    from data_ingestion.finnhub_fetcher import get_company_news, _normalize_ticker, _get

    symbol = _normalize_ticker(ticker)
    base_kw = symbol.split(".")[0]  # BRK.B → BRK

    # 获取公司全称（用于 LLM prompt）
    company_name = base_kw
    try:
        profile = _get("/stock/profile2", {"symbol": symbol}) or {}
        company_name = profile.get("name", base_kw) or base_kw
    except Exception:
        pass

    today = date.today().isoformat()
    raw = get_company_news(ticker, days=1)

    # 收集所有有效条目
    items = [
        it for it in raw
        if str(it.get("headline", "") or "").strip()
        and str(it.get("url", "") or "").strip()
    ]

    if not items:
        logger.info(f"Finnhub 新闻 [{ticker}] → 0 条")
        return []

    # 批量 LLM 过滤（1 次调用）
    headlines = [str(it.get("headline", "") or "") for it in items]
    keep_flags = _batch_relevance_filter(headlines, company_name, base_kw)

    results = []
    filtered = 0
    for item, keep in zip(items, keep_flags):
        if not keep:
            filtered += 1
            logger.debug(f"过滤无关新闻 [{ticker}]: {item.get('headline','')[:60]}")
            continue
        headline = str(item.get("headline", "") or "").strip()
        summary  = str(item.get("summary",  "") or "").strip()
        url      = str(item.get("url",      "") or "").strip()
        dt_raw   = str(item.get("datetime", "") or "").strip()
        pub_date = dt_raw[:10] if dt_raw else today
        results.append(_to_parsed_data(headline, summary, pub_date, url))

    logger.info(f"Finnhub 新闻 [{ticker}] → {len(results)} 条（过滤 {filtered} 条无关）")
    return results


# ── 统一入口 ─────────────────────────────────────────────────

def fetch_news_for_ticker(ticker: str) -> list[dict]:
    """按市场分发，返回当日全量新闻的 parsed_data 列表"""
    market = _detect_market(ticker)
    if market == "A股":
        return fetch_a_share_news(ticker)
    elif market == "美股":
        return fetch_us_news(ticker)
    else:
        # 港股暂无稳定源
        return []
