"""
data_ingestion/tushare_fetcher.py
财务快照同步
  - A股：Tushare Pro（daily_basic + fina_indicator）          ← Tushare
  - 港股：AKShare 东方财富（stock_hk_financial_indicator_em）  ← AKShare
  - 美股：AKShare 百度+东方财富（PE/PB + ROE/增速）            ← AKShare

港股/美股 Tushare Pro 限制说明：
  - hk_fina_indicator / us_fina_indicator 均需 15000+ 积分，
    2000积分等级每接口每天仅 2 次调用，不足以支持批量更新
  - us_daily 的 PE/PB 字段数据质量差（QFIN 显示 0.51 实际约 4x）
"""
from __future__ import annotations

import contextlib
import math
from datetime import date, timedelta
from typing import Optional

import pandas as pd
import tushare as ts
import akshare as ak
from loguru import logger

from config.settings import TUSHARE_TOKEN
from notion_sync.sync_manager import get_all_tickers
from notion_sync.notion_client_wrapper import get_client
from notion_sync.db_schema import build_notion_property
from config.settings import NOTION_UNIVERSE_DB_ID

# ── Tushare Pro 初始化 ─────────────────────────────────────────
_pro = ts.pro_api(TUSHARE_TOKEN)


# ── 代理绕过（AKShare 调国内接口时使用）──────────────────────────
@contextlib.contextmanager
def _no_proxy():
    """临时让国内网站绕过代理（百度/东方财富不需要走代理）"""
    import requests
    _orig = requests.Session.request

    def _patched(self, method, url, **kwargs):
        kwargs["proxies"] = {"http": "", "https": ""}
        return _orig(self, method, url, **kwargs)

    requests.Session.request = _patched
    try:
        yield
    finally:
        requests.Session.request = _orig


# ── 市场识别 & 代码转换 ────────────────────────────────────────

def _market(ticker: str) -> str:
    t = ticker.upper()
    if t.endswith(".SH") or t.endswith(".SZ") or t.endswith(".BJ"):
        return "A"
    if t.endswith(".HK"):
        return "HK"
    return "US"


def _hk_ak_code(ticker: str) -> str:
    """0700.HK → 00700（AKShare 5位补零）"""
    code = ticker.split(".")[0].lstrip("0") or "0"
    return code.zfill(5)


_US_EXCHANGE_SUFFIXES = {".O", ".N", ".A", ".NQ", ".NY"}

def _us_code(ticker: str) -> str:
    """QFIN.O → QFIN  /  BRK.B → BRK.B（.B 是代码一部分）"""
    for suffix in _US_EXCHANGE_SUFFIXES:
        if ticker.upper().endswith(suffix.upper()):
            return ticker[: -len(suffix)]
    return ticker


# ── 工具函数 ───────────────────────────────────────────────────

def _safe_float(val) -> Optional[float]:
    try:
        v = float(val)
        return None if pd.isna(v) else v
    except (TypeError, ValueError):
        return None


def _get_latest_value(df: pd.DataFrame, col: str) -> Optional[float]:
    if df is None or df.empty:
        return None
    series = df[col].dropna()
    if series.empty:
        return None
    return _safe_float(series.iloc[0])


# ── A股（Tushare Pro）─────────────────────────────────────────

def _fetch_a_stock(ticker: str) -> dict:
    result: dict = {}

    # PE_TTM / PB / DividendYield — daily_basic
    try:
        end = date.today()
        start = (end - timedelta(days=10)).strftime("%Y%m%d")
        df = _pro.daily_basic(
            ts_code=ticker,
            start_date=start,
            end_date=end.strftime("%Y%m%d"),
            fields="ts_code,trade_date,pe_ttm,pb,dv_ttm",
        )
        if df is not None and not df.empty:
            row = df.iloc[0]
            for src, dst in [("pe_ttm", "pe_ttm"), ("pb", "pb"), ("dv_ttm", "dividend_yield")]:
                v = _safe_float(row.get(src))
                if v is not None and v > 0:
                    result[dst] = round(v, 4)
    except Exception as e:
        logger.warning(f"A股 daily_basic 失败 [{ticker}]: {e}")

    # ROE / 营收增速 / 净利增速 — fina_indicator
    try:
        df = _pro.fina_indicator(
            ts_code=ticker,
            fields="ts_code,ann_date,roe,tr_yoy,netprofit_yoy",
            limit=1,
        )
        if df is not None and not df.empty:
            row = df.iloc[0]
            for src, dst in [
                ("roe",           "roe"),
                ("tr_yoy",        "revenue_growth"),
                ("netprofit_yoy", "net_profit_growth"),
            ]:
                v = _safe_float(row.get(src))
                if v is not None:
                    result[dst] = round(v, 4)
    except Exception as e:
        logger.warning(f"A股 fina_indicator 失败 [{ticker}]: {e}")

    return result


