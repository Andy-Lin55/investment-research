"""
main.py
投研自动化系统 — 统一命令行入口

用法：
  python main.py status                                       # 检查 API 连接
  python main.py scheduler                                    # 启动定时任务
  python main.py fetch --ticker 600519                        # 立即抓取单股公告
  python main.py parse --file path.pdf --ticker 600519 --type announcement

  # 标的名单管理
  python main.py import                                       # 从 Excel 批量导入
  python main.py add --ticker 300750.SZ --name 宁德时代 --market A股
  python main.py remove --ticker 0R37.L                       # 软删除 → Archived
  python main.py delete --ticker 0R37.L                       # 硬删除 → Notion 回收站
  python main.py setstatus --ticker 9618.HK --status Watch    # 调整状态

  # 公开文件获取（年报/季报/业绩会/新闻稿）
  python main.py filings --ticker QFIN.O                      # 全部类型
  python main.py filings --ticker 0700.HK --type annual       # 港股年报
  python main.py filings --ticker 601318.SH --type earnings   # A股业绩材料
  python main.py filings --all                                 # 所有 Active 股票
"""
import argparse
import sys

from loguru import logger

# ── 日志配置 ─────────────────────────────────────────────────
logger.remove()
logger.add(sys.stderr, level="INFO", colorize=True,
           format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}")
logger.add("investment_research.log", rotation="10 MB", retention="30 days", level="DEBUG")


# ── status 子命令 ────────────────────────────────────────────

def cmd_status() -> None:
    """检查所有 API 连接状态"""
    print("\n" + "═" * 55)
    print("  投研系统 — 环境检查")
    print("═" * 55)

    from config.settings import validate, NOTION_TOKEN, TUSHARE_TOKEN, DMXAPI_KEY

    cfg = validate()
    required = ["NOTION_TOKEN", "NOTION_UNIVERSE_DB_ID", "NOTION_EVENTS_DB_ID",
                "NOTION_RESEARCH_DB_ID", "TUSHARE_TOKEN", "DMXAPI_KEY"]
    optional = ["FEISHU_WEBHOOK"]

    print("\n📋 配置变量：")
    for key in required:
        ok = cfg.get(key, False)
        status = "✅" if ok else "❌ 未配置"
        print(f"  {status}  {key}")
    for key in optional:
        ok = cfg.get(key, False)
        status = "✅" if ok else "⚪ 未配置（可选）"
        print(f"  {status}  {key}")

    # Notion 连通性测试
    print("\n🌐 API 连通性：")
    if NOTION_TOKEN:
        try:
            from notion_sync.notion_client_wrapper import get_client
            client = get_client()
            me = client.users.me()
            print(f"  ✅ Notion API — 已连接 ({me.get('name', 'unknown')})")
        except Exception as e:
            print(f"  ❌ Notion API — 失败: {e}")
    else:
        print("  ⚠️  Notion API — Token 未配置")

    # Tushare 连通性测试
    if TUSHARE_TOKEN:
        try:
            import tushare as ts
            ts.set_token(TUSHARE_TOKEN)
            pro = ts.pro_api()
            df = pro.stock_basic(exchange="SSE", list_status="L",
                                  fields="ts_code,name", limit=1)
            print(f"  ✅ Tushare Pro — 已连接 (积分充足)")
        except Exception as e:
            print(f"  ❌ Tushare — 失败: {e}")
    else:
        print("  ⚠️  Tushare — Token 未配置")

    # DMXAPI 连通性测试
    if DMXAPI_KEY:
        try:
            from openai import OpenAI as _OpenAI
            from config.settings import DMXAPI_BASE_URL, DMXAPI_MODEL
            client_ai = _OpenAI(api_key=DMXAPI_KEY, base_url=DMXAPI_BASE_URL)
            resp = client_ai.chat.completions.create(
                model=DMXAPI_MODEL,
                messages=[{"role": "user", "content": "Say 'OK' in one word."}],
            )
            reply = resp.choices[0].message.content.strip()[:20]
            print(f"  ✅ DMXAPI — 已连接 (回应: {reply})")
        except Exception as e:
            print(f"  ❌ DMXAPI — 失败: {e}")
    else:
        print("  ⚠️  DMXAPI — Key 未配置")

    print("\n" + "═" * 55 + "\n")


