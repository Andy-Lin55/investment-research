import json
from datetime import datetime
from unittest.mock import Mock

import pytest

from data_ingestion.disclosure_analysis import (
    CHUNK_CHARS, DisclosureAnalysis, _read_document, analyze_disclosure,
    disclosure_key, is_primary_disclosure,
)
from scheduler.briefing import build_daily_briefing, fetch_briefing_events


def event(kind="季度报告", importance="Medium", identifier="report", summary="发布了三季报"):
    return {
        "id": identifier,
        "created_time": "2026-10-21T01:57:00Z",
        "properties": {
            "Name": {"title": [{"plain_text": f"600660.SH {kind} 2026-10-20"}]},
            "Ticker": {"rich_text": [{"plain_text": "600660.SH"}]},
            "EventType": {"select": {"name": kind}},
            "Importance": {"select": {"name": importance}},
            "EventDate": {"date": {"start": "2026-10-20"}},
            "ProcessedAt": {"date": {"start": "2026-10-21"}},
            "AI Summary": {"rich_text": [{"plain_text": summary}]},
            "KeyNumbers": {"rich_text": [{"plain_text": '{"单季营收": "100亿元，同比增长5%"}'}]},
            "SourceURL": {"url": "https://static.cninfo.com.cn/finalpage/2026-10-21/report.PDF"},
        },
    }


def analysis_json(fact="单季营收增长5%", interpretation="利润增幅低于营收，需核实成本变化。"):
    return json.dumps({"facts": [fact], "key_numbers": {"单季营收": "100亿元，同比增长5%"},
                       "interpretation": interpretation, "follow_up": ["关注经营现金流变化"], "missing": []}, ensure_ascii=False)


def matches(page, rule):
    if "and" in rule:
        return all(matches(page, item) for item in rule["and"])
    if "or" in rule:
        return any(matches(page, item) for item in rule["or"])
    if "timestamp" in rule:
        value = datetime.fromisoformat(page["created_time"].replace("Z", "+00:00"))
        condition = rule["created_time"]
        if "before" in condition:
            return value < datetime.fromisoformat(condition["before"])
        return value >= datetime.fromisoformat(condition["on_or_after"])
    return page["properties"][rule["property"]]["date"]["start"] == rule["date"]["equals"]


def test_previous_day_disclosure_is_selected_when_processed_today():
    report = event()
    old = event(identifier="old")
    old["created_time"] = "2026-10-20T01:57:00Z"
    old["properties"]["ProcessedAt"]["date"]["start"] = "2026-10-20"
    after_midnight = event(identifier="midnight")
    after_midnight["created_time"] = "2026-10-20T16:30:00Z"
    after_midnight["properties"]["ProcessedAt"]["date"]["start"] = "2026-10-20"
    tomorrow = event(identifier="tomorrow")
    tomorrow["created_time"] = "2026-10-21T16:00:00Z"
    query = lambda **kw: {"results": [item for item in [report, old, after_midnight, tomorrow] if matches(item, kw["filter"])]}

    assert [p["id"] for p in fetch_briefing_events(query, "db", "2026-10-21")] == ["report", "midnight"]


def test_pagination_keeps_low_importance_meeting_but_not_low_news():
    report = event()
    meeting = event("股东会", "Low", "meeting")
    news = event("新闻资讯", "Low", "news")
    query = Mock(side_effect=[{"results": [report], "has_more": True, "next_cursor": "next"},
                              {"results": [report, meeting, news], "has_more": False}])
    assert [p["id"] for p in fetch_briefing_events(query, "db", "2026-10-21")] == ["report", "meeting"]
    assert query.call_args.kwargs["start_cursor"] == "next"


@pytest.mark.parametrize("kind", ["季度报告", "股东会", "公告", "业绩会", "8-K", "DEF 14A"])
def test_first_party_types_are_mandatory(kind):
    assert is_primary_disclosure(event(kind))
    assert not is_primary_disclosure(event("新闻资讯"))


def test_each_disclosure_is_rendered_even_when_overview_omits_it():
    report = event(summary="A" * 700)
    meeting = event("股东会", "Low", "meeting")
    long_detail = "利润变化原因已解析。" * 400
    result = DisclosureAnalysis(long_detail + "\n归母净利润下降9.87%", "complete")
    analyses = {disclosure_key(report): result,
                disclosure_key(meeting): DisclosureAnalysis("议案已通过，分红每股1元，实施日期待定。", "complete")}
    material = build_daily_briefing([report, meeting], "2026-10-21", analyses)
    for overview in ("今日公司发布了三季报。", ""):
        body = material.render(overview)
        assert long_detail in body
        assert "归母净利润下降9.87%" in body
        assert "分红每股1元" in body
        assert len(body) > 3000
    assert "归母净利润下降9.87%" in material.prompt
    assert "其他 1" in material.overview


