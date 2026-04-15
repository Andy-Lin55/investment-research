import pytest
from unittest.mock import patch, MagicMock
from investment_research.notion_sync.sync_manager import SyncManager


@patch("investment_research.notion_sync.sync_manager.NotionClient")
def test_get_active_tickers_returns_list(mock_nc_cls):
    mock_nc = MagicMock()
    mock_nc_cls.return_value = mock_nc
    mock_nc.query_database.return_value = [
        {"properties": {"Ticker": {"rich_text": [{"plain_text": "600519"}]},
                        "Status": {"select": {"name": "Active"}}}},
        {"properties": {"Ticker": {"rich_text": [{"plain_text": "000001"}]},
                        "Status": {"select": {"name": "Active"}}}},
    ]
    mgr = SyncManager()
    tickers = mgr.get_active_tickers()
    assert "600519" in tickers
    assert "000001" in tickers
    assert len(tickers) == 2


@patch("investment_research.notion_sync.sync_manager.NotionClient")
def test_get_active_tickers_empty(mock_nc_cls):
    mock_nc = MagicMock()
    mock_nc_cls.return_value = mock_nc
    mock_nc.query_database.return_value = []
    mgr = SyncManager()
    assert mgr.get_active_tickers() == []


@patch("investment_research.notion_sync.sync_manager.NotionClient")
def test_event_exists_true(mock_nc_cls):
    mock_nc = MagicMock()
    mock_nc_cls.return_value = mock_nc
    mock_nc.query_database.return_value = [{"id": "existing-page-id"}]
    mgr = SyncManager()
    result = mgr._event_exists("600519", "公告", "2025-01-15")
    assert result is True


@patch("investment_research.notion_sync.sync_manager.NotionClient")
def test_event_exists_false(mock_nc_cls):
    mock_nc = MagicMock()
    mock_nc_cls.return_value = mock_nc
    mock_nc.query_database.return_value = []
    mgr = SyncManager()
    result = mgr._event_exists("600519", "公告", "2025-01-15")
    assert result is False


@patch("investment_research.notion_sync.sync_manager.NotionClient")
def test_create_event_skips_duplicate(mock_nc_cls):
    mock_nc = MagicMock()
    mock_nc_cls.return_value = mock_nc
    # First call (get_ticker_page_id for _event_exists) returns Universe page
    # Second call (_event_exists) returns existing event
    mock_nc.query_database.return_value = [{"id": "existing-page-id"}]
    mgr = SyncManager()
    result = mgr.create_event("600519", {
        "event_type": "公告",
        "event_date": "2025-01-15",
        "one_line_summary": "test",
        "importance": "High",
        "investment_signal": "Buy",
        "detailed_analysis": "",
        "key_numbers": {},
    })
    assert result is False
    mock_nc.create_page.assert_not_called()
