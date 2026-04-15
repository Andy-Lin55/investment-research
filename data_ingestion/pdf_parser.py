"""
data_ingestion/pdf_parser.py
PDF 文字提取（PyMuPDF）+ AI 解析（DMXAPI → Gemini fallback）
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF
from loguru import logger

from data_ingestion.ai_client import call_ai as _call_ai


# ── 工具函数 ─────────────────────────────────────────────────

def _extract_text(pdf_path: str | Path) -> str:
    """用 PyMuPDF 提取 PDF 全文（5 分钟超时保护）"""
    def _do_extract():
        text = ""
        doc = fitz.open(str(pdf_path))
        for page in doc:
            text += page.get_text()
        doc.close()
        return text

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_do_extract)
            return future.result(timeout=300).strip()
    except FuturesTimeoutError:
        logger.error(f"PDF 提取超时（>5min），跳过: {pdf_path}")
    except Exception as e:
        logger.error(f"PDF 提取失败 [{pdf_path}]: {e}")
    return ""


def _trim_text(text: str, max_chars: int = 6000) -> str:
    """超长文本截取：前 3000 + 后 1000"""
    if len(text) <= max_chars:
        return text
    return text[:3000] + "\n...[内容过长，已截断]...\n" + text[-1000:]


def _parse_json_response(raw: str) -> dict:
    """从 AI 返回中提取 JSON 块"""
    # 尝试直接解析
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # 提取 ```json ... ``` 块
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    # 提取第一个 { ... }
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    logger.warning("无法解析 AI 返回的 JSON，返回原始文本")
    return {"raw_response": raw}


# ── 公告解析 ─────────────────────────────────────────────────

def parse_announcement(
    pdf_path: str | Path,
    ticker: str,
    event_type: str = "公告",
) -> dict:
    """
    解析 A股公告 PDF

    返回格式：
    {
      "event_type": str,
      "importance": "High/Medium/Low",
      "key_numbers": {},
      "investment_signal": str,
      "one_line_summary": str,   # ≤30字
      "detailed_analysis": str,  # ≤200字
      "follow_up_actions": []
    }
    """
    logger.info(f"解析公告 [{ticker}]: {pdf_path}")
    text = _extract_text(pdf_path)
    if not text:
        logger.error(f"PDF 无文字内容: {pdf_path}")
        return {}

    trimmed = _trim_text(text)

    prompt = f"""你是专业投资研究员，请从以下A股公司（股票代码：{ticker}）的公告中提取投资关键信息。

公告内容：
{trimmed}

请严格按照以下 JSON 格式返回，不要有其他内容：
{{
  "event_type": "{event_type}",
  "importance": "High/Medium/Low",
  "key_numbers": {{}},
  "investment_signal": "Strong Buy/Buy/Hold/Sell/Strong Sell/中性",
  "one_line_summary": "不超过30字的摘要",
  "detailed_analysis": "不超过200字的投资分析",
  "follow_up_actions": ["后续跟进事项1", "后续跟进事项2"]
}}

判断 importance 标准：
- High：业绩大幅超预期/不及预期、重大并购、回购超5%、管理层重大变动
- Medium：一般业绩公告、股权激励、重大合同
- Low：例行公告、分红派息
"""

    raw = _call_ai(prompt, timeout=60)
    if not raw:
        logger.error(f"AI 调用失败 [{ticker}]: 两个后端均无响应")
        return {}
    result = _parse_json_response(raw)
    result["event_type"] = event_type
    logger.info(f"公告解析完成 [{ticker}]: importance={result.get('importance')}")
    return result


# ── 研报解析 ─────────────────────────────────────────────────

def parse_research_report(
    pdf_path: str | Path,
    ticker: str,
) -> dict:
    """
    解析卖方研报 PDF

    返回格式匹配 DB3 Research 字段：
    {
      "doc_type": str,
      "broker": str,
      "analyst": str,
      "rating": str,
      "target_price": float,
      "upside": float,
      "core_thesis": str,
      "catalysts": str,
      "report_date": str,  # YYYY-MM-DD
    }
    """
    logger.info(f"解析研报 [{ticker}]: {pdf_path}")
    text = _extract_text(pdf_path)
    if not text:
        logger.error(f"PDF 无文字内容: {pdf_path}")
        return {}

    trimmed = _trim_text(text)

    prompt = f"""你是专业投资研究员，请从以下关于股票（代码：{ticker}）的卖方研报中提取关键信息。

研报内容：
{trimmed}

请严格按照以下 JSON 格式返回，不要有其他内容：
{{
  "doc_type": "卖方研报/电话会纪要/专家纪要/自研",
  "broker": "券商/机构名称",
  "analyst": "分析师姓名",
  "rating": "买入/增持/中性/减持/卖出",
  "target_price": 目标价数字或null,
  "upside": 上涨空间百分比数字或null,
  "core_thesis": "核心投资逻辑，不超过200字",
  "catalysts": "主要催化剂，不超过100字",
  "report_date": "报告日期 YYYY-MM-DD 格式"
}}
"""

    raw = _call_ai(prompt, timeout=60)
    if not raw:
        logger.error(f"AI 调用失败 [{ticker}]: 两个后端均无响应")
        return {}
    result = _parse_json_response(raw)
    logger.info(f"研报解析完成 [{ticker}]: rating={result.get('rating')}")
    return result
