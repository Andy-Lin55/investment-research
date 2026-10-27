from copy import deepcopy
import importlib

import pytest


def api():
    try:
        return importlib.import_module("financials.statements")
    except ModuleNotFoundError:
        pytest.fail("Financial statement normalization is not implemented")


def row(end="20251231", ann="20260320", **values):
    return dict(ts_code="000001.SZ", end_date=end, ann_date=ann, f_ann_date=ann,
                report_type="1", comp_type="1", update_flag="1", **values)


def inputs():
    return {
        "income": [row(revenue=100, oper_cost=60, total_profit=30, income_tax=5,
                       n_income=25, n_income_attr_p=23, minority_gain=2, basic_eps=2.3)],
        "balancesheet": [row(total_assets=200, total_liab=120, total_hldr_eqy_inc_min_int=80,
                             money_cap=35, total_share=10)],
        "cashflow": [row(net_profit=25, n_cashflow_act=30, n_cashflow_inv_act=-15,
                         n_cash_flows_fnc_act=-5, eff_fx_flu_cash=0, n_incr_cash_cash_equ=10,
                         c_cash_equ_beg_period=20, c_cash_equ_end_period=30)],
    }


def normalize(data=None, **kwargs):
    return api().normalize_statements(data or inputs(), ticker="000001.SZ", as_of="2026-10-27",
                                      currency="CNY", **kwargs)


def test_keeps_all_three_statements_raw_units_and_missing_versus_zero():
    data = inputs()
    data["income"][0].update(unknown_amount=12, sell_exp=None, fin_exp=float("nan"), other_amount=0)
    package = normalize(data)
    r = package["statements"]["income"][0]
    assert r["values"]["revenue"] == 100
    assert r["values"]["unknown_amount"] == 12
    assert r["values"]["other_amount"] == 0
    assert r["values"]["sell_exp"] is None
    assert r["values"]["fin_exp"] is None
    assert r["units"]["basic_eps"] == "CNY/share"
    assert package["statements"]["balancesheet"][0]["units"]["total_share"] == "shares"
    assert r["period_start"] == "2025-01-01"
    assert package["statements"]["balancesheet"][0]["period_start"] is None
    assert package["point_in_time_certified"] is False


def test_excludes_future_disclosure_and_wrong_company_without_losing_valid_period():
    data = inputs()
    data["income"] += [row("20260930", "20261030", revenue=999),
                        dict(row(revenue=1), ts_code="000002.SZ")]
    package = normalize(data)
    assert [r["period_end"] for r in package["statements"]["income"]] == ["2025-12-31"]
    assert len(package["excluded"]) == 2


def test_actual_disclosure_date_also_controls_visibility():
    data = inputs()
    data["income"][0]["f_ann_date"] = "20261028"
    package = normalize(data)
    assert package["statements"]["income"] == []
    assert package["status"] == "incomplete"


def test_parent_and_single_quarter_rows_never_replace_consolidated_ytd():
    data = inputs()
    data["income"] += [dict(row(revenue=999), report_type=t) for t in ("2", "6")]
    assert normalize(data)["statements"]["income"][0]["values"]["revenue"] == 100


def test_same_period_latest_disclosed_revision_wins():
    data = inputs()
    data["income"] += [row(ann="20260820", revenue=110)]
    r = normalize(data)["statements"]["income"][0]
    assert r["values"]["revenue"] == 110
    assert r["published_at"] == "2026-08-20"
    assert r["source_row"] == 1


def test_equal_rank_conflicting_source_rows_are_not_silently_selected():
    data = inputs()
    data["income"].append(row(revenue=999))
    with pytest.raises(ValueError, match="conflict"):
        normalize(data)


def test_duplicate_null_and_nan_values_compare_as_missing():
    data = inputs()
    data["income"][0]["unused"] = None
    duplicate = deepcopy(data["income"][0]); duplicate["unused"] = float("nan")
    data["income"].append(duplicate)
    assert len(normalize(data)["statements"]["income"]) == 1


def test_reconciliation_checks_catch_wrong_balance_and_cash_without_filling_values():
    package = normalize()
    checks = api().reconcile(package)
    assert next(c for c in checks if c["check"] == "balance_sheet")["status"] == "pass"
    assert next(c for c in checks if c["check"] == "cash_rollforward")["status"] == "pass"
    assert next(c for c in checks if c["check"] == "cash_scope")["status"] == "needs_reconciliation"
    data = inputs(); data["balancesheet"][0]["total_assets"] = 202
    data["cashflow"][0]["c_cash_equ_end_period"] = 32
    checks = api().reconcile(normalize(data))
    assert next(c for c in checks if c["check"] == "balance_sheet")["status"] == "fail"
    assert next(c for c in checks if c["check"] == "cash_rollforward")["status"] == "fail"


def test_missing_check_inputs_are_missing_instead_of_passing_zero():
    data = inputs(); data["cashflow"][0]["eff_fx_flu_cash"] = None
    checks = api().reconcile(normalize(data))
    assert next(c for c in checks if c["check"] == "cash_components")["status"] == "missing"


def test_derive_quarter_is_explicit_and_uses_adjacent_cumulative_periods():
    data = inputs()
    data["income"] = [row("20250331", "20250420", revenue=40, basic_eps=0.4),
                       row("20250630", "20250820", revenue=90, basic_eps=0.85)]
    rows = normalize(data)["statements"]["income"]
    q = api().derive_quarter(rows[1], rows[0])
    assert q["values"]["revenue"] == 50
    assert "basic_eps" not in q["values"]
    assert q["value_kind"] == "derived"
    assert q["period_start"] == "2025-04-01"
    assert len(q["derived_from"]) == 2


@pytest.mark.parametrize("change", ["year", "gap", "currency", "scope", "balance"])
def test_quarter_derivation_rejects_noncomparable_inputs(change):
    data = inputs()
    data["income"] = [row("20250331", "20250420", revenue=40), row("20250630", "20250820", revenue=90)]
    a, b = normalize(data)["statements"]["income"]
    if change == "year": a["period_end"] = "2024-03-31"
    if change == "gap": a["period_end"] = "2025-01-31"
    if change == "currency": a["currency"] = "USD"
    if change == "scope": a["report_type"] = "4"
    if change == "balance": b["statement"] = "balancesheet"
    with pytest.raises(ValueError):
        api().derive_quarter(b, a)


def test_selects_five_annual_and_eight_recent_available_periods():
    data = {key: [row(f"{y}{end}", f"{y+1}0220", revenue=100) for y in range(2019, 2026)
                  for end in ("0331", "0630", "0930", "1231")] for key in inputs()}
    package = normalize(data)
    assert package["annual_periods"] == ["2021-12-31", "2022-12-31", "2023-12-31", "2024-12-31", "2025-12-31"]
    assert len(package["recent_periods"]) == 8


@pytest.mark.parametrize("change", ["date", "amount", "currency", "bank"])
def test_invalid_metadata_or_nonfinite_amount_is_not_silently_accepted(change):
    data = inputs()
    kwargs = {}
    if change == "date": data["income"][0]["end_date"] = "20251331"
    if change == "amount": data["income"][0]["revenue"] = float("inf")
    if change == "currency": kwargs["currency"] = ""
    if change == "bank": data["income"][0]["comp_type"] = "2"
    with pytest.raises(ValueError):
        api().normalize_statements(data, ticker="000001.SZ", as_of="2026-10-27", currency=kwargs.get("currency", "CNY"))
