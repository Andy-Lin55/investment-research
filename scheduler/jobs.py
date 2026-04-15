"""
scheduler/jobs.py
定时任务定义（使用 schedule 库）
"""
from __future__ import annotations

import contextlib
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import schedule
import time
from loguru import logger


# ── 防休眠上下文（macOS caffeinate）────────────────────────────

@contextlib.contextmanager
def _no_sleep(reason: str = ""):
    """macOS: 任务运行期间阻止空闲休眠，任务结束后自动恢复"""
    if sys.platform != "darwin":
        yield
        return
    proc = subprocess.Popen(["caffeinate", "-i"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    logger.debug(f"☕ caffeinate 已启动（{reason}，pid={proc.pid}）")
    try:
        yield
    finally:
        proc.terminate()
        proc.wait()
        logger.debug(f"☕ caffeinate 已退出（{reason}）")

from data_ingestion.cninfo_fetcher import batch_fetch, fetch_periodic_reports
from data_ingestion.pdf_parser import parse_announcement, parse_research_report
from data_ingestion.filings_router import fetch_filings
from notion_sync.sync_manager import (
    create_event,
    create_research,
    event_exists,
    update_company_signal,
    get_active_tickers,
    get_daily_tickers,
    attach_document_subpage,
)


# ── 辅助：从标题推断 event_type ───────────────────────────────

def _infer_event_type(title: str) -> str:
    """根据公告标题推断事件类型，避免同日期不同文件的幂等 key 碰撞"""
    t = title
    # 注意：先检查更具体的子串（半年度报告 > 年度报告）
    if "半年度报告" in t or "中期报告" in t or "半年报告" in t:
        return "中期报告"
    if "季度报告" in t:
        return "季度报告"
    if "年度报告" in t:
        return "年度报告"
    if "业绩说明会" in t or "业绩发布会" in t or "投资者说明会" in t:
        return "业绩会"
    if "业绩快报" in t or "业绩预告" in t:
        return "业绩公告"
    return "公告"


def _trigger_research_async(ticker: str, trigger_event_id: str) -> None:
    """High importance 事件后，在后台线程触发 Research Pipeline（不阻塞每日任务）"""
    import threading

    def _run():
        try:
            from data_ingestion.research_pipeline import run_research_pipeline
            from notion_sync.sync_manager import create_research
            logger.info(f"🔬 Auto-triggering Research Pipeline [{ticker}] from event {trigger_event_id[:8]}...")
            result = run_research_pipeline(ticker, trigger_event_id=trigger_event_id)
            if result:
                create_research(ticker, result)
                logger.info(f"✅ Auto Research 写入完成 [{ticker}]")
            else:
                logger.warning(f"Auto Research Pipeline 返回空结果 [{ticker}]")
        except Exception as e:
            logger.error(f"Auto Research Pipeline 失败 [{ticker}]: {e}")

    t = threading.Thread(target=_run, daemon=True, name=f"research-{ticker}")
    t.start()


# ── 每日 07:30 — 拉取昨日公告 ────────────────────────────────

def job_fetch_and_parse() -> None:
    """拉取所有 Active 股票的最新公告，解析后写入 Notion Events DB"""
    with _no_sleep("公告抓取"):
        _job_fetch_and_parse()


def _job_fetch_and_parse() -> None:
    logger.info("═" * 50)
    logger.info("📡 开始每日公告抓取任务")
    tickers = get_daily_tickers()
    if not tickers:
        logger.warning("没有 Active/Watch 股票，跳过")
        return

    # 合并两组关键字的结果：日常公告 + 年报/季报（仅抓近3天，历史已处理）
    all_announcements: dict[str, list[dict]] = batch_fetch(tickers, days_back=3)
    for ticker in tickers:
        if ticker.endswith((".SH", ".SZ", ".BJ")):
            try:
                periodic = fetch_periodic_reports(ticker, days_back=3)
                existing = {a["title"] for a in all_announcements.get(ticker, [])}
                new_items = [a for a in periodic if a["title"] not in existing]
                all_announcements.setdefault(ticker, []).extend(new_items)
            except Exception as e:
                logger.error(f"定期报告抓取失败 [{ticker}]: {e}")
    written = 0

    cutoff_date = (date.today() - timedelta(days=3)).isoformat()

    for ticker, ann_list in all_announcements.items():
        for ann in ann_list:
            if not ann.get("pdf_path"):
                logger.debug(f"[{ticker}] 无 PDF，跳过解析: {ann.get('title')}")
                continue
            # CNINFO 忽略日期过滤，手动跳过3天外的历史公告（避免反复 AI 解析）
            ann_date = ann.get("date", "")
            if ann_date and ann_date < cutoff_date:
                logger.debug(f"[{ticker}] 跳过历史公告（{ann_date}）: {ann.get('title')}")
                continue
            try:
                event_type = _infer_event_type(ann.get("title", ""))
                event_date = ann.get("date", date.today().isoformat())
                source_url = ann.get("url", "")
                if event_exists(ticker, event_type, event_date, source_url):
                    logger.debug(f"[{ticker}] 幂等跳过（解析前）: {ann.get('title')}")
                    continue
                parsed = parse_announcement(
                    pdf_path=ann["pdf_path"],
                    ticker=ticker,
                    event_type=event_type,
                )
                if not parsed:
                    continue
                parsed["event_date"] = ann.get("date", date.today().isoformat())
                parsed["source_url"] = ann.get("url", "")
                page_id = create_event(ticker, parsed)
                if page_id:
                    written += 1
                    # 如果信号明确，更新 Universe DB
                    signal = parsed.get("investment_signal", "")
                    if signal and signal != "中性":
                        update_company_signal(ticker, signal)
                    # High importance 事件自动触发 Research Pipeline（非阻塞）
                    if parsed.get("importance") == "High" and page_id:
                        _trigger_research_async(ticker, page_id)
            except Exception as e:
                logger.error(f"处理公告失败 [{ticker}]: {e}")

    logger.info(f"📊 公告抓取完成，写入 Notion {written} 条")


# ── 每日 07:45 — HK/US 公开文件抓取 ─────────────────────────

def job_fetch_filings() -> None:
    """拉取 HK/US Active 股票的最新公告/报告（元数据写入 Events DB，不做 AI 解析）"""
    with _no_sleep("HK/US文件抓取"):
        _job_fetch_filings()


def _job_fetch_filings() -> None:
    logger.info("═" * 50)
    logger.info("📡 开始 HK/US 文件抓取任务")
    tickers = get_daily_tickers()
    hk_us = [t for t in tickers if not (t.endswith(".SH") or t.endswith(".SZ") or t.endswith(".BJ"))]
    if not hk_us:
        logger.info("没有 HK/US Active 股票，跳过")
        return

    written = 0
    for ticker in hk_us:
        try:
            filings = fetch_filings(ticker, doc_type="all")
            for filing in filings:
                parsed = {
                    "event_type": filing.get("doc_type", "公告"),
                    "event_date": filing.get("date", ""),
                    "source_url": filing.get("url", ""),
                    "fiscal_period": filing.get("fiscal_period", ""),
                    "importance": "Medium",
                    "investment_signal": "中性",
                    "detailed_analysis": filing.get("title", ""),
                    "key_numbers": {},
                }
                page_id = create_event(ticker, parsed)
                if page_id:
                    written += 1
                    attach_document_subpage(
                        page_id=page_id,
                        title=filing.get("title", "原文"),
                        url=filing.get("url", ""),
                        pdf_path=filing.get("pdf_path"),
                    )
                    # 美股 8-K/6-K: 附上 EX-99.x exhibit slides
                    for ex_url in filing.get("exhibit_urls", []):
                        attach_document_subpage(
                            page_id=page_id,
                            title="Exhibit: " + ex_url.split("/")[-1],
                            url=ex_url,
                            pdf_path=None,
                        )
        except Exception as e:
            logger.error(f"HK/US 文件抓取失败 [{ticker}]: {e}")

    logger.info(f"📊 HK/US 文件抓取完成，写入 Notion {written} 条")


# ── 每日 08:00 — 当日监控简报 ───────────────────────────────

def _send_briefing(subject: str, body: str, today: str) -> None:
    """
    发送每日简报，按优先级尝试：
      1. 飞书 webhook（FEISHU_WEBHOOK 已配置时）
      2. SMTP 邮件（SMTP_USER/SMTP_PASS 已配置时）
      3. Apple Mail 草稿（macOS 本机降级）
    """
    import subprocess
    from config.settings import (
        FEISHU_WEBHOOK, BRIEFING_TO_EMAIL,
        SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, BRIEFING_FROM_EMAIL,
    )

    # ── 1. 飞书 Webhook ──────────────────────────────────────
    if FEISHU_WEBHOOK:
        try:
            import requests as _req
            payload = {
                "msg_type": "text",
                "content": {"text": f"{subject}\n\n{body}"},
            }
            r = _req.post(FEISHU_WEBHOOK, json=payload, timeout=10)
            if r.status_code == 200 and r.json().get("code") == 0:
                logger.info(f"✅ 飞书简报发送成功：{subject}")
                return
            logger.warning(f"飞书发送失败: {r.text[:200]}")
        except Exception as e:
            logger.warning(f"飞书发送异常: {e}")

    # ── 2. SMTP 邮件 ─────────────────────────────────────────
    if SMTP_USER and SMTP_PASS:
        try:
            import smtplib
            from email.message import EmailMessage
            msg = EmailMessage()
            msg["Subject"] = subject
            msg["From"] = BRIEFING_FROM_EMAIL or SMTP_USER
            msg["To"] = BRIEFING_TO_EMAIL
            msg.set_content(body)
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30) as s:
                s.login(SMTP_USER, SMTP_PASS)
                s.send_message(msg)
            logger.info(f"✅ SMTP 简报已发送：{subject} → {BRIEFING_TO_EMAIL}")
            return
        except Exception as e:
            logger.error(f"SMTP 发送失败: {e}")

    # ── 3. Apple Mail 草稿（macOS 降级）────────────────────────
    try:
        safe_body    = body.replace("\\", "\\\\").replace('"', '\\"')
        safe_subject = subject.replace('"', "'")
        safe_to      = BRIEFING_TO_EMAIL.replace('"', "")
        script = (
            'tell application "Mail"\n'
            '    set newMsg to make new outgoing message with properties {'
            f'subject:"{safe_subject}", content:"{safe_body}", visible:true}}\n'
            '    tell newMsg\n'
            f'        make new to recipient at end of to recipients '
            f'with properties {{address:"{safe_to}"}}\n'
            '    end tell\n'
            'end tell'
        )
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            logger.info(f"✅ Apple Mail 草稿已创建：{subject}")
        else:
            logger.error(f"AppleScript 失败: {result.stderr.strip()}")
    except Exception as e:
        logger.error(f"创建邮件草稿失败: {e}")


def job_daily_briefing() -> None:
    """从 Notion Events DB 汇总当日 High Importance 事件，打印简报"""
    with _no_sleep("每日简报"):
        _job_daily_briefing()


def _job_daily_briefing() -> None:
    logger.info("═" * 50)
    logger.info("📋 开始生成每日简报")
    from notion_sync.notion_client_wrapper import get_client
    from config.settings import NOTION_EVENTS_DB_ID, BRIEFING_TO_EMAIL
    from data_ingestion.ai_client import call_ai as _call_ai_briefing
    import subprocess

    from notion_sync.notion_client_wrapper import notion_request_with_retry
    client = get_client()
    today = date.today().isoformat()

    # 1. 查 Notion Events DB：今日 High + Medium 事件
    try:
        res = notion_request_with_retry(
            client.databases.query,
            database_id=NOTION_EVENTS_DB_ID,
            filter={
                "and": [
                    {"property": "EventDate", "date": {"equals": today}},
                    {"or": [
                        {"property": "Importance", "select": {"equals": "High"}},
                        {"property": "Importance", "select": {"equals": "Medium"}},
                    ]},
                ]
            },
            page_size=50,
        )
        events = res.get("results", [])
    except Exception as e:
        logger.error(f"简报查询 Notion 失败: {e}")
        return

    if not events:
        logger.info(f"今日（{today}）无 High/Medium 事件，跳过简报")
        return

    # 2. 格式化事件列表
    lines = []
    for page in events:
        props = page.get("properties", {})
        name   = (props.get("Name",       {}).get("title",     [{}])[0].get("plain_text", ""))
        ticker = (props.get("Ticker",     {}).get("rich_text", [{}])[0].get("plain_text", ""))
        etype  = ((props.get("EventType", {}).get("select") or {}).get("name", ""))
        signal = ((props.get("Signal",    {}).get("select") or {}).get("name", "中性"))
        imp    = ((props.get("Importance",{}).get("select") or {}).get("name", ""))
        summ_rt = props.get("AI Summary", {}).get("rich_text", [])
        summary = summ_rt[0]["plain_text"] if summ_rt else ""
        lines.append(
            f"[{imp}][{signal}] {ticker} {etype}: {name}\n  摘要: {summary[:200]}"
        )
    events_text = "\n\n".join(lines)

    # 3. AI 生成自然语言简报（DMXAPI → Gemini fallback）
    prompt = (
        f"今天是 {today}，以下是投资组合监控系统今日捕获的重要事件（共 {len(events)} 条）：\n\n"
        f"{events_text}\n\n"
        "请用简洁的中文，以投资者视角，撰写一份今日晨间投研简报。"
        "结构：先总结今日整体情况（1-2句），再逐条点评重要事件的投资影响，"
        "最后给出今日需要重点关注的1-2个风险或机会。"
        "语气专业但简洁，不要废话，总长度控制在300字以内。"
    )
    briefing_body = _call_ai_briefing(prompt, timeout=30)
    if briefing_body:
        logger.info("AI 简报生成成功")
    else:
        logger.warning("AI 生成简报失败，降级为原始事件列表")
        briefing_body = f"今日事件列表（{len(events)} 条）：\n\n" + events_text

    # 4. 发送简报：Feishu → SMTP → Apple Mail（按优先级）
    subject = f"📊 投研日报 {today}（{len(events)} 条事件）"
    _send_briefing(subject, briefing_body, today)


# ── 每日 08:15 — 个股新闻抓取 ───────────────────────────────

def job_daily_news() -> None:
    """每日 08:15 — 拉取所有 Active 股票的当日新闻，写入 Events DB"""
    with _no_sleep("每日新闻"):
        _job_daily_news()


def _job_daily_news() -> None:
    logger.info("═" * 50)
    logger.info("📰 开始每日新闻抓取")
    from data_ingestion.news_fetcher import fetch_news_for_ticker
    from notion_sync.sync_manager import create_event, event_exists, get_daily_tickers

    tickers = get_daily_tickers()
    written = 0
    skipped = 0
    failed = 0

    for ticker in tickers:
        try:
            news_list = fetch_news_for_ticker(ticker)
            for parsed_data in news_list:
                source_url = parsed_data.get("source_url", "")
                event_type = parsed_data.get("event_type", "新闻资讯")
                event_date = parsed_data.get("event_date", "")
                if event_exists(ticker, event_type, event_date, source_url):
                    logger.debug(f"[{ticker}] 新闻幂等跳过（写入前）: {source_url[:60]}")
                    skipped += 1
                    continue
                page_id = create_event(ticker, parsed_data)
                if page_id:
                    written += 1
                else:
                    skipped += 1
        except Exception as e:
            failed += 1
            logger.error(f"新闻抓取失败 [{ticker}]: {e}")

    logger.info(
        f"📰 新闻抓取完成：写入 {written} 条，幂等跳过 {skipped} 条，失败 {failed} 个 ticker"
    )
    # 所有当日数据已入库，触发每日简报
    job_daily_briefing()


def job_daily_news_us() -> None:
    """仅抓取美股（Finnhub）新闻 + 触发简报，供 GitHub Actions 独立调用"""
    with _no_sleep("美股新闻"):
        _job_daily_news_us()


def _job_daily_news_us() -> None:
    """仅跑美股 Finnhub 新闻（无 AKShare 依赖，适合境外云端运行）"""
    logger.info("═" * 50)
    logger.info("📰 开始美股新闻抓取（Finnhub）")
    from data_ingestion.news_fetcher import fetch_us_news
    from notion_sync.sync_manager import create_event, event_exists, get_daily_tickers

    tickers = get_daily_tickers()
    us_tickers = [
        t for t in tickers
        if not (t.endswith(".SH") or t.endswith(".SZ") or t.endswith(".BJ")
                or t.endswith(".HK"))
    ]
    written = skipped = failed = 0
    for ticker in us_tickers:
        try:
            news_list = fetch_us_news(ticker)
            for parsed_data in news_list:
                source_url = parsed_data.get("source_url", "")
                event_type = parsed_data.get("event_type", "新闻资讯")
                event_date = parsed_data.get("event_date", "")
                if event_exists(ticker, event_type, event_date, source_url):
                    skipped += 1
                    continue
                page_id = create_event(ticker, parsed_data)
                if page_id:
                    written += 1
                else:
                    skipped += 1
        except Exception as e:
            failed += 1
            logger.error(f"美股新闻抓取失败 [{ticker}]: {e}")

    logger.info(
        f"📰 美股新闻完成：写入 {written} 条，幂等跳过 {skipped} 条，失败 {failed} 个 ticker"
    )
    # 注意：简报由 GitHub Actions workflow 独立触发，不在此处调用，避免与本机重复


# ── 周日批量更新财务快照 ─────────────────────────────────────

def job_weekly_financials() -> None:
    """每周日批量更新财务快照（需要 Tushare Pro）"""
    with _no_sleep("财务快照"):
        _job_weekly_financials()


def _job_weekly_financials() -> None:
    logger.info("📈 开始每周财务快照更新")
    try:
        from data_ingestion.tushare_fetcher import batch_update_financials
        batch_update_financials()
    except Exception as e:
        logger.error(f"每周财务快照更新失败: {e}")


# ── 每周日 21:30 — A股情绪指标检查 ──────────────────────────

def job_weekly_sentiment_check() -> None:
    """每周日 21:30 — 拉取 Active A股情绪指标，记录到日志供 Research Pipeline 使用"""
    with _no_sleep("情绪指标"):
        _job_weekly_sentiment_check()


def _job_weekly_sentiment_check() -> None:
    logger.info("📊 开始每周情绪指标检查")
    from notion_sync.sync_manager import get_daily_tickers
    from data_ingestion.tushare_sentiment import get_sentiment_snapshot, format_sentiment_for_prompt

    tickers = get_daily_tickers()
    a_shares = [t for t in tickers if t.endswith((".SH", ".SZ", ".BJ"))]
    if not a_shares:
        logger.info("无 Active A股，跳过情绪检查")
        return

    for ticker in a_shares:
        try:
            sentiment = get_sentiment_snapshot(ticker)
            if sentiment:
                summary = format_sentiment_for_prompt(sentiment)
                logger.info(f"[{ticker}] 情绪快照:\n{summary}")
        except Exception as e:
            logger.error(f"情绪快照失败 [{ticker}]: {e}")

    logger.info("📊 每周情绪指标检查完成")


# ── 手动触发：处理单个 PDF ───────────────────────────────────

def process_pdf(pdf_path: str, ticker: str, doc_type: str = "announcement") -> None:
    """
    手动处理单个 PDF 文件

    doc_type: "announcement" | "research"
    """
    logger.info(f"手动处理 PDF: {pdf_path} [{ticker}] type={doc_type}")
    path = Path(pdf_path)
    if not path.exists():
        logger.error(f"文件不存在: {pdf_path}")
        return

    if doc_type == "announcement":
        parsed = parse_announcement(pdf_path=path, ticker=ticker)
        if parsed:
            parsed["event_date"] = date.today().isoformat()
            create_event(ticker, parsed)
    elif doc_type == "research":
        parsed = parse_research_report(pdf_path=path, ticker=ticker)
        if parsed:
            create_research(ticker, parsed)
    else:
        logger.error(f"未知 doc_type: {doc_type}")


# ── 每周日 22:00 — 全量 Research Pipeline ───────────────────

def _research_already_done(ticker: str, week_date: str) -> bool:
    """检查 ResearchHistory DB 中是否已有该 ticker+week_date 的条目（避免重复写入）"""
    try:
        from notion_sync.notion_client_wrapper import get_client
        from config.settings import NOTION_RESEARCH_HISTORY_DB_ID
        if not NOTION_RESEARCH_HISTORY_DB_ID:
            return False
        client = get_client()
        res = client.databases.query(
            database_id=NOTION_RESEARCH_HISTORY_DB_ID,
            filter={
                "and": [
                    {"property": "Ticker", "rich_text": {"equals": ticker}},
                    {"property": "WeekDate", "date": {"equals": week_date}},
                ]
            },
            page_size=1,
        )
        return len(res.get("results", [])) > 0
    except Exception:
        return False


def _get_latest_research_date(ticker: str) -> str | None:
    """查询该 ticker 最近一次 ResearchHistory 的 WeekDate，返回 ISO 日期或 None（从未分析过）"""
    try:
        from notion_sync.notion_client_wrapper import get_client
        from config.settings import NOTION_RESEARCH_HISTORY_DB_ID
        if not NOTION_RESEARCH_HISTORY_DB_ID:
            return None
        client = get_client()
        res = client.databases.query(
            database_id=NOTION_RESEARCH_HISTORY_DB_ID,
            filter={"property": "Ticker", "rich_text": {"equals": ticker}},
            sorts=[{"property": "WeekDate", "direction": "descending"}],
            page_size=1,
        )
        results = res.get("results", [])
        if not results:
            return None
        date_val = results[0]["properties"].get("WeekDate", {}).get("date")
        return date_val["start"] if date_val else None
    except Exception:
        return None


def _has_new_events_since(ticker: str, since_date: str) -> bool:
    """检查该 ticker 在 since_date 之后是否有新事件（任意 Importance）"""
    try:
        from notion_sync.notion_client_wrapper import get_client
        from config.settings import NOTION_EVENTS_DB_ID
        client = get_client()
        res = client.databases.query(
            database_id=NOTION_EVENTS_DB_ID,
            filter={"and": [
                {"property": "Ticker", "rich_text": {"equals": ticker}},
                {"property": "EventDate", "date": {"after": since_date}},
            ]},
            page_size=1,
        )
        return len(res.get("results", [])) > 0
    except Exception:
        return True  # 查询失败时保守策略：认为有新事件，照常分析


def job_weekly_research_all(week_date: str | None = None, skip_unchanged: bool = True) -> None:
    """每月第一个周日 22:00 — 全量 Active+Watch 股票 Research Pipeline → ResearchHistory DB
    week_date: 手动传入时跳过月度守卫（用于补跑历史日期）
    skip_unchanged: True=跳过上次分析后无新事件的 ticker（自动调度默认启用）；False=强制全量（补跑时用）
    """
    with _no_sleep("Research Pipeline"):
        _job_weekly_research_all(week_date=week_date, skip_unchanged=skip_unchanged)


def _job_weekly_research_all(week_date: str | None = None, skip_unchanged: bool = True) -> None:
    import concurrent.futures
    logger.info("═" * 50)
    logger.info("🔬 开始月度全量 Research Pipeline")
    from data_ingestion.research_pipeline import run_research_pipeline
    from notion_sync.sync_manager import create_research_history, get_daily_tickers
    from datetime import date

    # 月度守卫：自动调度时只在每月第一个周日执行（手动传入 week_date 则跳过守卫）
    if week_date is None:
        today = date.today()
        if today.weekday() != 6 or today.day > 7:
            logger.info("非本月第一个周日，跳过 Research Pipeline")
            return

    week_date = week_date or date.today().isoformat()
    all_tickers = get_daily_tickers()

    # 过滤本周已完成
    tickers = [t for t in all_tickers if not _research_already_done(t, week_date)]
    skipped = len(all_tickers) - len(tickers)
    if skipped:
        logger.info(f"⏭️  已跳过 {skipped} 只（ResearchHistory 已存在）")

    # 增量过滤：跳过上次分析后无新事件的 ticker（手动补跑时可关闭）
    if skip_unchanged and tickers:
        tickers_to_run = []
        skipped_no_change = 0
        for t in tickers:
            last_date = _get_latest_research_date(t)
            if last_date is None:
                tickers_to_run.append(t)  # 从未分析过，必须跑
            elif _has_new_events_since(t, last_date):
                tickers_to_run.append(t)  # 有新事件，需要重新分析
            else:
                skipped_no_change += 1
                logger.info(f"⏭️  [{t}] 上次分析（{last_date}）后无新事件，跳过")
        tickers = tickers_to_run
        if skipped_no_change:
            logger.info(f"📊 增量跳过 {skipped_no_change} 只（无新事件）")

    written = 0
    failed = 0

    # Per-ticker timeout: 20 minutes max
    TICKER_TIMEOUT = 1200
    MAX_PARALLEL = 3  # 同时处理 3 只，避免 Gemini 免费版限流

    for i in range(0, len(tickers), MAX_PARALLEL):
        batch = tickers[i:i + MAX_PARALLEL]
        logger.info(f"Research Pipeline 批次 {i // MAX_PARALLEL + 1}：{batch}")
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_PARALLEL) as ex:
            futures = {ex.submit(run_research_pipeline, t): t for t in batch}
            for fut in concurrent.futures.as_completed(futures, timeout=TICKER_TIMEOUT):
                ticker = futures[fut]
                try:
                    result = fut.result()
                    if result:
                        # 二次幂等检查（防并发/多进程重复写入）
                        if _research_already_done(ticker, week_date):
                            logger.info(f"⏭️  二次检查：{ticker} 已存在，跳过写入")
                            skipped += 1
                        else:
                            page_id = create_research_history(ticker, result, week_date)
                            if page_id:
                                written += 1
                            else:
                                failed += 1
                                logger.warning(f"ResearchHistory 写入失败 [{ticker}]")
                    else:
                        failed += 1
                        logger.warning(f"Research Pipeline 返回空结果 [{ticker}]")
                except concurrent.futures.TimeoutError:
                    failed += 1
                    logger.error(f"Research Pipeline 超时（>{TICKER_TIMEOUT}s）[{ticker}]，跳过")
                except Exception as e:
                    failed += 1
                    logger.error(f"Weekly Research 失败 [{ticker}]: {e}")

    logger.info(
        f"📊 月度 Research 完成，写入 {written}/{len(all_tickers)} 条，"
        f"失败 {failed} 条，已跳过 {skipped} 条"
    )