def test_primary_stored_evidence_is_not_limited_to_500_characters():
    report = event(summary="A" * 600 + "关键决议在文末")
    material = build_daily_briefing([report], "2026-10-21")
    assert "关键决议在文末" in material.render("")
    assert "100亿元" in material.render("")
    assert "尚未完成" in material.render("")


def test_long_document_keeps_middle_and_last_page_and_synthesizes():
    text = "起始" + "x" * CHUNK_CHARS + "中段议案" + "y" * CHUNK_CHARS + "末页决议"
    prompts = []

    def ai(prompt, **kwargs):
        prompts.append(prompt)
        return analysis_json()

    result = analyze_disclosure(event(), read_sources=lambda url: ([(url, text)], []), call_ai=ai)
    assert result.status == "complete"
    assert len(prompts) == 4
    assert any("中段议案" in prompt for prompt in prompts[:-1])
    assert "末页决议" in prompts[-2]
    assert "100亿元" in result.body


def test_source_failure_is_visible_and_keeps_existing_numbers_without_ai():
    ai = Mock()
    reader = Mock(side_effect=ValueError("unreadable"))
    result = analyze_disclosure(event(), read_sources=reader, call_ai=ai)
    assert result.status == "unavailable"
    assert "原文读取失败" in result.body
    assert "100亿元" in result.body
    ai.assert_not_called()


def test_invalid_model_output_is_not_reported_as_complete():
    result = analyze_disclosure(event(), read_sources=lambda url: ([(url, "原文")], []), call_ai=lambda *a, **k: '{"title": "发布了季报"}')
    assert result.status == "unavailable"
    assert "详细分析失败" in result.body
    assert "100亿元" in result.body


def test_failed_chunk_and_exhibit_are_visible():
    ai = Mock(side_effect=[analysis_json(), ""])
    result = analyze_disclosure(event(), read_sources=lambda url: ([(url, "x" * (CHUNK_CHARS + 1))], ["https://www.sec.gov/failed.htm"]), call_ai=ai)
    assert result.status == "partial"
    assert "1 段分析失败" in result.body
    assert "1 个附件读取失败" in result.body
    assert "100亿元" in result.body


def test_failed_synthesis_preserves_every_completed_fragment():
    ai = Mock(side_effect=[analysis_json("第一个议案"), analysis_json("第二个议案"), ""])
    result = analyze_disclosure(event(), read_sources=lambda url: ([(url, "x" * (CHUNK_CHARS + 1))], []), call_ai=ai)
    assert result.status == "partial"
    assert "第一个议案" in result.body
    assert "第二个议案" in result.body


def test_sec_html_includes_earnings_exhibit_links(monkeypatch):
    import requests

    url = "https://www.sec.gov/Archives/edgar/data/1/123/form8k.htm"
    response = Mock(content=b'<html><body>Item 2.02 Results<a href="ex991.htm">Exhibit 99.1</a><script>ignored</script></body></html>', url=url)
    monkeypatch.setattr(requests, "get", Mock(return_value=response))
    text, exhibits = _read_document(url)
    assert "Item 2.02 Results" in text
    assert "ignored" not in text
    assert exhibits == ["https://www.sec.gov/Archives/edgar/data/1/123/ex991.htm"]


def test_scanned_pdf_without_text_is_not_treated_as_readable(monkeypatch):
    import fitz
    import requests

    with fitz.open() as document:
        document.new_page()
        content = document.tobytes()
    monkeypatch.setattr(requests, "get", Mock(return_value=Mock(content=content)))
    with pytest.raises(ValueError, match="可读正文"):
        _read_document("https://static.cninfo.com.cn/scanned.pdf")


def test_failed_pagination_cannot_silently_drop_disclosures():
    query = Mock(return_value={"results": [event()], "has_more": True})
    with pytest.raises(ValueError, match="分页游标"):
        fetch_briefing_events(query, "db", "2026-10-21")


def test_cninfo_disclosure_date_uses_beijing_even_under_utc():
    from data_ingestion.cninfo_fetcher import _ts_to_date

    assert _ts_to_date(1792512000000) == "2026-10-21"
