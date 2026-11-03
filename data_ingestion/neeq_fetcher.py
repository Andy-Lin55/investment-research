"""Fetch listed-company originals from the official NEEQ disclosure inventory."""
from datetime import date, timedelta
import json
import re

import requests

from data_ingestion.disclosure_health import PaginationError, SourceHealth, beijing_date


BASE_URL = "https://www.neeq.com.cn"
QUERY_URL = BASE_URL + "/disclosureInfoController/infoResult.do"


def _document_type(title: str) -> str:
    if "摘要" not in title:
        if re.search(r"季度报告|季报", title):
            return "季度报告"
        if re.search(r"半年度报告|中期报告", title):
            return "中期报告"
        if "年度报告" in title:
            return "年度报告"
    return "公告"


def fetch_neeq_filings(
    ticker: str, *, since_date: str | None = None, until_date: str | None = None,
    max_items: int = 100, doc_type: str = "all", health: SourceHealth | None = None,
    strict: bool = False,
) -> list[dict]:
    health = health if health is not None else SourceHealth("NEEQ", ticker)
    end = date.fromisoformat(until_date) if until_date else beijing_date()
    start = date.fromisoformat(since_date) if since_date else end - timedelta(days=7)
    code = ticker.removesuffix(".NQ")
    if not re.fullmatch(r"\d{6}", code) or start > end or max_items < 1:
        raise ValueError("Invalid NEEQ ticker, filing window or item limit")
    requested_type = {"annual": "年度报告", "interim": "中期报告", "quarterly": "季度报告"}.get(doc_type)
    result = []
    seen = set()
    try:
        for page in range(50):
            response = requests.post(
                QUERY_URL,
                data={"page": page, "companyCd": code, "startTime": start.isoformat(),
                      "endTime": end.isoformat(), "disclosureType": "5", "keyword": ""},
                headers={"User-Agent": "Mozilla/5.0", "Referer": BASE_URL + "/disclosure/announcement.html"},
                timeout=15,
            )
            response.raise_for_status()
            wrapper = re.fullmatch(r"null\((.*)\);?\s*", response.text, re.DOTALL)
            if not wrapper:
                raise ValueError("Invalid NEEQ response wrapper")
            payload, = json.loads(wrapper[1])
            if payload.get("status") != 0:
                raise ValueError("NEEQ did not accept the disclosure query")
            batch = payload["listInfo"]
            rows = batch["content"]
            if batch["number"] != page or not isinstance(rows, list) or type(batch["lastPage"]) is not bool:
                raise PaginationError("Invalid NEEQ page")
            health.successful_requests += 1
            for row in rows:
                filing_date = date.fromisoformat(row["publishDate"])
                path = row["destFilePath"]
                if row["companyCd"] != code or not start <= filing_date <= end:
                    raise ValueError("NEEQ filing outside requested issuer or date window")
                if not re.fullmatch(r"/disclosure/\d{4}/\d{4}-\d{2}-\d{2}/[A-Za-z0-9_-]+\.pdf", path, re.I):
                    raise ValueError("Invalid NEEQ original document path")
                if path in seen:
                    raise PaginationError("Repeated NEEQ original")
                seen.add(path)
                source_title = row["disclosureTitle"]
                title = re.sub(r"^\[[^\]]+\]", "", source_title).strip()
                is_periodic = source_title.startswith("[定期报告]") or row.get("disclosureType") == "9503"
                kind = _document_type(title) if is_periodic else "公告"
                if doc_type == "all" or kind == requested_type:
                    result.append({"ticker": ticker, "title": title, "doc_type": kind, "fiscal_period": "",
                                   "date": filing_date.isoformat(), "url": BASE_URL + path, "pdf_path": None})
            if batch["lastPage"]:
                if len(seen) != batch["totalElements"]:
                    raise PaginationError("NEEQ total differs from observed originals")
                break
            if not rows:
                raise PaginationError("Empty NEEQ page before end")
        else:
            health.limitations.append("NEEQ page budget exhausted")
    except (requests.RequestException, ValueError, KeyError, TypeError, PaginationError) as error:
        health.fail("disclosure_inventory", error)
    if len(result) > max_items:
        health.limitations.append("NEEQ result limit reached")
        result = result[:max_items]
    health.finish(len(result), strict)
    return result
