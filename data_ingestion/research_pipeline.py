"""
data_ingestion/research_pipeline.py
Watch 个股深度研究 AI Pipeline（3步：多维分析 → 魔鬼代言人 → 综合判断）

输入：ticker + optional trigger_event_id
输出：Research DB 写入格式的 dict
"""
from __future__ import annotations

import json
import re
from datetime import date
from typing import Optional

from loguru import logger
from data_ingestion.ai_client import call_ai as _call_ai_backend

# ── Constants ────────────────────────────────────────────────
MAX_CHARS = 6000      # 超过此长度的文本触发分段摘要
MAX_CHUNK = 4000      # 每个分段最大字符数


# ── Signal mapping ───────────────────────────────────────────
_SIGNAL_MAP = {
    "Strong Buy": "买入",
    "Buy":        "增持",
    "Hold":       "中性",
    "Sell":       "减持",
    "Strong Sell": "卖出",
}


# ── Helper: AI call ──────────────────────────────────────────

def _call_ai(prompt: str) -> str:
    """Internal AI call helper. Returns empty string on exception."""
    result = _call_ai_backend(prompt, timeout=120)
    if not result:
        logger.error("AI 调用失败: 两个后端均无响应")
    return result


# ── Helper: chunk summarize ──────────────────────────────────

def _chunk_summarize(text: str, ticker: str, context_hint: str) -> str:
    """
    If text <= MAX_CHARS, return as-is.
    Otherwise split into chunks and summarize each chunk via AI,
    then join with separator.
    """
    if len(text) <= MAX_CHARS:
        return text

    chunks = [text[i:i + MAX_CHUNK] for i in range(0, len(text), MAX_CHUNK)]
    n = len(chunks)
    summaries = []
    for i, chunk in enumerate(chunks):
        segment_label = str(i + 1)
        total_label = str(n)
        prompt = (
            "这是关于" + ticker + "的研究材料片段（第" + segment_label + "段/共" + total_label + "段），" + context_hint + "。\n"
            "请用200字内提炼关键信息（数字、结论、风险）：\n"
            + chunk
        )
        summary = _call_ai(prompt)
        summaries.append(summary)

    return "\n\n---\n\n".join(summaries)


# ── Market detection ─────────────────────────────────────────

def _detect_market(ticker: str) -> str:
    upper = ticker.upper()
    if upper.endswith(".SH") or upper.endswith(".SZ") or upper.endswith(".BJ"):
        return "A股"
    if upper.endswith(".HK"):
        return "港股"
    return "美股"


# ── Context collection ───────────────────────────────────────

