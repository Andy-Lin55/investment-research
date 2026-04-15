"""
scripts/cleanup_duplicate_events.py
一次性脚本：清理 Notion Events DB 中同一 SourceURL 被重复写入的记录。

策略：
  - 按 SourceURL 分组，同一 URL 有多条记录时，保留"类型最具体"的那条，归档其余。
  - 类型优先级（越靠前越具体）：年度报告 > 中期报告 > 季度报告 > 业绩会 > 业绩公告 > 20-F > 6-K > 10-K > 10-Q > 8-K > 公告
  - URL 为空的记录，视为无 URL，不参与 URL 级别去重（单独跳过）。

用法：
  cd investment_research
  source ../../../.venv_invest/bin/activate
  python scripts/cleanup_duplicate_events.py [--ticker 601318.SH] [--dry-run]
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from typing import Optional

from loguru import logger
from notion_sync.notion_client_wrapper import get_client
from config.settings import NOTION_EVENTS_DB_ID

# 类型优先级：index 越小越具体，保留最具体那条
_TYPE_PRIORITY = [
    "年度报告", "中期报告", "季度报告",
    "业绩会", "业绩会材料", "业绩公告",
    "20-F", "10-K", "10-Q", "6-K", "8-K",
    "新闻稿", "并购", "研报", "专家纪要",
    "公告",
]


def _type_rank(event_type: str) -> int:
    """数字越小越具体，越优先保留"""
    try:
        return _TYPE_PRIORITY.index(event_type)
    except ValueError:
        return len(_TYPE_PRIORITY)  # 未知类型排最后


def _fetch_all_events(client, ticker: Optional[str] = None) -> list[dict]:
    """从 Events DB 拉取所有记录（可按 ticker 过滤）"""
    results = []
    cursor = None
    filt: dict = {}
    if ticker:
        filt = {"property": "Ticker", "rich_text": {"equals": ticker}}

    while True:
        kwargs: dict = {"database_id": NOTION_EVENTS_DB_ID, "page_size": 100}
        if filt:
            kwargs["filter"] = filt
        if cursor:
            kwargs["start_cursor"] = cursor

        res = client.databases.query(**kwargs)
        results.extend(res.get("results", []))

        if res.get("has_more"):
            cursor = res["next_cursor"]
        else:
            break

    logger.info(f"共拉取 {len(results)} 条 Events 记录 (ticker={ticker or 'ALL'})")
    return results


def _get_prop(page: dict, prop_name: str, prop_type: str) -> str:
    props = page.get("properties", {})
    p = props.get(prop_name, {})
    if prop_type == "rich_text":
        arr = p.get("rich_text", [])
        return arr[0]["plain_text"] if arr else ""
    if prop_type == "select":
        sel = p.get("select")
        return sel["name"] if sel else ""
    if prop_type == "url":
        return p.get("url") or ""
    if prop_type == "date":
        d = p.get("date")
        return d["start"] if d else ""
    return ""


def cleanup(ticker: Optional[str], dry_run: bool) -> None:
    client = get_client()
    pages = _fetch_all_events(client, ticker)

    # 按 SourceURL 分组（忽略空 URL 和泛域名 URL）
    # 泛域名：URL 无路径/查询参数，不足以唯一标识一份文件
    _GENERIC_URLS = {
        "https://www.cninfo.com.cn",
        "http://www.cninfo.com.cn",
        "https://www.hkexnews.hk",
        "http://www.hkexnews.hk",
        "https://www.sec.gov",
        "http://www.sec.gov",
    }

    url_groups: dict[str, list[dict]] = defaultdict(list)
    no_url_pages: list[dict] = []

    for page in pages:
        url = _get_prop(page, "SourceURL", "url")
        if not url or url.rstrip("/") in _GENERIC_URLS:
            no_url_pages.append(page)
        else:
            url_groups[url].append(page)

    logger.info(f"空 URL 记录：{len(no_url_pages)} 条（跳过）")

    archived = 0
    for url, group in url_groups.items():
        if len(group) == 1:
            continue  # 无重复，跳过

        # 按类型优先级排序：最具体的排第一
        group.sort(key=lambda p: _type_rank(_get_prop(p, "EventType", "select")))
        keep = group[0]
        to_archive = group[1:]

        keep_type = _get_prop(keep, "EventType", "select")
        keep_date = _get_prop(keep, "EventDate", "date")
        keep_ticker = _get_prop(keep, "Ticker", "rich_text")

        logger.info(
            f"[{keep_ticker}] URL={url[:60]}  保留: {keep_type} {keep_date} "
            f"({keep['id'][:8]})"
        )
        for p in to_archive:
            p_type = _get_prop(p, "EventType", "select")
            p_date = _get_prop(p, "EventDate", "date")
            logger.info(f"  └─ 归档: {p_type} {p_date} ({p['id'][:8]})")
            if not dry_run:
                try:
                    client.pages.update(page_id=p["id"], archived=True)
                    archived += 1
                except Exception as e:
                    logger.error(f"  归档失败 [{p['id'][:8]}]: {e}")
            else:
                archived += 1  # dry-run 计数

    if dry_run:
        logger.info(f"[DRY RUN] 将归档 {archived} 条重复记录（未实际执行）")
    else:
        logger.info(f"✅ 清理完成，共归档 {archived} 条重复记录")

    # 额外报告：无 URL 的记录列表
    if no_url_pages:
        print("\n── 无 SourceURL 记录（需手动核查）──")
        for p in no_url_pages:
            t = _get_prop(p, "Ticker", "rich_text")
            et = _get_prop(p, "EventType", "select")
            ed = _get_prop(p, "EventDate", "date")
            print(f"  [{t}] {et} {ed}  id={p['id'][:8]}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="清理 Notion Events DB 中的重复记录")
    parser.add_argument("--ticker", default=None, help="只处理指定 ticker，如 601318.SH")
    parser.add_argument("--dry-run", action="store_true", help="仅显示将归档的记录，不实际操作")
    args = parser.parse_args()
    cleanup(ticker=args.ticker, dry_run=args.dry_run)
