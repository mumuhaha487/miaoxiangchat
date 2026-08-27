from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app import main
from app import llm_retry
from app.llm_retry import RETRY_EXHAUSTED_HEADER, LLMResponseFatal, LLMUpstreamExhausted
from app.tool_bridge import normalize_completion


@pytest.mark.asyncio
async def test_upstream_proxy_retries_transient_statuses(monkeypatch):
    statuses = [524, 503, 200]
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        status = statuses.pop(0)
        return httpx.Response(status, headers={"retry-after": "0"}, json={"status": status})

    monkeypatch.setattr(llm_retry, "retry_delay", lambda _response, _attempt: 0)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        response = await main.post_llm_upstream_with_retry(
            client,
            "https://model.example/v1/chat/completions",
            headers={"Authorization": "Bearer test"},
            payload={"messages": [{"role": "user", "content": "test"}]},
            stream=False,
        )

    assert response.status_code == 200
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_upstream_proxy_retries_transport_error(monkeypatch):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("temporary", request=request)
        return httpx.Response(200, json={"choices": []})

    monkeypatch.setattr(llm_retry, "retry_delay", lambda _response, _attempt: 0)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        response = await main.post_llm_upstream_with_retry(
            client,
            "https://model.example/v1/chat/completions",
            headers={},
            payload={"messages": []},
            stream=True,
        )
        await response.aclose()

    assert response.status_code == 200
    assert calls == 2


@pytest.mark.asyncio
async def test_upstream_proxy_raises_after_five_reconnects(monkeypatch):
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(502, headers={"retry-after": "0"}, text="temporary gateway failure")

    monkeypatch.setattr(llm_retry, "retry_delay", lambda _response, _attempt: 0)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(LLMUpstreamExhausted, match="自动重连 5 次"):
            await main.post_llm_upstream_with_retry(
                client,
                "https://model.example/v1/chat/completions",
                headers={},
                payload={"messages": []},
                stream=False,
            )

    assert calls == 6
    assert calls == llm_retry.DEFAULT_MAX_RETRIES + 1


@pytest.mark.asyncio
async def test_upstream_proxy_does_not_retry_non_transient_status(monkeypatch):
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401, json={"error": "bad key"})

    monkeypatch.setattr(llm_retry, "retry_delay", lambda _response, _attempt: 0)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        response = await main.post_llm_upstream_with_retry(
            client,
            "https://model.example/v1/chat/completions",
            headers={},
            payload={"messages": []},
        )

    assert response.status_code == 401
    assert calls == 1


@pytest.mark.asyncio
async def test_upstream_proxy_retries_invalid_completion(monkeypatch):
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        content = "" if calls == 1 else "恢复成功"
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    def validate(response: httpx.Response) -> None:
        content = response.json()["choices"][0]["message"]["content"]
        if not content:
            raise ValueError("empty completion")

    monkeypatch.setattr(llm_retry, "retry_delay", lambda _response, _attempt: 0)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        response = await main.post_llm_upstream_with_retry(
            client,
            "https://model.example/v1/chat/completions",
            headers={},
            payload={"messages": []},
            validate_response=validate,
        )

    assert response.json()["choices"][0]["message"]["content"] == "恢复成功"
    assert calls == 2


@pytest.mark.asyncio
async def test_upstream_proxy_never_retries_fatal_model_identity_violation(monkeypatch):
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"model": "gpt-5.6-luna", "choices": []})

    def validate(_response: httpx.Response) -> None:
        raise LLMResponseFatal("configured and reported models differ")

    monkeypatch.setattr(llm_retry, "retry_delay", lambda _response, _attempt: 0)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(LLMResponseFatal, match="models differ"):
            await llm_retry.post_llm_with_retry(
                client,
                "https://model.example/v1/chat/completions",
                headers={},
                payload={"messages": []},
                max_retries=8,
                validate_response=validate,
            )

    assert calls == 1