# ── 港股（AKShare 东方财富）──────────────────────────────────────

# AKShare 东方财富和百度均无法覆盖的港股（非陆系大蓝筹/特殊证券代码）
_HK_NO_COVERAGE = {"0808.HK", "87001.HK"}

def _fetch_hk_stock(ticker: str) -> dict:
    """港股财务快照（AKShare 东方财富 — 单次调用取全部6字段）"""
    result: dict = {}

    # 已知永久无数据的股票，静默跳过
    if ticker in _HK_NO_COVERAGE:
        logger.info(f"港股 [{ticker}] 在已知数据盲区（AKShare 无覆盖），跳过")
        return result

    code = _hk_ak_code(ticker)  # "00700"
    try:
        with _no_proxy():
            df = ak.stock_hk_financial_indicator_em(symbol=code)
        if df is None or df.empty:
            logger.warning(f"港股 EM 财务指标无数据 [{ticker}]")
        else:
            row = df.iloc[0]
            for src_col, dst_key in [
                ("市盈率",               "pe_ttm"),
                ("市净率",               "pb"),
                ("股东权益回报率(%)",     "roe"),
                ("营业总收入滚动环比增长(%)", "revenue_growth"),
                ("净利润滚动环比增长(%)",    "net_profit_growth"),
                ("股息率TTM(%)",          "dividend_yield"),
            ]:
                v = _safe_float(row.get(src_col))
                if v is not None:
                    result[dst_key] = round(v, 4)
    except Exception as e:
        logger.warning(f"港股财务指标失败 [{ticker}]: {e}")

    direct_yield = result.get("dividend_yield")
    if direct_yield is None or not math.isfinite(direct_yield) or direct_yield < 0:
        from data_ingestion.dividend_yield import calculate_hk_dividend_yield
        result.pop("dividend_yield", None)
        calculated = calculate_hk_dividend_yield(ticker)
        if "dividend_yield" in calculated:
            result["dividend_yield"] = calculated["dividend_yield"]

    return result


# ── 美股（AKShare 优先，Finnhub 补齐）─────────────────────────
# 注：Tushare us_daily PE/PB 为脏数据（QFIN 显示 0.51 实际应约 4x）
#     us_fina_indicator 每天仅 2 次调用配额，不足以覆盖批量更新
# 字段映射：AKShare → 标准键；Finnhub → 标准键（fallback）
_US_FINNHUB_FALLBACK = {
    "pe_ttm":          "pe_normalized",
    "pb":              "pb_quarterly",
    "roe":             "roe_rfy",
    "revenue_growth":  "revenue_growth_ttm_yoy",
}

