from datetime import datetime, timezone


def _event(name, importance, summary, key_numbers="{}", source_url=""):
    return {
        "properties": {
            "Name": {"title": [{"plain_text": name}]},
            "Ticker": {"rich_text": [{"plain_text": "AAPL"}]},
            "EventType": {"select": {"name": "公告"}},
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
