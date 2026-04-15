"""
补跑 2026-04-07 缺失的 ResearchHistory 条目
"""
from __future__ import annotations
import concurrent.futures
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger
from data_ingestion.research_pipeline import run_research_pipeline
from notion_sync.sync_manager import create_research_history
from notion_sync.notion_client_wrapper import get_client
from config.settings import NOTION_RESEARCH_HISTORY_DB_ID

WEEK_DATE = "2026-04-07"
MISSING_TICKERS = [
    '9618.HK', '0808.HK', '600548.SH', '0788.HK', '601328.SH',
    'QFIN.O', '600900.SH', '2328.HK', '601318.SH', '600066.SH',
    '2319.HK', '0883.HK', '0288.HK', 'BRK.B', '1308.HK',
]
MAX_PARALLEL = 3
TICKER_TIMEOUT = 1200


def _already_done(ticker: str) -> bool:
    try:
        client = get_client()
        res = client.databases.query(
            database_id=NOTION_RESEARCH_HISTORY_DB_ID,
            filter={"and": [
                {"property": "Ticker", "rich_text": {"equals": ticker}},
                {"property": "WeekDate", "date": {"equals": WEEK_DATE}},
            ]},
            page_size=1,
        )
        return len(res.get("results", [])) > 0
    except Exception:
        return False


def main():
    pending = [t for t in MISSING_TICKERS if not _already_done(t)]
    logger.info(f"待补跑: {len(pending)} 只 → {pending}")

    written = failed = skipped = 0

    for i in range(0, len(pending), MAX_PARALLEL):
        batch = pending[i:i + MAX_PARALLEL]
        logger.info(f"批次 {i // MAX_PARALLEL + 1}: {batch}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_PARALLEL) as ex:
            futures = {ex.submit(run_research_pipeline, t): t for t in batch}
            try:
                for fut in concurrent.futures.as_completed(futures, timeout=TICKER_TIMEOUT):
                    ticker = futures[fut]
                    try:
                        result = fut.result()
                        if result:
                            if _already_done(ticker):
                                logger.info(f"⏭️  二次检查已存在，跳过 [{ticker}]")
                                skipped += 1
                            else:
                                page_id = create_research_history(ticker, result, WEEK_DATE)
                                if page_id:
                                    written += 1
                                    logger.info(f"✅ 写入成功 [{ticker}]")
                                else:
                                    failed += 1
                                    logger.warning(f"写入失败 [{ticker}]")
                        else:
                            failed += 1
                            logger.warning(f"Pipeline 返回空 [{ticker}]")
                    except Exception as e:
                        failed += 1
                        logger.error(f"Pipeline 异常 [{ticker}]: {e}")
            except concurrent.futures.TimeoutError:
                logger.error(f"批次超时（>{TICKER_TIMEOUT}s），继续下一批")
                failed += len([f for f in futures if not f.done()])

    logger.info(f"补跑完成：写入 {written}，失败 {failed}，跳过 {skipped} / 共 {len(pending)}")


if __name__ == "__main__":
    main()