def _fetch_us_stock(ticker: str) -> dict:
    """美股财务快照：AKShare 优先，缺失字段用 Finnhub /stock/metric 补齐
    PE/PB: 百度股市通 valuation（→ Finnhub peNormalizedAnnual/pbQuarterly）
    ROE/增速: 东方财富 EM（→ Finnhub roeRfy/revenueGrowthTTMYoy）
    注：AKShare 无美股股息率，Finnhub 免费版亦无，dividend_yield 不写入
    """
    result: dict = {}
    code = _us_code(ticker)

    # PE TTM / PB — 百度股市通
    for indicator, key in [("市盈率(TTM)", "pe_ttm"), ("市净率", "pb")]:
        try:
            with _no_proxy():
                df = ak.stock_us_valuation_baidu(
                    symbol=code, indicator=indicator, period="近一年"
                )
            v = _get_latest_value(df, "value")
            if v is not None and v > 0:
                result[key] = round(v, 4)
        except Exception as e:
            logger.warning(f"美股 {indicator} 失败 [{ticker}]: {e}")

    # ROE / 营收增速 / 净利增速 — 东方财富
    try:
        with _no_proxy():
            df = ak.stock_financial_us_analysis_indicator_em(
                symbol=code, indicator="年报"
            )
        if df is not None and not df.empty:
            row = df.iloc[0]
            for src_col, dst_key in [
                ("ROE_AVG",                     "roe"),
                ("OPERATE_INCOME_YOY",          "revenue_growth"),
                ("PARENT_HOLDER_NETPROFIT_YOY", "net_profit_growth"),
            ]:
                v = _safe_float(row.get(src_col))
                if v is not None:
                    result[dst_key] = round(v, 4)
    except Exception as e:
        logger.warning(f"美股财务指标失败 [{ticker}]: {e}")

    # Finnhub 补齐缺失字段（pe_ttm / pb / roe / revenue_growth）
    missing = [k for k in _US_FINNHUB_FALLBACK if k not in result]
    if missing:
        try:
            from data_ingestion.finnhub_fetcher import get_basic_financials
            fh = get_basic_financials(ticker)
            for std_key, fh_key in _US_FINNHUB_FALLBACK.items():
                if std_key in missing and fh_key in fh:
                    result[std_key] = round(fh[fh_key], 4)
                    logger.info(f"Finnhub 补齐 [{ticker}] {std_key}={result[std_key]}")
        except Exception as e:
            logger.warning(f"Finnhub fallback 失败 [{ticker}]: {e}")

    return result


# ── 对外接口 ───────────────────────────────────────────────────

def get_financial_snapshot(ticker: str) -> dict:
    """
    获取单只股票财务快照（A股/港股/美股）

    返回 dict，空字段不写入（跳过 Notion 更新）：
      pe_ttm, pb, roe, revenue_growth, net_profit_growth, dividend_yield
    """
    market = _market(ticker)
    logger.info(f"获取财务快照: {ticker} [{market}]")

    if market == "A":
        result = _fetch_a_stock(ticker)
    elif market == "HK":
        result = _fetch_hk_stock(ticker)
    else:
        result = _fetch_us_stock(ticker)

    logger.info(f"财务快照: {ticker} → {result}")
    return result


# ── 批量更新 ───────────────────────────────────────────────────

def batch_update_financials() -> None:
    """
    批量更新所有 Active+Watch 股票的财务快照到 Notion Universe DB
    每周日 22:00 自动运行
    """
    tickers = get_all_tickers()
    logger.info(f"开始批量更新财务快照，共 {len(tickers)} 只股票")
    client = get_client()

    field_map = {
        "pe_ttm":            "PE_TTM",
        "pb":                "PB",
        "roe":               "ROE",
        "revenue_growth":    "RevenueGrowth",
        "net_profit_growth": "NetProfitGrowth",
        "dividend_yield":    "DividendYield",
    }
    updated = 0

    for ticker in tickers:
        try:
            snapshot = get_financial_snapshot(ticker)
            if not snapshot:
                logger.warning(f"财务快照为空，跳过 [{ticker}]")
                continue

            res = client.databases.query(
                database_id=NOTION_UNIVERSE_DB_ID,
                filter={"property": "Ticker", "rich_text": {"equals": ticker}},
            )
            if not res.get("results"):
                logger.warning(f"Universe 中未找到 {ticker}")
                continue

            page_id = res["results"][0]["id"]
            props: dict = {}
            for key, notion_field in field_map.items():
                if key in snapshot:
                    props[notion_field] = build_notion_property(
                        notion_field, {"type": "number"}, snapshot[key]
                    )
            props["LastUpdated"] = build_notion_property(
                "LastUpdated", {"type": "date"}, date.today().isoformat()
            )

            client.pages.update(page_id=page_id, properties=props)
            logger.info(f"✅ {ticker} 财务快照更新成功: {snapshot}")
            updated += 1

        except Exception as e:
            logger.error(f"财务快照更新失败 [{ticker}]: {e}")

    logger.info(f"📊 财务快照更新完成，共更新 {updated}/{len(tickers)} 只")
