"""
data_ingestion/ai_client.py
AI 调用统一入口：可选本机 Codex 订阅 → DMXAPI → Google Gemini
"""
from __future__ import annotations

from loguru import logger

from config.settings import (
    DMXAPI_KEY, DMXAPI_BASE_URL, DMXAPI_MODEL,
    GEMINI_API_KEY, GEMINI_BASE_URL, GEMINI_MODEL,
    CODEX_SUBSCRIPTION_ENABLED,
)


def _call_api(prompt: str, *, api_key: str, base_url: str, model: str, timeout: int) -> str:
    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url=base_url, max_retries=0)
    response = client.chat.completions.create(
        model=model, messages=[{"role": "user", "content": prompt}], timeout=timeout,
    )
    return (response.choices[0].message.content or "").strip()


def call_ai(prompt: str, timeout: int = 60) -> str:
    """
    启用本机订阅时先尝试 Codex，失败后依次尝试两个现有 API 通道。
    所有通道均失败时返回空字符串。
    """
    if CODEX_SUBSCRIPTION_ENABLED:
        from data_ingestion.codex_client import call_codex

        try:
            content = call_codex(prompt, timeout=timeout)
            if content:
                logger.info("ChatGPT 订阅 Codex 调用成功")
                return content
        except Exception as error:
            logger.warning(f"Codex 订阅调用异常（{type(error).__name__}），使用 API 备用链")

    # ── 尝试 DMXAPI（最多 60s，快速失败让 Gemini 接手）──
    if DMXAPI_KEY:
        try:
            content = _call_api(
                prompt, api_key=DMXAPI_KEY, base_url=DMXAPI_BASE_URL,
                model=DMXAPI_MODEL, timeout=min(timeout, 120),
            )
            if content:
                return content
            logger.warning("DMXAPI 返回空内容，切换 Gemini")
        except Exception as e:
            logger.warning(f"DMXAPI 失败，切换 Gemini: {e}")

    # ── Fallback: Gemini（用完整 timeout）──
    if GEMINI_API_KEY:
        try:
            content = _call_api(
                prompt, api_key=GEMINI_API_KEY, base_url=GEMINI_BASE_URL,
                model=GEMINI_MODEL, timeout=timeout,
            )
            if content:
                logger.info("Gemini fallback 成功")
                return content
            logger.warning("Gemini 返回空内容")
        except Exception as e:
            logger.error(f"Gemini fallback 也失败: {e}")

    return ""