@pytest.mark.asyncio
async def test_internal_and_control_proxy_core_rejects_substituted_executor_model(monkeypatch):
    payload = {"model": "mumu-execution", "messages": [{"role": "user", "content": "test"}]}
    body = json.dumps(payload).encode()
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request({
        "type": "http",
        "method": "POST",
        "path": "/api/internal/llm/v1/chat/completions",
        "headers": [],
        "query_string": b"",
        "server": ("test", 80),
        "client": ("test", 123),
        "scheme": "http",
    }, receive)

    class Endpoint:
        base_url = "https://executor.example.test/v1"
        api_key = "secret"

    async def prepare_payload(role, incoming):
        assert role == "executor"
        prepared = dict(incoming)
        prepared["model"] = "gemini-3.7-flash-high"
        return Endpoint(), "gemini-3.7-flash-high", prepared

    async def fake_post(_client, url, *, validate_response, **_kwargs):
        response = httpx.Response(200, json={
            "model": "gpt-5.6-luna",
            "choices": [{"message": {"content": "wrong route"}}],
        }, request=httpx.Request("POST", url))
        validate_response(response)
        return response

    monkeypatch.setattr(main.model_gateway, "prepare_payload", prepare_payload)
    monkeypatch.setattr(main, "post_llm_upstream_with_retry", fake_post)
    with pytest.raises(HTTPException) as captured:
        await main._fixed_llm_proxy(request, "chat/completions", max_body_bytes=10000)

    assert captured.value.status_code == 502
    assert "gpt-5.6-luna" in captured.value.detail
    assert "gemini-3.7-flash-high" in captured.value.detail


@pytest.mark.asyncio
async def test_upstream_proxy_recovers_after_seven_invalid_responses(monkeypatch):
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        content = "恢复成功" if calls == 8 else ""
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    def validate(response: httpx.Response) -> None:
        if not response.json()["choices"][0]["message"]["content"]:
            raise ValueError("empty completion")

    monkeypatch.setattr(llm_retry, "retry_delay", lambda _response, _attempt: 0)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        response = await llm_retry.post_llm_with_retry(
            client,
            "https://high-error-model.example/v1/chat/completions",
            headers={},
            payload={"messages": []},
            max_retries=8,
            validate_response=validate,
        )

    assert response.json()["choices"][0]["message"]["content"] == "恢复成功"
    assert calls == 8


@pytest.mark.asyncio
async def test_upstream_proxy_limits_concurrent_requests_per_endpoint():
    active = 0
    peak = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        responses = await asyncio.gather(*(
            llm_retry.post_llm_with_retry(
                client,
                "https://concurrency-filter.example/v1/chat/completions",
                headers={},
                payload={"messages": [{"role": "user", "content": str(index)}]},
                max_retries=0,
                concurrency_limit=4,
            )
            for index in range(24)
        ))

    assert all(response.status_code == 200 for response in responses)
    assert peak <= 4


@pytest.mark.asyncio
async def test_upstream_proxy_reconnects_after_incomplete_tool_call(monkeypatch):
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        content = (
            '<tool_call>{"name":"write_file","arguments":{"content":"import os'
            if calls == 1
            else '<tool_call>{"name":"write_file","arguments":{"content":"ok"}}</tool_call>'
        )
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    def validate(response: httpx.Response) -> None:
        normalize_completion(response.json(), "fixed-model", {"write_file"})

    monkeypatch.setattr(llm_retry, "retry_delay", lambda _response, _attempt: 0)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        response = await main.post_llm_upstream_with_retry(
            client,
            "https://model.example/v1/chat/completions",
            headers={},
            payload={"messages": []},
            validate_response=validate,
        )

    completion = normalize_completion(response.json(), "fixed-model", {"write_file"})
    assert completion["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "write_file"
    assert calls == 2


@pytest.mark.asyncio
async def test_fixed_completion_marks_exhausted_retry_cycle(monkeypatch):
    async def exhausted(*_args, **_kwargs):
        raise LLMUpstreamExhausted(5, status_code=502)

    monkeypatch.setattr(main.model_gateway, "complete", exhausted)
    with pytest.raises(HTTPException) as captured:
        await main.fixed_chat_completion([{"role": "user", "content": "test"}])

    assert captured.value.status_code == 502
    assert captured.value.headers == {RETRY_EXHAUSTED_HEADER: "1"}
    assert "自动重连 5 次" in captured.value.detail
