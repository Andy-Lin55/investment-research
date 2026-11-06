from unittest.mock import Mock

import pytest
import requests

from data_ingestion.disclosure_health import SourceHealth, SourceFetchError


def test_neeq_numeric_ticker_is_not_silently_routed_to_sec(monkeypatch):
    from data_ingestion import filings_router

    fetchers = []
    for name in ["fetch_annual_report", "fetch_quarterly_reports", "fetch_earnings_releases", "fetch_sec_filings"]:
        fetcher = Mock(return_value=[])
        monkeypatch.setattr(filings_router, name, fetcher)
        fetchers.append(fetcher)
    nee_fetcher = Mock(return_value=[{"ticker": "830806.NQ"}])
    monkeypatch.setattr(filings_router, "fetch_neeq_filings", nee_fetcher)
    assert filings_router.fetch_filings("830806.NQ") == [{"ticker": "830806.NQ"}]
    nee_fetcher.assert_called_once()
    for fetcher in fetchers:
        fetcher.assert_not_called()


def test_hk_mapping_failure_has_explicit_health(monkeypatch):
    from data_ingestion import hkex_fetcher as hk

    monkeypatch.setattr(hk, "REQUEST_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(hk, "_get_stock_index", lambda code: None)
    health = SourceHealth("HKEX", "0700.HK")
    with pytest.raises(SourceFetchError):
        hk.fetch_hk_filings("0700.HK", health=health, strict=True)
    assert health.status == "error"


def test_hk_request_failure_is_not_empty_success(monkeypatch):
    from data_ingestion import hkex_fetcher as hk

    monkeypatch.setattr(hk, "REQUEST_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(hk, "_get_stock_index", lambda code: 700)
    monkeypatch.setattr(hk.requests, "get", Mock(side_effect=requests.Timeout("offline")))
    health = SourceHealth("HKEX", "0700.HK")
    with pytest.raises(SourceFetchError):
        hk.fetch_hk_filings("0700.HK", health=health, strict=True)
    assert health.errors


def test_sec_placeholder_contact_does_not_claim_empty_inventory(monkeypatch):
    from data_ingestion import sec_fetcher as sec

    monkeypatch.setattr(sec, "SEC_USER_AGENT", "InvestmentResearch research@invest.com")
    network = Mock()
    monkeypatch.setattr(sec.requests, "get", network)
    health = SourceHealth("SEC", "QFIN.O")
    with pytest.raises(SourceFetchError):
        sec.fetch_sec_filings("QFIN.O", ["20-F", "6-K"], health=health, strict=True)
    network.assert_not_called()
    assert health.status == "blocked"
    assert health.blocked_reason == "sec_user_agent_missing_or_invalid"
    assert not health.errors


def test_daily_roster_failure_can_be_explicit_instead_of_no_active_stocks(monkeypatch):
    from notion_sync import sync_manager

    client = Mock()
    client.databases.query.side_effect = RuntimeError("blocked")
    monkeypatch.setattr(sync_manager, "get_client", lambda: client)
    with pytest.raises(SourceFetchError):
        sync_manager.get_daily_tickers(strict=True)


def test_hk_all_covers_quarterly_outside_old_categories_and_both_languages(monkeypatch):
    import json
    from data_ingestion import hkex_fetcher as hk

    monkeypatch.setattr(hk, "REQUEST_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(hk, "_get_stock_index", lambda code: 12208)
    report = {"STOCK_CODE": "03968", "TITLE": "THIRD QUARTERLY REPORT OF 2026",
              "DATE_TIME": "30/10/2026 18:22", "FILE_LINK": "/listedco/listconews/sehk/2026/1030/report.pdf"}
    other = {**report, "STOCK_CODE": "00700", "FILE_LINK": "/other.pdf"}

    def query(url, *, params, **kwargs):
        rows = [report, other] if params["t2code"] == -2 else []
        return Mock(json=lambda: {"result": json.dumps(rows), "hasNextRow": False, "recordCnt": len(rows)})

    network = Mock(side_effect=query)
    monkeypatch.setattr(hk.requests, "get", network)
    health = SourceHealth("HKEX", "3968.HK")
    rows = hk.fetch_hk_filings("3968.HK", since_date="2026-10-26", until_date="2026-11-02", health=health)
    assert len(rows) == 1
    assert rows[0]["doc_type"] == "季度报告"
    assert {c.kwargs["params"]["lang"] for c in network.call_args_list} == {"en", "zh"}
    assert health.status == "complete"


def test_hk_server_more_rows_flag_is_explicitly_limited(monkeypatch):
    from data_ingestion import hkex_fetcher as hk

    monkeypatch.setattr(hk, "REQUEST_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(hk, "_get_stock_index", lambda code: 700)
    monkeypatch.setattr(hk.requests, "get", Mock(return_value=Mock(json=lambda: {
        "result": "[]", "hasNextRow": True, "recordCnt": 15,
    })))
    health = SourceHealth("HKEX", "0700.HK")
    with pytest.raises(SourceFetchError):
        hk.fetch_hk_filings("0700.HK", health=health, strict=True)
    assert health.status == "limited"


def test_a_share_router_honors_strict_source_health(monkeypatch):
    from data_ingestion import filings_router, cninfo_fetcher

    monkeypatch.setattr(cninfo_fetcher, "REQUEST_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(cninfo_fetcher.time, "sleep", lambda _: None)
    monkeypatch.setattr(cninfo_fetcher.requests, "post", Mock(side_effect=requests.Timeout("offline")))
    with pytest.raises(SourceFetchError):
        filings_router.fetch_filings("002475.SZ", strict=True)


@pytest.mark.parametrize("title", ["RESULTS OF ANNUAL GENERAL MEETING", "INTERIM DIVIDEND", "QUARTERLY CONNECTED TRANSACTIONS"])
def test_hk_non_financial_notices_are_not_formal_report_candidates(monkeypatch, title):
    import json
    from data_ingestion import hkex_fetcher as hk

    monkeypatch.setattr(hk, "REQUEST_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(hk, "_get_stock_index", lambda code: 700)
    row = {"STOCK_CODE": "00700", "TITLE": title, "DATE_TIME": "30/10/2026 18:00", "FILE_LINK": "/notice.pdf"}
    monkeypatch.setattr(hk.requests, "get", Mock(return_value=Mock(json=lambda: {"result": json.dumps([row]), "hasNextRow": False})))
    assert hk.fetch_hk_filings("0700.HK", since_date="2026-10-26", until_date="2026-11-02")[0]["doc_type"] == "公告"


def test_daily_roster_missing_results_is_not_clean_empty(monkeypatch):
    from notion_sync import sync_manager

    client = Mock()
    client.databases.query.return_value = {}
    monkeypatch.setattr(sync_manager, "get_client", lambda: client)
    with pytest.raises(SourceFetchError):
        sync_manager.get_daily_tickers(strict=True)


def test_hk_timeout_retries_one_request_before_marking_a_gap(monkeypatch):
    from data_ingestion import hkex_fetcher as hk

    monkeypatch.setattr(hk, "_get_stock_index", lambda code: 123)
    monkeypatch.setattr(hk, "REQUEST_INTERVAL_SECONDS", 0)
    success = Mock()
    success.json.return_value = {"result": [], "hasNextRow": False}
    network = Mock(side_effect=[requests.ReadTimeout(), success, success])
    monkeypatch.setattr(hk.requests, "get", network)
    health = SourceHealth("HKEX", "1658.HK")

    assert hk.fetch_hk_filings("1658.HK", health=health, strict=True) == []
    assert health.successful_requests == 2
    assert [call.kwargs["params"]["lang"] for call in network.call_args_list] == ["en", "en", "zh"]


def test_cninfo_retries_only_the_failed_page_without_losing_coverage(monkeypatch):
    import requests
    from data_ingestion import cninfo_fetcher
    from data_ingestion.disclosure_health import SourceHealth

    monkeypatch.setattr(cninfo_fetcher.time, "sleep", lambda _: None)
    error = requests.Response()
    error.status_code = 502
    first = Mock()
    first.json.return_value = {"announcements": [{"announcementId": "1", "secCode": "other"}], "hasMore": True}
    second = Mock()
    second.json.return_value = {"announcements": [{"announcementId": "2", "secCode": "other"}], "hasMore": False}
    post = Mock(side_effect=[first, error, second])
    monkeypatch.setattr(cninfo_fetcher.requests, "post", post)
    health = SourceHealth("CNINFO", "601318.SH")

    assert cninfo_fetcher.fetch_announcements("601318.SH", keywords=["业绩预告"],
                                             download_pdf=False, health=health, strict=True) == []
    assert [call.kwargs["data"]["pageNum"] for call in post.call_args_list] == [1, 2, 2]
    assert health.status == "complete" and health.successful_requests == 2
    assert health.requests[1]["attempts"] == 2


def test_cninfo_keeps_a_terminal_http_error_visible_without_retrying(monkeypatch):
    import requests
    from data_ingestion import cninfo_fetcher
    from data_ingestion.disclosure_health import SourceHealth

    monkeypatch.setattr(cninfo_fetcher.time, "sleep", lambda _: None)
    response = requests.Response()
    response.status_code = 403
    post = Mock(return_value=response)
    monkeypatch.setattr(cninfo_fetcher.requests, "post", post)
    health = SourceHealth("CNINFO", "601318.SH")

    cninfo_fetcher.fetch_announcements("601318.SH", keywords=["业绩预告"], download_pdf=False, health=health)
    assert health.status == "error"
    assert health.errors[0]["http_status"] == 403
    assert post.call_count == 1
