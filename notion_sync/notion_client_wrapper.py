"""
notion_sync/notion_client_wrapper.py
Notion API 封装，统一处理认证、错误和重试
"""
import time
from notion_client import Client, APIResponseError
from loguru import logger
from config.settings import NOTION_TOKEN


def get_client() -> Client:
    """返回已认证的 Notion 客户端"""
    if not NOTION_TOKEN:
        raise ValueError("NOTION_TOKEN 未配置，请检查 .env 文件")
    return Client(auth=NOTION_TOKEN)


def notion_request_with_retry(fn, *args, max_retries: int = 3, **kwargs):
    """
    带重试的 Notion API 调用。
    对 503 Service Unavailable / APIResponseError / timeout 自动重试，
    间隔 2s → 4s → 8s（指数退避）。
    """
    delay = 2
    last_exc = None
    for attempt in range(max_retries):
        try:
            return fn(*args, **kwargs)
        except APIResponseError as e:
            last_exc = e
            if e.status in (500, 502, 503, 504) and attempt < max_retries - 1:
                logger.warning(
                    f"Notion API {e.status}，{delay}s 后重试（{attempt + 1}/{max_retries}）: {e}"
                )
                time.sleep(delay)
                delay *= 2
            else:
                raise
        except Exception as e:
            last_exc = e
            err_str = str(e).lower()
            retryable = (
                "timeout" in err_str
                or "timed out" in err_str
                or "503" in err_str
                or "ssl" in err_str
                or "eof" in err_str
                or "connection" in err_str
            )
            if retryable and attempt < max_retries - 1:
                logger.warning(
                    f"Notion 请求失败，{delay}s 后重试（{attempt + 1}/{max_retries}）: {e}"
                )
                time.sleep(delay)
                delay *= 2
            else:
                raise
    raise last_exc
