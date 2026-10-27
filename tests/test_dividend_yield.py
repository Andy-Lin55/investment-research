from copy import deepcopy
from datetime import date
import importlib
import time
from unittest.mock import MagicMock

import pandas as pd
import pytest
import requests

from data_ingestion import dividend_yield


AS_OF = date(2026, 10, 27)


def distribution(day="2026/04/17", plan="每股派美元0.0941元(相当于港币0.737432元(计算值))"):
    return {
        "SECURITY_CODE": "09896", "SECUCODE": "09896.HK",
        "EX_DIVIDEND_DATE": day, "PLAN_EXPLAIN": plan,
        "REPORT_TYPE": "年度分配", "DIVIDEND_DATE": "2026/04/29",
    }


@pytest.fixture
def source(monkeypatch):
    payload = {
        "price": {"data": {"code": "09896", "market": 116, "klines": [
            "2026-10-26,19.87,19.82,19.88,19.52,1756600",
            "2026-10-27,19.82,19.09,19.92,19.07,964600",
        ]}},
        "dividends": {"success": True, "result": {
            "pages": 1, "count": 2, "data": [
                distribution(), distribution("2025/09/04", "每股派港币0.565584元"),
            ],
        }},
    }

    def get_json(url, params):
        if "kline" in url:
            assert params["secid"] == "116.09896"
            assert params["fqt"] == "0"
            assert params["end"] == "20261026"
            return deepcopy(payload["price"])
        assert params["filter"] == '(SECURITY_CODE="09896")(IS_BFP="0")'
        return deepcopy(payload["dividends"])

    monkeypatch.setattr(dividend_yield, "_get_json", get_json)
    return payload


def calculate():
    return dividend_yield.calculate_hk_dividend_yield("9896.HK", as_of=AS_OF)


def test_reconstructs_cash_dividends_and_uses_completed_unadjusted_close(source):
    result = calculate()
    assert result.get("dividend_yield") == 3.7206
    assert result["dividend_ttm_hkd"] == pytest.approx(0.737432)
    assert result["price_hkd"] == 19.82
    assert result["price_date"] == "2026-10-26"
    assert result["window_start"] == "2025-10-26"


def test_includes_special_cash_dividend_once_and_ignores_future_ex_date(source):
    rows = source["dividends"]["result"]
    special = distribution("2026/01/02", "每股派港币0.25元")
    special["REPORT_TYPE"] = "特别分配"
    rows["data"] += [special, deepcopy(special), distribution("2026/11/01", "每股派港币9元")]
    rows["count"] = 5
    assert calculate()["dividend_yield"] == 4.982


@pytest.mark.parametrize("plan", [
    "每股派美元0.0941元", "每10股派港币1元", "每股送0.1股",
    "每股派人民币1元", "每股派港币-1元", "每股派港币0.2元并送0.1股",
    "取消每股派港币0.2元", "待定", None,
])
def test_unknown_currency_or_non_cash_plan_does_not_become_zero(source, plan):
    source["dividends"]["result"]["data"][0]["PLAN_EXPLAIN"] = plan
    assert calculate() == {}


def test_conflicting_versions_on_same_ex_date_are_not_double_counted(source):
    rows = source["dividends"]["result"]
    rows["data"].append(distribution(plan="每股派港币1元"))
    rows["count"] += 1
    assert calculate() == {}


@pytest.mark.parametrize("data", [None, {"pages": 1, "count": 0, "data": []},
                                      {"pages": 2, "count": 201, "data": [distribution()]}])
def test_empty_or_partial_dividend_history_does_not_create_yield(source, data):
    source["dividends"]["result"] = data
    assert calculate() == {}


@pytest.mark.parametrize("line", [
    "2026-10-01,20,20,20,20,100", "2026-10-26,20,0,20,20,100",
    "2026-10-26,20,nan,20,20,100", "2026-10-26,20,inf,20,20,100",
])
def test_stale_zero_and_non_finite_quotes_are_rejected(source, line):
    source["price"]["data"]["klines"] = [line]
    assert calculate() == {}


def test_wrong_security_quote_is_rejected(source):
    source["price"]["data"]["code"] = "00700"
    assert calculate() == {}


@pytest.mark.parametrize("value", [[], "invalid", 1])
def test_malformed_quote_does_not_break_financial_update(source, value):
    source["price"]["data"] = value
    assert calculate() == {}


def test_missing_ex_date_is_not_silently_omitted(source):
    source["dividends"]["result"]["data"][0]["EX_DIVIDEND_DATE"] = None
    assert calculate() == {}


