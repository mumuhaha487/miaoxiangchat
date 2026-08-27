from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Callable
from email.utils import parsedate_to_datetime
from time import time
from typing import Any

import httpx


RETRYABLE_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524})
DEFAULT_MAX_RETRIES = 5
MAX_CONFIGURED_RETRIES = 12
RETRY_EXHAUSTED_HEADER = "X-Mumu-LLM-Retry-Exhausted"
logger = logging.getLogger(__name__)
_upstream_gates: dict[tuple[int, str], asyncio.Semaphore] = {}


def _upstream_gate(url: str, limit: int) -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    key = (id(loop), url)
    bounded = max(1, min(int(limit), 64))
    gate = _upstream_gates.get(key)
    if gate is None:
        gate = asyncio.Semaphore(bounded)
        _upstream_gates[key] = gate
    return gate


class LLMUpstreamExhausted(RuntimeError):
    def __init__(self, max_retries: int, *, status_code: int | None = None, reason: str = ""):
        detail = f"HTTP {status_code}" if status_code is not None else (reason or "连接中断")
        super().__init__(f"模型服务暂时不可用，初次请求失败后已自动重连 {max_retries} 次（{detail}）")
        self.max_retries = max_retries
        self.status_code = status_code


class LLMResponseFatal(ValueError):
    """The provider returned a deterministic policy violation that retries cannot fix."""


def retry_delay(response: httpx.Response | None, retry_index: int) -> float:
    if response is not None:
        retry_after = response.headers.get("retry-after", "").strip()
        if retry_after:
            try:
                return max(0.0, min(float(retry_after), 8.0))
            except ValueError:
                try:
                    parsed = parsedate_to_datetime(retry_after)
                    return max(0.0, min(parsed.timestamp() - time(), 8.0))
                except (TypeError, ValueError, OverflowError):
                    pass
    return min(0.5 * (2 ** max(0, retry_index - 1)), 8.0)


async def request_llm_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any] | None = None,
    stream: bool = False,
    max_retries: int = DEFAULT_MAX_RETRIES,
    concurrency_limit: int = 12,
    validate_response: Callable[[httpx.Response], Any] | None = None,
) -> httpx.Response:
    retries = max(0, min(int(max_retries), MAX_CONFIGURED_RETRIES))
    last_reason = "连接中断"
    for attempt in range(retries + 1):
        response: httpx.Response | None = None
        retry_reason = ""
        try:
            request = client.build_request(method, url, headers=headers, json=payload)
            async with _upstream_gate(url, concurrency_limit):
                response = await client.send(request, stream=stream)
            if response.status_code in RETRYABLE_STATUS_CODES:
                retry_reason = f"HTTP {response.status_code}"
            elif validate_response is not None and response.status_code < 400:
                try:
                    validate_response(response)
                except LLMResponseFatal:
                    await response.aclose()
                    raise
                except (TypeError, ValueError, KeyError, IndexError) as exc:
                    retry_reason = f"模型响应校验失败：{str(exc)[:240]}"
            if not retry_reason:
                return response
        except httpx.TransportError as exc:
            retry_reason = type(exc).__name__

        last_reason = retry_reason or last_reason
        if attempt >= retries:
            if response is not None:
                status_code = response.status_code if response.status_code in RETRYABLE_STATUS_CODES else None
                await response.aclose()
                raise LLMUpstreamExhausted(retries, status_code=status_code, reason=last_reason)
            raise LLMUpstreamExhausted(retries, reason=last_reason)

        logger.warning(
            "llm_upstream_reconnect reason=%s reconnect=%s/%s",
            retry_reason,
            attempt + 1,
            retries,
        )
        if response is not None:
            await response.aclose()
        base_delay = retry_delay(response, attempt + 1)
        await asyncio.sleep(base_delay * (0.75 + random.random() * 0.5))

    raise LLMUpstreamExhausted(retries, reason=last_reason)


async def post_llm_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any],
    stream: bool = False,
    max_retries: int = DEFAULT_MAX_RETRIES,
    concurrency_limit: int = 12,
    validate_response: Callable[[httpx.Response], Any] | None = None,
) -> httpx.Response:
    return await request_llm_with_retry(
        client,
        "POST",
        url,
        headers=headers,
        payload=payload,
        stream=stream,
        max_retries=max_retries,
        concurrency_limit=concurrency_limit,
        validate_response=validate_response,
    )