def _collect_context(ticker: str) -> dict:
    """Collect all available data for the ticker."""
    from notion_sync.sync_manager import get_universe_snapshot, get_recent_events
    from data_ingestion.akshare_research_fetcher import get_a_share_research
    from data_ingestion.finnhub_fetcher import (
        get_analyst_consensus,
        get_earnings_surprises,
        get_company_news,
    )

    market = _detect_market(ticker)

    ctx: dict = {
        "ticker": ticker,
        "market": market,
        "valuation": {},
        "recent_events": [],
        "research_reports": [],
        "analyst_consensus": None,
        "earnings_surprises": [],
        "news_headlines": [],
        "data_sources": [],
    }

    # Universe snapshot (all markets)
    try:
        snapshot = get_universe_snapshot(ticker)
        ctx["company_name"] = snapshot.pop("company_name", "")
        ctx["valuation"] = snapshot
        ctx["data_sources"].append("UniverseDB")
    except Exception as e:
        logger.warning(f"get_universe_snapshot 失败 [{ticker}]: {e}")

    # Recent events (all markets)
    try:
        raw_events = get_recent_events(ticker, limit=5)
        ctx["data_sources"].append("EventsDB")
        # 直接复用 Notion 中已存的 AI Summary，不重新下载/解析 PDF
        # （重新下载 HKEX/SEC PDF 会导致 HK 股数据采集挂起 40+ 分钟）
        for ev in raw_events:
            ev["doc_summary"] = ev.get("summary", "")
        ctx["recent_events"] = raw_events
    except Exception as e:
        logger.warning(f"get_recent_events 失败 [{ticker}]: {e}")

    # A股: research reports from AKShare
    if market == "A股":
        try:
            ctx["research_reports"] = get_a_share_research(ticker, limit=5)
            ctx["data_sources"].append("AKShare东方财富研报")
        except Exception as e:
            logger.warning(f"get_a_share_research 失败 [{ticker}]: {e}")

    # US: analyst consensus, EPS surprises, news from Finnhub
    elif market == "美股":
        try:
            ctx["analyst_consensus"] = get_analyst_consensus(ticker)
            ctx["data_sources"].append("Finnhub")
        except Exception as e:
            logger.warning(f"get_analyst_consensus 失败 [{ticker}]: {e}")

        try:
            ctx["earnings_surprises"] = get_earnings_surprises(ticker, limit=4)
        except Exception as e:
            logger.warning(f"get_earnings_surprises 失败 [{ticker}]: {e}")

        try:
            ctx["news_headlines"] = get_company_news(ticker, days=7)
        except Exception as e:
            logger.warning(f"get_company_news 失败 [{ticker}]: {e}")

    # A股情绪数据
    if ctx["market"] == "A股":
        try:
            from data_ingestion.tushare_sentiment import get_sentiment_snapshot
            ctx["sentiment"] = get_sentiment_snapshot(ticker)
            if ctx["sentiment"]:
                ctx["data_sources"].append("Tushare情绪指标")
        except Exception as e:
            logger.warning(f"情绪数据获取失败 [{ticker}]: {e}")
            ctx["sentiment"] = {}
    else:
        ctx["sentiment"] = {}

    return ctx


# ── Format context for prompt ────────────────────────────────

