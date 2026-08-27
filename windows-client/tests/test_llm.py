from __future__ import annotations

import httpx
import pytest

from vmss_agent.llm import ControlLLM, MAX_LLM_ATTEMPTS, RETRY_EXHAUSTED_HEADER


def completion_payload():
    return {
        "choices": [{"message": {"role": "assistant", "content": "完成", "tool_calls": []}}]
    }


@pytest.mark.asyncio
async def test_control_llm_recovers_from_gateway_failure():
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(524, headers={"retry-after": "0"}, text="temporary")
        return httpx.Response(200, json=completion_payload())

    llm = ControlLLM("https://example.com", "scoped-credential")
    await llm.client.aclose()
    llm.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        turn = await llm.complete([{"role": "user", "content": "test"}], [])
    finally:
        await llm.close()

    assert calls == 3
    assert turn.content == "完成"


@pytest.mark.asyncio
async def test_control_llm_reports_exhausted_transient_failure_in_chinese():
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(502, headers={"retry-after": "0"}, text="temporary")

    llm = ControlLLM("https://example.com", "scoped-credential")
    await llm.client.aclose()
    llm.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(RuntimeError, match="已自动重试"):
            await llm.complete([{"role": "user", "content": "test"}], [])
    finally:
        await llm.close()

    assert calls == MAX_LLM_ATTEMPTS


@pytest.mark.asyncio
async def test_control_llm_does_not_repeat_backend_exhausted_retry_cycle():
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            502,
            headers={RETRY_EXHAUSTED_HEADER: "1"},
            json={"detail": "初次请求失败后已自动重连 5 次"},
        )

    llm = ControlLLM("https://example.com", "scoped-credential")
    await llm.client.aclose()
    llm.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(RuntimeError, match="自动重连 5 次"):
            await llm.complete([{"role": "user", "content": "test"}], [])
    finally:
        await llm.close()

    assert calls == 1
