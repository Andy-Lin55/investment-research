from copy import deepcopy
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import ANY, Mock, call

import pytest

from scheduler import jobs


TICKER = "301004.SZ"


def filing(day="2026-10-29", title="嘉益股份：2026年三季度报告", identifier="q3"):
    return {
        "ticker": TICKER,
        "title": title,
        "date": day,
        "pdf_path": f"/mock/{identifier}.pdf",
        "url": f"https://static.cninfo.com.cn/finalpage/{day}/{identifier}.PDF",
    }


@pytest.fixture
def daily(monkeypatch):
    class Monday(date):
        @classmethod
        def today(cls):
            return cls(2026, 11, 2)

    state = SimpleNamespace(ordinary=[], periodic=[], stored=set(), written=[], actions=[], honor_window=False)
    monkeypatch.setattr(jobs, "date", Monday)
    monkeypatch.setattr(jobs, "beijing_date", lambda: Monday.today())
    monkeypatch.setattr(jobs, "get_daily_tickers", lambda **kwargs: [TICKER])
    state.batch = Mock(side_effect=lambda tickers, days_back, **kwargs: {TICKER: deepcopy(state.ordinary)})

    def fetch_periodic(ticker, days_back, **kwargs):
        reports = deepcopy(state.periodic)
        if state.honor_window:
            cutoff = (Monday.today() - timedelta(days=days_back)).isoformat()
            reports = [item for item in reports if item["date"] >= cutoff]
        return reports

    state.fetch = Mock(side_effect=fetch_periodic)
    monkeypatch.setattr(jobs, "batch_fetch", state.batch)
    monkeypatch.setattr(jobs, "fetch_periodic_reports", state.fetch)

    def exists(ticker, event_type, event_date, source_url):
        state.actions.append("check")
        return (ticker, event_type, event_date, source_url) in state.stored

    def parse(**kwargs):
        state.actions.append("parse")
        return {"event_type": kwargs["event_type"], "importance": "Medium", "investment_signal": "中性"}

    def create(ticker, parsed):
        state.actions.append("create")
        state.stored.add((ticker, parsed["event_type"], parsed["event_date"], parsed["source_url"]))
        state.written.append((ticker, deepcopy(parsed)))
        return "local-page-id"

    state.exists = Mock(side_effect=exists)
    state.parse = Mock(side_effect=parse)
    monkeypatch.setattr(jobs, "event_exists", state.exists)
    monkeypatch.setattr(jobs, "parse_announcement", state.parse)
    monkeypatch.setattr(jobs, "create_event", create)
    research = Mock(side_effect=AssertionError("Research must stay disabled in these tests"))
    send = Mock(side_effect=AssertionError("No notifications in these tests"))
    monkeypatch.setattr(jobs, "_trigger_research_async", research)
    monkeypatch.setattr(jobs, "_send_briefing", send)
    monkeypatch.setattr(jobs, "update_company_signal", Mock())
    yield state
    research.assert_not_called()
    send.assert_not_called()


@pytest.mark.parametrize("day", ["2026-10-29", "2026-10-30"])
@pytest.mark.parametrize("honor_window", [True, False], ids=["source-filters-dates", "source-ignores-dates"])
def test_monday_recovers_thursday_and_friday_periodic_reports(daily, day, honor_window):
    report = filing(day)
    daily.periodic = [report]
    daily.honor_window = honor_window

    jobs._job_fetch_and_parse()

    assert len(daily.written) == 1
    ticker, event = daily.written[0]
    assert ticker == TICKER
    assert event["event_type"] == "季度报告"
    assert event["event_date"] == day
    assert event["source_url"] == report["url"]
    assert daily.actions == ["check", "parse", "create"]
    daily.fetch.assert_called_once_with(TICKER, days_back=7, company_name=None, health=ANY, as_of=date(2026, 11, 2))
    daily.batch.assert_called_once_with([TICKER], days_back=3, company_names={}, health=ANY, as_of=date(2026, 11, 2))


@pytest.mark.parametrize("day, accepted", [("2026-10-26", True), ("2026-10-25", False)])
def test_periodic_seven_day_cutoff_is_enforced_when_cninfo_ignores_dates(daily, day, accepted):
    daily.periodic = [filing(day)]

    jobs._job_fetch_and_parse()

    assert len(daily.written) == int(accepted)
    assert daily.parse.call_count == int(accepted)
    assert daily.exists.call_count == int(accepted)


@pytest.mark.parametrize("day, accepted", [("2026-10-30", True), ("2026-10-29", False), ("2026-10-25", False)])
def test_ordinary_announcements_keep_three_day_cutoff(daily, day, accepted):
    daily.ordinary = [filing(day, "嘉益股份：重大合同公告", "contract")]

    jobs._job_fetch_and_parse()

    assert len(daily.written) == int(accepted)
    assert daily.parse.call_count == int(accepted)
    assert daily.exists.call_count == int(accepted)
    daily.batch.assert_called_once_with([TICKER], days_back=3, company_names={}, health=ANY, as_of=date(2026, 11, 2))


