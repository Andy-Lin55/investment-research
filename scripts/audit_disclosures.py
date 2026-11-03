"""Read-only Active/Watch inventory audit. No models, messages, or Notion mutations.

Run from the repository: python -m scripts.audit_disclosures --env-file PRIVATE_FILE
  --output LOCAL_DIRECTORY --as-of 2026-11-02 --days 7 --financials
Credentials stay in memory and are never exported or included in evidence.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlsplit

import requests
from dotenv import dotenv_values
from loguru import logger

from data_ingestion.cninfo_fetcher import fetch_announcements
from data_ingestion.disclosure_health import SourceHealth, beijing_date


def read_source(session, token: str, source_id: str, query_filter=None, max_pages: int = 100) -> list[dict]:
    """Only POST /query is allowed here: this is a Notion read operation."""
    headers = {"Authorization": "Bearer " + token, "Notion-Version": "2025-09-03"}
    rows, cursors, cursor = [], set(), None
    for _ in range(max_pages):
        body = {"page_size": 100}
        if query_filter:
            body["filter"] = query_filter
        if cursor:
            body["start_cursor"] = cursor
        response = session.post(f"https://api.notion.com/v1/data_sources/{source_id}/query",
                                headers=headers, json=body, timeout=30)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data.get("results"), list):
            raise ValueError("Notion missing results")
        rows.extend(data["results"])
        if not data.get("has_more"):
            return rows
        cursor = data.get("next_cursor")
        if not cursor or cursor in cursors:
            raise ValueError("Notion pagination did not advance")
        cursors.add(cursor)
    raise ValueError("Notion pagination limit exceeded")


def plain(prop: dict, kind: str) -> str:
    return "".join(item.get("plain_text") or item.get("text", {}).get("content", "") for item in prop.get(kind, []))


def market(ticker: str) -> str:
    if ticker.upper().endswith((".SH", ".SZ", ".BJ")):
        return "A"
    if ticker.upper().endswith(".NQ") and ticker[:-3].isdigit():
        return "NEEQ"
    return "HK" if ticker.upper().endswith(".HK") else "US"


def url_key(url: str) -> str:
    parts = urlsplit(url or "")
    return (parts.netloc + parts.path).lower().rstrip("/")


def compare_reports(items: list[dict], events: list[dict], events_complete: bool) -> list[dict]:
    originals = {(e["ticker"], url_key(e.get("url", ""))) for e in events
                 if e.get("event_type") not in {"新闻资讯", "新闻", "News"} and e.get("url")}
    candidates = []
    for item in items:
        title = item.get("title", "")
        kind = item.get("doc_type") or item.get("form_type", "")
        formal = re.search(r"20\d{2}年.*(?:季度报告|年度报告|中期报告|半年报告)", title)
        if not formal and kind not in {"年度报告", "中期报告", "季度报告", "业绩公告", "10-K", "20-F", "10-Q"}:
            continue
        status = "present" if (item["ticker"], url_key(item.get("url", ""))) in originals else (
            "absent_from_primary_events" if events_complete else "events_read_incomplete")
        candidates.append({**item, "event_status": status})
    return candidates


def financial_comparison(row: dict, reports: list[dict], window_start: str) -> dict:
    result = {"ticker": row["ticker"], "provider_status": row["status"], "latest": row.get("latest")}
    latest = row.get("latest")
    if not latest:
        return {**result, "inventory_check": "provider_unavailable_or_empty"}
    publication = latest.get("f_ann_date") or latest.get("ann_date") or ""
    if publication < window_start.replace("-", ""):
        return {**result, "inventory_check": "latest_publication_outside_window"}
    periods = set()
    for report in reports:
        if report["ticker"] != row["ticker"]:
            continue
        title = report["title"]
        year = re.search(r"(20\d{2})年", title)
        if not year:
            continue
        end = ("0930" if "三季度" in title else "0331" if "一季度" in title else
               "0630" if any(k in title for k in ["半年度", "中期", "半年报告"]) else
               "1231" if "年度报告" in title else None)
        if end:
            periods.add(year[1] + end)
    return {**result, "inventory_check": "provider_period_observed_in_inventory" if latest["end_date"] in periods
            else "provider_period_not_observed_in_inventory"}


def save(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(path)


def tushare_income(values: dict, ticker: str, as_of: date) -> dict:
    token = values.get("TUSHARE_TOKEN")
    if not token:
        return {"status": "blocked", "reason": "missing_token"}
    try:
        response = requests.post("https://api.tushare.pro", json={
            "api_name": "income", "token": token,
            "params": {"ts_code": ticker, "start_date": "20250101", "end_date": as_of.strftime("%Y%m%d")},
            "fields": "ts_code,ann_date,f_ann_date,end_date,report_type",
        }, timeout=25)
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 0:
            # Provider messages can include request data; keep only the response code.
            return {"status": "error", "provider_code": payload.get("code"), "reason": "provider_rejected_request"}
        data = payload.get("data") or {}
        if not isinstance(data.get("fields"), list) or not isinstance(data.get("items"), list):
            raise ValueError("Missing Tushare table")
        rows = [dict(zip(data["fields"], row)) for row in data["items"]]
        valid = [r for r in rows if r.get("ts_code") == ticker and r.get("end_date")
                 and (r.get("f_ann_date") or r.get("ann_date") or "99999999") <= as_of.strftime("%Y%m%d")]
        latest = max(valid, key=lambda r: (r["end_date"], r.get("f_ann_date") or r.get("ann_date") or "")) if valid else None
        return {"status": "observed" if latest else "no_eligible_rows", "latest": latest, "row_count": len(rows), "rows": rows}
    except Exception as error:
        return {"status": "error", "error_type": type(error).__name__}


def write_summary(output: Path, snapshot: dict) -> None:
    as_of = date.fromisoformat(snapshot["as_of_beijing"])
    start = date.fromisoformat(snapshot["window_start"])
    roster = snapshot["roster"]
    events = snapshot["events"]
    events_complete = snapshot["events_read_complete"]
    inventories = snapshot["inventories"]
    candidates = snapshot["report_candidates"]
    absent = [c for c in candidates if c["event_status"] == "absent_from_primary_events"]
    lines = ["# Disclosure coverage audit — " + as_of.isoformat(), "",
             f"Read-only snapshot: {len(roster)} Active/Watch rows; unique tickers {snapshot['unique_tickers']}; markets {snapshot['market_counts']}.",
             f"Window: {start.isoformat()} through {as_of.isoformat()} (Beijing, inclusive).",
             f"Events read complete: {events_complete}; records {len(events)}. Inventory status: {snapshot['inventory_status_counts']}.",
             f"Formal report candidates: {len(candidates)} original URLs; absent from primary Events: {len(absent)}.", "",
             "| Market | Roster | Inventory completed | Errors/limits | Items |", "|---|---:|---:|---:|---:|"]
    for name, count in snapshot["market_counts"].items():
        rows = [r for r in inventories if r["company"]["market"] == name]
        completed = sum(r["health"]["status"] == "complete" for r in rows)
        lines.append(f"| {name} | {count} | {completed} | {count - completed} | {sum(len(r['items']) for r in rows)} |")
    lines += ["",
             "| Ticker | Status | Title | Original |", "|---|---|---|---|"]
    for c in candidates:
        lines.append(f"| {c['ticker']} | {c['event_status']} | {c['title'].replace('|', '/')} | {c['url']} |")
    lines += ["", "Source issues and limits:"]
    for r in inventories:
        if r["health"]["status"] != "complete":
            health = r["health"]
            detail = health.get("blocked_reason") or f"errors={health['errors']}, limits={health['limitations']}"
            lines.append(f"- {r['company']['ticker']} ({health['status']}) — {detail}")
    lines += ["- " + s for s in snapshot["limitations"]]
    lines += ["", "Independent Tushare income-period checks:", "", "| Ticker | Latest period | Publication | Inventory check |", "|---|---|---|---|"]
    for check in snapshot["financial_period_checks"]:
        latest = check.get("latest") or {}
        lines.append(f"| {check['ticker']} | {latest.get('end_date', '')} | {latest.get('f_ann_date') or latest.get('ann_date', '')} | {check['inventory_check']} |")
    (output / "summary.md").write_text("\n".join(lines) + "\n")


def run(args) -> int:
    values = dotenv_values(args.env_file)
    output = Path(args.output)
    as_of = date.fromisoformat(args.as_of) if args.as_of else beijing_date()
    start = as_of - timedelta(days=args.days)
    previous = {}
    if args.resume and (output / "audit.json").exists():
        previous = json.loads((output / "audit.json").read_text())
        if previous.get("window_start") != start.isoformat() or previous.get("window_end") != as_of.isoformat():
            raise ValueError("Checkpoint window does not match")
        if not (output / "audit-initial.json").exists():
            save(output / "audit-initial.json", previous)
            if (output / "summary.md").exists():
                (output / "summary-initial.md").write_text((output / "summary.md").read_text())
    snapshot = {"started_at_utc": datetime.now(timezone.utc).isoformat(), "as_of_beijing": as_of.isoformat(),
                "window_start": start.isoformat(), "window_end": as_of.isoformat(), "read_only": True,
                "scope": "All Active + Watch rows from configured Universe data source", "source_errors": []}
    if previous:
        snapshot["reused_cninfo_observation_started_at_utc"] = previous["started_at_utc"]
    # Authorization is passed only to the dedicated Notion /query calls.
    session = requests.Session()
    try:
        pages = read_source(session, values["NOTION_TOKEN"], values["RESEARCH_UNIVERSE_SOURCE_ID"], {"or": [
            {"property": "Status", "select": {"equals": "Active"}},
            {"property": "Status", "select": {"equals": "Watch"}},
        ]})
    except Exception as error:
        snapshot["source_errors"].append({"source": "Notion Universe", "error_type": type(error).__name__})
        save(output / "audit.json", snapshot)
        print("Universe read failed; evidence saved, no coverage claim.", flush=True)
        return 2
    roster = []
    for page in pages:
        props = page.get("properties", {})
        ticker = plain(props.get("Ticker", {}), "rich_text").strip()
        roster.append({"page_id": page["id"], "ticker": ticker, "name": plain(props.get("Name", {}), "title").strip(),
                       "status": (props.get("Status", {}).get("select") or {}).get("name"),
                       "declared_market": (props.get("Market", {}).get("select") or {}).get("name"),
                       "market": market(ticker) if ticker else "unresolved"})
    snapshot.update(roster=roster, universe_rows=len(roster), unique_tickers=len({r["ticker"] for r in roster if r["ticker"]}),
                    market_counts=dict(Counter(r["market"] for r in roster)), status_counts=dict(Counter(r["status"] for r in roster)))
    save(output / "audit.json", snapshot)
    print("Universe:", snapshot["universe_rows"], "rows; markets:", snapshot["market_counts"], flush=True)
    events, events_complete = [], False
    try:
        # Read all Events, not just same-day headlines: URL comparison needs complete history.
        event_pages = read_source(session, values["NOTION_TOKEN"], values["RESEARCH_EVENTS_SOURCE_ID"])
        for page in event_pages:
            props = page.get("properties", {})
            events.append({"id": page["id"], "ticker": plain(props.get("Ticker", {}), "rich_text"),
                           "event_type": (props.get("EventType", {}).get("select") or {}).get("name"),
                           "date": (props.get("EventDate", {}).get("date") or {}).get("start"),
                           "url": props.get("SourceURL", {}).get("url") or "",
                           "name": plain(props.get("Name", {}), "title"),
                           "fiscal_period": plain(props.get("FiscalPeriod", {}), "rich_text")})
        events_complete = True
    except Exception as error:
        snapshot["source_errors"].append({"source": "Notion Events", "error_type": type(error).__name__})
    snapshot.update(events=events, events_read_complete=events_complete, event_count=len(events))
    save(output / "audit.json", snapshot)
    logger.remove()
    logger.add(sys.stderr, level="ERROR")

    def audit_a(company):
        checkpoint = output / "inventory" / (company["ticker"] + ".json")
        if previous and checkpoint.exists():
            result = json.loads(checkpoint.read_text())
            if result.get("company", {}).get("name") == company["name"]:
                result["company"] = company
                result["reused_from_checkpoint"] = True
                print(company["ticker"], "reused observed inventory", flush=True)
                return result
        health = SourceHealth("CNINFO", company["ticker"])
        items = fetch_announcements(company["ticker"], days_back=args.days, download_pdf=False,
                                    keywords=[""], company_name=company["name"], health=health, as_of=as_of, max_pages=25)
        by_query = {}
        for record in health.requests:
            by_query.setdefault(record["searchkey"], set()).update(record.get("exact_ids_in_window", []))
        result = {"company": company, "health": health.to_dict(), "items": items,
                  "query_unique_exact_in_window": {k: len(v) for k, v in by_query.items()}}
        save(output / "inventory" / (company["ticker"] + ".json"), result)
        print(company["ticker"], health.status, len(items), "announcements", flush=True)
        return result

    inventories = []
    a_companies = {r["ticker"]: r for r in roster if r["market"] == "A"}
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(audit_a, r): r for r in a_companies.values()}
        for future in as_completed(futures):
            company = futures[future]
            try:
                inventories.append(future.result())
            except Exception as error:
                snapshot["source_errors"].append({"source": "CNINFO", "ticker": company["ticker"], "error_type": type(error).__name__})
    from data_ingestion.hkex_fetcher import fetch_hk_filings
    from data_ingestion import sec_fetcher
    for company in {r["ticker"]: r for r in roster if r["market"] in {"HK", "US", "NEEQ"}}.values():
        ticker = company["ticker"]
        health = SourceHealth({"HK": "HKEX", "US": "SEC", "NEEQ": "NEEQ"}[company["market"]], ticker)
        items = []
        if company["market"] == "NEEQ":
            health.blocked_reason = "neeq_adapter_unavailable"
        elif company["market"] == "US" and not values.get("SEC_USER_AGENT"):
            health.blocked_reason = "sec_user_agent_missing_or_invalid"
        else:
            try:
                if company["market"] == "HK":
                    items = fetch_hk_filings(ticker, doc_type="all", max_items=100, since_date=start.isoformat(),
                                            until_date=as_of.isoformat(), health=health)
                else:
                    # Use a supplied real contact only; never manufacture an SEC identity.
                    sec_fetcher._HEADERS = {**sec_fetcher._HEADERS, "User-Agent": values["SEC_USER_AGENT"]}
                    sec_fetcher.SEC_USER_AGENT = values["SEC_USER_AGENT"]
                    items = sec_fetcher.fetch_sec_filings(ticker, ["10-K", "20-F", "10-Q", "8-K", "6-K"], max_items=100,
                                                         since_date=start.isoformat(), health=health)
            except Exception as error:
                health.fail("adapter", error)
        items = [item for item in items if start.isoformat() <= item.get("date", "") <= as_of.isoformat()]
        health.finish(len(items))
        result = {"company": company, "health": health.to_dict(), "items": items}
        inventories.append(result)
        save(output / "inventory" / (ticker + ".json"), result)
        print(ticker, health.status, len(items), "filings", flush=True)
    if args.financials:
        financials = []
        prior_financials = {r["ticker"]: r for r in previous.get("financials", [])}
        for company in a_companies.values():
            result = prior_financials.get(company["ticker"]) or {"ticker": company["ticker"], **tushare_income(values, company["ticker"], as_of)}
            financials.append(result)
            save(output / "financials.json", financials)
            print(company["ticker"], "Tushare", result["status"], flush=True)
            if company["ticker"] not in prior_financials:
                time.sleep(0.8)
        snapshot["financials"] = financials
    snapshot["inventories"] = sorted(inventories, key=lambda r: r["company"]["ticker"])
    snapshot["report_candidates"] = compare_reports([item for r in inventories for item in r["items"]], events, events_complete)
    snapshot["financial_period_checks"] = [financial_comparison(row, snapshot["report_candidates"], start.isoformat())
                                           for row in snapshot.get("financials", [])]
    snapshot["inventory_status_counts"] = dict(Counter(r["health"]["status"] for r in inventories))
    snapshot["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    snapshot["limitations"] = ["An empty search inventory is an observation, not proof of no announcements.",
                               "Only publication dates in the specified Beijing window were audited.",
                               "Candidate absence refers to primary Events by ticker + original URL, not news headlines.",
                               "No PDFs/model analysis or live backfills were performed."]
    save(output / "audit.json", snapshot)
    write_summary(output, snapshot)
    candidates = snapshot["report_candidates"]
    absent = [c for c in candidates if c["event_status"] == "absent_from_primary_events"]
    print("Audit saved:", output, "candidates", len(candidates), "absent", len(absent), flush=True)
    return 2 if snapshot["source_errors"] or any(r["health"]["status"] != "complete" for r in inventories) else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--as-of")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--financials", action="store_true")
    parser.add_argument("--resume", action="store_true", help="Reuse matching CNINFO/financial observations; refresh roster, Events and HKEX")
    raise SystemExit(run(parser.parse_args()))
