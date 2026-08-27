from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import asdict, dataclass, replace
from typing import Any, Literal
from urllib.parse import urlparse

from .config import Settings
from .database import Database


MODEL_CONFIGURATION_KEY = "model_configuration_v1"
ModelRole = Literal["chat", "coordinator", "executor"]
ReasoningEffort = Literal["minimal", "low", "medium", "high", "xhigh", "max", "ultra"]
REASONING_EFFORTS = {"minimal", "low", "medium", "high", "xhigh", "max", "ultra"}


def _normalize_base_url(value: Any) -> str:
    url = str(value or "").strip().rstrip("/")
    if url.endswith("/chat/completions"):
        url = url.removesuffix("/chat/completions").rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("模型 API URL 必须是有效的 HTTP 或 HTTPS 地址")
    return url


def _clean_model(value: Any) -> str:
    model = str(value or "").strip()
    if not model or len(model) > 200 or any(character in model for character in "\r\n\0"):
        raise ValueError("模型名称无效")
    return model


def _clean_reasoning_effort(value: Any) -> str:
    effort = str(value or "max").strip().lower()
    if effort not in REASONING_EFFORTS:
        raise ValueError("思考强度必须是 minimal、low、medium、high、xhigh、max 或 ultra")
    return effort


def _secret_key(app_secret: str) -> bytes:
    return hashlib.sha256(("mumu-model-config-v1:" + app_secret).encode("utf-8")).digest()


