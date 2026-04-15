"""
scripts/cleanup_duplicate_research_history.py
清理 ResearchHistory DB 中的重复条目（同 ticker + 同 week_date）。
保留最早创建的，删除其余。

用法：
    python scripts/cleanup_duplicate_research_history.py          # 直接执行
    python scripts/cleanup_duplicate_research_history.py --dry-run # 仅预览，不删除
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger
from notion_sync.notion_client_wrapper import get_client
from config.settings import NOTION_RESEARCH_HISTORY_DB_ID


def get_all_research_history(client) -> list[dict]:
    """获取 ResearchHistory DB 全量记录（带分页）"""
    records = []
    cursor = None
    while True:
        kwargs = {"database_id": NOTION_RESEARCH_HISTORY_DB_ID, "page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        res = client.databases.query(**kwargs)
        records.extend(res.get("results", []))
        if res.get("has_more"):
            cursor = res["next_cursor"]
        else:
            break
    return records


def main(dry_run: bool = False) -> None:
    client = get_client()
    records = get_all_research_history(client)
    logger.info(f"ResearchHistory 总记录数: {len(records)}")

    # 按 (Ticker, WeekDate) 分组
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in records:
        props = r.get("properties", {})
        ticker_rt = props.get("Ticker", {}).get("rich_text", [])
        ticker = ticker_rt[0]["plain_text"] if ticker_rt else ""
        date_prop = (props.get("WeekDate") or {}).get("date") or {}
        week_date = date_prop.get("start", "")
        created = r.get("created_time", "")
        groups[(ticker, week_date)].append({"id": r["id"], "created": created})

    # 找出有重复的组
    to_delete: list[str] = []
    for (ticker, week_date), entries in sorted(groups.items()):
        if len(entries) <= 1:
            continue
        # 按 created_time 升序，保留最早的
        sorted_entries = sorted(entries, key=lambda x: x["created"])
        keep = sorted_entries[0]
        duplicates = sorted_entries[1:]
        logger.info(
            f"  重复: {ticker} {week_date} → 保留 {keep['id'][:8]}...（创建于 {keep['created'][:10]}），"
            f"删除 {len(duplicates)} 条"
        )
        for d in duplicates:
            logger.info(f"    删除: {d['id'][:8]}... 创建于 {d['created'][:10]}")
            to_delete.append(d["id"])

    if not to_delete:
        logger.info("✅ 无重复条目，无需清理")
        return

    logger.info(f"共需删除 {len(to_delete)} 条重复记录")

    if dry_run:
        logger.info("🔍 dry-run 模式，不执行删除")
        return

    # 执行删除（archive）
    deleted = 0
    for page_id in to_delete:
        try:
            client.pages.update(page_id=page_id, archived=True)
            deleted += 1
            logger.info(f"  已 archive: {page_id[:8]}...")
        except Exception as e:
            logger.error(f"  删除失败 {page_id[:8]}...: {e}")

    logger.info(f"✅ 清理完成，已 archive {deleted}/{len(to_delete)} 条")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="清理 ResearchHistory 重复条目")
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不执行删除")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