# ── fetch 子命令 ─────────────────────────────────────────────

def cmd_fetch(ticker: str) -> None:
    """立即抓取单股公告并写入 Notion"""
    logger.info(f"立即抓取: {ticker}")
    from data_ingestion.cninfo_fetcher import fetch_announcements
    from data_ingestion.pdf_parser import parse_announcement
    from notion_sync.sync_manager import create_event, update_company_signal
    from datetime import date

    announcements = fetch_announcements(ticker)
    logger.info(f"抓取到 {len(announcements)} 条公告")
    for ann in announcements:
        if ann.get("pdf_path"):
            parsed = parse_announcement(ann["pdf_path"], ticker)
            if parsed:
                parsed["event_date"] = ann.get("date", date.today().isoformat())
                parsed["source_url"] = ann.get("url", "")
                page_id = create_event(ticker, parsed)
                if page_id:
                    signal = parsed.get("investment_signal", "")
                    if signal and signal != "中性":
                        update_company_signal(ticker, signal)


# ── parse 子命令 ─────────────────────────────────────────────

def cmd_parse(file_path: str, ticker: str, doc_type: str) -> None:
    """解析单个 PDF 文件"""
    from scheduler.jobs import process_pdf
    process_pdf(pdf_path=file_path, ticker=ticker, doc_type=doc_type)


# ── 标的名单管理子命令 ────────────────────────────────────────

def cmd_import() -> None:
    """从 ~/Downloads/AL.xlsx 和 ~/Downloads/我的自选股.xlsx 批量导入"""
    from scripts.import_excel import run
    run()


def cmd_add(ticker: str, name: str, market: str, status: str) -> None:
    """手动添加单只股票到 Universe DB"""
    from notion_sync.sync_manager import create_universe_entry
    page_id = create_universe_entry(ticker=ticker, name=name, market=market, status=status)
    if page_id:
        print(f"✅ 已添加: {ticker} {name} [{status}]")
    else:
        print(f"❌ 添加失败（请检查日志）")


def cmd_remove(ticker: str) -> None:
    """软删除：Status → Archived（条目保留，可恢复）"""
    from notion_sync.sync_manager import set_universe_status
    ok = set_universe_status(ticker, "Archived")
    print(f"{'✅' if ok else '❌'} {ticker} → Archived")


def cmd_delete(ticker: str) -> None:
    """硬删除：将页面移入 Notion 回收站（需二次确认）"""
    confirm = input(f"⚠️  确认永久删除 {ticker}？此操作不可直接撤销 (y/N): ")
    if confirm.strip().lower() != "y":
        print("已取消")
        return
    from notion_sync.sync_manager import archive_universe_entry
    ok = archive_universe_entry(ticker)
    print(f"{'✅' if ok else '❌'} {ticker} 已移入 Notion 回收站")


def cmd_setstatus(ticker: str, status: str) -> None:
    """调整股票状态：Active / Watch / Archived"""
    from notion_sync.sync_manager import set_universe_status
    ok = set_universe_status(ticker, status)
    print(f"{'✅' if ok else '❌'} {ticker} → {status}")


def cmd_filings(ticker: str | None, doc_type: str, fetch_all: bool, attach_content: bool = True) -> None:
    """获取公开文件（年报/季报/业绩会/新闻稿），写入 Notion Events DB，可选附加文件内容子页面"""
    from data_ingestion.filings_router import fetch_filings
    from notion_sync.sync_manager import create_event, get_active_tickers, attach_document_subpage

    if fetch_all:
        tickers = get_active_tickers()
        logger.info(f"批量获取 filings，共 {len(tickers)} 只 Active 股票")
    else:
        tickers = [ticker]

    total = 0
    for t in tickers:
        filings = fetch_filings(t, doc_type=doc_type)
        for filing in filings:
            parsed = {
                "event_type": filing.get("doc_type", "公告"),
                "event_date": filing.get("date", ""),
                "source_url": filing.get("url", ""),
                "fiscal_period": filing.get("fiscal_period", ""),
                "filing_type": filing.get("form_type", ""),
                "importance": "Medium",
                "investment_signal": "中性",
                "detailed_analysis": filing.get("title", ""),
                "key_numbers": {},
            }
            page_id = create_event(t, parsed)
            if page_id:
                total += 1
                print(f"  ✅ [{t}] {filing.get('title', '')} → {page_id[:8]}...")
                if attach_content:
                    attach_document_subpage(
                        page_id=page_id,
                        title=filing.get("title", "原文"),
                        url=filing.get("url", ""),
                        pdf_path=filing.get("pdf_path"),
                    )

    print(f"\n共写入 {total} 条 filings 到 Notion Events DB")


