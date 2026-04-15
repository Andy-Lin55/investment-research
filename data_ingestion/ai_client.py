"""
data_ingestion/ai_client.py
AI 调用统一入口：DMXAPI 优先，失败自动 fallback 到 Google Gemini
"""
from __future__ import annotations

from openai import OpenAI
from loguru import logger

from config.settings import (
    DMXAPI_KEY, DMXAPI_BASE_URL, DMXAPI_MODEL,
    GEMINI_API_KEY, GEMINI_BASE_URL, GEMINI_MODEL,
)


def call_ai(prompt: str, timeout: int = 60) -> str:
    """
    DMXAPI 优先调用，连接/服务器错误时自动切换 Google Gemini。
    两者均失败返回空字符串。
    """
    # ── 尝试 DMXAPI（最多 60s，快速失败让 Gemini 接手）──
    if DMXAPI_KEY:
        try:
            client = OpenAI(api_key=DMXAPI_KEY, base_url=DMXAPI_BASE_URL)
            resp = client.chat.completions.create(
                model=DMXAPI_MODEL,
                messages=[{"role": "user", "content": prompt}],
                timeout=min(timeout, 120),
            )
            content = resp.choices[0].message.content or ""
            if content:
                return content
            logger.warning("DMXAPI 返回空内容，切换 Gemini")
        except Exception as e:
            logger.warning(f"DMXAPI 失败，切换 Gemini: {e}")

    # ── Fallback: Gemini（用完整 timeout）──
    if GEMINI_API_KEY:
        try:
            client = OpenAI(api_key=GEMINI_API_KEY, base_url=GEMINI_BASE_URL)
            resp = client.chat.completions.create(
                model=GEMINI_MODEL,
                messages=[{"role": "user", "content": prompt}],
                timeout=timeout,
            )
            content = resp.choices[0].message.content or ""
            if content:
                logger.info("Gemini fallback 成功")
                return content
            logger.warning("Gemini 返回空内容")
        except Exception as e:
            logger.error(f"Gemini fallback 也失败: {e}")

    return ""
