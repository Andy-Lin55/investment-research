from unittest.mock import Mock

import pytest
from notion_sync import sync_manager as sync


def page(ticker):
    return {"properties": {"Ticker": {"rich_text": [{"plain_text": ticker}]}}}


@pytest.mark.parametrize("function,statuses", [
    (sync.get_active_tickers, ["Active"]),
    (sync.get_daily_tickers, ["Active", "Watch"]),
    (sync.get_all_tickers, ["Coverage", "Active", "Watch"]),
])
def test_universe_selection_keeps_status_contract_and_all_pages(monkeypatch, function, statuses):
    client = Mock()
    client.databases.query.side_effect = [
        {"results": [page("600519.SH")], "has_more": True, "next_cursor": "page2"},
        {"results": [page("0700.HK"), {"properties": {}}], "has_more": False},
    ]
    monkeypatch.setattr(sync, "get_client", lambda: client)
    assert function() == ["600519.SH", "0700.HK"]
    rule = client.databases.query.call_args_list[0].kwargs["filter"]
    filters = rule.get("or", [rule])
    assert [item["select"]["equals"] for item in filters] == statuses
    assert client.databases.query.call_args.kwargs["start_cursor"] == "page2"


def test_empty_active_universe_returns_empty_list(monkeypatch):
    client = Mock()
    client.databases.query.return_value = {"results": [], "has_more": False}
    monkeypatch.setattr(sync, "get_client", lambda: client)
    assert sync.get_active_tickers() == []


def test_duplicate_source_is_not_published_again(monkeypatch):
    client = Mock()
    client.databases.query.return_value = {"results": [{"id": "old-event"}]}
    monkeypatch.setattr(sync, "get_client", lambda: client)
    assert sync.create_event("600519.SH", {"event_type": "公告", "event_date": "2026-10-27",
                                          "source_url": "https://example.invalid/report.pdf"}) is None
    client.pages.create.assert_not_called()


def test_new_event_preserves_numbers_and_original_source(monkeypatch):
    client = Mock()
    client.databases.query.return_value = {"results": []}
    client.pages.create.return_value = {"id": "new-event"}
    monkeypatch.setattr(sync, "get_client", lambda: client)
    result = sync.create_event("600519.SH", {
        "event_type": "季度报告", "event_date": "2026-10-27", "importance": "Medium",
        "one_line_summary": "收入100，现金流20", "key_numbers": {"收入": 100},
        "source_url": "https://example.invalid/report.pdf",
    })
    assert result == "new-event"
    properties = client.pages.create.call_args.kwargs["properties"]
    assert properties["SourceURL"]["url"] == "https://example.invalid/report.pdf"
    assert "100" in str(properties["KeyNumbers"])
    assert properties["EventDate"]["date"]["start"] == "2026-10-27"