def cmd_research(ticker: str | None, run_all: bool) -> None:
    """对 Watch 股票运行 AI 深度研究 Pipeline，写入 Research DB"""
    from data_ingestion.research_pipeline import run_research_pipeline
    from notion_sync.sync_manager import create_research_history, get_active_tickers
    from notion_sync.sync_manager import get_all_tickers

    if run_all:
        # 只跑 Watch 状态的股票（Active 的已有 Events 触发，Watch 是重点）
        all_t = get_all_tickers()   # Active + Watch
        active_t = set(get_active_tickers())
        tickers = [t for t in all_t if t not in active_t]  # Watch only
        if not tickers:
            # fallback: run all if no Watch-only
            tickers = all_t
        logger.info(f"批量 Research Pipeline，共 {len(tickers)} 只 Watch 股票")
    else:
        tickers = [ticker]

    for t in tickers:
        logger.info(f"开始 Research Pipeline: {t}")
        result = run_research_pipeline(t)
        if result:
            from datetime import date as _date
            page_id = create_research_history(t, result, _date.today().isoformat())
            if page_id:
                print(f"  ✅ [{t}] ResearchHistory 写入成功 → {page_id[:8]}...")
            else:
                print(f"  ❌ [{t}] ResearchHistory Notion 写入失败")
        else:
            print(f"  ❌ [{t}] Pipeline 返回空结果")


def cmd_backfill_subpages(ticker_filter: str | None = None) -> None:
    """为 Events DB 中已有的页面补充文件内容子页面（跳过已有子页面的条目）"""
    from notion_sync.notion_client_wrapper import get_client
    from notion_sync.sync_manager import attach_document_subpage
    from config.settings import NOTION_EVENTS_DB_ID

    client = get_client()
    total_checked = 0
    total_attached = 0

    # 分页查询 Events DB
    has_more = True
    start_cursor = None
    while has_more:
        query_kwargs = {"database_id": NOTION_EVENTS_DB_ID, "page_size": 100}
        if ticker_filter:
            query_kwargs["filter"] = {
                "property": "Ticker", "rich_text": {"equals": ticker_filter}
            }
        if start_cursor:
            query_kwargs["start_cursor"] = start_cursor

        res = client.databases.query(**query_kwargs)
        pages = res.get("results", [])
        has_more = res.get("has_more", False)
        start_cursor = res.get("next_cursor")

        for page in pages:
            page_id = page["id"]
            total_checked += 1
            try:
                # 检查是否已有子页面
                children = client.blocks.children.list(block_id=page_id)
                existing_child_pages = [
                    b for b in children.get("results", [])
                    if b.get("type") == "child_page"
                ]
                if existing_child_pages:
                    continue  # 已有子页面，跳过

                props = page.get("properties", {})
                source_url = ""
                url_prop = props.get("SourceURL", {})
                if url_prop.get("type") == "url":
                    source_url = url_prop.get("url") or ""

                title_prop = props.get("Name", {}).get("title", [])
                title = title_prop[0]["plain_text"] if title_prop else "原文"

                if not source_url:
                    continue

                result = attach_document_subpage(
                    page_id=page_id,
                    title=title,
                    url=source_url,
                )
                if result:
                    total_attached += 1
            except Exception as e:
                logger.warning(f"backfill 跳过 {page_id}: {e}")

    print(f"\n检查 {total_checked} 条 Events，补充了 {total_attached} 个子页面")