def test_duplicate_recovered_periodic_is_checked_before_parsing_across_runs(daily):
    report = filing()
    daily.periodic = [report, deepcopy(report)]

    jobs._job_fetch_and_parse()
    jobs._job_fetch_and_parse()

    assert len(daily.written) == 1
    daily.parse.assert_called_once_with(pdf_path="/mock/q3.pdf", ticker=TICKER, event_type="季度报告")
    assert daily.exists.call_args_list == [call(TICKER, "季度报告", "2026-10-29", report["url"])] * 4
    assert daily.actions == ["check", "parse", "create", "check", "check", "check"]


def test_already_stored_recovered_periodic_never_reaches_parser(daily):
    report = filing()
    daily.periodic = [report]
    daily.stored.add((TICKER, "季度报告", "2026-10-29", report["url"]))

    jobs._job_fetch_and_parse()

    assert daily.actions == ["check"]
    daily.parse.assert_not_called()
    assert daily.written == []


def test_same_periodic_in_both_searches_is_parsed_once(daily):
    report = filing("2026-10-30")
    daily.ordinary = [report]
    daily.periodic = [deepcopy(report)]

    jobs._job_fetch_and_parse()

    assert len(daily.written) == 1
    assert daily.actions == ["check", "parse", "create"]


def test_old_ordinary_copy_does_not_hide_recoverable_periodic(daily):
    report = filing()
    daily.ordinary = [report]
    daily.periodic = [deepcopy(report)]

    jobs._job_fetch_and_parse()

    assert len(daily.written) == 1
    assert daily.actions == ["check", "parse", "create"]


def test_daily_reuses_roster_names_for_code_to_name_recovery_before_ai(daily, monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from data_ingestion import cninfo_fetcher

    def roster(company_names=None, **kwargs):
        if company_names is not None:
            company_names[TICKER] = "嘉益股份"
        return [TICKER]

    roster_read = Mock(side_effect=roster)
    monkeypatch.setattr(jobs, "get_daily_tickers", roster_read)
    monkeypatch.setattr(jobs, "batch_fetch", cninfo_fetcher.batch_fetch)
    monkeypatch.setattr(jobs, "fetch_periodic_reports", cninfo_fetcher.fetch_periodic_reports)
    monkeypatch.setattr(cninfo_fetcher, "REQUEST_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(cninfo_fetcher, "_download_pdf", Mock(return_value=True))
    report = {
        "announcementId": "1225636692", "secCode": "301004",
        "announcementTitle": "嘉益股份：2026年三季度报告",
        "announcementTime": int(datetime(2026, 10, 29, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp() * 1000),
        "adjunctUrl": "finalpage/2026-10-29/1225636692.PDF",
    }

    def query(url, *, data, **kwargs):
        hits = [report] if data["searchkey"] == "嘉益股份 季度报告" else None
        return Mock(json=lambda: {"announcements": hits})

    post = Mock(side_effect=query)
    monkeypatch.setattr(cninfo_fetcher.requests, "post", post)

    jobs._job_fetch_and_parse()
    jobs._job_fetch_and_parse()

    assert len(daily.written) == 1
    assert daily.written[0][1]["source_url"] == "http://static.cninfo.com.cn/finalpage/2026-10-29/1225636692.PDF"
    assert daily.actions == ["check", "parse", "create", "check"]
    assert roster_read.call_count == 2  # One roster read per daily run, shared by both fetch paths.


def test_failed_source_cannot_finish_daily_job_green(daily, monkeypatch):
    import requests
    from data_ingestion import cninfo_fetcher

    monkeypatch.setattr(jobs, "batch_fetch", cninfo_fetcher.batch_fetch)
    monkeypatch.setattr(jobs, "fetch_periodic_reports", cninfo_fetcher.fetch_periodic_reports)
    monkeypatch.setattr(cninfo_fetcher, "REQUEST_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(cninfo_fetcher.requests, "post", Mock(side_effect=requests.ConnectionError("offline")))

    with pytest.raises(RuntimeError):
        jobs._job_fetch_and_parse()
    daily.parse.assert_not_called()
    assert daily.written == []


def test_same_title_different_originals_are_not_discarded_before_idempotency(daily):
    daily.ordinary = [filing("2026-10-30", identifier="original")]
    daily.periodic = [filing("2026-10-30", identifier="revision")]

    jobs._job_fetch_and_parse()

    assert len(daily.written) == 2
    assert daily.parse.call_count == 2


def test_failed_parser_cannot_finish_daily_job_green(daily):
    daily.periodic = [filing()]
    daily.parse.side_effect = None
    daily.parse.return_value = {}
    with pytest.raises(RuntimeError):
        jobs._job_fetch_and_parse()


def test_hk_us_source_failure_is_reported_by_daily_job(daily, monkeypatch):
    from data_ingestion.disclosure_health import SourceFetchError

    monkeypatch.setattr(jobs, "get_daily_tickers", lambda **kwargs: ["QFIN.O"])

    def fetch(ticker, **kwargs):
        if "health" in kwargs:
            kwargs["health"].fail("contact_configuration", ValueError("missing"))
        return []

    monkeypatch.setattr(jobs, "fetch_filings", fetch)
    with pytest.raises(SourceFetchError):
        jobs._job_fetch_filings()
