from copy import deepcopy
import importlib
import io
import json
import sys

import pytest


def api():
    try:
        return importlib.import_module("research.evidence")
    except ModuleNotFoundError:
        pytest.fail("The offline information-card foundation is not implemented")


def payload(**updates):
    value = {
        "issuer_id": "issuer-example", "ticker": "1234.HK", "title": "2026年第三季度报告",
        "document_type": "季度报告", "source_url": "https://example.invalid/filing.pdf",
        "published_at": "2026-10-26T18:00:00+08:00", "ingested_at": "2026-10-27T09:00:00+08:00",
        "content": "公司披露本季度收入100港元。", "facts": ["本季度收入100港元"],
    }
    value.update(updates)
    return value


@pytest.mark.parametrize("document_type,title,category,subtype", [
    ("季度报告", "2026年第三季度报告", "fundamentals", "earnings"),
    ("业绩预告", "业绩预告修正", "fundamentals", "earnings"),
    ("行情", "收盘价及估值", "fundamentals", "price_valuation"),
    ("业绩会", "业绩会问答", "fundamentals", "earnings_call"),
    ("行业数据", "行业销量", "fundamentals", "industry"),
    ("减持", "股东减持计划", "trading_flows", "ownership"),
    ("大宗交易", "大宗交易记录", "trading_flows", "block_trade"),
    ("回购", "回购完成公告", "trading_flows", "buyback"),
    ("政策", "行业监管政策征求意见", "policy", "regulatory"),
])
def test_classifies_only_explicit_document_evidence(document_type, title, category, subtype):
    card = api().build_card(payload(document_type=document_type, title=title))
    assert card["category"] == category
    assert card["subtype"] == subtype
    assert card["human_review"] == "pending"
    assert card["source"]["url"] == "https://example.invalid/filing.pdf"


def test_ambiguous_news_is_pending_instead_of_an_invented_category():
    card = api().build_card(payload(document_type="新闻资讯", title="市场关注公司变化"))
    assert card["category"] is None
    assert card["classification_status"] == "needs_review"
    assert "classification" in card["missing"]


def test_facts_calculations_and_interpretations_stay_separate():
    card = api().build_card(payload(
        calculations=[{"statement": "同比增长10%", "formula": "110/100-1", "inputs": [110, 100]}],
        interpretations=["增长可持续性待验证"],
    ))
    assert card["facts"] == ["本季度收入100港元"]
    assert card["calculations"][0]["formula"] == "110/100-1"
    assert card["interpretations"] == ["增长可持续性待验证"]
    assert card["automatic_validation"] == "structure_only"


def test_planned_disposal_is_not_presented_as_executed_and_does_not_trade():
    card = api().build_card(payload(document_type="减持", title="股东减持计划"))
    assert card["event_stage"] == "planned"
    assert card["required_research"] is False
    assert card["research_status"] == "not_requested"
    assert card["calculations"] == []
    assert card["interpretations"] == []


def test_earnings_are_required_even_if_importance_is_low_or_content_missing():
    card = api().build_card(payload(importance="Low", content="", facts=[]))
    assert card["required_research"] is True
    assert card["research_status"] == "pending_evidence"
    assert "source_content" in card["missing"]


def test_material_disclosure_and_general_announcement_differ():
    required = api().build_card(payload(document_type="重大公告", title="重大收购"))
    routine = api().build_card(payload(document_type="公告", title="办公室地址变更"))
    assert required["required_research"] is True
    assert routine["required_research"] is False


def test_same_document_seen_later_deduplicates_but_changed_content_has_new_revision():
    first = api().build_card(payload())
    later = api().build_card(payload(ingested_at="2026-10-28T09:00:00+08:00"))
    amended = api().build_card(payload(content="更正：收入110港元。"))
    assert first["event_id"] == later["event_id"] == amended["event_id"]
    assert first["revision_id"] == later["revision_id"]
    assert first["revision_id"] != amended["revision_id"]
    other_company = api().build_card(payload(issuer_id="another-issuer"))
    assert first["event_id"] != other_company["event_id"]


def test_late_material_preserves_publication_and_ingestion_dates():
    card = api().build_card(payload())
    assert card["source"]["published_at"] == "2026-10-26T10:00:00+00:00"
    assert card["ingested_at"] == "2026-10-27T01:00:00+00:00"


@pytest.mark.parametrize("update", [
    {"source_url": "file:///etc/passwd"}, {"source_url": "https://user:password@example.invalid/file"},
    {"published_at": "2026-10-26T10:00:00"}, {"issuer_id": ""}, {"facts": "not-a-list"},
    {"category": "policy", "subtype": "earnings"}, {"calculations": [{"statement": "growth"}]},
    {"facts": [float("nan")]}, {"ingested_at": "2026-10-25T09:00:00+08:00"},
])
def test_invalid_source_timing_or_evidence_structure_is_rejected(update):
    with pytest.raises(ValueError):
        api().build_card(payload(**update))


def test_build_does_not_mutate_input_and_cross_tags_do_not_duplicate_events():
    value = payload(document_type="回购", title="回购完成公告", tags=["fundamentals:capital", "fundamentals:capital"])
    original = deepcopy(value)
    card = api().build_card(value)
    assert value == original
    assert card["tags"] == ["fundamentals:capital"]
    card["facts"].append("new")
    assert value == original


def test_no_policy_company_impact_is_invented():
    card = api().build_card(payload(document_type="政策", title="监管规则正式发布"))
    assert card["event_stage"] == "unknown"
    assert card["interpretations"] == []
    assert card["required_research"] is False


@pytest.mark.parametrize("kind,title", [
    ("减持", "关于股东减持计划尚未实施完毕的公告"),
    ("回购", "关于股份回购计划未完成的公告"),
    ("减持", "股东减持计划完成前的进展"),
    ("回购", "关于回购计划终止的公告"),
])
def test_negated_or_ambiguous_completion_never_becomes_executed(kind, title):
    assert api().build_card(payload(document_type=kind, title=title))["event_stage"] == "unknown"


@pytest.mark.parametrize("kind", ["中期报告", "业绩公告", "10-K/A", "10-Q/A", "20-F/A"])
def test_existing_hk_earnings_and_amendments_require_research(kind):
    card = api().build_card(payload(document_type=kind, importance="Low", content=""))
    assert card["category"] == "fundamentals"
    assert card["subtype"] == "earnings"
    assert card["required_research"] is True
    assert card["research_status"] == "pending_evidence"


def test_preview_prints_valid_card_without_persisting(monkeypatch, capsys):
    try:
        preview = importlib.import_module("research.preview")
    except ModuleNotFoundError:
        pytest.fail("Offline preview is not implemented")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload())))
    assert preview.main() == 0
    card = json.loads(capsys.readouterr().out)
    assert card["human_review"] == "pending"
    assert card["required_research"] is True


def test_preview_invalid_input_exits_nonzero_without_candidate(monkeypatch, capsys):
    try:
        preview = importlib.import_module("research.preview")
    except ModuleNotFoundError:
        pytest.fail("Offline preview is not implemented")
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"source_url":"file:///private"}'))
    assert preview.main() == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "Invalid evidence input" in output.err
