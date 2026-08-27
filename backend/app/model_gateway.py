from __future__ import annotations

import asyncio
import base64
import binascii
import json
import struct
import time
import zlib
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .config import Settings
from .llm_retry import LLMResponseFatal, LLMUpstreamExhausted, post_llm_with_retry, request_llm_with_retry
from .model_config import ModelConfigStore, ModelEndpoint, ModelRole


VISION_DESCRIPTION_PROMPT = "请用简洁但又比较详细的语言描述这张图片，包括主体、颜色、文字、布局和重要细节。"


def _completion_content(completion: Any) -> str:
    choices = completion.get("choices") if isinstance(completion, dict) else None
    message = choices[0].get("message") if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else ""
    if isinstance(content, list):
        return "".join(
            str(item.get("text") or "")
            for item in content
            if isinstance(item, dict) and item.get("type") in {None, "text"}
        ).strip()
    return str(content or "").strip()


def _stream_content(completion: Any) -> str:
    choices = completion.get("choices") if isinstance(completion, dict) else None
    choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None
    if not isinstance(choice, dict):
        return ""
    block = choice.get("delta") if isinstance(choice.get("delta"), dict) else choice.get("message")
    content = block.get("content") if isinstance(block, dict) else ""
    if isinstance(content, list):
        return "".join(
            str(item.get("text") or "")
            for item in content
            if isinstance(item, dict) and item.get("type") in {None, "text"}
        )
    return str(content or "")


def _model_identity(value: Any) -> str:
    identity = str(value or "").strip().casefold()
    if "/" in identity:
        identity = identity.rsplit("/", 1)[-1]
    return identity


def require_reported_model(completion: Any, requested_model: str) -> str:
    reported = str(completion.get("model") or "").strip() if isinstance(completion, dict) else ""
    if not reported:
        raise LLMResponseFatal(f"上游响应缺少 model，无法确认实际调用是否为 {requested_model}")
    if _model_identity(reported) != _model_identity(requested_model):
        raise LLMResponseFatal(f"上游实际返回模型 {reported}，与管理员配置 {requested_model} 不一致")
    return reported


def _validate_completion(response: httpx.Response, requested_model: str) -> None:
    completion = response.json()
    require_reported_model(completion, requested_model)
    if not _completion_content(completion):
        raise ValueError("模型没有返回文本内容")


def _red_png_data_url() -> str:
    def chunk(name: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + name + data + struct.pack(">I", binascii.crc32(name + data) & 0xFFFFFFFF)

    width = height = 16
    scanlines = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(scanlines))
        + chunk(b"IEND", b"")
    )
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


