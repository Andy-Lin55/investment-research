from datetime import datetime, timezone
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest
import requests

from data_ingestion import cninfo_fetcher as cninfo


def ann(identifier, day="2026-10-29", code="002475"):
    return {
        "announcementId": str(identifier), "secCode": code,
        "announcementTitle": f"立讯精密：2026年三季度报告 {identifier}",
        "announcementTime": int(datetime.fromisoformat(day).replace(tzinfo=ZoneInfo("Asia/Shanghai")).timestamp() * 1000),
        "adjunctUrl": f"finalpage/{day}/{identifier}.PDF",
    }


@pytest.fixture
def source(monkeypatch):
    monkeypatch.setattr(cninfo, "beijing_date", lambda: datetime(2026, 11, 2).date())
    monkeypatch.setattr(cninfo, "REQUEST_INTERVAL_SECONDS", 0)
    post = Mock()
    monkeypatch.setattr(cninfo.requests, "post", post)
    return post


def fetch(**kwargs):
    return cninfo.fetch_announcements("002475.SZ", days_back=7, download_pdf=False, keywords=[""], **kwargs)


def test_partial_code_index_is_merged_with_name_results(source):
    def query(url, *, data, **kwargs):
        rows = [ann(n) for n in range(4 if data["searchkey"].strip() == "002475" else 12)]
        rows.append(ann("other", code="600519"))
        return Mock(json=lambda: {"announcements": rows, "hasMore": False})

    source.side_effect = query
    reports = fetch(company_name="立讯精密")

    assert len(reports) == 12
    assert len({item["url"] for item in reports}) == 12
    assert [c.kwargs["data"]["searchkey"].strip() for c in source.call_args_list] == ["002475", "立讯精密"]


def test_pagination_uses_has_more_and_total_even_when_totalpages_is_wrong(source):
    def query(url, *, data, **kwargs):
        rows = [ann(n) for n in range(20)] if data["pageNum"] == 1 else [ann(n) for n in range(20, 27)]
        return Mock(json=lambda: {"announcements": rows, "hasMore": data["pageNum"] == 1,
                                  "totalAnnouncement": 27, "totalpages": 1})

    source.side_effect = query
    reports = fetch()

    assert len(reports) == 27
    assert [c.kwargs["data"]["pageNum"] for c in source.call_args_list] == [1, 2]


def test_date_bounds_reject_old_future_and_unknown_publication_dates(source):
    source.return_value = Mock(json=lambda: {"announcements": [
        ann("old", "2026-10-25"), ann("start", "2026-10-26"),
        ann("today", "2026-11-02"), ann("future", "2026-11-03"),
    ]})
    reports = fetch(as_of=datetime(2026, 11, 2).date())
    assert [r["date"] for r in reports] == ["2026-10-26", "2026-11-02"]
    assert source.call_args.kwargs["data"]["seDate"] == "2026-10-26~2026-11-02"


def test_beijing_date_at_utc_boundary():
    from data_ingestion.disclosure_health import beijing_date

    assert beijing_date(datetime(2026, 11, 1, 21, tzinfo=timezone.utc)).isoformat() == "2026-11-02"


def test_source_error_is_not_a_successful_empty_inventory(source):
    from data_ingestion.disclosure_health import SourceHealth, SourceFetchError

    source.side_effect = requests.ConnectionError("offline")
    health = SourceHealth("CNINFO", "002475.SZ")
    with pytest.raises(SourceFetchError):
        fetch(health=health, strict=True)
    assert health.status == "error"
    assert health.errors[0]["error_type"] == "ConnectionError"


def test_one_failed_query_keeps_other_results_but_reports_partial(source):
    from data_ingestion.disclosure_health import SourceHealth

    source.side_effect = [requests.ConnectionError("code index unavailable"),
                          Mock(json=lambda: {"announcements": [ann("report")]})]
    health = SourceHealth("CNINFO", "002475.SZ")
    reports = fetch(company_name="立讯精密", health=health)
    assert len(reports) == 1
    assert health.status == "partial"
    assert len(health.errors) == 1


@pytest.mark.parametrize("payload", [
    {"message": "blocked"},
    {"announcements": None, "totalAnnouncement": 4},
    {"announcements": "not a list"},
])
def test_invalid_response_cannot_report_clean_empty(source, payload):
    from data_ingestion.disclosure_health import SourceHealth, SourceFetchError

    source.return_value = Mock(json=lambda: payload)
    health = SourceHealth("CNINFO", "002475.SZ")
    with pytest.raises(SourceFetchError):
        fetch(health=health, strict=True)
    assert health.errors


def test_repeated_page_preserves_first_page_but_reports_incomplete(source):
    from data_ingestion.disclosure_health import SourceHealth

    source.return_value = Mock(json=lambda: {"announcements": [ann(n) for n in range(20)], "hasMore": True})
    health = SourceHealth("CNINFO", "002475.SZ")
    assert len(fetch(health=health)) == 20
    assert health.status == "partial"
    assert source.call_count == 2


def test_page_limit_is_explicitly_incomplete(source):
    from data_ingestion.disclosure_health import SourceHealth

    source.return_value = Mock(json=lambda: {"announcements": [ann(n) for n in range(20)], "hasMore": True})
    health = SourceHealth("CNINFO", "002475.SZ")
    assert len(fetch(health=health, max_pages=1)) == 20
    assert health.status == "partial"
    assert any(e["error_type"] == "PaginationError" for e in health.errors)


def test_clean_empty_is_distinct_from_source_error(source):
    from data_ingestion.disclosure_health import SourceHealth

    source.return_value = Mock(json=lambda: {"announcements": None, "totalAnnouncement": 0, "hasMore": False})
    health = SourceHealth("CNINFO", "002475.SZ")
    assert fetch(health=health, strict=True) == []
    assert health.status == "complete"


def test_batch_exposes_per_ticker_failures_without_changing_result_shape(source):
    from data_ingestion.disclosure_health import SourceFetchError

    source.side_effect = requests.Timeout("offline")
    health = {}
    assert cninfo.batch_fetch(["002475.SZ"], health=health) == {"002475.SZ": []}
    assert health["002475.SZ"].status == "error"
    with pytest.raises(SourceFetchError):
        cninfo.batch_fetch(["002475.SZ"], strict=True)


def test_bad_document_row_is_explicit_but_does_not_discard_other_rows(source):
    from data_ingestion.disclosure_health import SourceHealth

    missing_url = {**ann("valid"), "adjunctUrl": ""}
    invalid_date = {**ann("invalid"), "announcementTime": "invalid"}
    source.return_value = Mock(json=lambda: {"announcements": [missing_url, invalid_date, ann("valid")]})
    health = SourceHealth("CNINFO", "002475.SZ")
    reports = fetch(health=health)
    assert len(reports) == 1
    assert reports[0]["url"].endswith("valid.PDF")
    assert health.status == "partial"
    assert len(health.errors) == 2


def test_overlapping_pages_cannot_claim_the_reported_total_was_collected(source):
    from data_ingestion.disclosure_health import SourceHealth

    source.side_effect = [Mock(json=lambda: {"announcements": [ann(n) for n in range(20)], "hasMore": True, "totalAnnouncement": 22}),
                          Mock(json=lambda: {"announcements": [ann(19), ann(20)], "hasMore": False, "totalAnnouncement": 22})]
    health = SourceHealth("CNINFO", "002475.SZ")
    assert len(fetch(health=health)) == 21
    assert health.status == "partial"
    assert health.errors[0]["error_type"] == "PaginationError"
