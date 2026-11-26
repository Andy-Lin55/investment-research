"""A validated Workbench handoff must actually drive progress and follow-ups.

Before this bridge the handoff was validated and then compressed into prompt
context: the tracker still had no baseline to grade a filing against, and the
monitoring conditions never reached the follow-up register.
"""
import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "handoff-v1"


def _load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _three_year_handoff():
    """A candidate with history plus three annual scenarios in every case."""
    base = _load("candidate.json")
    base["handoff_id"] = "three-year-v1"
    forecasts = []
    for origin, scenario, year, revenue, income, cash in (
        ("reported_actual", "actual", 2025, "180.00", "18.00", "21.00"),
        ("calculated", "base", 2026, "195.05", "19.50", "22.00"),
        ("calculated", "base", 2027, "214.56", "21.45", "24.20"),
        ("calculated", "base", 2028, "236.02", "23.60", "26.62"),
        ("calculated", "bull", 2026, "210.00", "21.00", "24.00"),
        ("calculated", "bear", 2026, "175.00", "17.50", "19.00"),
    ):
        for metric, value in (("revenue", revenue),
                              ("parent_net_income", income),
                              ("operating_cashflow", cash)):
            forecasts.append({
                "metric": metric,
                "period_start": f"{year}-01-01",
                "period_end": f"{year}-12-31",
                "period_basis": "annual",
                "value": value,
                "currency": "CNY",
                "unit_scale": "100000000",
                "scope": "consolidated",
                "ownership_basis": "parent",
                "origin": origin,
                "scenario": scenario,
                "source_refs": ["disclosure-h1"],
            })
    base["forecasts"] = forecasts
    return base


# --- forecast bridge -------------------------------------------------------

def test_forecasts_become_a_progress_model_with_all_three_scenarios():
    from research.forecast_progress import handoff_forecasts_to_progress_model

    model = handoff_forecasts_to_progress_model(_three_year_handoff(), profile={"issuer_id": "issuer-a"})
    assert model is not None
    assert model["issuer_id"] == "issuer-a"
    assert model["currency"] == "CNY"
    assert model["baseline"]["period_end"] == "2025-12-31"
    # The tracker works in raw currency units (Tushare's own scale), so a
    # handoff value of 180.00 × 1e8 is 18bn here, not 180.
    assert model["baseline"]["revenue"] == pytest.approx(18_000_000_000)
    base_rows = {row["year"]: row for row in model["scenarios"]["base"]}
    assert set(base_rows) == {2026, 2027, 2028}
    assert base_rows[2026]["income"]["revenue"] == pytest.approx(19_505_000_000)
    assert base_rows[2026]["cashflow"]["operating"] == pytest.approx(2_200_000_000)
    assert all(row["status"] == "ready_for_review" for row in base_rows.values())
    assert model["scenarios"]["bull"][0]["income"]["revenue"] == pytest.approx(21_000_000_000)
    assert model["scenarios"]["bear"][0]["income"]["revenue"] == pytest.approx(17_500_000_000)


def test_no_baseline_fixture_is_not_padded_with_a_guess():
    from research.forecast_progress import handoff_forecasts_to_progress_model

    model = handoff_forecasts_to_progress_model(_load("no-baseline.json"), profile={"issuer_id": "issuer-a"})
    assert model is None


def test_missing_annual_scenarios_return_none_instead_of_empty_rows():
    from research.forecast_progress import handoff_forecasts_to_progress_model

    handoff = _load("candidate.json")
    handoff["forecasts"] = [row for row in handoff["forecasts"] if row["scenario"] != "base"]
    assert handoff_forecasts_to_progress_model(handoff, profile={"issuer_id": "issuer-a"}) is None


