"""
data_ingestion/ai_client.py
AI 调用统一入口：可选本机 Codex 订阅 → DMXAPI → Google Gemini
"""
from __future__ import annotations

from collections.abc import Callable

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


def _usable(content: str, provider: str, validate: Callable[[str], object] | None) -> bool:
    if not content or not content.strip():
        logger.warning(f"{provider} 返回空内容")
        return False
    if validate is not None:
        try:
            validate(content)
        except ValueError as error:
            # Invalid output can include source text; record only the error type.
            logger.warning(f"{provider} 返回内容未通过结构校验（{type(error).__name__}）")
            return False
    return True


def call_ai(prompt: str, timeout: int = 60, *, validate: Callable[[str], object] | None = None) -> str:
    """
    启用本机订阅时先尝试 Codex，失败后依次尝试两个现有 API 通道。
    调用方可在接受输出前校验结构；无可用通道时返回空字符串。
    """
    if CODEX_SUBSCRIPTION_ENABLED:
        from data_ingestion.codex_client import call_codex

        try:
            content = call_codex(prompt, timeout=timeout)
            if _usable(content, "Codex", validate):
                logger.info("ChatGPT 订阅 Codex 调用成功")
                return content
        except Exception as error:
            logger.warning(f"Codex 订阅调用异常（{type(error).__name__}），使用 API 备用链")

    # ── 尝试 DMXAPI，失败或结果不可用时让 Gemini 接手 ──
    if DMXAPI_KEY:
        try:
            content = _call_api(
                prompt, api_key=DMXAPI_KEY, base_url=DMXAPI_BASE_URL,
                model=DMXAPI_MODEL, timeout=min(timeout, 120),
            )
            if _usable(content, "DMXAPI", validate):
                return content
            logger.info("DMXAPI 内容不可用，切换 Gemini")
        except Exception as error:
            logger.warning(f"DMXAPI 失败，切换 Gemini（{type(error).__name__}）")

    # ── Fallback: Gemini（用完整 timeout）──
    if GEMINI_API_KEY:
        try:
            content = _call_api(
                prompt, api_key=GEMINI_API_KEY, base_url=GEMINI_BASE_URL,
                model=GEMINI_MODEL, timeout=timeout,
            )
            if _usable(content, "Gemini", validate):
                logger.info("Gemini fallback 成功")
                return content
        except Exception as error:
            logger.error(f"Gemini fallback 也失败（{type(error).__name__}）")

    return ""
