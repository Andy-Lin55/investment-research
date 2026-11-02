from unittest.mock import Mock

from notion_sync import sync_manager


def company(ticker, title):
    return {"properties": {
        "Ticker": {"rich_text": [{"plain_text": ticker}]},
        "Name": {"title": [{"plain_text": part} for part in title]},
    }}


def test_daily_roster_supplies_verified_names_without_per_ticker_queries(monkeypatch):
    client = Mock()
    pages = [company("301004.SZ", ["嘉益", "股份"]), company("000001.SZ", [])]
    pages += [company(f"{n:06d}.SH", [f"公司{n}"]) for n in range(600000, 600042)]
    client.databases.query.return_value = {"results": pages, "has_more": False}
    monkeypatch.setattr(sync_manager, "get_client", lambda: client)
    names = {}

    tickers = sync_manager.get_daily_tickers(company_names=names)

    assert len(tickers) == 44
    assert names["301004.SZ"] == "嘉益股份"
    assert names["000001.SZ"] == ""
    assert names["600041.SH"] == "公司600041"
    assert tickers[:2] == ["301004.SZ", "000001.SZ"]
    client.databases.query.assert_called_once()
    assert client.databases.query.call_args.kwargs["filter"] == {"or": [
        {"property": "Status", "select": {"equals": "Active"}},
        {"property": "Status", "select": {"equals": "Watch"}},
    ]}
    client.pages.create.assert_not_called()
    client.pages.update.assert_not_called()


def test_daily_roster_names_and_tickers_follow_same_pagination(monkeypatch):
    client = Mock()
    client.databases.query.side_effect = [
        {"results": [company("301004.SZ", ["嘉益股份"])], "has_more": True, "next_cursor": "page2"},
        {"results": [company("600519.SH", ["贵州茅台"])], "has_more": False},
    ]
    monkeypatch.setattr(sync_manager, "get_client", lambda: client)
    names = {}

    assert sync_manager.get_daily_tickers(company_names=names) == ["301004.SZ", "600519.SH"]
    assert names == {"301004.SZ": "嘉益股份", "600519.SH": "贵州茅台"}
    assert client.databases.query.call_count == 2
    assert client.databases.query.call_args.kwargs["start_cursor"] == "page2"


def test_daily_roster_legacy_call_still_returns_ticker_list(monkeypatch):
    client = Mock()
    client.databases.query.return_value = {
        "results": [company("301004.SZ", ["嘉益股份"]), company("AAPL", ["Apple"])], "has_more": False,
    }
    monkeypatch.setattr(sync_manager, "get_client", lambda: client)

    assert sync_manager.get_daily_tickers() == ["301004.SZ", "AAPL"]
    client.databases.query.assert_called_once()