def _format_context_for_prompt(ctx: dict) -> str:
    """Format collected context into readable string for AI prompts."""
    lines = []
    ticker = ctx.get("ticker", "")
    market = ctx.get("market", "")

    lines.append("### 基本信息")
    company = ctx.get("company_name", "")
    company_str = f"  公司：{company}" if company else ""
    lines.append("股票：" + ticker + company_str + "  市场：" + market)
    lines.append("")

    # Valuation block
    val = ctx.get("valuation") or {}
    if val:
        lines.append("### 估值数据")
        field_labels = [
            ("pe_ttm",           "PE (TTM)"),
            ("pb",               "PB"),
            ("roe",              "ROE (%)"),
            ("revenue_growth",   "营收增速 (%)"),
            ("net_profit_growth","净利增速 (%)"),
            ("dividend_yield",   "股息率 (%)"),
            ("latest_signal",    "最新信号"),
        ]
        for key, label in field_labels:
            v = val.get(key)
            if v is not None and v != "":
                lines.append("- " + label + ": " + str(v))
        lines.append("")

    # Recent events
    events = ctx.get("recent_events") or []
    if events:
        lines.append("### 近期重要事件（最近5条）")
        for ev in events:
            date_str = ev.get("date", "")
            etype = ev.get("event_type", "")
            signal = ev.get("signal", "")
            importance = ev.get("importance", "")
            summary = ev.get("summary", "")
            doc_sum = ev.get("doc_summary") or summary
            snippet = doc_sum[:300] if doc_sum else ""
            line = "- [" + date_str + "] " + etype
            if importance:
                line += " (" + importance + ")"
            if signal:
                line += " 信号:" + signal
            if snippet:
                line += " — " + snippet
            lines.append(line)
        lines.append("")

    # A股: research reports
    reports = ctx.get("research_reports") or []
    if reports:
        lines.append("### 卖方研报摘要（最新5条）")
        for r in reports:
            date_str = r.get("date", "")
            broker = r.get("broker", "")
            rating = r.get("rating", "")
            report_name = r.get("report_name", "")
            pe_fcast = r.get("pe_forecast", "")
            line = "- [" + date_str + "] " + broker + " " + rating
            if report_name:
                line += " 《" + report_name + "》"
            if pe_fcast:
                line += " PE预测: " + pe_fcast
            lines.append(line)
        lines.append("")

    # US: analyst consensus
    consensus = ctx.get("analyst_consensus")
    if consensus:
        lines.append("### 分析师共识")
        period = consensus.get("period", "")
        buy = consensus.get("buy", 0) or 0
        strong_buy = consensus.get("strongBuy", 0) or 0
        hold = consensus.get("hold", 0) or 0
        sell = consensus.get("sell", 0) or 0
        strong_sell = consensus.get("strongSell", 0) or 0
        lines.append(
            "期间: " + str(period) + "  "
            "强烈买入: " + str(strong_buy) + "  "
            "买入: " + str(buy) + "  "
            "持有: " + str(hold) + "  "
            "卖出: " + str(sell) + "  "
            "强烈卖出: " + str(strong_sell)
        )
        lines.append("")

    # EPS surprises
    eps_list = ctx.get("earnings_surprises") or []
    if eps_list:
        lines.append("### EPS超预期历史")
        for ep in eps_list:
            period = ep.get("period", "")
            actual = ep.get("actual")
            estimate = ep.get("estimate")
            surprise_pct = ep.get("surprisePercent")
            line = "- " + str(period)
            if actual is not None:
                line += " 实际: " + str(actual)
            if estimate is not None:
                line += " 预期: " + str(estimate)
            if surprise_pct is not None:
                line += " 超预期: " + str(round(surprise_pct, 2)) + "%"
            lines.append(line)
        lines.append("")

    # News headlines (up to 3)
    news_list = ctx.get("news_headlines") or []
    if news_list:
        lines.append("### 近期新闻（最多3条）")
        for item in news_list[:3]:
            dt = item.get("datetime", "")[:10] if item.get("datetime") else ""
            headline = item.get("headline", "")
            source = item.get("source", "")
            line = "- [" + dt + "] " + headline
            if source:
                line += " (" + source + ")"
            lines.append(line)
        lines.append("")

    # A股情绪指标
    if ctx.get("sentiment"):
        from data_ingestion.tushare_sentiment import format_sentiment_for_prompt
        sentiment_text = format_sentiment_for_prompt(ctx["sentiment"])
        if sentiment_text:
            lines.append(sentiment_text)
            lines.append("")

    return "\n".join(lines)


# ── JSON parse helper (reuse pattern from pdf_parser) ────────