def test_unit_scale_is_honoured_against_the_reported_baseline():
    from research.forecast_progress import handoff_forecasts_to_progress_model

    handoff = _three_year_handoff()
    for row in handoff["forecasts"]:
        if row["origin"] == "reported_actual" and row["metric"] == "revenue":
            row["value"] = "18000000000"  # same amount expressed in yuan
            row["unit_scale"] = "1"
    model = handoff_forecasts_to_progress_model(handoff, profile={"issuer_id": "issuer-a"})
    # Baseline already declares its scale; mixing units silently would be worse
    # than refusing, so an inconsistent scale must surface as a mismatch.
    assert model is None


def test_bridge_output_is_accepted_by_compare_with_reported_progress():
    from research.forecast_progress import (
        compare_with_reported_progress, handoff_forecasts_to_progress_model,
    )
    from financials.forecast import project
    from financials.statements import normalize_statements
    from tests.test_forecast_model import baseline, scenarios

    handoff = _three_year_handoff()
    model = handoff_forecasts_to_progress_model(handoff, profile={"issuer_id": "issuer-a"})
    assert model is not None

    meta = dict(ts_code="000001.SZ", comp_type="1", report_type="1")
    rows = {
        "income": [dict(meta, end_date="20260630", ann_date="20260820", revenue=95, n_income_attr_p=9)],
        "balancesheet": [],
        "cashflow": [dict(meta, end_date="20260630", ann_date="20260820", n_cashflow_act=10)],
    }
    data = normalize_statements(rows, ticker="000001.SZ", as_of="2026-11-02", currency="CNY")
    data["issuer_id"] = "issuer-a"
    covered = compare_with_reported_progress(model, data, tickers=["000001.SZ"], as_of="2026-11-02")
    assert covered["actual_period"] == "2026-06-30"
    assert any(row["metric"] == "revenue" and row["case"] == "base" for row in covered["rows"])


# --- follow-up bridge ------------------------------------------------------

class FakeFollowups:
    """Minimal in-memory stand-in for NotionFollowups' create/lookup contract."""

    def __init__(self):
        self.items = {}
        self.created = []

    def ensure(self, item, *, report_ref, lookup_only=False):
        key = item.get("key")
        if key in self.items:
            saved = self.items[key]
            # The contract: a human-owned field is never overwritten.
            return {"id": saved["id"], "key": key, "verified": True, "url": None,
                    "state": saved.get("state"), "human_notes": saved.get("human_notes")}
        if lookup_only:
            return None
        self.items[key] = {"id": f"page-{len(self.items) + 1}", "state": "待看",
                           "human_notes": "", **item}
        self.created.append(item)
        return {"id": self.items[key]["id"], "key": key, "verified": True, "url": None}


def test_monitoring_and_gaps_become_follow_up_items():
    from research.followups import identity, items_from_handoff

    items = items_from_handoff(_three_year_handoff(), issuer_id="issuer-a",
                              origin_task="task-1", origin_publication="pub-1")
    kinds = {item["kind"] for item in items}
    assert "conditional_action" in kinds
    assert "open_question" in kinds
    for item in items:
        assert item["key"] == identity(item)
        assert item["issuer_id"] == "issuer-a"
    conditions = " ".join(item.get("condition", "") for item in items)
    assert "年报披露经营现金流" in conditions
    # The gap's missing evidence becomes a question, not a silent drop.
    assert any("年报现金流" in item.get("question", "") for item in items)


def test_follow_up_creation_is_idempotent_and_preserves_human_state():
    from research.followups import items_from_handoff

    store = FakeFollowups()
    handoff = _three_year_handoff()
    items = items_from_handoff(handoff, issuer_id="issuer-a",
                               origin_task="task-1", origin_publication="pub-1")
    for item in items:
        store.ensure(item, report_ref={"url": "https://notion.example/page"})
    assert len(store.created) == len(items)

    # A human marks one item done and writes a note.
    first_key = items[0]["key"]
    store.items[first_key]["state"] = "已完成"
    store.items[first_key]["human_notes"] = "已在电话会确认"

    again = items_from_handoff(handoff, issuer_id="issuer-a",
                               origin_task="task-1", origin_publication="pub-1")
    results = [store.ensure(item, report_ref={"url": "https://notion.example/page"}) for item in again]
    assert len(store.created) == len(items), "a rerun must not duplicate follow-ups"
    preserved = next(row for row in results if row["key"] == first_key)
    assert preserved["state"] == "已完成"
    assert preserved["human_notes"] == "已在电话会确认"


