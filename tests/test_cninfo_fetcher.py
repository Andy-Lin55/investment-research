from datetime import date, datetime
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
    monkeypatch.setattr(cninfo_fetcher, "beijing_date", lambda: date(2026, 11, 2))
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


def test_company_name_fallback_recovers_exact_filing_after_empty_code_query(source):
    post, download = source
    report = announcement(identifier="1225636692")
    report["adjunctUrl"] = "finalpage/2026-10-29/1225636692.PDF"
    post.side_effect = [Mock(json=lambda: {"announcements": None}),
                        Mock(json=lambda: {"announcements": [report]})]

    reports = cninfo_fetcher.fetch_announcements(
        "301004.SZ", days_back=7, keywords=["季度报告"], company_name="嘉益股份",
    )

    assert len(reports) == 1
    assert reports[0]["url"] == "http://static.cninfo.com.cn/finalpage/2026-10-29/1225636692.PDF"
    assert [c.kwargs["data"]["searchkey"] for c in post.call_args_list] == [
        "301004 季度报告", "嘉益股份 季度报告",
    ]
    assert post.call_args_list[0].kwargs["data"]["seDate"] == post.call_args_list[1].kwargs["data"]["seDate"]
    download.assert_called_once()


def test_wrong_ticker_hits_do_not_prevent_fallback_and_are_never_returned(source):
    post, download = source
    other = announcement(code="000001", identifier="other")
    post.side_effect = [Mock(json=lambda: {"announcements": [other]}),
                        Mock(json=lambda: {"announcements": [other, announcement()]})]

    reports = cninfo_fetcher.fetch_announcements(
        "301004.SZ", keywords=["季度报告"], company_name="嘉益股份",
    )

    assert len(reports) == 1
    assert reports[0]["ticker"] == "301004.SZ"
    assert post.call_count == 2
    download.assert_called_once()


def test_company_name_fallback_with_only_wrong_tickers_remains_empty(source):
    post, download = source
    post.side_effect = [Mock(json=lambda: {"announcements": None}),
                        Mock(json=lambda: {"announcements": [announcement(code="000001")]})]

    assert cninfo_fetcher.fetch_announcements(
        "301004.SZ", keywords=["季度报告"], company_name="嘉益股份",
    ) == []
    assert post.call_count == 2
    download.assert_not_called()


def test_code_and_name_queries_are_merged_even_when_code_already_has_hits(source):
    post, download = source
    post.return_value = Mock(json=lambda: {"announcements": [announcement()]})

    reports = cninfo_fetcher.fetch_announcements(
        "301004.SZ", keywords=["第三季度报告", "季度报告"], company_name="嘉益股份",
    )

    assert len(reports) == 1
    assert [c.kwargs["data"]["searchkey"] for c in post.call_args_list] == [
        "301004 第三季度报告", "嘉益股份 第三季度报告", "301004 季度报告", "嘉益股份 季度报告",
    ]
    download.assert_called_once()


def test_fallback_is_per_keyword_and_duplicate_name_hits_download_once(source):
    post, download = source

    def query(url, *, data, **kwargs):
        hits = None if data["searchkey"].startswith("301004 ") else [announcement()]
        return Mock(json=lambda: {"announcements": hits})

    post.side_effect = query

    reports = cninfo_fetcher.fetch_announcements(
        "301004.SZ", keywords=["第三季度报告", "季度报告"], company_name="嘉益股份",
    )

    assert len(reports) == 1
    assert [c.kwargs["data"]["searchkey"] for c in post.call_args_list] == [
        "301004 第三季度报告", "嘉益股份 第三季度报告", "301004 季度报告", "嘉益股份 季度报告",
    ]
    download.assert_called_once()


@pytest.mark.parametrize("name", [None, "", "  "])
def test_no_company_name_means_no_guessed_fallback(source, name):
    post, download = source
    post.return_value = Mock(json=lambda: {"announcements": None})

    assert cninfo_fetcher.fetch_announcements(
        "301004.SZ", keywords=["季度报告"], company_name=name,
    ) == []
    assert [c.kwargs["data"]["searchkey"] for c in post.call_args_list] == ["301004 季度报告"]
    download.assert_not_called()


def test_batch_optional_names_recover_reports_without_changing_legacy_calls(source):
    post, download = source

    def query(url, *, data, **kwargs):
        report = announcement(title="嘉益股份：重大合同公告")
        report["announcementTime"] = int(datetime(2026, 10, 30, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp() * 1000)
        hits = [report] if data["searchkey"] == "嘉益股份 重大合同" else None
        return Mock(json=lambda: {"announcements": hits})

    post.side_effect = query
    # Existing callers can omit names and still receive the same result shape.
    assert cninfo_fetcher.batch_fetch(["301004.SZ", "AAPL"], days_back=3) == {"301004.SZ": [], "AAPL": []}

    reports = cninfo_fetcher.batch_fetch(
        ["301004.SZ", "AAPL"], days_back=3, company_names={"301004.SZ": "嘉益股份"},
    )
    assert len(reports["301004.SZ"]) == 1
    assert reports["301004.SZ"][0]["title"] == "嘉益股份：重大合同公告"
    assert reports["AAPL"] == []
    download.assert_called_once()


def test_periodic_optional_name_recovers_filing_without_changing_legacy_calls(source):
    post, download = source

    def query(url, *, data, **kwargs):
        hits = [announcement()] if data["searchkey"] == "嘉益股份 季度报告" else None
        return Mock(json=lambda: {"announcements": hits})

    post.side_effect = query
    assert cninfo_fetcher.fetch_periodic_reports("301004.SZ", days_back=7) == []
    reports = cninfo_fetcher.fetch_periodic_reports("301004.SZ", days_back=7, company_name="嘉益股份")
    assert len(reports) == 1
    download.assert_called_once()