# ── 主入口 ───────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="投研自动化系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command")

    # status
    subparsers.add_parser("status", help="检查 API 连接状态")

    # scheduler
    subparsers.add_parser("scheduler", help="启动定时任务调度器")

    # fetch
    fetch_parser = subparsers.add_parser("fetch", help="立即抓取单股公告")
    fetch_parser.add_argument("--ticker", required=True, help="股票代码，如 600519")

    # parse
    parse_parser = subparsers.add_parser("parse", help="解析单个 PDF")
    parse_parser.add_argument("--file", required=True, help="PDF 文件路径")
    parse_parser.add_argument("--ticker", required=True, help="股票代码")
    parse_parser.add_argument("--type", dest="doc_type",
                              choices=["announcement", "research"],
                              default="announcement", help="文档类型")

    # ── 标的名单管理 ──────────────────────────────────────────
    subparsers.add_parser("import", help="从 Excel 批量导入标的宇宙")

    add_parser = subparsers.add_parser("add", help="手动添加单只股票")
    add_parser.add_argument("--ticker", required=True, help="如 600519.SH")
    add_parser.add_argument("--name",   required=True, help="公司名称")
    add_parser.add_argument("--market", required=True, choices=["A股", "港股", "美股"])
    add_parser.add_argument("--status", default="Watch", choices=["Active", "Watch", "Archived"])

    remove_parser = subparsers.add_parser("remove", help="软删除：Status → Archived")
    remove_parser.add_argument("--ticker", required=True)

    delete_parser = subparsers.add_parser("delete", help="硬删除：移入 Notion 回收站")
    delete_parser.add_argument("--ticker", required=True)

    setstatus_parser = subparsers.add_parser("setstatus", help="调整股票状态")
    setstatus_parser.add_argument("--ticker", required=True)
    setstatus_parser.add_argument("--status", required=True, choices=["Active", "Watch", "Archived"])

    # filings
    filings_parser = subparsers.add_parser("filings", help="获取公开文件（年报/季报/业绩会/新闻稿）")
    filings_ticker_group = filings_parser.add_mutually_exclusive_group(required=True)
    filings_ticker_group.add_argument("--ticker", help="单只股票，如 QFIN.O")
    filings_ticker_group.add_argument("--all", dest="fetch_all", action="store_true",
                                      help="抓取所有 Active 股票")
    filings_parser.add_argument("--type", dest="doc_type",
                                choices=["annual", "interim", "quarterly", "earnings", "all"],
                                default="all", help="文件类型（默认 all）")
    filings_parser.add_argument("--no-content", dest="no_content", action="store_true",
                                help="不附加文件内容子页面")

    # research
    research_parser = subparsers.add_parser("research", help="AI 深度研究 Pipeline → 写入 Research DB")
    research_group = research_parser.add_mutually_exclusive_group(required=True)
    research_group.add_argument("--ticker", help="单只股票，如 601318.SH")
    research_group.add_argument("--all", dest="run_all", action="store_true", help="所有 Watch 股票")

    # backfill-subpages
    backfill_parser = subparsers.add_parser("backfill-subpages",
                                            help="为已有 Events 页面补充文件内容子页面")
    backfill_parser.add_argument("--ticker", help="只处理指定 ticker（不填则全部）")

    args = parser.parse_args()

    if args.command == "status":
        cmd_status()
    elif args.command == "scheduler":
        from scheduler.jobs import start_scheduler
        start_scheduler()
    elif args.command == "fetch":
        cmd_fetch(args.ticker)
    elif args.command == "parse":
        cmd_parse(args.file, args.ticker, args.doc_type)
    elif args.command == "import":
        cmd_import()
    elif args.command == "add":
        cmd_add(args.ticker, args.name, args.market, args.status)
    elif args.command == "remove":
        cmd_remove(args.ticker)
    elif args.command == "delete":
        cmd_delete(args.ticker)
    elif args.command == "setstatus":
        cmd_setstatus(args.ticker, args.status)
    elif args.command == "filings":
        cmd_filings(
            ticker=getattr(args, "ticker", None),
            doc_type=args.doc_type,
            fetch_all=getattr(args, "fetch_all", False),
            attach_content=not getattr(args, "no_content", False),
        )
    elif args.command == "research":
        cmd_research(
            ticker=getattr(args, "ticker", None),
            run_all=getattr(args, "run_all", False),
        )
    elif args.command == "backfill-subpages":
        cmd_backfill_subpages(getattr(args, "ticker", None))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