class ModelGateway:
    def __init__(self, store: ModelConfigStore, settings: Settings):
        self.store = store
        self.settings = settings
        self._model_cache: dict[tuple[str, str], tuple[str, float]] = {}

    @staticmethod
    def _headers(endpoint: ModelEndpoint) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {endpoint.api_key}",
            "Content-Type": "application/json",
            "Accept-Encoding": "identity",
        }

    @staticmethod
    def _choose_model(model_ids: list[str]) -> str:
        incompatible = ("embedding", "rerank", "moderation", "audio", "tts", "whisper")
        candidates = [
            model_id.strip()
            for model_id in model_ids
            if model_id.strip() and not any(keyword in model_id.lower() for keyword in incompatible)
        ]
        standard = [model_id for model_id in candidates if not model_id.lower().endswith("-agent")]
        return (standard or candidates or [""])[0]

    async def resolve_model(self, endpoint: ModelEndpoint) -> str:
        if endpoint.model.lower() != "auto":
            return endpoint.model
        cache_key = (endpoint.base_url, endpoint.api_key)
        cached = self._model_cache.get(cache_key)
        now = time.monotonic()
        if cached and cached[1] > now:
            return cached[0]

        def validate(response: httpx.Response) -> None:
            payload = response.json()
            rows = payload.get("data") if isinstance(payload, dict) else None
            models = [str(row.get("id") or "") for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
            if not self._choose_model(models):
                raise ValueError("没有可用模型")

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(20),
            verify=self.settings.llm_verify_tls,
            proxy=self.settings.llm_proxy or None,
        ) as client:
            response = await request_llm_with_retry(
                client,
                "GET",
                f"{endpoint.base_url}/models",
                headers=self._headers(endpoint),
                max_retries=self.settings.llm_max_retries,
                concurrency_limit=self.settings.llm_concurrency_limit,
                validate_response=validate,
            )
        response.raise_for_status()
        rows = response.json().get("data") or []
        selected = self._choose_model([str(row.get("id") or "") for row in rows if isinstance(row, dict)])
        self._model_cache[cache_key] = (selected, now + 300)
        return selected

    async def _describe_image(self, endpoint: ModelEndpoint, image_url: str) -> str:
        model = await self.resolve_model(endpoint)
        payload = {
            "model": model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": VISION_DESCRIPTION_PROMPT},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }],
            "max_tokens": 900,
            "stream": False,
            "reasoning_effort": endpoint.reasoning_effort if endpoint.reasoning_enabled else "none",
        }
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(120, connect=20),
            verify=self.settings.llm_verify_tls,
            proxy=self.settings.llm_proxy or None,
        ) as client:
            response = await post_llm_with_retry(
                client,
                f"{endpoint.base_url}/chat/completions",
                headers=self._headers(endpoint),
                payload=payload,
                max_retries=self.settings.llm_max_retries,
                concurrency_limit=self.settings.llm_concurrency_limit,
                validate_response=lambda response: _validate_completion(response, model),
            )
        response.raise_for_status()
        completion = response.json()
        require_reported_model(completion, model)
        return _completion_content(completion)

    async def prepare_messages(self, endpoint: ModelEndpoint, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if endpoint.supports_vision:
            return messages
        vision = endpoint.vision_endpoint()
        prepared: list[dict[str, Any]] = []
        described = 0
        for original in messages:
            message = dict(original)
            content = message.get("content")
            if not isinstance(content, list):
                prepared.append(message)
                continue
            blocks: list[dict[str, Any]] = []
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "image_url":
                    blocks.append(block)
                    continue
                if vision is None:
                    raise ValueError("当前模型不支持视觉，且尚未配置视觉多模态模型")
                if described >= 10:
                    blocks.append({"type": "text", "text": "[其余图片未处理：单次最多识别 10 张图片]"})
                    continue
                image_value = block.get("image_url")
                image_url = str(image_value.get("url") or "") if isinstance(image_value, dict) else str(image_value or "")
                if not image_url:
                    raise ValueError("图片地址为空")
                description = await self._describe_image(vision, image_url)
                described += 1
                blocks.append({
                    "type": "text",
                    "text": f"[视觉模型对第 {described} 张图片的描述]\n{description}",
                })
            message["content"] = blocks
            prepared.append(message)
        return prepared

    async def prepare_payload(
        self,
        role: ModelRole,
        payload: dict[str, Any],
    ) -> tuple[ModelEndpoint, str, dict[str, Any]]:
        endpoint = self.store.endpoint(role)
        model = await self.resolve_model(endpoint)
        prepared = dict(payload)
        prepared["model"] = model
        prepared["reasoning_effort"] = endpoint.reasoning_effort if endpoint.reasoning_enabled else "none"
        messages = prepared.get("messages")
        if isinstance(messages, list):
            prepared["messages"] = await self.prepare_messages(
                endpoint,
                [dict(item) for item in messages if isinstance(item, dict)],
            )
        return endpoint, model, prepared

    async def complete(
        self,
        role: ModelRole,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 2048,
    ) -> tuple[str, dict[str, Any], str]:
        endpoint, model, prepared = await self.prepare_payload(role, {
            "messages": messages,
            "max_tokens": max(64, min(max_tokens, 8192)),
            "stream": False,
        })
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(120, connect=20),
            verify=self.settings.llm_verify_tls,
            proxy=self.settings.llm_proxy or None,
        ) as client:
            response = await post_llm_with_retry(
                client,
                f"{endpoint.base_url}/chat/completions",
                headers=self._headers(endpoint),
                payload=prepared,
                max_retries=self.settings.llm_max_retries,
                concurrency_limit=self.settings.llm_concurrency_limit,
                validate_response=lambda response: _validate_completion(response, model),
            )
        response.raise_for_status()
        completion = response.json()
        reported_model = require_reported_model(completion, model)
        usage = completion.get("usage") if isinstance(completion.get("usage"), dict) else {}
        return _completion_content(completion), usage, reported_model

    async def stream(
        self,
        role: ModelRole,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 2048,
    ) -> AsyncIterator[dict[str, Any]]:
        endpoint, model, prepared = await self.prepare_payload(role, {
            "messages": messages,
            "max_tokens": max(64, min(max_tokens, 8192)),
            "stream": True,
            "stream_options": {"include_usage": True},
        })
        retries = self.settings.llm_max_retries
        last_reason = "模型流没有返回文本"
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(120, connect=20, read=None),
            verify=self.settings.llm_verify_tls,
            proxy=self.settings.llm_proxy or None,
        ) as client:
            for attempt in range(retries + 1):
                response: httpx.Response | None = None
                emitted = False
                finished = False
                usage: dict[str, Any] = {}
                reported_model = ""
                try:
                    response = await post_llm_with_retry(
                        client,
                        f"{endpoint.base_url}/chat/completions",
                        headers=self._headers(endpoint),
                        payload=prepared,
                        stream=True,
                        max_retries=0,
                        concurrency_limit=self.settings.llm_concurrency_limit,
                    )
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        raw = line.strip()
                        if not raw or raw.startswith(":") or raw.startswith("event:"):
                            continue
                        if raw.startswith("data:"):
                            raw = raw[5:].strip()
                        if raw == "[DONE]":
                            finished = True
                            break
                        try:
                            chunk = json.loads(raw)
                        except json.JSONDecodeError:
                            last_reason = "模型流包含无效 JSON"
                            continue
                        if isinstance(chunk, dict) and isinstance(chunk.get("usage"), dict):
                            usage = chunk["usage"]
                        if isinstance(chunk, dict) and str(chunk.get("model") or "").strip():
                            reported_model = require_reported_model(chunk, model)
                        delta = _stream_content(chunk)
                        if delta:
                            emitted = True
                            yield {"type": "delta", "content": delta}
                        choices = chunk.get("choices") if isinstance(chunk, dict) else None
                        if (
                            isinstance(choices, list)
                            and choices
                            and isinstance(choices[0], dict)
                            and choices[0].get("finish_reason") is not None
                        ):
                            finished = True
                    if emitted and finished:
                        if not reported_model:
                            raise LLMResponseFatal(f"上游流式响应缺少 model，无法确认实际调用是否为 {model}")
                        yield {"type": "done", "usage": usage, "model": reported_model}
                        return
                    if emitted:
                        raise LLMUpstreamExhausted(0, reason="模型流在完成前中断")
                    last_reason = "模型流没有返回文本"
                except httpx.HTTPStatusError:
                    raise
                except LLMResponseFatal:
                    raise
                except (httpx.TransportError, LLMUpstreamExhausted, ValueError) as exc:
                    if emitted:
                        raise LLMUpstreamExhausted(0, reason="模型流在完成前中断") from exc
                    last_reason = str(exc)
                finally:
                    if response is not None:
                        await response.aclose()
                if attempt >= retries:
                    break
                await asyncio.sleep(min(0.4 * (2 ** min(attempt, 4)), 6.0))
        raise LLMUpstreamExhausted(retries, reason=last_reason)

    async def test_connection(self, role: ModelRole) -> dict[str, Any]:
        endpoint = self.store.endpoint(role)
        started = time.monotonic()
        stages: list[dict[str, Any]] = []
        if endpoint.supports_vision:
            reply, _usage, model = await self.complete(role, [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "hi。请简短回复，并说明这张纯色图片是什么颜色。"},
                    {"type": "image_url", "image_url": {"url": _red_png_data_url()}},
                ],
            }], max_tokens=180)
            stages.append({"name": "模型与视觉", "ok": True, "reply": reply[:500]})
        else:
            reply, _usage, model = await self.complete(role, [{"role": "user", "content": "hi"}], max_tokens=120)
            stages.append({"name": "文本模型", "ok": True, "reply": reply[:500]})
            vision = endpoint.vision_endpoint()
            if vision is not None:
                description = await self._describe_image(vision, _red_png_data_url())
                stages.append({"name": "视觉模型", "ok": True, "reply": description[:500]})
                final, _usage, _model = await self.complete(role, [{
                    "role": "user",
                    "content": f"视觉模型描述了一张测试图：{description}\n请简短确认你理解了图片内容。",
                }], max_tokens=180)
                stages.append({"name": "视觉描述转交", "ok": True, "reply": final[:500]})

        async def resilience_probe(index: int) -> tuple[str, str]:
            probe_reply, _probe_usage, probe_model = await self.complete(
                role,
                [{"role": "user", "content": f"连接稳定性探针 {index + 1}：只回复 OK"}],
                max_tokens=64,
            )
            return probe_reply, probe_model

        probe_results = await asyncio.gather(
            *(resilience_probe(index) for index in range(4)),
            return_exceptions=True,
        )
        successful_probes = [
            result for result in probe_results
            if isinstance(result, tuple) and result and str(result[0]).strip()
        ]
        filtered_errors = len(probe_results) - len(successful_probes)
        if not successful_probes:
            raise LLMUpstreamExhausted(
                self.settings.llm_max_retries,
                reason="并发稳定性探针全部失败",
            )
        stages.append({
            "name": "并发容错",
            "ok": True,
            "successful": len(successful_probes),
            "total": len(probe_results),
            "filteredErrors": filtered_errors,
            "reply": str(successful_probes[0][0])[:500],
        })
        return {
            "ok": True,
            "role": role,
            "model": model,
            "latencyMs": round((time.monotonic() - started) * 1000),
            "stages": stages,
        }


__all__ = ["LLMUpstreamExhausted", "ModelGateway", "_red_png_data_url", "require_reported_model"]