class NotionStub:
    """Replays the exact property encoding `NotionFollowups.ensure` performs.

    A fake that only records calls would miss the readback comparison inside
    `ensure`, which is the real integration risk: an extra payload key that
    Notion does not return as `plain_text` fails the whole filing.
    """

    def __init__(self):
        self.pages = {}

    def __call__(self, method, path, body=None):
        if method == "POST" and path.endswith("/query"):
            wanted = (body.get("filter") or {}).get("title", {}).get("equals")
            rows = [
                {"id": pid} for pid, page in self.pages.items()
                if page["properties"]["Key"]["title"][0]["plain_text"] == wanted
            ]
            return {"results": rows, "has_more": False}
        if method == "POST" and path == "/pages":
            pid = f"p{len(self.pages)}"
            props = {}
            for key, value in body["properties"].items():
                if "rich_text" in value:
                    props[key] = {"rich_text": [{"plain_text": p["text"]["content"]} for p in value["rich_text"]]}
                elif "title" in value:
                    props[key] = {"title": [{"plain_text": p["text"]["content"]} for p in value["title"]]}
                else:
                    props[key] = value
            self.pages[pid] = {"id": pid, "properties": props, "url": f"https://notion.example/{pid}"}
            return {"id": pid}
        if method == "GET" and path.startswith("/pages/"):
            return self.pages[path.split("/")[-1]]
        raise AssertionError(path)


def test_items_survive_the_real_notion_followups_readback():
    from research.followups import NotionFollowups, items_from_handoff

    handoff = _load("candidate.json")
    items = items_from_handoff(handoff, issuer_id="issuer-a",
                               origin_task="t1", origin_publication="p1")
    stub = NotionStub()
    followups = NotionFollowups(stub, "ds-1")
    for item in items:
        receipt = followups.ensure(item, report_ref={"url": "https://notion.example/report"})
        assert receipt["verified"] is True, item

    again = items_from_handoff(handoff, issuer_id="issuer-a",
                              origin_task="t1", origin_publication="p1")
    for item in again:
        followups.ensure(item, report_ref={"url": "https://notion.example/report"})
    assert len(stub.pages) == len(items), "a rerun must not create duplicate pages"

    first = next(iter(stub.pages))
    stub.pages[first]["properties"]["State"] = {"select": {"name": "已完成"}}
    stub.pages[first]["properties"]["HumanNotes"] = {"rich_text": [{"plain_text": "已确认"}]}
    decoded = followups._decode(stub.pages[first])
    assert decoded["state"] == "已完成"
    assert decoded["human_notes"] == "已确认"


def test_accepted_review_status_still_flows_as_candidate():
    from research.handoff_v1 import validate_handoff
    from research.forecast_progress import handoff_forecasts_to_progress_model

    handoff = _three_year_handoff()
    handoff["review_status"] = "accepted"
    raw = json.dumps(handoff, ensure_ascii=False).encode("utf-8")
    from hashlib import sha256

    validated = validate_handoff(
        raw, issuer_id="issuer-a", tickers=["000001.SZ"],
        research_cutoff="2027-12-31T00:00:00+08:00",
        expected_sha256=sha256(raw).hexdigest(),
    )
    assert validated["review_status"] == "accepted"
    # The consumer treats a self-declared accepted handoff as a candidate: an
    # adoption decision belongs to the operator, not to the producer.
    model = handoff_forecasts_to_progress_model(validated, profile={"issuer_id": "issuer-a"})
    assert model is not None
    assert model["review_status"] == "candidate"


