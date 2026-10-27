"""Conservative HKD ordinary-share dividend yield fallback, in percentage points."""
from __future__ import annotations

import csv
from datetime import date, datetime, timedelta
from decimal import Decimal
import re
import time
from zoneinfo import ZoneInfo

import requests
from loguru import logger


_DIVIDENDS_URL = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
_PRICE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
_AMOUNT = r"([0-9]+(?:\.[0-9]+)?)"


def _get_json(url: str, params: dict) -> dict:
    with requests.Session() as session:
        session.trust_env = False
        for attempt in range(3):
            try:
                response = session.get(url, params=params, timeout=10)
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError("Unexpected source response")
                return payload
            except (requests.ConnectionError, requests.Timeout):
                if attempt == 2:
                    raise
                time.sleep(0.5 * (attempt + 1))


def _source_date(value) -> date:
    return datetime.fromisoformat(str(value).replace("/", "-")).date()


def _cash_hkd(plan: str) -> Decimal:
    # Accept only per-ordinary-share cash, with an explicit HKD amount.
    if not isinstance(plan, str):
        raise ValueError("Missing dividend plan")
    text = re.sub(r"\s+", "", plan).replace("（", "(").replace("）", ")")
    direct = re.fullmatch(r"每股派(?:港币|港元|HKD|HK\$)" + _AMOUNT + r"元?", text)
    if direct:
        return Decimal(direct.group(1))
    converted = re.fullmatch(
        r"每股派(?:美元|人民币|欧元|英镑|日元)" + _AMOUNT
        + r"元\(相当于(?:港币|港元)" + _AMOUNT + r"元\(计算值\)\)", text,
    )
    if converted:
        return Decimal(converted.group(2))
    raise ValueError("Unsupported cash plan or unverified currency conversion")


def calculate_hk_dividend_yield(ticker: str, *, as_of: date | None = None) -> dict:
    """Use ex-dated cash dividends / latest completed HKD close; {} means unknown.

    Includes special cash dividends, does not annualize or substitute ADS prices.
    Converted amounts are the dividend source's explicitly stated HKD equivalents.
    """
    if not re.fullmatch(r"[0-9]{4,5}\.HK", ticker.upper()):
        return {}
    code = ticker.split(".")[0].zfill(5)
    if int(code) >= 80000:  # RMB counters must not use an HKD denominator.
        return {}
    today = as_of or datetime.now(ZoneInfo("Asia/Hong_Kong")).date()
    try:
        quote = _get_json(_PRICE_URL, {
            "secid": f"116.{code}", "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56", "klt": "101", "fqt": "0",
            "end": (today - timedelta(days=1)).strftime("%Y%m%d"), "lmt": "20",
        }).get("data")
        if not isinstance(quote, dict) or quote.get("code") != code or quote.get("market") != 116:
            raise ValueError("Missing quote or wrong security")
        closes = []
        for line in quote.get("klines", []):
            fields = next(csv.reader([line]))
            day = _source_date(fields[0])
            if day < today:
                closes.append((day, Decimal(fields[2])))
        if not closes:
            raise ValueError("No completed trading-day close")
        price_day, price = max(closes, key=lambda item: item[0])
        if (today - price_day).days > 10 or not price.is_finite() or price <= 0:
            raise ValueError("Stale or invalid unadjusted close")
        try:
            window_start = price_day.replace(year=price_day.year - 1)
        except ValueError:  # Feb 29 -> Feb 28 in the preceding calendar year.
            window_start = price_day.replace(year=price_day.year - 1, day=28)

        payload = _get_json(_DIVIDENDS_URL, {
            "reportName": "RPT_HKF10_MAIN_DIVBASIC", "columns": "ALL",
            "filter": f'(SECURITY_CODE="{code}")(IS_BFP="0")',
            "pageNumber": "1", "pageSize": "1000", "sortTypes": "-1,-1",
            "sortColumns": "NOTICE_DATE,EX_DIVIDEND_DATE", "source": "F10", "client": "PC",
        })
        history = payload.get("result")
        if not payload.get("success") or not isinstance(history, dict):
            raise ValueError("Dividend history unavailable")
        records = history.get("data")
        if (not isinstance(records, list) or not records or history.get("pages") != 1
                or history.get("count") != len(records)):
            raise ValueError("Empty or incomplete dividend history")

        total = Decimal("0")
        seen = {}
        included = []
        for row in records:
            if not isinstance(row, dict) or str(row.get("SECURITY_CODE", "")).zfill(5) != code:
                raise ValueError("Dividend history security mismatch")
            plan = row.get("PLAN_EXPLAIN")
            if plan in {"不分红", "不派息", "不分配不转增"}:
                continue
            ex_day = _source_date(row.get("EX_DIVIDEND_DATE"))
            if not window_start < ex_day <= price_day:
                continue
            key = (ex_day, row.get("REPORT_TYPE"))
            if key in seen:
                if seen[key] != plan:
                    raise ValueError("Conflicting dividend versions")
                continue
            seen[key] = plan
            amount = _cash_hkd(plan)
            total += amount
            included.append({"ex_date": ex_day.isoformat(), "cash_hkd": str(amount), "plan": plan})

        result = {
            "dividend_yield": round(float(total / price * 100), 4),
            "dividend_ttm_hkd": float(total), "price_hkd": float(price),
            "price_date": price_day.isoformat(), "window_start": window_start.isoformat(),
            "dividends": included,
        }
        logger.info(f"HK dividend yield calculated [{ticker}]: {result}")
        return result
    except (requests.RequestException, ValueError, TypeError, KeyError, IndexError, ArithmeticError, csv.Error) as exc:
        logger.warning(f"HK dividend yield remains unavailable [{ticker}]: {exc}")
        return {}
