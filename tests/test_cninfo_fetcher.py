from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
from data_ingestion import cninfo_fetcher as source


@pytest.mark.parametrize("ticker", ["0700.HK", "AAPL", "BRK.B"])
def test_non_a_shares_do_not_query_cninfo(ticker):
    assert source.fetch_announcements(ticker) == []


def test_fetch_matches_exact_company_and_deduplicates_keyword_hits(monkeypatch):
    rows = [
        {"announcementId": "report1", "secCode": "600519", "announcementTitle": "年度报告",
         "announcementTime": 1793030400000, "adjunctUrl": "finalpage/report.pdf"},
        {"announcementId": "other", "secCode": "000001", "announcementTitle": "年度报告"},
    ]
    response = Mock()
    response.json.return_value = {"announcements": rows}
    post = Mock(return_value=response)
    monkeypatch.setattr(source.requests, "post", post)
    monkeypatch.setattr(source.time, "sleep", lambda _: None)
    result = source.fetch_announcements("600519.SH", download_pdf=False, keywords=["年度报告", "业绩"])
    assert len(result) == 1
    assert result[0]["ticker"] == "600519.SH"
    assert result[0]["url"] == "http://static.cninfo.com.cn/finalpage/report.pdf"
    assert result[0]["pdf_path"] is None
    assert post.call_args.kwargs["data"]["searchkey"] == "600519 业绩"


def test_timestamp_uses_beijing_calendar_day():
    milliseconds = int(datetime(2026, 10, 26, 17, tzinfo=timezone.utc).timestamp() * 1000)
    assert source._ts_to_date(milliseconds) == "2026-10-27"
    assert source._ts_to_date("invalid") == ""


def test_pdf_cache_does_not_redownload_existing_file(tmp_path):
    path = tmp_path / "original.pdf"
    path.write_bytes(b"existing-source")
    assert source._download_pdf("https://example.invalid/report.pdf", path)
    assert path.read_bytes() == b"existing-source"