def test_partial_metrics_and_mixed_case_scales_in_handoff_bridge():
    from research.forecast_progress import (
        compare_with_reported_progress, handoff_forecasts_to_progress_model,
    )

    handoff = _three_year_handoff()
    # Keep only revenue in 2026 forecast (omit parent_net_income and operating_cashflow)
    handoff["forecasts"] = [
        row for row in handoff["forecasts"]
        if not (str(row.get("period_end", "")).startswith("2026")
                and row.get("metric") in {"parent_net_income", "operating_cashflow"})
    ]
    model = handoff_forecasts_to_progress_model(handoff, profile={"issuer_id": "issuer-a"})
    assert model is not None
    y2026 = next(row for row in model["scenarios"]["base"] if row["year"] == 2026)
    assert y2026["income"]["parent_net_income"] is None
    assert y2026["cashflow"]["operating"] is None

    # compare_with_reported_progress must not raise KeyError on missing parent_net_income
    data = {
        "issuer_id": "issuer-a",
        "statements": {
            "income": [{
                "ticker": "000001.SZ", "period_basis": "year_to_date", "scope": "consolidated",
                "currency": "CNY", "period_start": "2026-01-01", "period_end": "2026-06-30",
                "source_provider": "Tushare", "value_kind": "reported_provider",
                "revision_id": "rev_1", "published_at": "2026-08-20",
                "values": {"revenue": 500_000_000.0, "n_income_attr_p": 40_000_000.0},
            }],
            "cashflow": [],
        },
    }
    prog = compare_with_reported_progress(model, data, tickers=["000001.SZ"], as_of="2026-09-01")
    assert prog and any(r["metric"] == "revenue" for r in prog["rows"])
    assert not any(r["metric"] == "parent_net_income" for r in prog["rows"])

    # Mixed unit_scale in bull/bear must fail closed (return None)
    mixed = _three_year_handoff()
    for row in mixed["forecasts"]:
        if row.get("scenario") == "bull":
            row["unit_scale"] = "1"
            break
    assert handoff_forecasts_to_progress_model(mixed, profile={"issuer_id": "issuer-a"}) is None


