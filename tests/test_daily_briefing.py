from datetime import datetime, timezone


def _event(name, importance, summary, key_numbers="{}", source_url="", event_type="新闻资讯"):
    return {
        "properties": {
            "Name": {"title": [{"plain_text": name}]},
            "Ticker": {"rich_text": [{"plain_text": "AAPL"}]},
            "EventType": {"select": {"name": event_type}},
            "Importance": {"select": {"name": importance}},
            "Signal": {"select": {"name": "Hold"}},
            "AI Summary": {"rich_text": [{"plain_text": summary[:180]}, {"plain_text": summary[180:]}]},
            "KeyNumbers": {"rich_text": [{"plain_text": key_numbers}]},
            "SourceURL": {"url": source_url},
        }
    }


def test_beijing_today_uses_local_date_at_utc_boundary():
    from scheduler.briefing import beijing_today

    now = datetime(2026, 9, 27, 23, 30, tzinfo=timezone.utc)
    assert beijing_today(now) == "2026-09-28"


def test_briefing_keeps_full_source_details_and_adds_citations():
    from scheduler.briefing import build_daily_briefing

    summary = "公司披露业务调整。" + "收入结构变化需继续观察。" * 20
    event = _event(
        "AAPL 公告 2026-09-28", "High", summary,
        key_numbers='{"收入增速": "12%"}',
        source_url="https://example.com/filing",
    )

    briefing = build_daily_briefing([event], "2026-09-28")
    body = briefing.render("事实与影响。[1]")

    assert summary in briefing.prompt
    assert "收入增速: 12%" in briefing.prompt
    assert "300字以内" not in briefing.prompt
    assert "High 1" in body
    assert "事实与影响。[1]" in body
    assert summary in body
    assert "收入增速: 12%" in body
    assert "https://example.com/filing" in body
    assert "不要编造" in briefing.prompt


def test_briefing_prioritizes_high_events_and_preserves_fallback():
    from scheduler.briefing import build_daily_briefing

    medium = _event("AAPL Medium", "Medium", "中等事件摘要")
    high = _event("AAPL High", "High", "高重要性事件摘要")

    briefing = build_daily_briefing([medium, high], "2026-09-28")
    body = briefing.render("")

    assert briefing.prompt.index("AAPL High") < briefing.prompt.index("AAPL Medium")
    assert "高重要性事件摘要" in body
    assert "中等事件摘要" in body
    assert "AI 分析暂不可用" in body
    assert "资料来源" in body


def test_each_event_summary_is_limited_to_500_characters():
    from scheduler.briefing import build_daily_briefing

    summary = "A" * 500 + "B" * 100
    briefing = build_daily_briefing([_event("AAPL 公告", "High", summary)], "2026-09-28")

    assert "A" * 500 in briefing.prompt
    assert "B" not in briefing.prompt
    assert "B" not in briefing.render("")


def test_email_preserves_long_analysis_and_all_event_details():
    from scheduler.briefing import build_daily_briefing

    events = [
        _event(f"AAPL 事件 {i}", "High", "事件资料。" * 110, source_url=f"https://example.com/filing/{i}")
        for i in range(12)
    ]
    briefing = build_daily_briefing(events, "2026-09-28")

    analysis = "分析结论。" * 900
    for analysis_text in (analysis, ""):
        body = briefing.render(analysis_text)
        assert len(body) > 3000
        assert "资料来源" in body
        assert "https://example.com/filing/0" in body
        assert "https://example.com/filing/11" in body
        assert "[12]" in body
        assert "重点事件 12 条" in body
        if analysis_text:
            assert analysis in body


def test_email_keeps_complete_analysis():
    from scheduler.briefing import build_daily_briefing

    analysis = "分析。" * 700
    event = _event("AAPL 公告", "High", "已确认事实。" * 40, source_url="https://example.com/filing")

    body = build_daily_briefing([event], "2026-09-28").render(analysis)

    assert analysis in body
    assert "3000 字" not in body


def test_email_keeps_all_sources_even_when_list_is_long():
    from scheduler.briefing import build_daily_briefing

    first_url = "https://example.com/0/" + "a" * 100
    events = [
        _event(
            f"AAPL 事件 {i}", "High", "已确认事实。" * 90,
            source_url=f"https://example.com/{i}/" + "a" * 100,
        )
        for i in range(50)
    ]

    briefing = build_daily_briefing(events, "2026-09-28")
    for analysis in ("分析。" * 900, ""):
        body = briefing.render(analysis)
        assert first_url in body
        assert "https://example.com/49/" in body
        assert "条来源略" not in body
    assert "不超过 3000 字" not in briefing.prompt
