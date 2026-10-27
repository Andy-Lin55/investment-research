"""Normalize supplied Tushare consolidated A-share industrial statements.

No data fetching, forecasts or external writes. Provider rows are not proof of
historical point-in-time availability; the complete source payload is retained
separately by the workpaper exporter.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
import hashlib
import json
import math
import re

STATEMENTS = ("income", "balancesheet", "cashflow")
_META = {"ts_code", "ann_date", "f_ann_date", "end_date", "report_type", "comp_type", "end_type", "update_flag"}
_NON_ADDITIVE = {"basic_eps", "diluted_eps", "c_cash_equ_beg_period", "c_cash_equ_end_period",
                 "beg_bal_cash", "end_bal_cash", "beg_bal_cash_equ", "end_bal_cash_equ"}
_CORE_FIELDS = {"income": (("revenue", "total_revenue"), ("n_income",)),
                "balancesheet": (("total_assets",), ("total_liab",), ("total_hldr_eqy_inc_min_int",)),
                "cashflow": (("n_cashflow_act",), ("n_cashflow_inv_act",), ("n_cash_flows_fnc_act",))}


def _day(value):
    if not isinstance(value, str):
        raise ValueError("date must be a string")
    try:
        return date.fromisoformat(value if "-" in value else f"{value[:4]}-{value[4:6]}-{value[6:]}")
    except ValueError as error:
        raise ValueError(f"invalid date: {value}") from error


def _number(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("statement values must be numbers or missing")
    if math.isnan(value):
        return None
    if not math.isfinite(value):
        raise ValueError("infinite statement value")
    return value


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def normalize_statements(payloads, *, ticker, as_of, currency, annual_years=5, interim_periods=8):
    """Choose latest disclosed consolidated YTD rows and retain field provenance.

    v1 supports calendar-year industrial A-shares and provider amounts in CNY.
    Report types 1/4 are consolidated cumulative; parent and single-quarter
    source rows are excluded. EPS remains per share and shares remain counts.
    """
    cutoff = _day(as_of)
    if not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", ticker) or currency != "CNY":
        raise ValueError("v1 requires an A-share ticker and explicitly confirmed CNY")
    if not isinstance(payloads, dict) or any(k not in payloads for k in STATEMENTS):
        raise ValueError("all three statement source payloads are required")
    if any(isinstance(n, bool) or not isinstance(n, int) or n < 1 for n in (annual_years, interim_periods)):
        raise ValueError("history coverage counts must be positive integers")
    sources, excluded = {}, []
    for kind in STATEMENTS:
        envelope = payloads[kind]
        rows = envelope.get("rows") if isinstance(envelope, dict) else envelope
        if not isinstance(rows, list):
            raise ValueError(f"{kind} must contain a list of source rows")
        selected, seen_ranks = {}, {}
        for index, raw in enumerate(rows):
            if not isinstance(raw, dict):
                raise ValueError("source row must be an object")
            if raw.get("ts_code") != ticker or str(raw.get("report_type")) not in {"1", "4"}:
                excluded.append({"statement": kind, "row": index, "reason": "different_company_or_nonconsolidated_ytd"})
                continue
            if str(raw.get("comp_type")) != "1":
                raise ValueError("bank/insurance/securities statements require a separate sector template")
            end = _day(raw.get("end_date"))
            if (end.month, end.day) not in {(3, 31), (6, 30), (9, 30), (12, 31)}:
                raise ValueError("v1 requires calendar-quarter statement dates")
            published = max(_day(raw.get("ann_date")), _day(raw.get("f_ann_date") or raw.get("ann_date")))
            if end > cutoff or published > cutoff:
                excluded.append({"statement": kind, "row": index, "reason": "not_disclosed_by_cutoff"})
                continue
            if published < end:
                raise ValueError("statement publication precedes its reporting date")
            values = {k: _number(v) for k, v in raw.items() if k not in _META}
            rank = (published, int(str(raw["report_type"]) == "4"), str(raw.get("update_flag", "0")))
            identity = (end, rank)
            if identity in seen_ranks and values != seen_ranks[identity]:
                raise ValueError(f"conflicting equal-rank {kind} source rows for {end}")
            seen_ranks[identity] = values
            old = selected.get(end)
            if old and rank <= old[0]:
                continue
            units = {k: ("CNY/share" if k in {"basic_eps", "diluted_eps"} else "shares" if k == "total_share" else currency) for k in values}
            normalized = {
                "statement": kind, "ticker": ticker, "currency": currency,
                "scope": "consolidated", "report_type": str(raw["report_type"]),
                "period_start": None if kind == "balancesheet" else f"{end.year}-01-01",
                "period_end": end.isoformat(), "period_basis": "instant" if kind == "balancesheet" else "year_to_date",
                "published_at": published.isoformat(), "value_kind": "reported_provider",
                "values": values, "units": units, "source_provider": "Tushare",
                "source_row": index,
                "source_locator": f"{kind}(ts_code={ticker}) / rows[{index}]",
            }
            normalized["revision_id"] = _hash(normalized)
            selected[end] = (rank, normalized)
        sources[kind] = [item[1] for _, item in sorted(selected.items())]
    periods = sorted({r["period_end"] for values in sources.values() for r in values})
    annual = [p for p in periods if p.endswith("12-31")][-annual_years:]
    recent = periods[-interim_periods:]
    keep = set(annual + recent)
    retained = {k: [r for r in rows if r["period_end"] in keep] for k, rows in sources.items()}
    missing = []
    for period in sorted(keep):
        for kind in STATEMENTS:
            if not any(r["period_end"] == period for r in retained[kind]):
                missing.append({"period_end": period, "statement": kind})
    missing_data = []
    for kind, rows in retained.items():
        for record in rows:
            absent = [" or ".join(group) for group in _CORE_FIELDS[kind]
                      if all(record["values"].get(field) is None for field in group)]
            if absent:
                missing_data.append({"period_end": record["period_end"], "statement": kind, "missing_inputs": absent})
    covered = (len(annual) == annual_years and len(recent) == interim_periods
               and not missing and not missing_data and all(retained.values()))
    return {"schema_version": 1, "ticker": ticker, "currency": currency, "as_of": cutoff.isoformat(),
            "statements": retained, "annual_periods": annual, "recent_periods": recent,
            "missing_statements": missing, "missing_financial_data": missing_data, "excluded": excluded,
            "status": "ready_for_review" if covered else "incomplete", "human_review": "pending",
            "point_in_time_certified": False,
            "coverage_target": {"annual_years": annual_years, "recent_periods": interim_periods}}


def reconcile(package, *, tolerance=1.0):
    """Checks read facts only; unavailable inputs never become passing zeroes."""
    if not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError("invalid reconciliation tolerance")
    indexed = {k: {r["period_end"]: r["values"] for r in rows} for k, rows in package["statements"].items()}
    results = []
    for period in sorted(set(package["annual_periods"] + package["recent_periods"])):
        income, balance, cash = (indexed[k].get(period, {}) for k in STATEMENTS)
        rules = [
            ("balance_sheet", [(balance, "total_assets", 1), (balance, "total_liab", -1), (balance, "total_hldr_eqy_inc_min_int", -1)]),
            ("net_income", [(income, "total_profit", 1), (income, "income_tax", -1), (income, "n_income", -1)]),
            ("minority_interest", [(income, "n_income", 1), (income, "n_income_attr_p", -1), (income, "minority_gain", -1)]),
            ("cash_components", [(cash, "n_cashflow_act", 1), (cash, "n_cashflow_inv_act", 1), (cash, "n_cash_flows_fnc_act", 1), (cash, "eff_fx_flu_cash", 1), (cash, "n_incr_cash_cash_equ", -1)]),
            ("cash_rollforward", [(cash, "c_cash_equ_beg_period", 1), (cash, "n_incr_cash_cash_equ", 1), (cash, "c_cash_equ_end_period", -1)]),
            ("net_income_to_cf", [(income, "n_income", 1), (cash, "net_profit", -1)]),
        ]
        for name, terms in rules:
            absent = [key for values, key, _ in terms if values.get(key) is None]
            difference = None if absent else sum(values[key] * sign for values, key, sign in terms)
            results.append({"period_end": period, "check": name, "status": "missing" if absent else "pass" if abs(difference) <= tolerance else "fail",
                            "difference": difference, "missing_inputs": absent, "tolerance": tolerance})
        money, equivalents = balance.get("money_cap"), cash.get("c_cash_equ_end_period")
        results.append({"period_end": period, "check": "cash_scope", "status": "missing" if money is None or equivalents is None else "needs_reconciliation",
                        "difference": None if money is None or equivalents is None else money - equivalents,
                        "note": "BS monetary funds and CF cash equivalents have different definitions; a source-backed bridge is required even if amounts match."})
    return results


def derive_quarter(current, previous, *, comparability=None):
    """Subtract adjacent YTD flows only with source-backed revision comparability.

    Dates/report types alone cannot prove a common accounting basis. The caller
    must supply a source locator and an explicit confirmation tied to both exact
    revision IDs. No production caller makes this confirmation automatically.
    """
    current, previous = deepcopy(current), deepcopy(previous)
    end, prior_end = _day(current["period_end"]), _day(previous["period_end"])
    same = ("ticker", "currency", "scope", "report_type", "statement", "period_start")
    if (current["statement"] == "balancesheet" or any(current[k] != previous[k] for k in same)
            or current["period_basis"] != "year_to_date" or previous["period_basis"] != "year_to_date"
            or end.year != prior_end.year or end.month - prior_end.month != 3
            or (end.month, end.day) not in {(6, 30), (9, 30), (12, 31)}
            or prior_end not in {date(end.year, 3, 31), date(end.year, 6, 30), date(end.year, 9, 30)}):
        raise ValueError("quarter derivation requires adjacent comparable consolidated YTD flows")
    if _day(previous["published_at"]) > _day(current["published_at"]):
        raise ValueError("quarter derivation cannot mix a later prior-period revision with an older current report")
    if (not isinstance(comparability, dict) or comparability.get("basis_confirmed") is not True
            or comparability.get("current_revision_id") != current["revision_id"]
            or comparability.get("previous_revision_id") != previous["revision_id"]
            or not isinstance(comparability.get("source_locator"), str)
            or not comparability["source_locator"].strip()):
        raise ValueError("quarter derivation requires source-backed comparability evidence for both revisions")
    values = {}
    for key, value in current["values"].items():
        if key in _NON_ADDITIVE:
            continue
        if key in previous["values"] and current["units"][key] != previous["units"].get(key):
            raise ValueError("quarter derivation requires matching units")
        prior = previous["values"].get(key)
        values[key] = None if value is None or prior is None else value - prior
    current.update(values=values, units={k: current["units"][k] for k in values}, value_kind="derived",
                   period_start=(prior_end + timedelta(days=1)).isoformat(), period_basis="single_quarter",
                   derived_from=[previous["revision_id"], current["revision_id"]],
                   comparability_evidence=deepcopy(comparability),
                   published_at=max(current["published_at"], previous["published_at"]))
    current["revision_id"] = _hash(current)
    return current
