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
    assert filings_router.fetch_filings("830806.NQ") == []
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