# ── 启动调度器 ───────────────────────────────────────────────

def start_scheduler() -> None:
    """启动定时任务调度器（阻塞运行），同时在后台启动 monitor agent"""
    import threading
    from scripts.monitor_agent import run_monitor

    logger.info("🚀 启动调度器")

    # 启动 monitor agent 守护线程
    monitor_thread = threading.Thread(target=run_monitor, daemon=True, name="monitor-agent")
    monitor_thread.start()
    logger.info("🔍 Monitor agent 已启动（后台守护线程）")

    schedule.every().day.at("07:30").do(job_fetch_and_parse)
    schedule.every().day.at("07:45").do(job_fetch_filings)
    schedule.every().day.at("08:15").do(job_daily_news)
    # 每日简报不单独调度，由 job_daily_news 完成后自动触发
    schedule.every().sunday.at("22:00").do(job_weekly_financials)
    schedule.every().sunday.at("21:30").do(job_weekly_sentiment_check)
    schedule.every().sunday.at("22:00").do(job_weekly_research_all)

    logger.info("定时任务已注册：")
    logger.info("  每天 07:30 — A股公告 + AI 解析 + 写入 Notion")
    logger.info("  每天 07:45 — HK/US 文件抓取 + 写入 Notion")
    logger.info("  每天 08:15 — 个股新闻抓取 + 写入 Notion → 触发每日简报（GLM+邮件草稿）")
    logger.info("  每周日 22:00 — 财务快照批量更新")
    logger.info("  每周日 21:30 — A股情绪指标检查")
    logger.info("  每周日 22:00 — Research Pipeline 守卫检查（仅第一个周日实际执行）")

    while True:
        schedule.run_pending()
        time.sleep(30)