def _seal(value: str, app_secret: str) -> str:
    if not value:
        return ""
    key = _secret_key(app_secret)
    nonce = secrets.token_bytes(16)
    raw = value.encode("utf-8")
    stream = bytearray()
    counter = 0
    while len(stream) < len(raw):
        stream.extend(hmac.new(key, nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest())
        counter += 1
    ciphertext = bytes(left ^ right for left, right in zip(raw, stream, strict=False))
    mac = hmac.new(key, b"model-secret:" + nonce + ciphertext, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(nonce + mac + ciphertext).decode("ascii")


def _open(value: Any, app_secret: str) -> str:
    encoded = str(value or "")
    if not encoded:
        return ""
    try:
        raw = base64.urlsafe_b64decode(encoded.encode("ascii"))
        nonce, mac, ciphertext = raw[:16], raw[16:48], raw[48:]
        key = _secret_key(app_secret)
        expected = hmac.new(key, b"model-secret:" + nonce + ciphertext, hashlib.sha256).digest()
        if len(nonce) != 16 or not hmac.compare_digest(mac, expected):
            return ""
        stream = bytearray()
        counter = 0
        while len(stream) < len(ciphertext):
            stream.extend(hmac.new(key, nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest())
            counter += 1
        return bytes(left ^ right for left, right in zip(ciphertext, stream, strict=False)).decode("utf-8")
    except (ValueError, UnicodeError):
        return ""


@dataclass(frozen=True)
class ModelEndpoint:
    base_url: str
    api_key: str
    model: str
    supports_vision: bool
    reasoning_enabled: bool = True
    reasoning_effort: str = "max"
    vision_base_url: str = ""
    vision_api_key: str = ""
    vision_model: str = ""

    def vision_endpoint(self) -> "ModelEndpoint | None":
        if self.supports_vision:
            return self
        if not self.vision_base_url or not self.vision_api_key or not self.vision_model:
            return None
        return ModelEndpoint(
            base_url=self.vision_base_url,
            api_key=self.vision_api_key,
            model=self.vision_model,
            supports_vision=True,
            reasoning_enabled=self.reasoning_enabled,
            reasoning_effort=self.reasoning_effort,
        )


@dataclass(frozen=True)
class ModelConfiguration:
    split_enabled: bool
    chat: ModelEndpoint
    coordinator: ModelEndpoint
    executor: ModelEndpoint


class ModelConfigStore:
    def __init__(self, database: Database, settings: Settings):
        self.database = database
        self.settings = settings

    def _fallback(self, role: ModelRole) -> ModelEndpoint:
        if role == "chat":
            base_url = self.settings.chat_llm_base_url
            api_key = self.settings.chat_llm_api_key
            model = self.settings.chat_llm_model
        elif role == "coordinator":
            base_url = self.settings.coordinator_llm_base_url
            api_key = self.settings.coordinator_llm_api_key
            model = self.settings.coordinator_llm_model
        else:
            base_url = self.settings.llm_base_url
            api_key = self.settings.llm_api_key
            model = self.settings.llm_model
        return ModelEndpoint(
            base_url=base_url,
            api_key=api_key,
            model=model,
            supports_vision=True,
            reasoning_enabled=True,
            reasoning_effort="max",
        )

    def _raw(self) -> dict[str, Any]:
        value = self.database.get_app_setting(MODEL_CONFIGURATION_KEY)
        if not value:
            return {}
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _endpoint(self, value: Any, fallback: ModelEndpoint) -> ModelEndpoint:
        data = value if isinstance(value, dict) else {}
        supports_vision = bool(data.get("supportsVision", fallback.supports_vision))
        return ModelEndpoint(
            base_url=_normalize_base_url(data.get("baseUrl") or fallback.base_url),
            api_key=_open(data.get("apiKeyCipher"), self.settings.app_secret) or fallback.api_key,
            model=_clean_model(data.get("model") or fallback.model),
            supports_vision=supports_vision,
            reasoning_enabled=bool(data.get("reasoningEnabled", fallback.reasoning_enabled)),
            reasoning_effort=_clean_reasoning_effort(data.get("reasoningEffort") or fallback.reasoning_effort),
            vision_base_url=(
                _normalize_base_url(data.get("visionBaseUrl")) if str(data.get("visionBaseUrl") or "").strip() else ""
            ),
            vision_api_key=_open(data.get("visionApiKeyCipher"), self.settings.app_secret),
            vision_model=_clean_model(data.get("visionModel")) if str(data.get("visionModel") or "").strip() else "",
        )

    def get(self) -> ModelConfiguration:
        raw = self._raw()
        chat = self._endpoint(raw.get("chat"), self._fallback("chat"))
        executor = self._endpoint(raw.get("executor"), self._fallback("executor"))
        coordinator = self._endpoint(raw.get("coordinator"), self._fallback("coordinator"))
        return ModelConfiguration(
            split_enabled=bool(raw.get("splitEnabled", self.settings.model_split_enabled)),
            chat=chat,
            coordinator=coordinator,
            executor=executor,
        )

    def endpoint(self, role: ModelRole) -> ModelEndpoint:
        configuration = self.get()
        if role == "chat":
            return configuration.chat
        if role == "coordinator" and configuration.split_enabled:
            return configuration.coordinator
        return configuration.executor

    def _stored_endpoint(self, endpoint: ModelEndpoint, api_key: str, vision_api_key: str) -> dict[str, Any]:
        return {
            "baseUrl": _normalize_base_url(endpoint.base_url),
            "apiKeyCipher": _seal(api_key, self.settings.app_secret),
            "model": _clean_model(endpoint.model),
            "supportsVision": bool(endpoint.supports_vision),
            "reasoningEnabled": bool(endpoint.reasoning_enabled),
            "reasoningEffort": _clean_reasoning_effort(endpoint.reasoning_effort),
            "visionBaseUrl": _normalize_base_url(endpoint.vision_base_url) if endpoint.vision_base_url else "",
            "visionApiKeyCipher": _seal(vision_api_key, self.settings.app_secret),
            "visionModel": _clean_model(endpoint.vision_model) if endpoint.vision_model else "",
        }

    def update(self, payload: dict[str, Any]) -> ModelConfiguration:
        current = self.get()
        raw = self._raw()

        def updated(role: ModelRole, existing: ModelEndpoint) -> tuple[ModelEndpoint, str, str]:
            value = payload.get(role)
            data = value if isinstance(value, dict) else {}
            endpoint = replace(
                existing,
                base_url=_normalize_base_url(data.get("base_url") or existing.base_url),
                model=_clean_model(data.get("model") or existing.model),
                supports_vision=bool(data.get("supports_vision", existing.supports_vision)),
                reasoning_enabled=bool(data.get("reasoning_enabled", existing.reasoning_enabled)),
                reasoning_effort=_clean_reasoning_effort(
                    data.get("reasoning_effort") or existing.reasoning_effort
                ),
                vision_base_url=(
                    _normalize_base_url(data.get("vision_base_url")) if str(data.get("vision_base_url") or "").strip()
                    else ("" if "vision_base_url" in data else existing.vision_base_url)
                ),
                vision_model=(
                    _clean_model(data.get("vision_model")) if str(data.get("vision_model") or "").strip()
                    else ("" if "vision_model" in data else existing.vision_model)
                ),
            )
            stored = raw.get(role) if isinstance(raw.get(role), dict) else {}
            api_key = str(data.get("api_key") or "").strip() or _open(
                stored.get("apiKeyCipher"), self.settings.app_secret
            ) or existing.api_key
            vision_api_key = str(data.get("vision_api_key") or "").strip() or _open(
                stored.get("visionApiKeyCipher"), self.settings.app_secret
            ) or existing.vision_api_key
            if not api_key:
                raise ValueError(f"{role} 模型缺少 API Key")
            if not endpoint.supports_vision and not all(
                (endpoint.vision_base_url, vision_api_key, endpoint.vision_model)
            ):
                raise ValueError(f"{role} 的视觉模型 URL、API Key 和模型名称必须同时填写")
            return endpoint, api_key, vision_api_key

        chat, chat_key, chat_vision_key = updated("chat", current.chat)
        executor, executor_key, executor_vision_key = updated("executor", current.executor)
        coordinator, coordinator_key, coordinator_vision_key = updated("coordinator", current.coordinator)
        split_enabled = bool(payload.get("split_enabled", current.split_enabled))
        stored = {
            "splitEnabled": split_enabled,
            "chat": self._stored_endpoint(chat, chat_key, chat_vision_key),
            "executor": self._stored_endpoint(executor, executor_key, executor_vision_key),
            "coordinator": self._stored_endpoint(coordinator, coordinator_key, coordinator_vision_key),
        }
        self.database.set_app_setting(MODEL_CONFIGURATION_KEY, json.dumps(stored, ensure_ascii=False))
        return self.get()

    @staticmethod
    def public_endpoint(endpoint: ModelEndpoint) -> dict[str, Any]:
        value = asdict(endpoint)
        value.pop("api_key", None)
        value.pop("vision_api_key", None)
        return {
            "baseUrl": value["base_url"],
            "model": value["model"],
            "supportsVision": value["supports_vision"],
            "reasoningEnabled": value["reasoning_enabled"],
            "reasoningEffort": value["reasoning_effort"],
            "apiKeyConfigured": bool(endpoint.api_key),
            "visionBaseUrl": value["vision_base_url"],
            "visionModel": value["vision_model"],
            "visionApiKeyConfigured": bool(endpoint.vision_api_key),
        }

    def public(self) -> dict[str, Any]:
        configuration = self.get()
        return {
            "splitEnabled": configuration.split_enabled,
            "chat": self.public_endpoint(configuration.chat),
            "coordinator": self.public_endpoint(configuration.coordinator),
            "executor": self.public_endpoint(configuration.executor),
        }
