import pytest
from unittest.mock import patch, MagicMock
from investment_research.data_ingestion.cninfo_fetcher import (
    filter_announcements_by_keyword,
    build_request_params,
)


def test_filter_announcements_by_keyword():
    announcements = [
        {"announcementTitle": "2024年业绩预告"},
        {"announcementTitle": "关于公司日常经营情况的说明"},
        {"announcementTitle": "重大合同签订公告"},
        {"announcementTitle": "股东大会决议公告"},
    ]
    keywords = ["业绩预告", "重大合同"]
    result = filter_announcements_by_keyword(announcements, keywords)
    assert len(result) == 2
    assert result[0]["announcementTitle"] == "2024年业绩预告"
    assert result[1]["announcementTitle"] == "重大合同签订公告"


def test_filter_announcements_empty():
    assert filter_announcements_by_keyword([], ["业绩预告"]) == []


def test_filter_announcements_no_match():
    announcements = [{"announcementTitle": "普通公告"}]
    assert filter_announcements_by_keyword(announcements, ["业绩预告"]) == []


def test_build_request_params_basic():
    params = build_request_params("600519")
    assert params["stock"] == "600519"
    assert "pageNum" in params
    assert params["pageSize"] == 30


def test_build_request_params_custom():
    params = build_request_params("000001", page_num=2, page_size=10)
    assert params["stock"] == "000001"
    assert params["pageNum"] == 2
    assert params["pageSize"] == 10