def test_v17_sorts_newest_handoff_delivers_followups_and_invalidates_cleanly():
    from research.followups import NotionFollowups
    from research.v17 import StageWait, build_adapters
    from research.workflow import invalidate_inputs

    old_h = _three_year_handoff()
    old_h["handoff_id"] = "wb_old"
    old_h["knowledge_cutoff"] = "2026-01-15T00:00:00+08:00"
    old_h["producer"]["created_at"] = "2026-01-16T00:00:00+08:00"
    old_h["monitoring"] = [{
        "question_id": "q_old", "condition": "旧条件", "due_date": "2026-04-30",
        "verification": "旧验证", "action": "旧动作",
    }]

    new_h = _three_year_handoff()
    new_h["handoff_id"] = "wb_new"
    new_h["knowledge_cutoff"] = "2026-06-01T00:00:00+08:00"
    new_h["producer"]["created_at"] = "2026-06-02T00:00:00+08:00"
    new_h["monitoring"] = [{
        "question_id": "q_new", "condition": "新条件", "due_date": "2026-10-31",
        "verification": "新验证", "action": "新动作",
    }]

    class MemStore:
        def __init__(self):
            self.rows = {}

        def get(self, tid):
            return self.rows.get(tid)

        def put(self, doc):
            self.rows[doc["task_id"]] = json.loads(json.dumps(doc, ensure_ascii=False))

    class MemDrive:
        def put_immutable(self, path, raw):
            return {"path": path, "verified": True, "sha256": "a" * 64}

        def read(self, path):
            return b"{}", {}

    class MemReports:
        def publish(self, task, analysis, files, candidate=True, lookup_only=False):
            return {"id": "rep-1", "url": "https://notion.example/rep-1", "verified": True}

    store = MemStore()
    stub = NotionStub()
    followups = NotionFollowups(stub, "ds-1")
    task = {
        "task_id": "v17:t1", "issuer_id": "issuer-a", "ticker": "000001.SZ",
        "company_path": "投资/消费/样本", "created_at": "2026-08-30T00:00:00+00:00",
        "updated_at": "2026-08-30T00:00:00+00:00",
        "input_digest": "d1", "stage_refs": {"coverage": "r1"}, "checkpoints": {},
        "trigger": {"kind": "earnings"},
        "previous_state": {},
        "profile": {"issuer_id": "issuer-a", "tickers": ["000001.SZ"], "market": "CN"},
    }
    store.put(task)

    def fake_reasoner(stage, payload, t):
        return {}

    adapters = build_adapters(
        store=store, drive=MemDrive(), reports=MemReports(), env={},
        documents=lambda t: [{"issuer_id": "issuer-a", "document_id": "d1", "document_type": "annual",
                              "revision_id": "rev1", "published_at": "2026-08-25T00:00:00+08:00",
                              "pages": [{"text": "营业收入 1000"}], "source": {"url": "https://x/1"}}],
        reasoner=fake_reasoner,
        followups=followups,
    )
    payload = {
        "coverage": {
            "documents": [{"issuer_id": "issuer-a", "document_id": "d1", "document_type": "annual",
                           "revision_id": "rev1", "published_at": "2026-08-25T00:00:00+08:00",
                           "pages": [{"text": "营业收入 1000"}], "source": {"url": "https://x/1"}}],
            "prior_context": [{"handoff": old_h}, {"handoff": new_h}],
            "financial_data": {
                "issuer_id": "issuer-a",
                "statements": {
                    "income": [{
                        "ticker": "000001.SZ", "period_basis": "year_to_date", "scope": "consolidated",
                        "currency": "CNY", "period_start": "2026-01-01", "period_end": "2026-06-30",
                        "source_provider": "Tushare", "value_kind": "reported_provider",
                        "revision_id": "rev_ytd", "published_at": "2026-08-20",
                        "values": {"revenue": 500_000_000.0, "n_income_attr_p": 40_000_000.0},
                    }],
                    "cashflow": [],
                },
            },
        },
        "retrieval": {"reports": []},
    }
    cand = adapters["model"](payload, task)
    assert cand["forecast_progress"]["handoff_id"] == "wb_new"
    assert len(task["handoff_followups"]) == 4
    assert task["handoff_followups"][0]["handoff_id"] == "wb_new"

    payload["model"] = cand
    payload["report"] = {
        "title": "更新", "position": "中性",
        "next_actions": [{"condition": "报告条件", "action": "报告动作", "due": "2026-11-30", "evidence": ["rev1"]}],
        "open_questions": ["报告待核实问题"],
    }
    res = adapters["publish"](payload, task)
    assert res["verified"] is True
    assert len(stub.pages) == 5  # 3 unique from handoffs (shared gap deduped) + 2 from report
    assert len(task["checkpoints"]["followups"]) == 5

    # Re-run publish stays idempotent via checkpoints & lookup_only
    adapters["publish"](payload, task)
    assert len(stub.pages) == 5

    # Unverified follow-up forces StageWait instead of false completion
    class FlakyFollowups:
        def ensure(self, item, *, report_ref, lookup_only=False):
            return None

    flaky_adapters = build_adapters(
        store=store, drive=MemDrive(), reports=MemReports(), env={},
        documents=lambda t: [], reasoner=fake_reasoner, followups=FlakyFollowups(),
    )
    task["checkpoints"].pop("followups", None)
    task["checkpoints"].pop("followups_attempted", None)
    import pytest
    with pytest.raises(StageWait):
        flaky_adapters["publish"](payload, task)

    invalidate_inputs(task, "new_filing")
    assert "handoff_followups" not in task
    assert task["checkpoints"] == {}
