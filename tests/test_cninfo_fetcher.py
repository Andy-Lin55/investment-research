from datetime import datetime
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

from data_ingestion import cninfo_fetcher


def announcement(title="嘉益股份：2026年三季度报告", code="301004", identifier="q3"):
    return {
        "announcementId": identifier,
        "secCode": code,
        "announcementTitle": title,
        "announcementTime": int(datetime(2026, 10, 29, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp() * 1000),
        "adjunctUrl": "finalpage/2026-10-29/q3.PDF",
    }


@pytest.fixture
def source(monkeypatch):
    monkeypatch.setattr(cninfo_fetcher, "REQUEST_INTERVAL_SECONDS", 0)
    download = Mock(return_value=True)
    monkeypatch.setattr(cninfo_fetcher, "_download_pdf", download)
    post = Mock()
    monkeypatch.setattr(cninfo_fetcher.requests, "post", post)
    return post, download


@pytest.mark.parametrize("title", [
    "嘉益股份：2026年三季度报告",
    "嘉益股份：2026年第三季度报告",
    "嘉益股份：2026年第一季度报告",
])
def test_periodic_search_captures_quarterly_title_variants_once(source, title):
    post, download = source
    report = announcement(title)

    def query(url, *, data, **kwargs):
        keyword = data["searchkey"].split(" ", 1)[1]
        return Mock(json=lambda: {"announcements": [report] if keyword in title else None})

    post.side_effect = query

    reports = cninfo_fetcher.fetch_periodic_reports("301004.SZ", days_back=7)

    assert len(reports) == 1
    assert reports[0]["title"] == title
    assert reports[0]["ticker"] == "301004.SZ"
    assert reports[0]["date"] == "2026-10-29"
    assert reports[0]["url"] == "http://static.cninfo.com.cn/finalpage/2026-10-29/q3.PDF"
    assert reports[0]["pdf_path"].endswith(".pdf")
    download.assert_called_once()


def test_null_announcements_are_empty_without_query_error(source, monkeypatch):
    post, download = source
    post.return_value = Mock(json=lambda: {"announcements": None})
    error = Mock()
    monkeypatch.setattr(cninfo_fetcher.logger, "error", error)

    assert cninfo_fetcher.fetch_periodic_reports("301004.SZ", days_back=7) == []
    error.assert_not_called()
    download.assert_not_called()


def test_search_excludes_other_stocks_and_deduplicates_keyword_hits(source):
    post, download = source
    post.return_value = Mock(json=lambda: {"announcements": [
        announcement(code="000001", identifier="other"), announcement(), announcement(),
    ]})

    reports = cninfo_fetcher.fetch_periodic_reports("301004.SZ", days_back=7)

    assert len(reports) == 1
    assert reports[0]["ticker"] == "301004.SZ"
    download.assert_called_once()


def test_non_a_share_does_not_query_cninfo(source):
    post, download = source
    assert cninfo_fetcher.fetch_periodic_reports("AAPL", days_back=7) == []
    post.assert_not_called()
    download.assert_not_called()
