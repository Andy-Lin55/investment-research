from research.guidance_comparison import compare_management_guidance


def _model():
    return {
        "issuer_id": "issuer-a", "currency": "CNY", "status": "ready_for_review", "model_version": "m1",
        "scenarios": {
            "base": [{"year": 2026, "status": "ready_for_review", "income": {"parent_net_income": 210_000, "eps": 2.6}}],
            "bear": [{"year": 2026, "status": "ready_for_review", "income": {"parent_net_income": 140_000, "eps": 2.3}}],
        },
    }


def _doc(text, **kw):
    return dict({"issuer_id": "issuer-a", "document_type": "业绩预告",
                 "title": "2026年年度业绩预告", "published_at": "2026-10-30", "revision_id": "g1",
                 "pages": [{"page": 1, "text": text}], "source": {"original": {"verified": True}}}, **kw)


ORIGINAL = """业绩预告期间：2026 年1 月1 日至2026 年12 月31 日。
单位：万元
项目
本报告期
上年同期
归属于上市公司股
东的净利润
15.0
～
20.0
13.20
扣除非经常性损益后的净利润
9.00～10.00
基本每股收益（元/
股）
2.55
～
2.67
2.29
"""


def test_annual_company_guidance_uses_literal_units_and_boundaries():
    out = compare_management_guidance(_model(), [_doc(ORIGINAL)], issuer_id="issuer-a", as_of="2026-11-03")
    by = {(r["metric"], r["case"]): r for r in out["rows"]}
    profit = by["parent_net_income", "base"]
    assert (profit["low"], profit["high"], profit["difference"], profit["position"]) == (150_000, 200_000, 10_000, "above")
    assert profit["source_page"] == 1 and profit["quote"] in ORIGINAL and profit["source_ref"] == "g1"
    assert by["parent_net_income", "bear"]["difference"] == -10_000
    assert by["eps", "base"]["position"] == "within"
    assert len(by) == 4 and not out["gaps"]


def test_unreadable_latest_revision_blocks_stale_guidance_and_future_revision_is_ignored():
    latest = _doc(ORIGINAL.replace("单位：万元", "单位："), revision_id="g2",
                  published_at="2026-11-02", document_type="业绩预告修正")
    future = _doc(ORIGINAL, revision_id="g3", published_at="2026-11-05")
    out = compare_management_guidance(_model(), [_doc(ORIGINAL), latest, future],
                                      issuer_id="issuer-a", as_of="2026-11-03")
    assert [(r["metric"], r["source_ref"]) for r in out["rows"]] == [("eps", "g2"), ("eps", "g2")]

    unreadable = _doc("业绩预告修订，原件缺页。", revision_id="g2", published_at="2026-11-02")
    out = compare_management_guidance(_model(), [_doc(ORIGINAL), unreadable],
                                      issuer_id="issuer-a", as_of="2026-11-03")
    assert not out["rows"] and out["gaps"][0]["reason"] == "latest_guidance_range_not_verified"


def test_interim_original_cannot_be_compared_to_annual_model():
    interim = _doc(ORIGINAL.replace("12 月31 日", "6 月30 日"))
    out = compare_management_guidance(_model(), [interim], issuer_id="issuer-a", as_of="2026-11-03")
    assert not out["rows"] and out["gaps"]


def test_unverified_original_is_not_promoted_to_company_guidance():
    unverified = _doc(ORIGINAL, source={"original": {"verified": False}})
    out = compare_management_guidance(_model(), [unverified], issuer_id="issuer-a", as_of="2026-11-03")
    assert not out["rows"] and out["gaps"]