def _parse_json_response(raw: str) -> dict:
    """Extract JSON from AI response."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    logger.warning("无法解析 AI 返回的 JSON")
    return {}


# ── Main pipeline ─────────────────────────────────────────────

def run_research_pipeline(ticker: str, trigger_event_id: str = "") -> dict:
    """
    3-step AI research pipeline:
    1. Multi-dimensional analysis
    2. Devil's advocate
    3. Synthesis + JSON output

    Returns dict formatted for create_research(), or {} on failure.
    """
    # Collect context
    ctx = _collect_context(ticker)
    formatted_context = _format_context_for_prompt(ctx)
    company_name = ctx.get("company_name", "")
    company_label = f"{company_name}（{ticker}）" if company_name else ticker

    # ── Step 1: Value investing analysis (Graham + Buffett) ──
    step1_prompt = (
        "你是一位严格遵循格雷厄姆和巴菲特价值投资方法论的基本面研究员，正在分析 " + company_label + "。\n\n"
        "## 巴菲特核心原则（分析时须贯穿始终）\n"
        "- 「用合理价格买入优秀公司，远好过以便宜价格买入平庸公司。」\n"
        "- 「只投资你能理解的业务——能力圈之外的机会不是机会。」\n"
        "- 「时间是优秀企业的朋友，是平庸企业的敌人。」\n"
        "- 「真正的护城河必须能抵御通货膨胀：公司必须有定价权，才能在不增加资本投入的情况下提价。」\n"
        "- 「我们衡量管理层的标准只有一条：资本配置的成绩单。」\n"
        "- 「永久性本金损失——而非波动——才是真正的风险。」\n\n"
        "## 当前市场数据\n"
        + formatted_context + "\n\n"
        "分析框架（请按此顺序逐项作答，每项给出具体数据依据）\n\n"
        "一、能力圈判断（首要过滤器）\n"
        "- 这家公司的业务模式是否足够简单、可预测，使得10年后的盈利能力可以合理估计？\n"
        "- 如果不能，直接标注「超出能力圈」，后续分析降低权重。\n\n"
        "二、护城河深度与持久性（巴菲特最看重）\n"
        "- 竞争优势的来源：品牌溢价 / 客户转换成本 / 网络效应 / 低成本优势 / 高效规模（选最符合的1-2项并举证）\n"
        "- 定价权测试：过去5年公司是否在不丢失市场份额的情况下成功提价？通胀期间毛利率是否稳定？\n"
        "- 护城河耐久性：未来5-10年最可能侵蚀这条护城河的力量是什么？\n"
        "- 喜诗糖果标准：若明天关闭所有门店、两年后重开，客户还会回来吗？\n"
        "- 竞争者测试（1986年信）：一个资金充裕的理性竞争者，愿意以当前经济条件进入这个市场吗？若不愿，护城河是真实的。\n\n"
        "三、财务质量检验（格雷厄姆+巴菲特双重标准）\n"
        "- 盈利能力：ROE是否连续5年 >15%？ROE提升靠的是盈利改善还是加杠杆？\n"
        "- Owner Earnings（股东盈余）：自由现金流 = 净利润 + 折旧摊销 - 资本支出。该公司「真实」的盈利能力如何？\n"
        "- 资本轻重：维持竞争优势需要投入多少资本？（轻资产 = 好生意）\n"
        "- 资产负债表：负债/权益比是否合理？账上现金是否充裕到可以安然度过衰退？\n"
        "- 盈利质量：经营现金流/净利润比率是否长期 >0.8？应收账款增速是否超过营收增速？\n"
        "- 一美元测试（1983年信）：每$1留存收益，是否为股东创造了≥$1市值？若否，公司应分红而非留存。\n"
        "- 摇椅测试（1985年信）：利润增长是来自更高资本回报，还是只是投入了更多资本保持同等回报率？后者「连摇椅都能做到」，不代表管理层能力。\n\n"
        "三点五、商业模式三项检验\n"
        "- 收费桥梁测试：顾客有别的选择吗？（可口可乐/喜诗糖果几乎没有→高分；大宗商品→不通过）\n"
        "- 低成本生产商测试：公司是否凭成本优势主导市场？（GEICO从2%拿到12%市场份额）\n"
        "- 资本再投资测试：留存收益能以高回报率再投资吗？（喜诗糖果：$2500万净资产→20年$4亿利润）\n\n"
        "四、管理层与资本配置（巴菲特称之为「最重要的单一因素」）\n"
        "- 管理层是否像企业所有者而非「受雇经理人」那样思考？（持股比例、薪酬与业绩挂钩程度）\n"
        "- 资本配置历史：留存收益的再投资回报率如何？有无以合理价格回购股票？\n"
        "- 诚信信号：近期公告/事件中，管理层是否坦诚披露不利信息？\n\n"
        "五、内在价值与安全边际\n"
        "- 以当前PE/PB/ROE，估值相对历史区间和同业处于什么水位？\n"
        "- 简化DCF思维：若未来10年利润年增长保守估计X%，折现后现价是否有≥30%的安全边际？\n"
        "- 格雷厄姆测试：PE × PB < 22.5？\n"
        "- 「公允价格的优秀公司 vs 低价的平庸公司」：当前机会属于哪类？\n\n"
        "六、10年持有测试（最终过滤器）\n"
        "- 如果市场明天关闭10年，你仍会持有这只股票吗？\n"
        "- 综合以上，给出买入/持有/观察/回避的建议，并说明触发买入的具体价格或估值条件。\n\n"
        "请以结构化格式输出，语言简洁，每个结论必须有数据支撑。"
    )

    step1_result = _call_ai(step1_prompt)
    if not step1_result:
        logger.error("Research Pipeline [" + ticker + "] Step 1 失败：AI 返回为空")
        return {}
    logger.info("Research Pipeline [" + ticker + "] Step 1 完成")

    # Chunk-summarize step1 if too long to keep step2 prompt manageable
    step1_for_step2 = _chunk_summarize(step1_result, ticker, "多维度投资分析")

    # ── Step 2: Value trap interrogator ──────────────────────
    step2_prompt = (
        "基于以下对 " + ticker + " 的价值投资分析，你现在扮演「价值陷阱审讯官」角色。\n"
        "你的任务是找出上述分析中可能存在的致命漏洞——那些让一只股票看起来便宜但实则是陷阱的因素。\n\n"
        "## 巴菲特的价值陷阱警告（审讯时对照）\n"
        "- 「雪茄烟蒂式投资（捡廉价烟蒂）只有一口好抽，但优秀生意会自动复利。」—— 低价不等于好投资\n"
        "- 「对于困境中的企业，管理层的能力无法弥补糟糕的商业模式。」—— 拒绝「困境反转」幻想\n"
        "- 「没有定价权的大宗商品型企业，无论账面多便宜都不值得拥有。」—— 识别伪护城河\n"
        "- 「我们几乎从未见过大型并购能真正为买方股东创造价值。」—— 并购驱动增长是危险信号\n"
        "- 「当管理层谈论协同效应时，拿好你的钱包。」—— 警惕管理层讲故事\n\n"
        "## 待审查的分析\n"
        + step1_for_step2 + "\n\n"
        "## 审讯清单（逐项检查，有证据的说证据，没有数据时也要指出数据缺失本身是风险）\n\n"
        "1. **护城河真伪鉴别**\n"
        "   - 所谓竞争优势是结构性的，还是只是暂时的行业顺风/政策红利？\n"
        "   - 这条护城河能否抵御通胀？公司有没有在没有定价权的市场里假装有护城河？\n"
        "   - 有哪些新进入者/替代技术正在悄然侵蚀这个「护城河」？\n\n"
        "2. **估值陷阱识别**\n"
        "   - 低PE背后：盈利是否处于周期高点即将均值回归？有无一次性收益虚增利润？\n"
        "   - Owner Earnings 检验：若用自由现金流而非会计利润估值，结论是否不变？\n"
        "   - 商誉/无形资产占净资产比例：减值风险有多大？\n\n"
        "3. **资产负债表暗雷**\n"
        "   - 应收账款/存货增速是否超过收入增速？\n"
        "   - 债务结构：短期债务到期压力 + 利息覆盖倍数是否安全？\n"
        "   - 经营现金流/净利润长期 <0.8 是财务健康恶化的红旗。\n\n"
        "4. **管理层风险信号**\n"
        "   - 有无频繁更换审计师、CFO，或大股东连续减持？\n"
        "   - 资本配置劣迹：高位并购、贱价增发、脱离主业的多元化？\n"
        "   - 薪酬与业绩是否脱钩？管理层是否把公司当提款机？\n\n"
        "5. **宏观与行业周期风险**\n"
        "   - 当前ROE/利润率是否处于行业周期高点？均值回归后真实盈利能力如何？\n"
        "   - 政策/监管风险（尤其A股）：行业是否面临政策逆风或竞争格局重塑？\n\n"
        "6. **最坏情景与永久损失概率**\n"
        "   - 如果核心投资假设错误，股价会跌多少？本金永久损失（非暂时下跌）的概率有多大？\n"
        "   - 用巴菲特标准：「不确定的时候，答案就是不。」这里有多少不确定性？\n\n"
        "7. **摇椅检验与留存价值质疑（1983/1985年信）**\n"
        "   - 利润增长是真实能力还是假象？剔除资本增量后，ROE有没有在下降？\n"
        "   - 每$1留存收益为股东创造了多少市值？低于$1意味着公司应该分红而不是留存——这是管理层掩盖低效的信号。\n\n"
        "请给出3-5个最关键的反驳点，按破坏力从大到小排列。"
    )

    step2_result = _call_ai(step2_prompt)
    if not step2_result:
        logger.error("Research Pipeline [" + ticker + "] Step 2 失败：AI 返回为空")
        return {}
    logger.info("Research Pipeline [" + ticker + "] Step 2 完成")

    # ── Step 3: Synthesis (Buffett final verdict) ────────────
    step3_prompt = (
        "你是最终的投资决策者，综合以下价值投资框架下的正反两面分析，对 " + ticker + " 给出最终判断。\n\n"
        "## 价值投资者视角（正面）\n"
        + step1_for_step2 + "\n\n"
        "## 价值陷阱风险（反面）\n"
        + step2_result + "\n\n"
        "## 决策要求\n"
        "请用巴菲特的「能力圈 + 安全边际 + 长期持有」标准做出最终裁定：\n\n"
        "1. **核心投资论点**（100字内）：用一句话概括「为什么这是/不是一笔好生意，当前价格是否合适」\n"
        "2. **主要风险因素**（3条，每条50字内）：最可能导致永久性本金损失的因素\n"
        "3. **估值判断**：低估/合理/高估（基于安全边际标准）\n"
        "4. **最终信号**：Strong Buy（显著低估+高质量）/ Buy（合理低估）/ Hold（公允价值附近）"
        "/ Sell（高估）/ Strong Sell（显著高估或基本面恶化）\n"
        "5. **置信度**：High/Medium/Low（取决于数据充分程度和业务可理解性；Low 置信度下用巴菲特标准「不确定时答案是否」，默认 Hold 或 Sell；另：长期持有有税收递延效应，Hold 不等于放弃）\n"
        "6. **辩论摘要**（50字内）：正反双方的核心分歧是什么\n\n"
        "严格按以下 JSON 格式返回（不要其他内容）：\n"
        '{"core_thesis": "...", "risk_factors": "...", "valuation_view": "低估/合理/高估", '
        '"final_signal": "Strong Buy/Buy/Hold/Sell/Strong Sell", '
        '"confidence": "High/Medium/Low", "debate_summary": "..."}'
    )

    step3_result = _call_ai(step3_prompt)
    if not step3_result:
        logger.error("Research Pipeline [" + ticker + "] Step 3 失败：AI 返回为空")
        return {}
    logger.info("Research Pipeline [" + ticker + "] Step 3 完成")

    # ── Parse Step 3 JSON ────────────────────────────────────
    parsed = _parse_json_response(step3_result)
    if not parsed:
        logger.error("Research Pipeline [" + ticker + "] Step 3 JSON 解析失败，原始内容: " + step3_result[:300])
        return {}

    # Map signal to Chinese rating
    final_signal = parsed.get("final_signal", "Hold")
    rating = _SIGNAL_MAP.get(final_signal, "中性")

    return {
        "doc_type":         "AI深度分析",
        "core_thesis":      parsed.get("core_thesis", ""),
        "risk_factors":     parsed.get("risk_factors", ""),
        "valuation_view":   parsed.get("valuation_view", ""),
        "catalysts":        "",
        "rating":           rating,
        "confidence":       parsed.get("confidence", ""),
        "debate_summary":   parsed.get("debate_summary", ""),
        "trigger_event_id": trigger_event_id,
        "data_sources":     ", ".join(ctx["data_sources"]),
        "report_date":      date.today().isoformat(),
        "broker":           "AI Research Pipeline",
        "analyst":          "gpt-5.4",
        "_step1_text":      step1_result,
        "_step2_text":      step2_result,
        "_step3_raw":       step3_result,
    }