def test_uses_calendar_year_boundary_not_fixed_365_days(source, monkeypatch):
    source["price"]["data"]["klines"] = ["2024-03-01,20,20,20,20,100"]
    source["dividends"]["result"] = {"pages": 1, "count": 2, "data": [
        distribution("2023/03/01", "每股派港币9元"),
        distribution("2023/03/02", "每股派港币1元"),
    ]}
    monkeypatch.setattr(dividend_yield, "_get_json", lambda url, params:
                        deepcopy(source["price"] if "kline" in url else source["dividends"]))
    result = dividend_yield.calculate_hk_dividend_yield("9896.HK", as_of=date(2024, 3, 2))
    assert result["dividend_yield"] == 5.0


def test_rmb_counter_and_ads_symbols_are_not_treated_as_hkd_ordinary_shares(source):
    assert dividend_yield.calculate_hk_dividend_yield("87001.HK", as_of=AS_OF) == {}
    assert dividend_yield.calculate_hk_dividend_yield("MNSO", as_of=AS_OF) == {}


def test_network_failure_remains_missing(monkeypatch):
    def timeout(*args, **kwargs):
        raise requests.Timeout("source unavailable")
    monkeypatch.setattr(dividend_yield, "_get_json", timeout)
    assert calculate() == {}


def test_transport_disconnect_retries_before_giving_up(monkeypatch):
    session = MagicMock()
    session.__enter__.return_value = session
    response = MagicMock()
    response.json.return_value = {"ready": True}
    session.get.side_effect = [requests.ConnectionError("closed"), response]
    monkeypatch.setattr(dividend_yield.requests, "Session", lambda: session)
    monkeypatch.setattr(time, "sleep", lambda delay: None)
    assert dividend_yield._get_json("https://example.com", {}) == {"ready": True}
    assert session.get.call_count == 2


def test_transport_retries_are_bounded(monkeypatch):
    session = MagicMock()
    session.__enter__.return_value = session
    session.get.side_effect = requests.Timeout("unavailable")
    monkeypatch.setattr(dividend_yield.requests, "Session", lambda: session)
    monkeypatch.setattr(time, "sleep", lambda delay: None)
    with pytest.raises(requests.Timeout):
        dividend_yield._get_json("https://example.com", {})
    assert session.get.call_count == 3


@pytest.fixture
def financial_fetcher(monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "TUSHARE_TOKEN", "test-token")
    return importlib.import_module("data_ingestion.tushare_fetcher")


@pytest.mark.parametrize("direct_yield", [None, float("nan"), float("inf"), -1])
def test_missing_direct_yield_falls_back_without_losing_other_metrics(financial_fetcher, monkeypatch, direct_yield):
    monkeypatch.setattr(financial_fetcher.ak, "stock_hk_financial_indicator_em", lambda **kwargs:
                        pd.DataFrame([{"市盈率": 16.6, "股息率TTM(%)": direct_yield}]))
    monkeypatch.setattr(dividend_yield, "calculate_hk_dividend_yield", lambda ticker:
                        {"dividend_yield": 3.7206})
    result = financial_fetcher.get_financial_snapshot("9896.HK")
    assert result == {"pe_ttm": 16.6, "dividend_yield": 3.7206}


@pytest.mark.parametrize("direct_yield", [0, 4.5])
def test_valid_direct_yield_is_preserved(financial_fetcher, monkeypatch, direct_yield):
    monkeypatch.setattr(financial_fetcher.ak, "stock_hk_financial_indicator_em", lambda **kwargs:
                        pd.DataFrame([{"股息率TTM(%)": direct_yield}]))
    def unavailable(*args, **kwargs):
        raise AssertionError("A valid primary yield must not trigger fallback")
    monkeypatch.setattr(dividend_yield, "calculate_hk_dividend_yield", unavailable)
    assert financial_fetcher.get_financial_snapshot("9896.HK")["dividend_yield"] == direct_yield


@pytest.mark.parametrize("failed", [False, True])
def test_empty_or_failed_primary_api_still_attempts_calculation(financial_fetcher, monkeypatch, failed):
    def primary(**kwargs):
        if failed:
            raise requests.Timeout("primary unavailable")
        return pd.DataFrame()
    monkeypatch.setattr(financial_fetcher.ak, "stock_hk_financial_indicator_em", primary)
    monkeypatch.setattr(dividend_yield, "calculate_hk_dividend_yield", lambda ticker:
                        {"dividend_yield": 3.7206})
    assert financial_fetcher.get_financial_snapshot("9896.HK") == {"dividend_yield": 3.7206}


def test_failed_calculation_preserves_primary_data(financial_fetcher, monkeypatch):
    monkeypatch.setattr(financial_fetcher.ak, "stock_hk_financial_indicator_em", lambda **kwargs:
                        pd.DataFrame([{"市盈率": 16.6}]))
    monkeypatch.setattr(dividend_yield, "calculate_hk_dividend_yield", lambda ticker: {})
    assert financial_fetcher.get_financial_snapshot("9896.HK") == {"pe_ttm": 16.6}
