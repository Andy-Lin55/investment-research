from unittest.mock import Mock

import pytest


def test_audit_comparison_requires_primary_event_exact_ticker_and_original_url():
    from scripts.audit_disclosures import compare_reports

    reports = [{"ticker": "301004.SZ", "title": "嘉益股份：2026年三季度报告", "date": "2026-10-29",
                "url": "http://static.cninfo.com.cn/finalpage/2026-10-29/1225636692.PDF"}]
    news = {"ticker": "301004.SZ", "event_type": "新闻资讯", "url": reports[0]["url"], "id": "news"}
    assert compare_reports(reports, [news], True)[0]["event_status"] == "absent_from_primary_events"
    original = {**news, "event_type": "季度报告", "url": reports[0]["url"].replace("http:", "https:")}
    assert compare_reports(reports, [original], True)[0]["event_status"] == "present"
    assert compare_reports(reports, [{**original, "ticker": "600519.SH"}], True)[0]["event_status"] == "absent_from_primary_events"


def test_incomplete_events_read_never_claims_absence():
    from scripts.audit_disclosures import compare_reports

    report = {"ticker": "301004.SZ", "title": "2026年三季度报告", "date": "2026-10-29", "url": "https://example.com/one.pdf"}
    assert compare_reports([report], [], False)[0]["event_status"] == "events_read_incomplete"


def test_unrelated_titles_are_not_formal_report_candidates():
    from scripts.audit_disclosures import compare_reports

    assert compare_reports([
        {"ticker": "002475.SZ", "title": "2026年股东会决议", "url": "https://example.com/meeting.pdf"},
        {"ticker": "002475.SZ", "title": "关于修改年度报告编制管理制度", "url": "https://example.com/rules.pdf"},
    ], [], True) == []


def test_readonly_notion_pagination_and_repeated_cursor_guard():
    from scripts.audit_disclosures import read_source

    session = Mock()
    session.post.side_effect = [Mock(json=lambda: {"results": [{"id": "1"}], "has_more": True, "next_cursor": "p2"}),
                                Mock(json=lambda: {"results": [{"id": "2"}], "has_more": False})]
    assert [r["id"] for r in read_source(session, "private-token", "source-id")] == ["1", "2"]
    assert session.post.call_args.kwargs["json"]["start_cursor"] == "p2"
    assert session.post.call_args.args[0].endswith("/data_sources/source-id/query")
    session.post.side_effect = None
    session.post.return_value = Mock(json=lambda: {"results": [], "has_more": True, "next_cursor": "same"})
    with pytest.raises(ValueError):
        read_source(session, "private-token", "source-id")


def test_audit_separates_neeq_from_us_and_latest_period_outside_window():
    from scripts.audit_disclosures import market, financial_comparison

    assert market("830806.NQ") == "NEEQ"
    assert market("QFIN.O") == "US"
    row = {"ticker": "600660.SH", "status": "observed", "latest": {"end_date": "20260930", "ann_date": "20261021"}}
    result = financial_comparison(row, [], "2026-10-26")
    assert result["inventory_check"] == "latest_publication_outside_window"


def test_provider_period_is_matched_to_report_title_not_guessed_from_today():
    from scripts.audit_disclosures import financial_comparison

    row = {"ticker": "002475.SZ", "status": "observed", "latest": {"end_date": "20260930", "ann_date": "20261030"}}
    current = {"ticker": "002475.SZ", "title": "2026年三季度报告"}
    stale = {"ticker": "002475.SZ", "title": "2025年三季度报告"}
    assert financial_comparison(row, [stale], "2026-10-26")["inventory_check"] == "provider_period_not_observed_in_inventory"
    assert financial_comparison(row, [current], "2026-10-26")["inventory_check"] == "provider_period_observed_in_inventory"
