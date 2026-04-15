"""
data_ingestion/tushare_sentiment.py
Tushare 5000积分情绪指标 — 融资融券、北向资金、大宗交易
数据不落库，仅在 Research Pipeline 调用时临时拉取注入 prompt。
仅适用于 A 股（.SH / .SZ / .BJ）
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

import tushare as ts
from loguru import logger

from config.settings import TUSHARE_TOKEN

# ── Tushare Pro 初始化 ─────────────────────────────────────────
_pro = ts.pro_api(TUSHARE_TOKEN)


# ── 融资融券 ──────────────────────────────────────────────────

def get_margin_data(ticker: str) -> dict:
    """
    获取个股融资融券数据（近5日）

    返回：
      latest_balance  — 最新融资余额（元）
      balance_chg_pct — 近5日余额变动百分比
      avg_daily_buy   — 日均融资买入额（元）
    """
    try:
        df = _pro.margin_detail(ts_code=ticker, limit=10)
        if df is None or df.empty:
            return {}

        df = df.sort_values("trade_date", ascending=True)
        df = df.tail(5)
        if len(df) < 2:
            return {}

        latest_rzye = float(df["rzye"].iloc[-1])
        oldest_rzye = float(df["rzye"].iloc[0])
        if oldest_rzye == 0:
            balance_chg_pct = 0.0
        else:
            balance_chg_pct = round((latest_rzye - oldest_rzye) / oldest_rzye * 100, 2)

        avg_daily_buy = round(float(df["rzmre"].mean()), 0)

        return {
            "latest_balance": latest_rzye,
            "balance_chg_pct": balance_chg_pct,
            "avg_daily_buy": avg_daily_buy,
        }
    except Exception as e:
        logger.warning(f"融资融券数据获取失败 [{ticker}]: {e}")
        return {}


# ── 北向资金 ──────────────────────────────────────────────────

def get_northbound_flow(days: int = 5) -> dict:
    """
    获取北向资金近N个交易日净流向（全市场，非个股）

    返回：
      net_5d_hundred_million — 近N日北向净流入（亿元，正为流入负为流出）
      trend                  — "流入" 或 "流出"
      latest_date            — 最新数据日期
    """
    try:
        today = date.today()
        start = today - timedelta(days=days * 2)
        end_str = today.strftime("%Y%m%d")
        start_str = start.strftime("%Y%m%d")

        df = _pro.moneyflow_hsgt(start_date=start_str, end_date=end_str)
        if df is None or df.empty:
            return {}

        df = df.sort_values("trade_date", ascending=True)
        df = df.tail(days)
        if df.empty:
            return {}

        # north_money 是累计北向持仓余额（亿元），字段为字符串类型，需先转 float
        # 区间净变化 = 最新余额 - 最早余额
        north_vals = df["north_money"].astype(float)
        net_hundred_million = round(float(north_vals.iloc[-1]) - float(north_vals.iloc[0]), 2)
        trend = "流入" if net_hundred_million > 0 else "流出"
        latest_date = str(df["trade_date"].iloc[-1])

        return {
            "net_5d_hundred_million": net_hundred_million,
            "trend": trend,
            "latest_date": latest_date,
        }
    except Exception as e:
        logger.warning(f"北向资金数据获取失败: {e}")
        return {}


# ── 大宗交易 ──────────────────────────────────────────────────

def get_block_trades(ticker: str, days: int = 30) -> list:
    """
    获取个股近N天大宗交易记录

    每条记录：
      date       — 交易日期
      amount_wan — 成交金额（万元）
      buyer      — 买方席位
      seller     — 卖方席位
      price      — 成交价
    """
    try:
        df = _pro.block_trade(ts_code=ticker, limit=10)
        if df is None or df.empty:
            return []

        today = date.today()
        result = []
        for _, row in df.iterrows():
            trade_date_str = str(row.get("trade_date", ""))
            if len(trade_date_str) == 8:
                trade_date = date(
                    int(trade_date_str[:4]),
                    int(trade_date_str[4:6]),
                    int(trade_date_str[6:8]),
                )
                days_ago = (today - trade_date).days
                if days_ago <= days:
                    vol = float(row.get("vol", 0) or 0)
                    price = float(row.get("price", 0) or 0)
                    amount_wan = round(vol * price / 10000, 1)
                    result.append({
                        "date": trade_date_str,
                        "amount_wan": amount_wan,
                        "buyer": str(row.get("buyer", "") or ""),
                        "seller": str(row.get("seller", "") or ""),
                        "price": price,
                    })
        return result
    except Exception as e:
        logger.warning(f"大宗交易数据获取失败 [{ticker}]: {e}")
        return []


# ── 综合情绪快照 ──────────────────────────────────────────────

def get_sentiment_snapshot(ticker: str) -> dict:
    """
    获取 A 股个股情绪快照（融资融券 + 北向资金 + 大宗交易）

    非 A 股（.SH/.SZ/.BJ 以外）直接返回 {}
    """
    upper = ticker.upper()
    if not (upper.endswith(".SH") or upper.endswith(".SZ") or upper.endswith(".BJ")):
        return {}

    return {
        "margin": get_margin_data(ticker),
        "northbound": get_northbound_flow(5),
        "block_trades": get_block_trades(ticker),
    }


# ── Prompt 格式化 ─────────────────────────────────────────────

def format_sentiment_for_prompt(sentiment: dict) -> str:
    """
    将情绪快照格式化为 AI prompt 可读字符串

    空 dict 返回 ""，缺失的子项跳过对应行
    """
    if not sentiment:
        return ""

    lines = ["## 市场情绪指标（A股）"]

    # 融资融券
    margin = sentiment.get("margin") or {}
    if margin:
        latest_balance = margin.get("latest_balance")
        balance_chg_pct = margin.get("balance_chg_pct")
        avg_daily_buy = margin.get("avg_daily_buy")

        if latest_balance is not None:
            balance_yi = round(latest_balance / 1e8, 2)
            avg_buy_wan = round((avg_daily_buy or 0) / 1e4, 1)
            chg_str = str(balance_chg_pct) if balance_chg_pct is not None else "N/A"
            balance_str = str(balance_yi)
            avg_str = str(avg_buy_wan)
            lines.append(
                "融资融券：余额 " + balance_str + "亿元，近5日变动 "
                + chg_str + "%，日均买入 " + avg_str + "万元"
            )

    # 北向资金
    northbound = sentiment.get("northbound") or {}
    if northbound:
        net = northbound.get("net_5d_hundred_million")
        trend = northbound.get("trend", "")
        latest_date = northbound.get("latest_date", "")
        if net is not None:
            net_abs = abs(net)
            net_str = str(net_abs)
            lines.append(
                "北向资金：近5日净" + trend + " " + net_str + "亿元（截至" + latest_date + "）"
            )

    # 大宗交易
    block_trades = sentiment.get("block_trades") or []
    count = len(block_trades)
    trade_line = "大宗交易：近30日 " + str(count) + " 笔"
    if count > 0:
        latest = block_trades[0]
        trade_date = latest.get("date", "")
        buyer = latest.get("buyer", "")
        amount_wan = latest.get("amount_wan", 0)
        amount_str = str(amount_wan)
        buyer_str = buyer if buyer else "未知"
        trade_line += "，[最近一笔: " + trade_date + " " + buyer_str + "买入 " + amount_str + "万元]"
    lines.append(trade_line)

    return "\n".join(lines)
