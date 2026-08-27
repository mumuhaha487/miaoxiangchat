from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import mimetypes
import os
import secrets
import smtplib
import sqlite3
import ssl
import time
import re
import zipfile
from contextlib import asynccontextmanager
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import httpx
import jwt
import websockets
from fastapi import (
    Depends,
    FastAPI,
    File,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from .config import get_settings
from .capabilities import CapabilityManager, json_list
from .computer_control import (
    ALLOWED_EVENT_TYPES,
    ControlAgentHub,
    ComputerControlStore,
    public_control_device,
    public_control_event,
    public_control_task,
)
from .context_manager import context_messages, fallback_summary, plan_compression, summary_prompt
from .coordinator import Coordinator
from .conversation_title import summarize_conversation_title
from .database import Database, now_ms
from .hermes_client import HermesClient, HermesError
from .llm_retry import (
    RETRY_EXHAUSTED_HEADER,
    LLMUpstreamExhausted,
    post_llm_with_retry,
)
from .model_config import ModelConfigStore, ModelRole
from .model_gateway import ModelGateway, require_reported_model
from .quality_reviewer import QualityReviewer
from .runtime_manager import RuntimeManager, RuntimeUnavailable
from .savepoints import SavepointError, SavepointStore
from .schemas import (
    ActivationCodeCreateRequest,
    ActivationCodeUpdateRequest,
    ActivationRedeemRequest,
    AdminLoginRequest,
    AdminModelSettingsUpdateRequest,
    AdminModelTestRequest,
    AdminUserCreateRequest,
    AdminUserUpdateRequest,
    BindEmailRequest,
    BindEmailVerifyRequest,
    BrowserActionRequest,
    CapabilityImportRequest,
    CapabilityShareRequest,
    ConversationChatRequest,
    ConversationControlBindingRequest,
    ConversationCreateRequest,
    ConversationUpdateRequest,
    ControlDeviceRegisterRequest,
    ControlDeviceUpdateRequest,
    ControlTaskApprovalRequest,
    ControlTaskCreateRequest,
    GuestArchiveImportRequest,
    GuestChatRequest,
    NotificationPreferencesUpdateRequest,
    PortOpenRequest,
    RequestCodeRequest,
    SavepointCreateRequest,
    SkillInstallRequest,
    SkillSearchRequest,
    TaskApprovalRequest,
    TaskCreateRequest,
    TaskSteerRequest,
    VerifyCodeRequest,
    WebviewTicketExchangeRequest,
    WechatCloudLoginRequest,
    WechatLoginRequest,
    WorkflowCategoryCreateRequest,
    WorkflowCreateRequest,
    WorkflowFromConversationRequest,
    WorkflowUpdateRequest,
)
from .security import (
    OneTimeReplayGuard,
    SlidingWindowLimiter,
    activation_code_digest,
    activation_registration_token,
    code_digest,
    decode_access_token,
    hash_password,
    issue_access_token,
    issue_browser_scope,
    issue_runtime_scope,
    normalize_email,
    normalize_username,
    opaque_digest,
    validate_password,
    verify_browser_scope,
    verify_runtime_scope,
    verify_internal_runtime_token,
    verify_activation_registration_token,
    verify_password,
    wechat_cloud_login_signature,
)
from .task_dispatcher import ACTIVE_STATUSES, TaskDispatcher
from .tool_bridge import completion_sse, normalize_completion, prepare_upstream_payload


settings = get_settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
database = Database(settings.data_dir / "app.db")
computer_store = ComputerControlStore(database, settings.app_secret, settings.data_dir)
computer_hub = ControlAgentHub()
runtime_manager = RuntimeManager(settings)
hermes_client = HermesClient(settings)
capability_manager = CapabilityManager(database, runtime_manager)
model_store = ModelConfigStore(database, settings)
model_gateway = ModelGateway(model_store, settings)
coordinator = Coordinator(model_gateway)
quality_reviewer = QualityReviewer(settings, model_gateway)
dispatcher = TaskDispatcher(
    database,
    runtime_manager,
    hermes_client,
    capability_manager=capability_manager,
    coordinator=coordinator,
    quality_reviewer=quality_reviewer,
)
savepoint_store = SavepointStore()
savepoint_locks: dict[str, asyncio.Lock] = {}
workspace_upload_locks: dict[str, asyncio.Lock] = {}
wechat_cloud_replay_guard = OneTimeReplayGuard()
logger = logging.getLogger(__name__)

LLM_PROXY_MAX_ATTEMPTS = settings.llm_max_retries + 1


def write_terminal_stream(stream: Any, content: bytes) -> None:
    raw_socket = getattr(stream, "_sock", None)
    if hasattr(raw_socket, "sendall"):
        raw_socket.sendall(content)
    elif hasattr(stream, "sendall"):
        stream.sendall(content)
    else:
        stream.write(content)
        flush = getattr(stream, "flush", None)
        if flush:
            flush()


post_llm_upstream_with_retry = post_llm_with_retry


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await asyncio.to_thread(computer_store.prune_expired_data)
    try:
        active_users = database.all(
            "SELECT id FROM users WHERE status = 'active' AND role = 'user' ORDER BY created_at ASC"
        )
        synced_users = await runtime_manager.sync_builtin_skills_for_users(
            [str(row["id"]) for row in active_users]
        )
        logger.info("builtin_skills_synced users=%s", synced_users)
    except Exception:
        logger.exception("builtin_skills_sync_failed")
    await dispatcher.start()
    try:
        yield
    finally:
        await computer_hub.close()
        await dispatcher.close()


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://appassets.androidplatform.net"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Accept", "Authorization", "Content-Type"],
    expose_headers=["Content-Disposition", "X-API-Version"],
)
app.add_middleware(GZipMiddleware, minimum_size=1024)

ANDROID_APP_VERSION_CODE = 24
ANDROID_APP_VERSION_NAME = "3.8.7"
ANDROID_APK_PATH = Path(
    os.getenv("ANDROID_APK_PATH", "/app/artifacts/MiaoxiangZhiDi-arm64-v3.8.7.apk")
)
ANDROID_LEGACY_APK_PATH = Path("/app/artifacts/AIchatMUMU-arm64.apk")
WINDOWS_AGENT_VERSION = "0.6.2"
WINDOWS_AGENT_PATH = Path(os.getenv("WINDOWS_AGENT_PATH", "/app/artifacts/MiaoxiangComputerAgent-x64.exe"))


def versioned_openapi() -> dict[str, Any]:
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    schema["paths"] = {
        (f"/api/v1/{path.removeprefix('/api/')}" if path.startswith("/api/") else path): operation
        for path, operation in schema.get("paths", {}).items()
    }
    schema["servers"] = [{"url": "/"}]
    app.openapi_schema = schema
    return schema


app.openapi = versioned_openapi
admin_login_limiter = SlidingWindowLimiter(limit=10, window_seconds=300, max_keys=1)
verification_email_limiter = SlidingWindowLimiter(limit=30, window_seconds=300, max_keys=1)
verification_address_limiter = SlidingWindowLimiter(limit=10, window_seconds=900)
guest_chat_global_limiter = SlidingWindowLimiter(limit=300, window_seconds=300, max_keys=1)
guest_chat_client_limiter = SlidingWindowLimiter(limit=30, window_seconds=300)
user_chat_limiter = SlidingWindowLimiter(limit=120, window_seconds=300)
trusted_login_limiter = SlidingWindowLimiter(limit=30, window_seconds=300)
control_device_register_limiter = SlidingWindowLimiter(limit=30, window_seconds=300)
control_task_limiter = SlidingWindowLimiter(limit=120, window_seconds=300)
wechat_login_limiter = SlidingWindowLimiter(limit=60, window_seconds=300)
email_bind_limiter = SlidingWindowLimiter(limit=10, window_seconds=900)
preview_path_pattern = re.compile(r"^/[a-z0-9][a-z0-9._-]{2,31}/[0-9]{1,5}(?:/|$)", re.IGNORECASE)


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    email = str(user.get("email") or "")
    internal_wechat = email.endswith("@internal.invalid")
    return {
        "id": str(user.get("id") or ""),
        "username": str(user.get("username") or ("admin" if user.get("role") == "admin" else "")),
        "email": "" if internal_wechat else email,
        "displayName": str(user.get("display_name") or ""),
        "role": str(user.get("role") or "user"),
        "accessTier": str(user.get("access_tier") or ("vip" if user.get("role") == "admin" else "basic")),
        "status": str(user.get("status") or "active"),
        "emailVerified": bool(user.get("email_verified", 1)) and not internal_wechat,
        "emailBound": not internal_wechat,
        "hasCustomBackground": bool(user.get("background_filename")),
        "createdAt": int(user.get("created_at") or 0),
        "lastLoginAt": int(user.get("last_login_at") or 0) or None,
    }


def public_device(device: dict[str, Any], current_device_id: str) -> dict[str, Any]:
    return {
        "id": str(device.get("id") or ""),
        "name": str(device.get("name") or "未知设备"),
        "platform": str(device.get("platform") or "web"),
        "trusted": bool(device.get("trusted_at")),
        "activeSessions": int(device.get("active_sessions") or 0),
        "current": str(device.get("id") or "") == current_device_id,
        "createdAt": int(device.get("created_at") or 0),
        "lastSeenAt": int(device.get("last_seen_at") or 0),
    }


def public_savepoint(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(item.get("id") or ""),
        "name": str(item.get("name") or "保存点"),
        "fileCount": int(item.get("file_count") or 0),
        "logicalBytes": int(item.get("logical_bytes") or 0),
        "storedBytes": int(item.get("stored_bytes") or 0),
        "createdAt": int(item.get("created_at") or 0),
    }


def public_conversation(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(item.get("id") or ""),
        "title": str(item.get("title") or "新对话"),
        "mode": str(item.get("mode") or "agent"),
        "agentProfile": str(item.get("agent_profile") or "expert"),
        "controlDeviceId": str(item.get("control_device_id") or "") or None,
        "controlTargetId": str(item.get("control_target_id") or "") or None,
        "controlTargetKind": str(item.get("control_target_kind") or "") or None,
        "createdAt": int(item.get("created_at") or 0),
        "updatedAt": int(item.get("updated_at") or 0),
    }


def public_message(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(item.get("id") or ""),
        "role": str(item.get("role") or "assistant"),
        "content": str(item.get("content") or ""),
        "createdAt": int(item.get("created_at") or 0),
    }


def _json_list(value: Any) -> list[Any]:
    try:
        result = json.loads(value or "[]")
        return result if isinstance(result, list) else []
    except (TypeError, json.JSONDecodeError):
        return []


def _json_object_from_text(value: str) -> dict[str, Any]:
    text = value.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("模型没有返回 JSON 对象")
        parsed = json.loads(text[start:end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("模型返回的工作流不是对象")
    return parsed


def public_task(item: dict[str, Any]) -> dict[str, Any]:
    task_id = str(item.get("id") or "")
    artifacts = database.list_task_artifacts(task_id)
    attempts = database.list_quality_attempts(task_id)
    selected_attempt = int(item.get("quality_selected_attempt") or 0) or None
    history: list[dict[str, Any]] = []
    for attempt_row in attempts:
        try:
            report = json.loads(str(attempt_row.get("report_json") or "{}"))
        except json.JSONDecodeError:
            report = {"summary": "质检记录读取失败", "issues": []}
        try:
            attempt_artifacts = json.loads(str(attempt_row.get("artifacts_json") or "[]"))
        except json.JSONDecodeError:
            attempt_artifacts = []
        if not isinstance(attempt_artifacts, list):
            attempt_artifacts = []
        attempt_number = int(attempt_row.get("attempt") or 0)
        history.append({
            "attempt": attempt_number,
            "score": max(1, min(100, int(attempt_row.get("score") or 1))),
            "passed": bool(attempt_row.get("passed")),
            "selected": selected_attempt == attempt_number,
            "output": str(attempt_row.get("output") or ""),
            "report": report if isinstance(report, dict) else {},
            "createdAt": int(attempt_row.get("created_at") or 0),
            "artifacts": [
                {
                    "id": f"{task_id}:{attempt_number}:{index}",
                    "path": str(artifact.get("relative_path") or ""),
                    "filename": str(artifact.get("filename") or "file"),
                    "mimeType": str(artifact.get("mime_type") or "application/octet-stream"),
                    "sizeBytes": int(artifact.get("size_bytes") or 0),
                }
                for index, artifact in enumerate(attempt_artifacts)
                if isinstance(artifact, dict) and str(artifact.get("relative_path") or "")
            ],
        })
    try:
        coordination_plan = json.loads(str(item.get("coordination_plan_json") or "{}"))
    except json.JSONDecodeError:
        coordination_plan = {}
    try:
        quality_report = json.loads(str(item.get("quality_report_json") or "{}"))
    except json.JSONDecodeError:
        quality_report = {}
    return {
        "id": task_id,
        "conversationId": str(item.get("conversation_id") or ""),
        "source": str(item.get("source") or "user"),
        "scheduleId": item.get("schedule_id"),
        "prompt": str(item.get("prompt") or ""),
        "attachmentIds": _json_list(item.get("attachment_ids")),
        "status": str(item.get("status") or "queued"),
        "agentProfile": str(item.get("agent_profile") or "expert"),
        "coordination": {
            "status": str(item.get("coordination_status") or ""),
            "plan": coordination_plan if isinstance(coordination_plan, dict) else {},
        },
        "quality": {
            "status": str(item.get("quality_status") or ""),
            "score": int(item["quality_score"]) if item.get("quality_score") is not None else None,
            "attempt": int(item.get("quality_attempt") or 0),
            "selectedAttempt": selected_attempt,
            "report": quality_report if isinstance(quality_report, dict) else {},
            "history": history,
        },
        "output": str(item.get("output") or ""),
        "error": str(item.get("error") or ""),
        "createdAt": int(item.get("created_at") or 0),
        "updatedAt": int(item.get("updated_at") or 0),
        "startedAt": int(item.get("started_at") or 0) or None,
        "completedAt": int(item.get("completed_at") or 0) or None,
        "artifacts": [
            {
                "id": str(artifact.get("id") or ""),
                "path": str(artifact.get("relative_path") or ""),
                "filename": str(artifact.get("filename") or "file"),
                "mimeType": str(artifact.get("mime_type") or "application/octet-stream"),
                "sizeBytes": int(artifact.get("size_bytes") or 0),
            }
            for artifact in artifacts
        ],
    }


def public_workflow_category(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(item.get("id") or ""),
        "name": str(item.get("name") or ""),
        "description": str(item.get("description") or ""),
        "createdAt": int(item.get("created_at") or 0),
        "updatedAt": int(item.get("updated_at") or 0),
    }


def public_workflow(item: dict[str, Any]) -> dict[str, Any]:
    try:
        validation = json.loads(str(item.get("validation_report_json") or "{}"))
    except json.JSONDecodeError:
        validation = {}
    return {
        "id": str(item.get("id") or ""),
        "categoryId": item.get("category_id"),
        "categoryName": item.get("category_name"),
        "sourceConversationId": item.get("source_conversation_id"),
        "name": str(item.get("name") or ""),
        "description": str(item.get("description") or ""),
        "instructions": str(item.get("instructions") or ""),
        "triggers": json_list(item.get("triggers_json")),
        "status": str(item.get("status") or "validated"),
        "validation": validation,
        "createdAt": int(item.get("created_at") or 0),
        "updatedAt": int(item.get("updated_at") or 0),
    }


def public_skill(item: dict[str, Any]) -> dict[str, Any]:
    try:
        validation = json.loads(str(item.get("validation_report_json") or "{}"))
    except json.JSONDecodeError:
        validation = {}
    return {
        "id": str(item.get("id") or ""),
        "name": str(item.get("name") or ""),
        "description": str(item.get("description") or ""),
        "source": str(item.get("source") or "local"),
        "sourceRef": str(item.get("source_ref") or ""),
        "triggers": json_list(item.get("triggers_json")),
        "status": str(item.get("status") or "installed"),
        "validation": validation,
        "createdAt": int(item.get("created_at") or 0),
        "updatedAt": int(item.get("updated_at") or 0),
    }


def public_attachment(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(item.get("id") or ""),
        "conversationId": item.get("conversation_id"),
        "filename": str(item.get("filename") or "file"),
        "mimeType": str(item.get("mime_type") or "application/octet-stream"),
        "sizeBytes": int(item.get("size_bytes") or 0),
        "createdAt": int(item.get("created_at") or 0),
    }


def public_notification_preferences(item: dict[str, Any]) -> dict[str, bool]:
    return {
        "chatCompleted": bool(item.get("chat_completed", 1)),
        "agentCompleted": bool(item.get("agent_completed", 1)),
        "scheduleCompleted": bool(item.get("schedule_completed", 1)),
        "taskFailed": bool(item.get("task_failed", 1)),
        "approvalRequired": bool(item.get("approval_required", 1)),
        "system": bool(item.get("system", 1)),
    }


def public_notification(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(item.get("id") or 0),
        "category": str(item.get("category") or "system"),
        "title": str(item.get("title") or "通知"),
        "body": str(item.get("body") or ""),
        "entityType": str(item.get("entity_type") or ""),
        "entityId": str(item.get("entity_id") or ""),
        "readAt": int(item.get("read_at") or 0) or None,
        "createdAt": int(item.get("created_at") or 0),
    }


def public_schedule(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(item.get("id") or ""),
        "conversationId": str(item.get("conversation_id") or ""),
        "title": str(item.get("title") or ""),
        "prompt": str(item.get("prompt") or ""),
        "cron": str(item.get("cron_expr") or ""),
        "timezone": str(item.get("timezone") or "Asia/Shanghai"),
        "status": str(item.get("status") or "active"),
        "nextRunAt": int(item.get("next_run_at") or 0) or None,
        "lastRunAt": int(item.get("last_run_at") or 0) or None,
        "createdAt": int(item.get("created_at") or 0),
    }


def ok(data: Any = None) -> dict[str, Any]:
    return {"ok": True, "data": data if data is not None else {}}


def public_activation_code(item: dict[str, Any], code: str = "") -> dict[str, Any]:
    code_id = str(item.get("id") or "")
    registration_token = activation_registration_token(settings.activation_secret, code_id) if code_id else ""
    return {
        "id": code_id,
        "code": code,
        "codePreview": str(item.get("code_preview") or ""),
        "note": str(item.get("note") or ""),
        "status": str(item.get("status") or "active"),
        "maxUses": int(item.get("max_uses") or 1),
        "useCount": int(item.get("use_count") or 0),
        "expiresAt": int(item.get("expires_at") or 0) or None,
        "createdAt": int(item.get("created_at") or 0),
        "updatedAt": int(item.get("updated_at") or 0),
        "registrationPath": f"/?activation={registration_token}" if registration_token else "",
    }


def require_registration_activation(code: str, token: str) -> dict[str, Any] | None:
    if not code.strip() and not token.strip():
        return None
    try:
        if code.strip():
            digest = activation_code_digest(settings.activation_secret, code)
            activation = database.one("SELECT * FROM activation_codes WHERE code_hash = ?", (digest,))
        else:
            code_id = verify_activation_registration_token(settings.activation_secret, token)
            activation = database.one("SELECT * FROM activation_codes WHERE id = ?", (code_id,))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    current = now_ms()
    if (
        not activation
        or activation.get("status") != "active"
        or int(activation.get("use_count") or 0) >= int(activation.get("max_uses") or 1)
        or (activation.get("expires_at") and int(activation["expires_at"]) <= current)
    ):
        raise HTTPException(400, "激活码不可用或已过期")
    return activation


def client_rate_key(request: Request) -> str:
    forwarded = str(request.headers.get("x-real-ip") or "").strip()
    return forwarded or (request.client.host if request.client else "unknown")


def _completion_content(payload: dict[str, Any]) -> str:
    choices = payload.get("choices") if isinstance(payload.get("choices"), list) else []
    message = choices[0].get("message") if choices and isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(
            str(item.get("text") or "") for item in content if isinstance(item, dict) and item.get("type") == "text"
        ).strip()
    return ""


def choose_automatic_model(model_ids: list[str]) -> str:
    incompatible = ("embedding", "rerank", "image", "audio", "whisper", "tts")
    candidates = [
        model_id.strip()
        for model_id in model_ids
        if model_id.strip()
        and not any(keyword in model_id.lower() for keyword in incompatible)
        and not ("3.7" in model_id.lower() and model_id.lower().endswith("flash-high"))
    ]
    standard = [model_id for model_id in candidates if not model_id.lower().endswith("-agent")]
    return (standard or candidates or [""])[0]


async def resolved_llm_model(role: ModelRole = "chat") -> str:
    try:
        endpoint = model_store.endpoint(role)
        return await model_gateway.resolve_model(endpoint)
    except (httpx.HTTPError, LLMUpstreamExhausted, ValueError) as exc:
        raise HTTPException(502, f"模型发现失败: {str(exc)[:240]}") from exc


async def fixed_chat_completion(
    messages: list[dict[str, Any]],
    max_tokens: int = 2048,
    *,
    role: ModelRole = "chat",
) -> tuple[str, dict[str, Any]]:
    try:
        content, usage, _model = await model_gateway.complete(
            role,
            [{"role": item["role"], "content": item["content"]} for item in messages],
            max_tokens=max_tokens,
        )
        return content, usage
    except LLMUpstreamExhausted as exc:
        raise HTTPException(502, str(exc), headers={RETRY_EXHAUSTED_HEADER: "1"}) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, f"模型连接失败: {str(exc)[:240]}") from exc


async def fixed_chat_completion_stream(
    messages: list[dict[str, Any]],
    max_tokens: int = 2048,
    *,
    role: ModelRole = "chat",
):
    try:
        async for item in model_gateway.stream(
            role,
            [{"role": message["role"], "content": message["content"]} for message in messages],
            max_tokens=max_tokens,
        ):
            yield item
    except LLMUpstreamExhausted as exc:
        raise HTTPException(502, str(exc), headers={RETRY_EXHAUSTED_HEADER: "1"}) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, f"模型连接失败: {str(exc)[:240]}") from exc


def sse_frame(event: str, payload: dict[str, Any], *, event_id: int | None = None) -> str:
    prefix = f"id: {event_id}\n" if event_id is not None else ""
    return f"{prefix}event: {event}\ndata: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"


def fallback_agent_execution_route(instruction: str) -> str:
    normalized = re.sub(r"\s+", "", instruction).lower()
    device_context = re.search(
        r"(我的|这台|远程|本机|电脑|主机|windows|桌面|模拟器|adb|手机|设备)", normalized,
    )
    device_action = re.search(
        r"(打开|启动|运行|关闭|退出|点击|输入|操作|控制|拖动|安装|卸载|截屏|截图|按键)", normalized,
    )
    launch_application = re.search(
        r"(打开|启动|运行).{0,16}(浏览器|软件|应用|程序|微信|qq|计算器|记事本|文件管理器|资源管理器)",
        normalized,
    )
    return "remote" if launch_application or (device_context and device_action) else "local"


def explicit_remote_capture_route(instruction: str) -> bool:
    normalized = re.sub(r"\s+", "", instruction).lower()
    capture = re.search(r"(截屏|截图|截个图|截一张图)", normalized)
    target = re.search(
        r"(我的|我电脑|这台|远程|本机|电脑|主机|windows|桌面|模拟器|adb|手机|设备|"
        r"当前.{0,16}(浏览器|edge|chrome|firefox|窗口)|"
        r"(浏览器|edge|chrome|firefox).{0,16}(界面|窗口|画面|屏幕))",
        normalized,
    )
    local_work = re.search(r"(搜索|检索|查找资料|新闻|热点|访问网址|写代码|处理文档)", normalized)
    return bool(capture and target and not local_work)


async def classify_agent_execution(instruction: str, target_kind: str) -> str:
    fallback = fallback_agent_execution_route(instruction)
    if explicit_remote_capture_route(instruction):
        return "remote"
    prompt = (
        "你是 Agent 执行位置分类器，只能输出一个 JSON 对象："
        '{"route":"local"} 或 {"route":"remote"}。\n'
        "local 表示使用服务器内的 Hermes、内置浏览器、终端和工作区；"
        "remote 表示控制用户已经绑定的 Windows 电脑或 ADB 设备。\n"
        "只有任务需要操纵真实设备界面、启动/关闭设备上的应用、点击、输入或按键时才选 remote。"
        "网页搜索、新闻热点、资料调研、访问网站、写代码、处理文档都选 local，即使用户提到浏览器。"
        "但单独要求‘打开浏览器’或‘启动某个软件’属于启动设备应用，选 remote。"
        "明确要求操作我的电脑、主机、Windows、模拟器、ADB 或手机时选 remote。"
        "无法确定时选 local。不要执行指令，不要解释。\n"
        f"已绑定目标类型：{target_kind}\n"
        f"用户指令：{json.dumps(instruction, ensure_ascii=False)}"
    )
    try:
        content, _usage = await fixed_chat_completion(
            [{"role": "user", "content": prompt}],
            max_tokens=96,
            role="coordinator",
        )
        match = re.search(r"\{[^{}]*\}", content, flags=re.DOTALL)
        payload = json.loads(match.group(0)) if match else {}
        route = str(payload.get("route") or "").strip().lower()
        return route if route in {"local", "remote"} else fallback
    except (HTTPException, json.JSONDecodeError, TypeError, ValueError):
        return fallback


async def compact_chat_context(
    messages: list[dict[str, Any]], previous_summary: str = ""
) -> tuple[list[dict[str, str]], str, bool, int | None]:
    plan = plan_compression(messages, settings.llm_context_length, previous_summary)
    summary = previous_summary
    summarized_through: int | None = None
    if plan.compressed and plan.older:
        try:
            summary, _usage = await fixed_chat_completion(
                [{"role": "user", "content": summary_prompt(previous_summary, plan.older)}],
                max_tokens=2000,
            )
            if len(summary.strip()) < 40:
                raise ValueError("压缩摘要过短")
        except Exception:
            summary = fallback_summary(previous_summary, plan.older)
        last_id = plan.older[-1].get("id")
        summarized_through = int(last_id) if last_id is not None else None
    return context_messages(summary, plan.recent), summary, plan.compressed, summarized_through


def bearer_value(authorization: str | None) -> str:
    value = str(authorization or "")
    if not value.lower().startswith("bearer "):
        raise HTTPException(401, "请先登录")
    return value[7:].strip()


def current_identity(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    try:
        payload = decode_access_token(settings.app_secret, bearer_value(authorization))
    except (jwt.InvalidTokenError, HTTPException):
        raise HTTPException(401, "登录已失效，请重新登录")
    if payload["role"] == "admin":
        return {
            "id": "admin",
            "email": settings.smtp_username or "admin@local",
            "display_name": "管理员",
            "role": "admin",
            "status": "active",
        }
    user = database.get_user_by_id(str(payload["sub"]))
    if not user or user.get("status") != "active":
        raise HTTPException(403, "账户不存在或已停用")
    session_id = str(payload.get("sid") or "")
    if not session_id:
        return user
    session = database.get_auth_session(session_id, str(user["id"]))
    if not session:
        raise HTTPException(401, "登录已失效，请重新登录")
    if now_ms() - int(session.get("last_seen_at") or 0) >= 60_000:
        database.touch_auth_session(session_id, str(session["device_id"]))
    identity = dict(user)
    identity["_session_id"] = session_id
    identity["_device_id"] = str(session["device_id"])
    identity["_client_platform"] = str(session.get("device_platform") or "web")
    return identity


def current_user(identity: dict[str, Any] = Depends(current_identity)) -> dict[str, Any]:
    if identity.get("role") != "user":
        raise HTTPException(403, "该功能仅供普通用户使用")
    return identity


def current_admin(identity: dict[str, Any] = Depends(current_identity)) -> dict[str, Any]:
    if identity.get("role") != "admin":
        raise HTTPException(403, "需要管理员权限")
    return identity


def require_vip(user: dict[str, Any]) -> dict[str, Any]:
    if str(user.get("access_tier") or "basic") != "vip":
        raise HTTPException(403, "该功能需要 VIP 激活后使用")
    return user


def current_vip_user(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    return require_vip(user)


def require_conversation(conversation_id: str, user_id: str) -> dict[str, Any]:
    conversation = database.get_conversation(conversation_id, user_id)
    if not conversation:
        raise HTTPException(404, "会话不存在")
    return conversation


def require_agent_conversation(conversation_id: str, user_id: str) -> dict[str, Any]:
    user = database.get_user_by_id(user_id)
    if not user:
        raise HTTPException(404, "账户不存在")
    require_vip(user)
    conversation = require_conversation(conversation_id, user_id)
    if str(conversation.get("mode") or "agent") != "agent":
        raise HTTPException(409, "请先切换到 Agent 模式")
    return conversation


def require_task(task_id: str, user_id: str) -> dict[str, Any]:
    task = database.get_task(task_id, user_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    return task


@app.middleware("http")
async def security_headers(request: Request, call_next):
    original_path = request.scope.get("path", "")
    versioned = original_path.startswith("/api/v1/")
    is_preview = bool(preview_path_pattern.match(original_path))
    if versioned:
        request.scope["path"] = "/api/" + original_path[len("/api/v1/") :]
        request.scope["raw_path"] = request.scope["path"].encode("utf-8")
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    if not is_preview:
        response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    if versioned:
        response.headers["X-API-Version"] = "1"
    return response


@app.exception_handler(RuntimeUnavailable)
async def runtime_error_handler(_request: Request, exc: RuntimeUnavailable):
    return JSONResponse(status_code=503, content={"ok": False, "error": {"message": str(exc)}})


@app.exception_handler(HermesError)
async def hermes_error_handler(_request: Request, exc: HermesError):
    return JSONResponse(status_code=502, content={"ok": False, "error": {"message": str(exc)}})


@app.get("/api/health")
def health():
    return ok({"service": "mumu-hermes-workspace", "time": now_ms()})


@app.get("/api/runtime")
def runtime():
    return ok(
        {
            "appName": settings.app_name,
            "registrationEnabled": settings.registration_enabled,
            "model": model_store.endpoint("chat").model,
            "workerLimit": runtime_manager.current_worker_limit(),
            "uploadMaxBytes": settings.upload_max_bytes,
            "apiVersion": "v1",
            "appDownloadUrl": f"/downloads/AIchatMUMU-arm64.apk?v={ANDROID_APP_VERSION_CODE}",
            "windowsAgentVersion": WINDOWS_AGENT_VERSION,
            "windowsAgentDownloadUrl": "/downloads/MiaoxiangComputerAgent-x64.exe?v=17",
        }
    )


@app.get("/api/app/android-release")
def android_release():
    if not ANDROID_APK_PATH.is_file():
        return ok({"available": False})
    with ANDROID_APK_PATH.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return ok(
        {
            "available": True,
            "versionCode": ANDROID_APP_VERSION_CODE,
            "versionName": ANDROID_APP_VERSION_NAME,
            "sha256": digest,
            "sizeBytes": ANDROID_APK_PATH.stat().st_size,
            "downloadUrl": f"{settings.public_app_origin}/downloads/AIchatMUMU-arm64.apk?v={ANDROID_APP_VERSION_CODE}",
        }
    )


@app.get("/api/app/windows-release")
def windows_release():
    if not WINDOWS_AGENT_PATH.is_file():
        return ok({"available": False})
    with WINDOWS_AGENT_PATH.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return ok(
        {
            "available": True,
            "version": WINDOWS_AGENT_VERSION,
            "sha256": digest,
            "sizeBytes": WINDOWS_AGENT_PATH.stat().st_size,
            "downloadUrl": f"{settings.public_app_origin}/downloads/MiaoxiangComputerAgent-x64.exe?v=17",
        }
    )


@app.post("/api/chat/completions")
async def guest_chat(payload: GuestChatRequest, request: Request):
    key = client_rate_key(request)
    if not guest_chat_global_limiter.consume("global") or not guest_chat_client_limiter.consume(key):
        raise HTTPException(429, "访客对话请求过多，请稍后再试")
    messages = [message.model_dump() for message in payload.messages]
    if sum(len(str(message.get("content") or "")) for message in messages) > 500_000:
        raise HTTPException(413, "对话上下文过大")
    if messages[-1]["role"] != "user":
        raise HTTPException(400, "最后一条消息必须来自用户")
    if not str(messages[-1]["content"]).strip():
        raise HTTPException(400, "消息不能为空")
    prepared, _summary, compressed, _through = await compact_chat_context(messages)
    content, usage = await fixed_chat_completion(prepared)
    return ok(
        {
            "message": {"role": "assistant", "content": content, "createdAt": now_ms()},
            "model": model_store.endpoint("chat").model,
            "contextCompressed": compressed,
            "usage": usage,
        }
    )


@app.post("/api/chat/completions/stream")
async def guest_chat_stream(payload: GuestChatRequest, request: Request):
    key = client_rate_key(request)
    if not guest_chat_global_limiter.consume("global") or not guest_chat_client_limiter.consume(key):
        raise HTTPException(429, "访客对话请求过多，请稍后再试")
    messages = [message.model_dump() for message in payload.messages]
    if sum(len(str(message.get("content") or "")) for message in messages) > 500_000:
        raise HTTPException(413, "对话上下文过大")
    if messages[-1]["role"] != "user":
        raise HTTPException(400, "最后一条消息必须来自用户")
    if not str(messages[-1]["content"]).strip():
        raise HTTPException(400, "消息不能为空")
    prepared, _summary, compressed, _through = await compact_chat_context(messages)

    async def relay():
        parts: list[str] = []
        try:
            async for item in fixed_chat_completion_stream(prepared):
                if item.get("type") == "delta":
                    content = str(item.get("content") or "")
                    if content:
                        parts.append(content)
                        yield sse_frame("delta", {"content": content})
                elif item.get("type") == "done":
                    answer = "".join(parts).strip()
                    if not answer:
                        raise HTTPException(502, "模型流没有返回文本")
                    yield sse_frame("done", {
                        "message": {"role": "assistant", "content": answer, "createdAt": now_ms()},
                        "model": str(item.get("model") or model_store.endpoint("chat").model),
                        "contextCompressed": compressed,
                        "usage": item.get("usage") if isinstance(item.get("usage"), dict) else {},
                    })
                    return
        except HTTPException as exc:
            yield sse_frame("error", {"message": str(exc.detail), "status": exc.status_code})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("guest_chat_stream_failed")
            yield sse_frame("error", {"message": f"流式对话失败: {str(exc)[:240]}", "status": 502})

    return StreamingResponse(
        relay(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@app.post("/api/auth/admin-login", include_in_schema=False)
def admin_login(payload: AdminLoginRequest):
    if not admin_login_limiter.consume("admin"):
        raise HTTPException(429, "登录尝试过多，请稍后再试")
    password_matches = hmac.compare_digest(payload.password, settings.admin_password)
    if not password_matches:
        raise HTTPException(401, "管理员密码不正确")
    token = issue_access_token(settings.app_secret, "admin", "admin", settings.smtp_username or "admin@local")
    user = {
        "id": "admin",
        "email": settings.smtp_username or "admin@local",
        "display_name": "管理员",
        "role": "admin",
        "access_tier": "vip",
        "status": "active",
    }
    return ok({"token": token, "user": public_user(user)})


def device_hash(device_id: str) -> str:
    value = str(device_id or "").strip()
    return opaque_digest(settings.app_secret, "device", value) if value else ""


def trust_hash(trust_token: str) -> str:
    value = str(trust_token or "").strip()
    return opaque_digest(settings.app_secret, "trust", value) if value else ""


def create_user_login(
    user: dict[str, Any],
    payload: RequestCodeRequest,
    *,
    trust_device: bool = False,
) -> dict[str, Any]:
    raw_device_id = str(payload.device_id or "").strip() or secrets.token_urlsafe(24)
    raw_trust_token = secrets.token_urlsafe(48) if trust_device else ""
    default_device_names = {
        "android": "Android 设备",
        "wechat": "微信小程序",
        "web": "网页浏览器",
        "windows": "Windows 客户端",
    }
    device = database.upsert_device(
        user_id=str(user["id"]),
        device_key_hash=device_hash(raw_device_id),
        name=str(payload.device_name or "").strip() or default_device_names[payload.client_platform],
        platform=payload.client_platform,
        trust_token_hash=trust_hash(raw_trust_token),
    )
    lifetime_hours = 24 * 30
    session = database.create_auth_session(
        str(user["id"]),
        str(device["id"]),
        now_ms() + lifetime_hours * 60 * 60 * 1000,
    )
    database.execute("UPDATE users SET last_login_at = ?, updated_at = ? WHERE id = ?", (now_ms(), now_ms(), user["id"]))
    refreshed = database.get_user_by_id(str(user["id"])) or user
    token = issue_access_token(
        settings.app_secret,
        str(refreshed["id"]),
        "user",
        str(refreshed["email"]),
        lifetime_hours=lifetime_hours,
        session_id=str(session["id"]),
    )
    return {
        "token": token,
        "user": public_user(refreshed),
        "deviceCredential": raw_trust_token,
    }


def send_verification_email(email: str, code: str, purpose: str) -> None:
    if not settings.smtp_username or not settings.smtp_password or not settings.smtp_from:
        raise RuntimeError("SMTP 尚未配置")
    action = {"register": "注册", "login": "登录", "reset": "重置密码", "bind": "绑定邮箱"}.get(purpose, "账户验证")
    message = EmailMessage()
    message["Subject"] = f"{settings.app_name} {action}验证码"
    message["From"] = settings.smtp_from
    message["To"] = email
    message.set_content(f"你的{action}验证码是：{code}\n\n验证码 10 分钟内有效。若非本人操作，请忽略此邮件。")
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, context=context, timeout=20) as smtp:
        smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(message)


@app.post("/api/auth/request-code")
async def request_code(payload: RequestCodeRequest):
    user = None
    try:
        if payload.purpose == "register":
            email = normalize_email(payload.email or payload.identifier)
            password = validate_password(payload.password)
            username = normalize_username(payload.username or email.split("@", 1)[0])
        else:
            identifier = (payload.identifier or payload.email).strip().lower()
            user = database.get_user_by_identifier(identifier)
            if not user:
                raise HTTPException(401, "用户名、邮箱或密码不正确" if payload.purpose == "login" else "账户不存在")
            email = str(user["email"])
            password = validate_password(payload.password) if payload.purpose == "login" else ""
            username = str(user.get("username") or "")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if payload.purpose == "register" and not settings.registration_enabled:
        raise HTTPException(403, "当前未开放注册")
    if payload.purpose == "register":
        require_registration_activation(payload.activation_code, payload.activation_token)
    if payload.purpose == "register" and user:
        raise HTTPException(409, "该邮箱已注册")
    if payload.purpose == "register" and database.get_user_by_email(email):
        raise HTTPException(409, "该邮箱已注册")
    if payload.purpose == "register" and database.get_user_by_username(username):
        raise HTTPException(409, "该用户名已被使用")
    if payload.purpose == "login":
        if not user or not verify_password(password, str(user.get("password_hash") or "")):
            raise HTTPException(401, "用户名、邮箱或密码不正确")
        if user.get("status") != "active":
            raise HTTPException(403, "账户已停用")
        trusted = database.trusted_device(
            str(user["id"]),
            device_hash(payload.device_id),
            trust_hash(payload.trust_token),
        )
        if trusted:
            if not trusted_login_limiter.consume(str(user["id"])):
                raise HTTPException(429, "登录尝试过多，请稍后再试")
            result = create_user_login(user, payload)
            return ok({"sent": False, "verificationRequired": False, **result})
    if payload.purpose == "reset" and user and user.get("status") != "active":
        raise HTTPException(403, "账户已停用")
    if not verification_email_limiter.consume("global") or not verification_address_limiter.consume(
        f"{payload.purpose}:{email}"
    ):
        raise HTTPException(429, "验证码请求过多，请稍后再试")
    previous = database.one(
        "SELECT * FROM verification_codes WHERE email = ? AND purpose = ?",
        (email, payload.purpose),
    )
    current = int(time.time())
    if previous and current - int(previous.get("sent_at") or 0) < 60:
        raise HTTPException(429, "验证码发送过于频繁，请稍后再试")
    code = f"{secrets.randbelow(1_000_000):06d}"
    nonce = secrets.token_hex(12)
    try:
        await asyncio.to_thread(send_verification_email, email, code, payload.purpose)
    except Exception:
        raise HTTPException(503, "验证码邮件发送失败，请检查 SMTP 配置")
    with database.connection() as connection:
        connection.execute(
            """INSERT INTO verification_codes (email, purpose, digest, nonce, sent_at, expires_at, attempts)
            VALUES (?, ?, ?, ?, ?, ?, 0)
            ON CONFLICT(email, purpose) DO UPDATE SET digest=excluded.digest, nonce=excluded.nonce,
            sent_at=excluded.sent_at, expires_at=excluded.expires_at, attempts=0""",
            (email, payload.purpose, code_digest(settings.app_secret, email, payload.purpose, code, nonce), nonce, current, current + 600),
        )
    return ok({"sent": True, "verificationRequired": True, "expiresIn": 600})


@app.post("/api/auth/verify")
def verify_code(payload: VerifyCodeRequest):
    user = None
    try:
        if payload.purpose == "register":
            email = normalize_email(payload.email or payload.identifier)
            username = normalize_username(payload.username or email.split("@", 1)[0])
            password = validate_password(payload.password)
        else:
            identifier = (payload.identifier or payload.email).strip().lower()
            user = database.get_user_by_identifier(identifier)
            if not user:
                raise HTTPException(401, "账户不存在")
            email = str(user["email"])
            username = str(user.get("username") or "")
            password = validate_password(payload.password)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    record = database.one(
        "SELECT * FROM verification_codes WHERE email = ? AND purpose = ?",
        (email, payload.purpose),
    )
    current = int(time.time())
    if not record or int(record.get("expires_at") or 0) < current:
        raise HTTPException(400, "验证码已失效")
    if int(record.get("attempts") or 0) >= 5:
        database.execute("DELETE FROM verification_codes WHERE email = ? AND purpose = ?", (email, payload.purpose))
        raise HTTPException(429, "验证码错误次数过多，请重新获取")
    actual = code_digest(settings.app_secret, email, payload.purpose, payload.code, str(record["nonce"]))
    if not hmac.compare_digest(actual, str(record["digest"])):
        database.execute(
            "UPDATE verification_codes SET attempts = attempts + 1 WHERE email = ? AND purpose = ?",
            (email, payload.purpose),
        )
        raise HTTPException(400, "验证码不正确")
    activation = None
    if payload.purpose == "register":
        if user:
            raise HTTPException(409, "该邮箱已注册")
        if database.get_user_by_email(email):
            raise HTTPException(409, "该邮箱已注册")
        if database.get_user_by_username(username):
            raise HTTPException(409, "该用户名已被使用")
        activation = require_registration_activation(payload.activation_code, payload.activation_token)
        display_name = payload.display_name.strip() or email.split("@", 1)[0]
        try:
            user = database.create_user(
                username=username,
                email=email,
                display_name=display_name,
                password_hash=hash_password(password),
                access_tier="vip" if activation else "basic",
                activation_code_id=str(activation["id"]) if activation else None,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    elif payload.purpose == "login" and (not user or not verify_password(password, str(user.get("password_hash") or ""))):
        raise HTTPException(401, "用户名、邮箱或密码不正确")
    elif payload.purpose == "reset" and user:
        database.revoke_all_sessions(str(user["id"]))
        database.untrust_all_devices(str(user["id"]))
        database.execute(
            "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
            (hash_password(password), now_ms(), user["id"]),
        )
        user = database.get_user_by_id(str(user["id"])) or user
    if user.get("status") != "active":
        raise HTTPException(403, "账户已停用")
    database.execute("DELETE FROM verification_codes WHERE email = ? AND purpose = ?", (email, payload.purpose))
    return ok(create_user_login(user, payload, trust_device=payload.trust_device))


@app.get("/api/auth/me")
def me(identity: dict[str, Any] = Depends(current_identity)):
    return ok({"user": public_user(identity)})


@app.post("/api/auth/logout")
def logout(identity: dict[str, Any] = Depends(current_identity)):
    session_id = str(identity.get("_session_id") or "")
    if session_id:
        database.revoke_session(session_id, str(identity["id"]))
    return ok()


@app.post("/api/auth/webview-ticket", include_in_schema=False)
def create_webview_ticket(identity: dict[str, Any] = Depends(current_user)):
    session_id = str(identity.get("_session_id") or "")
    if identity.get("_client_platform") != "wechat" or not session_id:
        raise HTTPException(403, "仅微信小程序会话可以创建网页登录票据")
    ticket = secrets.token_urlsafe(32)
    digest = opaque_digest(settings.app_secret, "webview-login-ticket", ticket)
    expires_at = now_ms() + 120_000
    database.create_webview_login_ticket(digest, str(identity["id"]), session_id, expires_at)
    return ok({"ticket": ticket, "expiresIn": 120})


@app.post("/api/auth/webview-ticket/exchange", include_in_schema=False)
def exchange_webview_ticket(payload: WebviewTicketExchangeRequest):
    digest = opaque_digest(settings.app_secret, "webview-login-ticket", payload.ticket)
    ticket = database.consume_webview_login_ticket(digest)
    if not ticket:
        raise HTTPException(400, "网页登录票据无效或已过期")
    user = database.get_user_by_id(str(ticket["user_id"]))
    if not user or user.get("status") != "active" or user.get("role") != "user":
        raise HTTPException(403, "账户不可用")
    remaining_ms = max(1, int(ticket["session_expires_at"]) - now_ms())
    lifetime_hours = max(1, (remaining_ms + 3_599_999) // 3_600_000)
    token = issue_access_token(
        settings.app_secret,
        str(user["id"]),
        "user",
        str(user["email"]),
        lifetime_hours=lifetime_hours,
        session_id=str(ticket["session_id"]),
    )
    return ok({"token": token, "user": public_user(user)})


def wechat_login_payload(
    payload: WechatLoginRequest | WechatCloudLoginRequest | BindEmailVerifyRequest,
) -> RequestCodeRequest:
    return RequestCodeRequest(
        identifier="wechat",
        purpose="login",
        device_id=payload.device_id,
        device_name=payload.device_name,
        client_platform="wechat",
    )


async def exchange_wechat_code(code: str) -> tuple[str, str]:
    if not settings.wechat_app_id or not settings.wechat_app_secret:
        raise HTTPException(503, "微信登录尚未配置 AppSecret")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20)) as client:
            response = await client.get(
                "https://api.weixin.qq.com/sns/jscode2session",
                params={
                    "appid": settings.wechat_app_id,
                    "secret": settings.wechat_app_secret,
                    "js_code": code,
                    "grant_type": "authorization_code",
                },
            )
        response.raise_for_status()
        result = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, "微信登录服务连接失败") from exc
    open_id = str(result.get("openid") or "")
    if not open_id:
        logger.warning("wechat_code_exchange_failed errcode=%s", result.get("errcode"))
        raise HTTPException(401, "微信登录凭证无效或已使用")
    # session_key is deliberately neither stored nor returned to the client.
    return open_id, str(result.get("unionid") or "")


def login_wechat_identity(
    open_id: str,
    union_id: str,
    payload: WechatLoginRequest | WechatCloudLoginRequest,
) -> dict[str, Any]:
    account = database.get_wechat_account(settings.wechat_app_id, open_id)
    if account:
        user = database.get_user_by_id(str(account["user_id"]))
        if not user or user.get("status") != "active":
            raise HTTPException(403, "账户不存在或已停用")
        database.link_wechat_account(settings.wechat_app_id, open_id, union_id, str(user["id"]))
    else:
        digest = opaque_digest(settings.app_secret, "wechat-user", f"{settings.wechat_app_id}:{open_id}")[:16]
        username = f"wx_{digest}"
        email = f"wechat-{digest}@internal.invalid"
        user = database.create_user(
            email=email,
            username=username,
            display_name="微信用户",
            password_hash="!wechat-login-only",
            access_tier="basic",
        )
        database.execute("UPDATE users SET email_verified = 0 WHERE id = ?", (user["id"],))
        database.link_wechat_account(settings.wechat_app_id, open_id, union_id, str(user["id"]))
        user = database.get_user_by_id(str(user["id"])) or user
    return create_user_login(user, wechat_login_payload(payload), trust_device=True)


@app.post("/api/auth/wechat")
async def wechat_login(payload: WechatLoginRequest, request: Request):
    if not wechat_login_limiter.consume(client_rate_key(request)):
        raise HTTPException(429, "微信登录尝试过多，请稍后再试")
    open_id, union_id = await exchange_wechat_code(payload.code)
    return ok(login_wechat_identity(open_id, union_id, payload))


@app.post("/api/auth/wechat/cloud")
def wechat_cloud_login(payload: WechatCloudLoginRequest, request: Request):
    secret = settings.wechat_cloud_bridge_secret
    if len(secret) < 32:
        raise HTTPException(503, "微信云登录桥接尚未配置")
    if payload.app_id != settings.wechat_app_id:
        raise HTTPException(401, "微信云登录来源无效")
    if abs(int(time.time()) - payload.timestamp) > 120:
        raise HTTPException(401, "微信云登录请求已过期")
    expected = wechat_cloud_login_signature(
        secret,
        app_id=payload.app_id,
        open_id=payload.open_id,
        union_id=payload.union_id,
        timestamp=payload.timestamp,
        nonce=payload.nonce,
        device_id=payload.device_id,
        device_name=payload.device_name,
    )
    if not hmac.compare_digest(payload.signature.lower(), expected):
        raise HTTPException(401, "微信云登录签名无效")
    replay_key = opaque_digest(secret, "wechat-cloud-nonce", f"{payload.app_id}:{payload.nonce}")
    if not wechat_cloud_replay_guard.consume(replay_key, ttl_seconds=180):
        raise HTTPException(409, "微信云登录请求已使用")
    if not wechat_login_limiter.consume(client_rate_key(request)):
        raise HTTPException(429, "微信登录尝试过多，请稍后再试")
    return ok(login_wechat_identity(payload.open_id, payload.union_id, payload))


def current_wechat_account(user: dict[str, Any]) -> dict[str, Any]:
    if str(user.get("_client_platform") or "") != "wechat":
        raise HTTPException(403, "邮箱绑定仅在微信小程序内提供")
    account = database.one(
        "SELECT * FROM wechat_accounts WHERE app_id = ? AND user_id = ?",
        (settings.wechat_app_id, user["id"]),
    )
    if not account:
        raise HTTPException(403, "当前账户不是微信登录账户")
    return account


def verify_saved_email_code(email: str, purpose: str, code: str) -> None:
    record = database.one(
        "SELECT * FROM verification_codes WHERE email = ? AND purpose = ?",
        (email, purpose),
    )
    current = int(time.time())
    if not record or int(record.get("expires_at") or 0) < current:
        raise HTTPException(400, "验证码已失效")
    if int(record.get("attempts") or 0) >= 5:
        database.execute("DELETE FROM verification_codes WHERE email = ? AND purpose = ?", (email, purpose))
        raise HTTPException(429, "验证码错误次数过多，请重新获取")
    actual = code_digest(settings.app_secret, email, purpose, code, str(record["nonce"]))
    if not hmac.compare_digest(actual, str(record["digest"])):
        database.execute(
            "UPDATE verification_codes SET attempts = attempts + 1 WHERE email = ? AND purpose = ?",
            (email, purpose),
        )
        raise HTTPException(400, "验证码不正确")


@app.post("/api/auth/wechat/bind-email/request-code")
async def request_wechat_email_bind(
    payload: BindEmailRequest,
    user: dict[str, Any] = Depends(current_user),
):
    require_vip(user)
    current_wechat_account(user)
    try:
        email = normalize_email(payload.email)
        password = validate_password(payload.password)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    target = database.get_user_by_email(email)
    if target and str(target["id"]) == str(user["id"]):
        raise HTTPException(409, "当前账户已绑定该邮箱")
    if target and (target.get("status") != "active" or not verify_password(password, str(target.get("password_hash") or ""))):
        raise HTTPException(401, "邮箱或密码不正确")
    if not email_bind_limiter.consume(f"{user['id']}:{email}"):
        raise HTTPException(429, "验证码请求过多，请稍后再试")
    code = f"{secrets.randbelow(1_000_000):06d}"
    nonce = secrets.token_hex(12)
    try:
        await asyncio.to_thread(send_verification_email, email, code, "bind")
    except Exception as exc:
        raise HTTPException(503, "验证码邮件发送失败，请检查 SMTP 配置") from exc
    current = int(time.time())
    with database.connection() as connection:
        connection.execute(
            """INSERT INTO verification_codes (email, purpose, digest, nonce, sent_at, expires_at, attempts)
            VALUES (?, 'bind', ?, ?, ?, ?, 0)
            ON CONFLICT(email, purpose) DO UPDATE SET digest=excluded.digest, nonce=excluded.nonce,
            sent_at=excluded.sent_at, expires_at=excluded.expires_at, attempts=0""",
            (email, code_digest(settings.app_secret, email, "bind", code, nonce), nonce, current, current + 600),
        )
    return ok({"sent": True, "expiresIn": 600, "existingAccount": bool(target)})


@app.post("/api/auth/wechat/bind-email/verify")
def verify_wechat_email_bind(
    payload: BindEmailVerifyRequest,
    user: dict[str, Any] = Depends(current_user),
):
    require_vip(user)
    account = current_wechat_account(user)
    try:
        email = normalize_email(payload.email)
        password = validate_password(payload.password)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    verify_saved_email_code(email, "bind", payload.code)
    target = database.get_user_by_email(email)
    if target:
        if str(target["id"]) == str(user["id"]):
            raise HTTPException(409, "当前账户已绑定该邮箱")
        if target.get("status") != "active" or not verify_password(password, str(target.get("password_hash") or "")):
            raise HTTPException(401, "邮箱或密码不正确")
        linked = database.one(
            "SELECT user_id FROM wechat_accounts WHERE app_id = ? AND user_id = ?",
            (settings.wechat_app_id, target["id"]),
        )
        if linked:
            raise HTTPException(409, "该邮箱账户已经绑定其他微信账号")
        database.merge_wechat_user(str(user["id"]), str(target["id"]), settings.wechat_app_id, str(account["open_id"]))
        user = database.get_user_by_id(str(target["id"])) or target
    else:
        base = re.sub(r"[^a-z0-9_.-]", "_", email.split("@", 1)[0].lower()).strip("._-")[:24]
        username = base if len(base) >= 3 else f"user_{str(user['id'])[:8]}"
        suffix = 2
        candidate = username
        while database.get_user_by_username(candidate):
            candidate = f"{username[:27]}_{suffix}"
            suffix += 1
        database.execute(
            """UPDATE users SET email = ?, username = ?, password_hash = ?, email_verified = 1,
            updated_at = ? WHERE id = ?""",
            (email, candidate, hash_password(password), now_ms(), user["id"]),
        )
        user = database.get_user_by_id(str(user["id"])) or user
    database.execute("DELETE FROM verification_codes WHERE email = ? AND purpose = 'bind'", (email,))
    return ok(create_user_login(user, wechat_login_payload(payload), trust_device=payload.trust_device))


@app.post("/api/auth/activation/redeem")
def redeem_activation_code(payload: ActivationRedeemRequest, user: dict[str, Any] = Depends(current_user)):
    if str(user.get("access_tier") or "basic") == "vip":
        return ok({"user": public_user(user), "alreadyActive": True})
    try:
        digest = activation_code_digest(settings.activation_secret, payload.code)
        database.redeem_activation_code(digest, str(user["id"]))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    refreshed = database.get_user_by_id(str(user["id"])) or user
    return ok({"user": public_user(refreshed), "alreadyActive": False})


def require_app_session(user: dict[str, Any]) -> None:
    require_vip(user)
    if user.get("_client_platform") not in {"android", "wechat"} or not user.get("_session_id"):
        raise HTTPException(403, "登录设备管理仅在 APP 或微信小程序内提供")


@app.get("/api/devices")
def list_login_devices(user: dict[str, Any] = Depends(current_user)):
    require_app_session(user)
    rows = database.list_devices(str(user["id"]))
    return ok({"devices": [public_device(row, str(user.get("_device_id") or "")) for row in rows]})


@app.post("/api/devices/{device_id}/logout")
def logout_login_device(device_id: str, user: dict[str, Any] = Depends(current_user)):
    require_app_session(user)
    row = database.one("SELECT id FROM devices WHERE id = ? AND user_id = ?", (device_id, user["id"]))
    if not row:
        raise HTTPException(404, "设备不存在")
    database.revoke_device_sessions(device_id, str(user["id"]))
    return ok({"current": device_id == str(user.get("_device_id") or "")})


@app.delete("/api/devices/{device_id}")
def delete_login_device(device_id: str, user: dict[str, Any] = Depends(current_user)):
    require_app_session(user)
    current = device_id == str(user.get("_device_id") or "")
    if not database.delete_device(device_id, str(user["id"])):
        raise HTTPException(404, "设备不存在")
    return ok({"current": current})


def control_credential(authorization: str | None) -> str:
    value = str(authorization or "")
    if not value.lower().startswith("bearer "):
        raise HTTPException(401, "电脑客户端授权无效")
    credential = value[7:].strip()
    if not credential:
        raise HTTPException(401, "电脑客户端授权无效")
    return credential


def current_control_device(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    device = computer_store.authenticate_credential(control_credential(authorization))
    if not device:
        raise HTTPException(401, "电脑客户端授权已失效")
    user = database.get_user_by_id(str(device.get("user_id") or ""))
    if not user or user.get("status") != "active" or user.get("access_tier") != "vip":
        raise HTTPException(403, "该账户未开通电脑控制权限")
    return device


async def dispatch_next_control_task(device_id: str) -> bool:
    computer_store.expire_stale_tasks(device_id)
    task = computer_store.claim_next_task(device_id)
    if not task:
        return False
    sent = await computer_hub.send(
        device_id,
        {
            "type": "task.assign",
            "task": public_control_task(task),
            "leaseId": str(task.get("lease_id") or ""),
        },
    )
    if not sent:
        computer_store.requeue_unaccepted(device_id)
    return sent


@app.post("/api/control/devices/register")
def register_control_device(
    payload: ControlDeviceRegisterRequest,
    request: Request,
    user: dict[str, Any] = Depends(current_vip_user),
):
    if not control_device_register_limiter.consume(f"account:{user['id']}:{client_rate_key(request)}"):
        raise HTTPException(429, "设备登记尝试过多，请稍后再试")
    result = computer_store.register_account_device(
        user_id=str(user["id"]),
        installation_id=payload.installation_id,
        hostname=payload.hostname,
        name=payload.name,
        agent_version=payload.agent_version,
    )
    if not result:
        raise HTTPException(400, "本机安装标识无效")
    return ok(
        {
            "device": public_control_device(result["device"], online=False),
            "credential": result["credential"],
        }
    )


@app.get("/api/control/devices")
def list_control_devices(user: dict[str, Any] = Depends(current_vip_user)):
    rows = computer_store.list_devices(str(user["id"]))
    return ok(
        {
            "devices": [
                public_control_device(row, online=computer_hub.is_online(str(row["id"]))) for row in rows
            ]
        }
    )


@app.patch("/api/control/devices/{device_id}")
def update_control_device(
    payload: ControlDeviceUpdateRequest,
    device_id: str,
    user: dict[str, Any] = Depends(current_vip_user),
):
    row = computer_store.rename_device(device_id, str(user["id"]), payload.name)
    if not row:
        raise HTTPException(404, "可控设备不存在")
    return ok({"device": public_control_device(row, online=computer_hub.is_online(device_id))})


@app.delete("/api/control/devices/{device_id}")
async def delete_control_device(device_id: str, user: dict[str, Any] = Depends(current_vip_user)):
    if not computer_store.revoke_device(device_id, str(user["id"])):
        raise HTTPException(404, "可控设备不存在")
    await computer_hub.revoke(device_id)
    return ok({})


@app.get("/api/control/tasks")
def list_control_tasks(
    limit: int = Query(default=100, ge=1, le=200),
    conversation_id: str | None = Query(default=None, min_length=1, max_length=80),
    user: dict[str, Any] = Depends(current_vip_user),
):
    computer_store.expire_stale_tasks()
    if conversation_id is not None:
        require_conversation(conversation_id, str(user["id"]))
    rows = computer_store.list_tasks(str(user["id"]), limit=limit, conversation_id=conversation_id)
    return ok({"tasks": [public_control_task(row) for row in rows]})


@app.post("/api/control/tasks")
async def create_control_task(
    payload: ControlTaskCreateRequest,
    request: Request,
    user: dict[str, Any] = Depends(current_vip_user),
):
    if not control_task_limiter.consume(f"{user['id']}:{client_rate_key(request)}"):
        raise HTTPException(429, "电脑任务提交过于频繁")
    conversation = None
    if payload.conversation_id:
        conversation = require_agent_conversation(payload.conversation_id, str(user["id"]))
        if (
            str(conversation.get("control_device_id") or "") != payload.device_id
            or str(conversation.get("control_target_id") or "") != payload.target_id
        ):
            raise HTTPException(409, "当前对话未绑定该远程目标")
    try:
        task = computer_store.create_task(
            str(user["id"]), payload.device_id, payload.target_id, payload.instruction,
            conversation_id=payload.conversation_id,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not task:
        raise HTTPException(404, "可控设备不存在")
    if conversation:
        database.execute(
            "UPDATE conversations SET title = CASE WHEN title IN ('新对话','新任务') THEN ? ELSE title END, updated_at = ? WHERE id = ? AND user_id = ?",
            (payload.instruction.strip()[:36], now_ms(), payload.conversation_id, user["id"]),
        )
    await dispatch_next_control_task(payload.device_id)
    refreshed = computer_store.get_task(str(task["id"]), str(user["id"])) or task
    return ok({"task": public_control_task(refreshed)})


@app.get("/api/control/tasks/{task_id}")
def get_control_task(task_id: str, user: dict[str, Any] = Depends(current_vip_user)):
    task = computer_store.get_task(task_id, str(user["id"]))
    if not task:
        raise HTTPException(404, "电脑任务不存在")
    return ok({"task": public_control_task(task)})


@app.get("/api/control/tasks/{task_id}/events")
def get_control_task_events(
    task_id: str,
    after: int = Query(default=0, ge=0),
    user: dict[str, Any] = Depends(current_vip_user),
):
    if not computer_store.get_task(task_id, str(user["id"])):
        raise HTTPException(404, "电脑任务不存在")
    rows = computer_store.list_events(task_id, str(user["id"]), after=after)
    return ok({"events": [public_control_event(row) for row in rows]})


@app.post("/api/control/tasks/{task_id}/stop")
async def stop_control_task(task_id: str, user: dict[str, Any] = Depends(current_vip_user)):
    task = computer_store.request_stop(task_id, str(user["id"]))
    if not task:
        raise HTTPException(404, "电脑任务不存在")
    if task.get("status") == "stopping":
        await computer_hub.send(
            str(task["device_id"]),
            {"type": "task.cancel", "taskId": task_id, "leaseId": str(task.get("lease_id") or "")},
        )
    return ok({"task": public_control_task(task)})


@app.post("/api/control/tasks/{task_id}/approval")
async def approve_control_task(
    payload: ControlTaskApprovalRequest,
    task_id: str,
    user: dict[str, Any] = Depends(current_vip_user),
):
    task = computer_store.respond_approval(task_id, str(user["id"]), payload.decision)
    if not task:
        raise HTTPException(409, "该电脑任务当前不等待批准")
    await computer_hub.send(
        str(task["device_id"]),
        {
            "type": "approval.response",
            "taskId": task_id,
            "leaseId": str(task.get("lease_id") or ""),
            "decision": payload.decision,
            "actionHash": str(task.get("approval_hash") or ""),
        },
    )
    return ok({"task": public_control_task(task)})


@app.get("/api/control/frames/{frame_id}")
def get_control_frame(frame_id: str, user: dict[str, Any] = Depends(current_vip_user)):
    row = computer_store.get_frame(frame_id, str(user["id"]))
    if not row:
        raise HTTPException(404, "电脑任务截图不存在")
    path = computer_store.frame_path(row)
    if not path:
        raise HTTPException(404, "电脑任务截图文件不存在")
    return FileResponse(
        path,
        media_type=str(row.get("mime_type") or "application/octet-stream"),
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


@app.websocket("/api/v1/control/agent/ws")
@app.websocket("/api/control/agent/ws")
async def control_agent_websocket(websocket: WebSocket):
    try:
        credential = control_credential(websocket.headers.get("authorization"))
    except HTTPException:
        await websocket.close(code=4401)
        return
    device = computer_store.authenticate_credential(credential)
    if not device:
        await websocket.close(code=4401)
        return
    device_user = database.get_user_by_id(str(device.get("user_id") or ""))
    if not device_user or device_user.get("status") != "active" or device_user.get("access_tier") != "vip":
        await websocket.close(code=4403)
        return
    device_id = str(device["id"])
    await websocket.accept()
    await computer_hub.attach(device_id, websocket)
    errors = 0
    try:
        await websocket.send_json({"type": "hello.request", "deviceId": device_id, "heartbeatSeconds": 20})
        while True:
            raw = await websocket.receive_text()
            if len(raw.encode("utf-8")) > 4 * 1024 * 1024:
                await websocket.close(code=4409, reason="消息过大")
                return
            try:
                message = json.loads(raw)
                if not isinstance(message, dict):
                    raise ValueError("消息必须为对象")
                message_type = str(message.get("type") or "")
                if message_type == "hello":
                    computer_store.update_hello(
                        device_id,
                        hostname=str(message.get("hostname") or device.get("hostname") or ""),
                        agent_version=str(message.get("agentVersion") or ""),
                        capabilities=message.get("capabilities") if isinstance(message.get("capabilities"), list) else [],
                        targets=message.get("targets") if isinstance(message.get("targets"), list) else [],
                    )
                    await websocket.send_json({"type": "hello.ack", "deviceId": device_id})
                    await dispatch_next_control_task(device_id)
                elif message_type == "heartbeat":
                    computer_store.touch_device(device_id)
                    running = message.get("runningTaskIds") if isinstance(message.get("runningTaskIds"), list) else []
                    computer_store.touch_task_leases(device_id, [str(item) for item in running])
                    await websocket.send_json({"type": "heartbeat.ack", "time": now_ms()})
                elif message_type == "task.accepted":
                    task_id = str(message.get("taskId") or "")
                    lease_id = str(message.get("leaseId") or "")
                    task = computer_store.accept_task(device_id, task_id, lease_id)
                    if not task:
                        raise ValueError("任务租约无效")
                    event = computer_store.add_event(
                        device_id=device_id,
                        task_id=task_id,
                        lease_id=lease_id,
                        sequence=max(1, int(message.get("sequence") or 1)),
                        client_event_id=str(message.get("clientEventId") or f"accepted-{lease_id}"),
                        event_type="task.started",
                        payload={"message": "Windows 客户端已开始执行"},
                    )
                    await websocket.send_json(
                        {"type": "event.ack", "taskId": task_id, "eventId": event.get("id") if event else None}
                    )
                elif message_type == "task.event":
                    task_id = str(message.get("taskId") or "")
                    lease_id = str(message.get("leaseId") or "")
                    sequence = max(0, int(message.get("sequence") or 0))
                    event_type = str(message.get("eventType") or "event")[:60]
                    if event_type not in ALLOWED_EVENT_TYPES:
                        raise ValueError("不支持的任务事件类型")
                    frame_id: str | None = None
                    if message.get("frameBase64"):
                        frame = computer_store.store_frame(
                            device_id=device_id,
                            task_id=task_id,
                            lease_id=lease_id,
                            sequence=sequence,
                            encoded=str(message["frameBase64"]),
                        )
                        frame_id = str(frame["id"])
                    payload = message.get("payload") if isinstance(message.get("payload"), dict) else {}
                    event = computer_store.add_event(
                        device_id=device_id,
                        task_id=task_id,
                        lease_id=lease_id,
                        sequence=sequence,
                        client_event_id=str(message.get("clientEventId") or ""),
                        event_type=event_type,
                        payload=payload,
                        frame_id=frame_id,
                    )
                    if not event:
                        raise ValueError("任务事件已失效")
                    await websocket.send_json(
                        {"type": "event.ack", "taskId": task_id, "eventId": event["id"], "frameId": frame_id}
                    )
                    if event_type in {"task.completed", "task.failed", "task.cancelled", "task.stopped"}:
                        await dispatch_next_control_task(device_id)
                elif message_type == "task.ready":
                    await dispatch_next_control_task(device_id)
                else:
                    raise ValueError("未知消息类型")
                errors = 0
            except (TypeError, ValueError) as exc:
                errors += 1
                await websocket.send_json({"type": "protocol.error", "message": str(exc)[:240]})
                if errors >= 5:
                    await websocket.close(code=4400, reason="协议错误过多")
                    return
    except WebSocketDisconnect:
        pass
    finally:
        if await computer_hub.detach(device_id, websocket):
            computer_store.requeue_unaccepted(device_id)


def _background_format(content: bytes) -> tuple[str, str] | None:
    if content.startswith(b"\xff\xd8\xff"):
        return ".jpg", "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png", "image/png"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return ".webp", "image/webp"
    return None


@app.get("/api/profile/background")
def profile_background(user: dict[str, Any] = Depends(current_user)):
    filename = Path(str(user.get("background_filename") or "")).name
    if not filename:
        raise HTTPException(404, "未设置自定义背景")
    target = runtime_manager.user_paths(str(user["id"]))["container_profile"] / filename
    if not target.is_file():
        raise HTTPException(404, "背景文件不存在")
    media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return FileResponse(target, media_type=media_type, headers={"Cache-Control": "private, no-store"})


@app.put("/api/profile/background")
async def upload_profile_background(
    file: UploadFile = File(...),
    user: dict[str, Any] = Depends(current_user),
):
    limit = min(settings.upload_max_bytes, 12 * 1024 * 1024)
    content = await file.read(limit + 1)
    await file.close()
    if not content:
        raise HTTPException(400, "背景图片不能为空")
    if len(content) > limit:
        raise HTTPException(413, "背景图片不能超过 12 MB")
    detected = _background_format(content)
    if not detected:
        raise HTTPException(400, "仅支持 JPEG、PNG 或 WebP 图片")
    suffix, _media_type = detected
    profile_dir = runtime_manager.user_paths(str(user["id"]))["container_profile"]
    profile_dir.mkdir(parents=True, exist_ok=True)
    target = profile_dir / f"background{suffix}"
    temporary = profile_dir / ".background-upload"
    try:
        await asyncio.to_thread(temporary.write_bytes, content)
        await asyncio.to_thread(temporary.replace, target)
        for old in profile_dir.glob("background.*"):
            if old != target and old.is_file():
                await asyncio.to_thread(old.unlink)
        database.execute(
            "UPDATE users SET background_filename = ?, updated_at = ? WHERE id = ?",
            (target.name, now_ms(), user["id"]),
        )
    except Exception:
        if temporary.is_file():
            await asyncio.to_thread(temporary.unlink)
        raise HTTPException(500, "背景图片保存失败")
    return ok({"hasCustomBackground": True})


@app.delete("/api/profile/background")
async def delete_profile_background(user: dict[str, Any] = Depends(current_user)):
    profile_dir = runtime_manager.user_paths(str(user["id"]))["container_profile"]
    if profile_dir.exists():
        for target in profile_dir.glob("background.*"):
            if target.is_file():
                await asyncio.to_thread(target.unlink)
    database.execute(
        "UPDATE users SET background_filename = '', updated_at = ? WHERE id = ?",
        (now_ms(), user["id"]),
    )
    return ok({"hasCustomBackground": False})


@app.get("/api/notifications/preferences")
def notification_preferences(user: dict[str, Any] = Depends(current_user)):
    preferences = database.notification_preferences(str(user["id"]))
    return ok({"preferences": public_notification_preferences(preferences)})


@app.patch("/api/notifications/preferences")
def update_notification_preferences(
    payload: NotificationPreferencesUpdateRequest,
    user: dict[str, Any] = Depends(current_user),
):
    preferences = database.update_notification_preferences(
        str(user["id"]),
        payload.model_dump(exclude_none=True),
    )
    return ok({"preferences": public_notification_preferences(preferences)})


@app.get("/api/notifications")
def list_notifications(
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    user: dict[str, Any] = Depends(current_user),
):
    user_id = str(user["id"])
    items = database.list_notifications(user_id, after=after, limit=limit)
    return ok({
        "notifications": [public_notification(item) for item in items],
        "unreadCount": database.unread_notification_count(user_id),
    })


@app.post("/api/notifications/{notification_id}/read")
def read_notification(notification_id: int, user: dict[str, Any] = Depends(current_user)):
    if not database.mark_notification_read(str(user["id"]), notification_id):
        raise HTTPException(404, "通知不存在")
    return ok()


@app.post("/api/notifications/read-all")
def read_all_notifications(user: dict[str, Any] = Depends(current_user)):
    count = database.mark_all_notifications_read(str(user["id"]))
    return ok({"updated": count})


@app.get("/api/conversations")
def list_conversations(user: dict[str, Any] = Depends(current_user)):
    rows = database.all("SELECT * FROM conversations WHERE user_id = ? ORDER BY updated_at DESC LIMIT 100", (user["id"],))
    return ok({"conversations": [public_conversation(row) for row in rows]})


@app.get("/api/capabilities")
def list_capabilities(user: dict[str, Any] = Depends(current_vip_user)):
    user_id = str(user["id"])
    skills = database.list_skill_records(user_id)
    if not skills:
        skills = capability_manager.sync_skill_records(user_id)
    return ok({
        "categories": [public_workflow_category(item) for item in database.list_workflow_categories(user_id)],
        "workflows": [public_workflow(item) for item in database.list_workflows(user_id)],
        "skills": [public_skill(item) for item in skills],
    })


@app.post("/api/workflow-categories")
def create_workflow_category(
    payload: WorkflowCategoryCreateRequest,
    user: dict[str, Any] = Depends(current_vip_user),
):
    try:
        item = database.create_workflow_category(
            str(user["id"]), payload.name.strip(), payload.description.strip()
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "该工作流分类已存在") from exc
    return ok({"category": public_workflow_category(item)})


@app.delete("/api/workflow-categories/{category_id}")
def delete_workflow_category(category_id: str, user: dict[str, Any] = Depends(current_vip_user)):
    if not database.delete_workflow_category(category_id, str(user["id"])):
        raise HTTPException(404, "工作流分类不存在")
    return ok({"deleted": True})


@app.post("/api/workflows")
def create_workflow(payload: WorkflowCreateRequest, user: dict[str, Any] = Depends(current_vip_user)):
    try:
        item = capability_manager.create_workflow_file(
            str(user["id"]),
            name=payload.name,
            description=payload.description,
            instructions=payload.instructions,
            triggers=payload.triggers,
            category_id=payload.category_id,
            source_conversation_id=None,
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "同名工作流已存在") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return ok({"workflow": public_workflow(item)})


@app.post("/api/workflows/from-conversation")
async def create_workflow_from_conversation(
    payload: WorkflowFromConversationRequest,
    user: dict[str, Any] = Depends(current_vip_user),
):
    user_id = str(user["id"])
    conversation = require_conversation(payload.conversation_id, user_id)
    rows = database.all(
        "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT 100",
        (payload.conversation_id,),
    )
    rows.reverse()
    transcript = "\n\n".join(
        f"{str(item['role']).upper()}: {str(item['content'])}" for item in rows
    )[-100_000:]
    response, _usage = await fixed_chat_completion(
        [
            {
                "role": "system",
                "content": (
                    "Convert the conversation into one reusable executable workflow. Return only a JSON object with "
                    "name, description, triggers (array of concise phrases), and instructions. Instructions must be "
                    "self-contained, ordered, testable, preserve safety boundaries, and state concrete success checks. "
                    "Do not include secrets, one-time IDs, passwords, or conversation-specific private values."
                ),
            },
            {
                "role": "user",
                "content": f"Conversation title: {conversation['title']}\n\n{transcript}",
            },
        ],
        role="coordinator",
        max_tokens=4096,
    )
    try:
        compiled = _json_object_from_text(response)
        name = payload.name.strip() or str(compiled.get("name") or "").strip()
        description = str(compiled.get("description") or "").strip()
        instructions = str(compiled.get("instructions") or "").strip()
        triggers = [str(value).strip() for value in compiled.get("triggers") or [] if str(value).strip()]
        item = capability_manager.create_workflow_file(
            user_id,
            name=name,
            description=description,
            instructions=instructions,
            triggers=triggers,
            category_id=payload.category_id,
            source_conversation_id=payload.conversation_id,
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "同名工作流已存在") from exc
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise HTTPException(502, f"工作流生成结果未通过验证：{exc}") from exc
    return ok({"workflow": public_workflow(item)})


@app.patch("/api/workflows/{workflow_id}")
def update_workflow(
    payload: WorkflowUpdateRequest,
    workflow_id: str,
    user: dict[str, Any] = Depends(current_vip_user),
):
    values = payload.model_dump(exclude_unset=True)
    try:
        item = capability_manager.update_workflow_file(str(user["id"]), workflow_id, **values)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "同名工作流已存在") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return ok({"workflow": public_workflow(item)})


@app.delete("/api/workflows/{workflow_id}")
def delete_workflow(workflow_id: str, user: dict[str, Any] = Depends(current_vip_user)):
    try:
        capability_manager.delete_workflow(str(user["id"]), workflow_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return ok({"deleted": True})


@app.post("/api/skills/search")
async def search_skills(payload: SkillSearchRequest, user: dict[str, Any] = Depends(current_vip_user)):
    user_id = str(user["id"])
    try:
        await runtime_manager.ensure_worker(user_id, dispatcher.busy_user_ids())
        result = await capability_manager.search_online(user_id, payload.query.strip(), payload.source.strip())
    except (RuntimeUnavailable, ValueError) as exc:
        raise HTTPException(502, str(exc)) from exc
    return ok(result)


@app.post("/api/skills/install")
async def install_skill(payload: SkillInstallRequest, user: dict[str, Any] = Depends(current_vip_user)):
    user_id = str(user["id"])
    try:
        await runtime_manager.ensure_worker(user_id, dispatcher.busy_user_ids())
        result = await capability_manager.install_online(
            user_id, payload.source_ref, force=payload.force, probe=payload.probe
        )
    except (RuntimeUnavailable, ValueError) as exc:
        raise HTTPException(502, str(exc)) from exc
    return ok({"skill": public_skill(result["skill"]), "validation": result["validation"]})


@app.post("/api/skills/audit")
async def audit_skills(user: dict[str, Any] = Depends(current_vip_user)):
    user_id = str(user["id"])
    try:
        await runtime_manager.ensure_worker(user_id, dispatcher.busy_user_ids())
        result = await capability_manager.audit(user_id)
    except RuntimeUnavailable as exc:
        raise HTTPException(502, str(exc)) from exc
    return ok({
        "ok": result["ok"],
        "output": result["output"],
        "skills": [public_skill(item) for item in result["skills"]],
    })


@app.delete("/api/skills/{skill_id}")
def delete_skill(skill_id: str, user: dict[str, Any] = Depends(current_vip_user)):
    try:
        capability_manager.delete_skill(str(user["id"]), skill_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return ok({"deleted": True})


@app.post("/api/capabilities/share")
def share_capability(payload: CapabilityShareRequest, user: dict[str, Any] = Depends(current_vip_user)):
    try:
        code, share = capability_manager.create_share(str(user["id"]), payload.kind, payload.item_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return ok({"code": code, "shareId": share["id"], "sha256": share["sha256"]})


@app.post("/api/capabilities/import")
def import_capability(payload: CapabilityImportRequest, user: dict[str, Any] = Depends(current_vip_user)):
    try:
        result = capability_manager.import_share(str(user["id"]), payload.code)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        raise HTTPException(400, str(exc)) from exc
    item = public_workflow(result["item"]) if result["kind"] == "workflow" else public_skill(result["item"])
    return ok({"kind": result["kind"], "item": item})


@app.post("/api/conversations/import-guest")
def import_guest_conversations(
    payload: GuestArchiveImportRequest,
    user: dict[str, Any] = Depends(current_user),
):
    conversations = [
        {
            "client_id": item.client_id,
            "title": item.title.strip() or "访客对话",
            "created_at": item.created_at,
            "messages": [message.model_dump() for message in item.messages],
        }
        for item in payload.conversations
    ]
    total_characters = sum(
        len(str(message.get("content") or ""))
        for item in conversations
        for message in item["messages"]
    )
    if total_characters > 1_000_000:
        raise HTTPException(413, "访客对话数据过大")
    imported = database.import_guest_archive(str(user["id"]), payload.client_import_id, conversations)
    return ok({"imported": imported})


@app.post("/api/conversations")
def create_conversation(payload: ConversationCreateRequest, user: dict[str, Any] = Depends(current_user)):
    if payload.mode == "agent":
        require_vip(user)
    conversation = database.create_conversation(
        str(user["id"]),
        payload.title.strip() or "新对话",
        payload.mode,
        payload.agent_profile,
    )
    return ok({"conversation": public_conversation(conversation), "browserStatus": "idle"})


@app.patch("/api/conversations/{conversation_id}")
def update_conversation(
    payload: ConversationUpdateRequest,
    conversation_id: str,
    user: dict[str, Any] = Depends(current_user),
):
    conversation = require_conversation(conversation_id, str(user["id"]))
    values = payload.model_dump(exclude_none=True)
    if values.get("mode") == "agent":
        require_vip(user)
    if values.get("mode") == "chat":
        active = database.one(
            "SELECT id FROM tasks WHERE conversation_id = ? AND status IN ('starting','running','waiting_approval','stopping') LIMIT 1",
            (conversation_id,),
        )
        if active:
            raise HTTPException(409, "请先停止当前 Agent 任务再切换到 Chat 模式")
        active_schedule = database.one(
            "SELECT id FROM schedules WHERE conversation_id = ? AND status = 'active' LIMIT 1",
            (conversation_id,),
        )
        if active_schedule:
            raise HTTPException(409, "请先暂停该会话的定时任务再切换到 Chat 模式")
    updates: list[str] = []
    parameters: list[Any] = []
    if "title" in values:
        updates.append("title = ?")
        parameters.append(str(values["title"]).strip())
    if "mode" in values:
        updates.append("mode = ?")
        parameters.append(str(values["mode"]))
    if "agent_profile" in values:
        updates.append("agent_profile = ?")
        parameters.append(str(values["agent_profile"]))
    if updates:
        updates.append("updated_at = ?")
        parameters.extend([now_ms(), conversation_id, user["id"]])
        database.execute(
            f"UPDATE conversations SET {', '.join(updates)} WHERE id = ? AND user_id = ?",
            tuple(parameters),
        )
    return ok({"conversation": public_conversation(database.get_conversation(conversation_id, str(user["id"])) or conversation)})


@app.put("/api/conversations/{conversation_id}/control-binding")
def update_conversation_control_binding(
    payload: ConversationControlBindingRequest,
    conversation_id: str,
    user: dict[str, Any] = Depends(current_user),
):
    conversation = require_agent_conversation(conversation_id, str(user["id"]))
    device_id = str(payload.device_id or "").strip()
    target_id = str(payload.target_id or "").strip()
    if not device_id:
        database.execute(
            """UPDATE conversations SET control_device_id = '', control_target_id = '',
            control_target_kind = '', updated_at = ? WHERE id = ? AND user_id = ?""",
            (now_ms(), conversation_id, user["id"]),
        )
    else:
        device = computer_store.get_device(device_id, str(user["id"]))
        if not device:
            raise HTTPException(404, "可控设备不存在")
        requested_target = target_id or "desktop"
        try:
            target_kind = computer_store.resolve_target_kind(device, requested_target)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        database.execute(
            """UPDATE conversations SET control_device_id = ?, control_target_id = ?,
            control_target_kind = ?, updated_at = ? WHERE id = ? AND user_id = ?""",
            (device_id, requested_target, target_kind, now_ms(), conversation_id, user["id"]),
        )
    refreshed = database.get_conversation(conversation_id, str(user["id"])) or conversation
    return ok({"conversation": public_conversation(refreshed)})


@app.delete("/api/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str, user: dict[str, Any] = Depends(current_user)):
    require_conversation(conversation_id, str(user["id"]))
    active = database.one(
        "SELECT id FROM tasks WHERE conversation_id = ? AND status IN ('starting','running','waiting_approval','stopping') LIMIT 1",
        (conversation_id,),
    )
    if active:
        raise HTTPException(409, "请先停止该会话中正在运行的任务")
    await runtime_manager.close_page(str(user["id"]), conversation_id)
    database.execute(
        "UPDATE control_tasks SET conversation_id = NULL WHERE conversation_id = ? AND user_id = ?",
        (conversation_id, user["id"]),
    )
    database.execute("DELETE FROM conversations WHERE id = ? AND user_id = ?", (conversation_id, user["id"]))
    return ok()


@app.get("/api/conversations/{conversation_id}/messages")
def list_messages(conversation_id: str, user: dict[str, Any] = Depends(current_user)):
    require_conversation(conversation_id, str(user["id"]))
    rows = database.all("SELECT * FROM messages WHERE conversation_id = ? ORDER BY id ASC LIMIT 500", (conversation_id,))
    return ok({"messages": [public_message(row) for row in rows]})


@app.post("/api/conversations/{conversation_id}/chat")
async def conversation_chat(
    payload: ConversationChatRequest,
    conversation_id: str,
    user: dict[str, Any] = Depends(current_user),
):
    conversation = require_conversation(conversation_id, str(user["id"]))
    if str(conversation.get("mode") or "agent") != "chat":
        raise HTTPException(409, "当前会话不是 Chat 模式")
    if not user_chat_limiter.consume(str(user["id"])):
        raise HTTPException(429, "对话请求过多，请稍后再试")
    summary_through = int(conversation.get("summary_through_message_id") or 0)
    rows = database.all(
        "SELECT id, role, content FROM messages WHERE conversation_id = ? AND id > ? ORDER BY id ASC LIMIT 2000",
        (conversation_id, summary_through),
    )
    content = payload.content.strip()
    if not content:
        raise HTTPException(400, "消息不能为空")
    pending = [*rows, {"role": "user", "content": content}]
    prepared, summary, compressed, summarized_through = await compact_chat_context(
        pending,
        str(conversation.get("summary") or ""),
    )
    answer, usage = await fixed_chat_completion(prepared)
    database.add_message(conversation_id, "user", content)
    assistant = database.add_message(conversation_id, "assistant", answer)
    database.record_latest_instruction(
        conversation_id,
        str(user["id"]),
        content,
        summarize_conversation_title(content),
    )
    database.add_conversation_memory(
        conversation_id=conversation_id,
        user_id=str(user["id"]),
        source="chat",
        source_id=f"message:{assistant.get('id')}",
        user_content=content,
        assistant_content=answer,
    )
    database.create_notification(
        user_id=str(user["id"]),
        category="chat_completed",
        title="对话回复已完成",
        body=re.sub(r"\s+", " ", answer).strip()[:180],
        entity_type="conversation",
        entity_id=conversation_id,
        dedupe_key=f"message:{assistant.get('id')}:completed",
    )
    if compressed and summarized_through is not None:
        database.execute(
            "UPDATE conversations SET summary = ?, summary_through_message_id = ?, updated_at = ? WHERE id = ? AND user_id = ?",
            (summary, summarized_through, now_ms(), conversation_id, user["id"]),
        )
    return ok(
        {
            "message": public_message(assistant),
            "conversation": public_conversation(database.get_conversation(conversation_id, str(user["id"])) or conversation),
            "model": model_store.endpoint("chat").model,
            "contextCompressed": compressed,
            "usage": usage,
        }
    )


@app.post("/api/conversations/{conversation_id}/chat/stream")
async def conversation_chat_stream(
    payload: ConversationChatRequest,
    conversation_id: str,
    user: dict[str, Any] = Depends(current_user),
):
    conversation = require_conversation(conversation_id, str(user["id"]))
    if str(conversation.get("mode") or "agent") != "chat":
        raise HTTPException(409, "当前会话不是 Chat 模式")
    if not user_chat_limiter.consume(str(user["id"])):
        raise HTTPException(429, "对话请求过多，请稍后再试")
    summary_through = int(conversation.get("summary_through_message_id") or 0)
    rows = database.all(
        "SELECT id, role, content FROM messages WHERE conversation_id = ? AND id > ? ORDER BY id ASC LIMIT 2000",
        (conversation_id, summary_through),
    )
    content = payload.content.strip()
    if not content:
        raise HTTPException(400, "消息不能为空")
    prepared, summary, compressed, summarized_through = await compact_chat_context(
        [*rows, {"role": "user", "content": content}],
        str(conversation.get("summary") or ""),
    )

    async def relay():
        parts: list[str] = []
        try:
            async for item in fixed_chat_completion_stream(prepared):
                if item.get("type") == "delta":
                    delta = str(item.get("content") or "")
                    if delta:
                        parts.append(delta)
                        yield sse_frame("delta", {"content": delta})
                    continue
                if item.get("type") != "done":
                    continue
                answer = "".join(parts).strip()
                if not answer:
                    raise HTTPException(502, "模型流没有返回文本")
                database.add_message(conversation_id, "user", content)
                assistant = database.add_message(conversation_id, "assistant", answer)
                database.record_latest_instruction(
                    conversation_id,
                    str(user["id"]),
                    content,
                    summarize_conversation_title(content),
                )
                database.add_conversation_memory(
                    conversation_id=conversation_id,
                    user_id=str(user["id"]),
                    source="chat",
                    source_id=f"message:{assistant.get('id')}",
                    user_content=content,
                    assistant_content=answer,
                )
                database.create_notification(
                    user_id=str(user["id"]),
                    category="chat_completed",
                    title="对话回复已完成",
                    body=re.sub(r"\s+", " ", answer).strip()[:180],
                    entity_type="conversation",
                    entity_id=conversation_id,
                    dedupe_key=f"message:{assistant.get('id')}:completed",
                )
                if compressed and summarized_through is not None:
                    database.execute(
                        "UPDATE conversations SET summary = ?, summary_through_message_id = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                        (summary, summarized_through, now_ms(), conversation_id, user["id"]),
                    )
                refreshed = database.get_conversation(conversation_id, str(user["id"])) or conversation
                yield sse_frame("done", {
                    "message": public_message(assistant),
                    "conversation": public_conversation(refreshed),
                    "model": str(item.get("model") or model_store.endpoint("chat").model),
                    "contextCompressed": compressed,
                    "usage": item.get("usage") if isinstance(item.get("usage"), dict) else {},
                })
                return
        except HTTPException as exc:
            yield sse_frame("error", {"message": str(exc.detail), "status": exc.status_code})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("conversation_chat_stream_failed conversation=%s", conversation_id)
            yield sse_frame("error", {"message": f"流式对话失败: {str(exc)[:240]}", "status": 502})

    return StreamingResponse(
        relay(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@app.post("/api/conversations/{conversation_id}/tasks")
def create_task(payload: TaskCreateRequest, conversation_id: str, user: dict[str, Any] = Depends(current_user)):
    conversation = require_agent_conversation(conversation_id, str(user["id"]))
    for attachment_id in payload.attachment_ids:
        attachment = database.one(
            "SELECT id FROM attachments WHERE id = ? AND user_id = ? AND conversation_id = ?",
            (attachment_id, user["id"], conversation_id),
        )
        if not attachment:
            raise HTTPException(400, "附件不存在或不属于当前会话")
    content = payload.content.strip()
    task = database.create_task(
        user_id=str(user["id"]),
        conversation_id=conversation_id,
        prompt=content,
        attachment_ids=payload.attachment_ids,
    )
    database.record_latest_instruction(
        conversation_id, str(user["id"]), content, summarize_conversation_title(content)
    )
    database.add_task_event(str(task["id"]), "task.queued", {"message": "任务已进入持久队列"})
    dispatcher.wake()
    refreshed = database.get_conversation(conversation_id, str(user["id"])) or conversation
    return ok({"task": public_task(task), "conversation": public_conversation(refreshed)})


@app.post("/api/conversations/{conversation_id}/dispatch")
async def dispatch_agent_task(
    payload: TaskCreateRequest,
    conversation_id: str,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
):
    conversation = require_agent_conversation(conversation_id, str(user["id"]))
    for attachment_id in payload.attachment_ids:
        attachment = database.one(
            "SELECT id FROM attachments WHERE id = ? AND user_id = ? AND conversation_id = ?",
            (attachment_id, user["id"], conversation_id),
        )
        if not attachment:
            raise HTTPException(400, "附件不存在或不属于当前会话")

    content = payload.content.strip()
    database.record_latest_instruction(
        conversation_id, str(user["id"]), content, summarize_conversation_title(content)
    )
    conversation = database.get_conversation(conversation_id, str(user["id"])) or conversation
    device_id = str(conversation.get("control_device_id") or "")
    target_id = str(conversation.get("control_target_id") or "")
    target_kind = str(conversation.get("control_target_kind") or "")
    execution = "local"
    if device_id and target_id and target_kind and not payload.attachment_ids:
        execution = await classify_agent_execution(content, target_kind)

    if execution == "remote":
        if not control_task_limiter.consume(f"{user['id']}:{client_rate_key(request)}"):
            raise HTTPException(429, "电脑任务提交过于频繁")
        try:
            task = computer_store.create_task(
                str(user["id"]),
                device_id,
                target_id,
                content,
                conversation_id=conversation_id,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not task:
            raise HTTPException(409, "绑定的远程设备已不可用，请重新绑定")
        await dispatch_next_control_task(device_id)
        refreshed = computer_store.get_task(str(task["id"]), str(user["id"])) or task
        return ok({
            "execution": "remote",
            "task": public_control_task(refreshed),
            "conversation": public_conversation(conversation),
        })

    task = database.create_task(
        user_id=str(user["id"]),
        conversation_id=conversation_id,
        prompt=content,
        attachment_ids=payload.attachment_ids,
    )
    database.add_task_event(str(task["id"]), "task.queued", {"message": "任务已进入持久队列"})
    dispatcher.wake()
    return ok({
        "execution": "local",
        "task": public_task(task),
        "conversation": public_conversation(conversation),
    })


@app.get("/api/conversations/{conversation_id}/tasks")
def list_conversation_tasks(conversation_id: str, user: dict[str, Any] = Depends(current_vip_user)):
    require_agent_conversation(conversation_id, str(user["id"]))
    rows = database.all("SELECT * FROM tasks WHERE conversation_id = ? ORDER BY created_at ASC LIMIT 500", (conversation_id,))
    return ok({"tasks": [public_task(row) for row in rows]})


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str, user: dict[str, Any] = Depends(current_vip_user)):
    return ok({"task": public_task(require_task(task_id, str(user["id"])))})


@app.get("/api/tasks/{task_id}/events")
def task_events(task_id: str, after: int = Query(default=0, ge=0), user: dict[str, Any] = Depends(current_vip_user)):
    require_task(task_id, str(user["id"]))
    rows = database.all(
        "SELECT * FROM task_events WHERE task_id = ? AND id > ? ORDER BY id ASC LIMIT 500",
        (task_id, after),
    )
    events = []
    for row in rows:
        try:
            payload = json.loads(row.get("payload_json") or "{}")
        except json.JSONDecodeError:
            payload = {}
        events.append(
            {
                "id": int(row["id"]),
                "type": str(row["event_type"]),
                "payload": payload,
                "createdAt": int(row["created_at"]),
            }
        )
    return ok({"events": events})


@app.get("/api/tasks/{task_id}/stream")
async def task_event_stream(
    task_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
    user: dict[str, Any] = Depends(current_vip_user),
):
    require_task(task_id, str(user["id"]))

    async def relay():
        cursor = after
        last_updated = -1
        last_heartbeat = time.monotonic()
        yield "retry: 1000\n\n"
        while True:
            task = database.get_task(task_id, str(user["id"]))
            if not task:
                yield sse_frame("error", {"message": "任务不存在", "status": 404})
                return
            updated = int(task.get("updated_at") or 0)
            if updated != last_updated:
                yield sse_frame("task", {"task": public_task(task)})
                last_updated = updated
            rows = database.all(
                "SELECT * FROM task_events WHERE task_id = ? AND id > ? ORDER BY id ASC LIMIT 500",
                (task_id, cursor),
            )
            for row in rows:
                try:
                    payload = json.loads(row.get("payload_json") or "{}")
                except json.JSONDecodeError:
                    payload = {}
                event = {
                    "id": int(row["id"]),
                    "type": str(row["event_type"]),
                    "payload": payload if isinstance(payload, dict) else {},
                    "createdAt": int(row["created_at"]),
                }
                cursor = event["id"]
                yield sse_frame("task-event", {"event": event}, event_id=cursor)
            if str(task.get("status") or "") in {"completed", "failed", "cancelled"}:
                latest = database.get_task(task_id, str(user["id"])) or task
                yield sse_frame("done", {"task": public_task(latest)})
                return
            if await request.is_disconnected():
                return
            if time.monotonic() - last_heartbeat >= 10:
                yield ": keepalive\n\n"
                last_heartbeat = time.monotonic()
            await asyncio.sleep(0.25)

    return StreamingResponse(
        relay(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@app.post("/api/tasks/{task_id}/approval")
async def approve_task(payload: TaskApprovalRequest, task_id: str, user: dict[str, Any] = Depends(current_vip_user)):
    task = require_task(task_id, str(user["id"]))
    if task.get("status") != "waiting_approval" or not task.get("hermes_run_id") or not task.get("worker_name"):
        raise HTTPException(409, "该任务当前不需要审批")
    result = await hermes_client.approve(
        str(task["worker_name"]), str(user["id"]), str(task["hermes_run_id"]), payload.decision
    )
    database.update_task(task_id, status="running")
    database.add_task_event(task_id, "approval.responded", {"choice": payload.decision})
    return ok({"result": result})


@app.post("/api/tasks/{task_id}/steer")
async def steer_task(payload: TaskSteerRequest, task_id: str, user: dict[str, Any] = Depends(current_vip_user)):
    task = require_task(task_id, str(user["id"]))
    if task.get("status") != "running" or not task.get("hermes_run_id") or not task.get("worker_name"):
        raise HTTPException(409, "该任务当前不能接收追加指令")
    result = await hermes_client.steer(
        str(task["worker_name"]), str(user["id"]), str(task["hermes_run_id"]), payload.content.strip()
    )
    database.add_task_event(task_id, "run.steered", {"content": payload.content.strip()})
    return ok({"result": result})


@app.post("/api/tasks/{task_id}/stop")
async def stop_task(task_id: str, user: dict[str, Any] = Depends(current_vip_user)):
    task = require_task(task_id, str(user["id"]))
    if task.get("status") == "queued":
        database.finish_task(task_id, status="cancelled")
        database.add_task_event(task_id, "run.cancelled", {"message": "排队任务已取消"})
        return ok()
    if task.get("status") not in ACTIVE_STATUSES or not task.get("hermes_run_id") or not task.get("worker_name"):
        raise HTTPException(409, "任务已结束")
    result = await hermes_client.stop(str(task["worker_name"]), str(user["id"]), str(task["hermes_run_id"]))
    database.update_task(task_id, status="stopping")
    return ok({"result": result})


@app.post("/api/conversations/{conversation_id}/attachments")
async def upload_attachment(
    conversation_id: str,
    file: UploadFile = File(...),
    user: dict[str, Any] = Depends(current_user),
):
    require_agent_conversation(conversation_id, str(user["id"]))
    filename = _safe_upload_filename(file.filename or "attachment")
    content = await file.read(settings.upload_max_bytes + 1)
    await file.close()
    if not content:
        raise HTTPException(400, "附件不能为空")
    if len(content) > settings.upload_max_bytes:
        raise HTTPException(413, "附件超过大小限制")
    attachment = database.create_attachment(
        user_id=str(user["id"]),
        conversation_id=conversation_id,
        filename=filename,
        relative_path="pending",
        mime_type=file.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream",
        size_bytes=len(content),
    )
    suffix = Path(filename).suffix[:16]
    relative_path = f"{attachment['id']}{suffix}"
    target = runtime_manager.user_paths(str(user["id"]))["container_attachments"] / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    workspace_path = ""
    try:
        await asyncio.to_thread(target.write_bytes, content)
        workspace_target = await _store_workspace_upload(str(user["id"]), "", filename, content)
        workspace_path = workspace_target.relative_to(runtime_manager.user_paths(str(user["id"]))["container_workspace"]).as_posix()
        database.execute("UPDATE attachments SET relative_path = ? WHERE id = ?", (relative_path, attachment["id"]))
    except Exception:
        database.execute("DELETE FROM attachments WHERE id = ?", (attachment["id"],))
        if target.is_file():
            await asyncio.to_thread(target.unlink)
        raise HTTPException(500, "附件保存失败")
    attachment = database.one("SELECT * FROM attachments WHERE id = ?", (attachment["id"],)) or attachment
    result = public_attachment(attachment)
    result["workspacePath"] = workspace_path
    return ok({"attachment": result})


@app.get("/api/attachments/{attachment_id}")
def download_attachment(attachment_id: str, user: dict[str, Any] = Depends(current_vip_user)):
    attachment = database.one("SELECT * FROM attachments WHERE id = ? AND user_id = ?", (attachment_id, user["id"]))
    if not attachment:
        raise HTTPException(404, "附件不存在")
    path = runtime_manager.user_paths(str(user["id"]))["container_attachments"] / str(attachment["relative_path"])
    if not path.is_file():
        raise HTTPException(404, "附件文件不存在")
    return FileResponse(path, filename=str(attachment["filename"]), media_type=str(attachment["mime_type"]))


@app.delete("/api/attachments/{attachment_id}")
async def delete_attachment(attachment_id: str, user: dict[str, Any] = Depends(current_vip_user)):
    attachment = database.one("SELECT * FROM attachments WHERE id = ? AND user_id = ?", (attachment_id, user["id"]))
    if not attachment:
        raise HTTPException(404, "附件不存在")
    path = runtime_manager.user_paths(str(user["id"]))["container_attachments"] / str(attachment["relative_path"])
    database.execute("DELETE FROM attachments WHERE id = ? AND user_id = ?", (attachment_id, user["id"]))
    if path.is_file():
        await asyncio.to_thread(path.unlink)
    return ok()


def _workspace_path(user_id: str, relative: str) -> tuple[Path, Path]:
    root = runtime_manager.user_paths(user_id)["container_workspace"].resolve()
    root.mkdir(parents=True, exist_ok=True)
    candidate = (root / relative.lstrip("/\\")).resolve()
    if candidate != root and root not in candidate.parents:
        raise HTTPException(400, "工作区路径无效")
    return root, candidate


def _safe_upload_filename(filename: str) -> str:
    value = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()[:240]
    if value in {"", ".", ".."}:
        return "upload"
    return value


def _available_workspace_target(directory: Path, filename: str) -> Path:
    target = directory / filename
    if not target.exists():
        return target
    suffix = "".join(Path(filename).suffixes)
    stem = filename[:-len(suffix)] if suffix else filename
    for index in range(2, 10_000):
        candidate = directory / f"{stem} ({index}){suffix}"
        if not candidate.exists():
            return candidate
    raise HTTPException(409, "同名文件过多")


async def _store_workspace_upload(user_id: str, relative_directory: str, filename: str, content: bytes) -> Path:
    root, directory = _workspace_path(user_id, relative_directory)
    if not directory.exists() or not directory.is_dir():
        raise HTTPException(404, "目录不存在")
    lock = workspace_upload_locks.setdefault(user_id, asyncio.Lock())
    async with lock:
        target = _available_workspace_target(directory, filename)
        if target != root and root not in target.resolve(strict=False).parents:
            raise HTTPException(400, "工作区路径无效")
        temporary = directory / f".{target.name}.{secrets.token_hex(6)}.upload"
        try:
            await asyncio.to_thread(temporary.write_bytes, content)
            await asyncio.to_thread(temporary.replace, target)
        finally:
            if temporary.is_file():
                await asyncio.to_thread(temporary.unlink)
        return target


def _public_workspace_entry(root: Path, item: Path) -> dict[str, Any]:
    stat = item.stat()
    is_file = item.is_file()
    return {
        "name": item.name,
        "path": item.relative_to(root).as_posix(),
        "type": "file" if is_file else "directory",
        "mimeType": (mimetypes.guess_type(item.name)[0] or "application/octet-stream") if is_file else "",
        "sizeBytes": stat.st_size if is_file else 0,
        "updatedAt": int(stat.st_mtime * 1000),
    }


MAX_PREVIEW_BYTES = 15 * 1024 * 1024
TEXT_PREVIEW_SUFFIXES = {
    ".txt", ".md", ".markdown", ".json", ".jsonl", ".yaml", ".yml", ".xml", ".html", ".htm",
    ".css", ".js", ".jsx", ".ts", ".tsx", ".py", ".java", ".kt", ".c", ".cpp", ".h", ".hpp",
    ".ini", ".conf", ".log", ".csv", ".tsv", ".sql", ".sh", ".ps1", ".bat", ".toml",
}
MARKDOWN_PREVIEW_SUFFIXES = {".md", ".markdown"}
OFFICE_PREVIEW_SUFFIXES = {
    ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".odt", ".odp", ".ods",
}
AUDIO_PREVIEW_SUFFIXES = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
IMAGE_PREVIEW_SUFFIXES = {".avif", ".bmp", ".gif", ".jpg", ".jpeg", ".png", ".webp"}


def _read_text_preview(target: Path) -> tuple[str, bool]:
    payload = target.read_bytes()[:2_000_001]
    truncated = target.stat().st_size > 2_000_000
    if len(payload) > 2_000_000:
        payload = payload[:2_000_000]
        truncated = True
    for encoding in ("utf-8-sig", "utf-16", "gb18030"):
        try:
            text = payload.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = payload.decode("utf-8", errors="replace")
    if len(text) > 500_000:
        return text[:500_000], True
    return text, truncated


async def _render_document_preview(user_id: str, root: Path, target: Path) -> list[str]:
    stat = target.stat()
    relative = target.relative_to(root).as_posix()
    cache_key = hashlib.sha256(
        f"{relative}\0{stat.st_mtime_ns}\0{stat.st_size}".encode("utf-8")
    ).hexdigest()[:24]
    output_dir = root / ".file-previews" / cache_key
    output_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(
        output_dir.glob("page-*.png"),
        key=lambda path: int(path.stem.rsplit("-", 1)[-1]),
    )
    if existing:
        return [path.relative_to(root).as_posix() for path in existing]

    await runtime_manager.ensure_worker(user_id, dispatcher.busy_user_ids())
    source_pdf = target
    if target.suffix.casefold() != ".pdf":
        conversion = await runtime_manager.exec_worker(
            user_id,
            [
                "libreoffice", "--headless", "--convert-to", "pdf", "--outdir",
                f"/workspace/{output_dir.relative_to(root).as_posix()}",
                f"/workspace/{relative}",
            ],
            timeout_seconds=180,
        )
        if int(conversion.get("exitCode") or 0) != 0:
            raise HTTPException(422, "Office 文件转换失败，无法生成预览")
        converted = sorted(output_dir.glob("*.pdf"))
        if not converted:
            raise HTTPException(422, "Office 文件没有生成可预览的 PDF")
        source_pdf = converted[0]

    render = await runtime_manager.exec_worker(
        user_id,
        [
            "pdftoppm", "-f", "1", "-l", "80", "-r", "120", "-png",
            f"/workspace/{source_pdf.relative_to(root).as_posix()}",
            f"/workspace/{(output_dir / 'page').relative_to(root).as_posix()}",
        ],
        timeout_seconds=240,
    )
    if int(render.get("exitCode") or 0) != 0:
        raise HTTPException(422, "文档页面渲染失败")
    pages = sorted(
        output_dir.glob("page-*.png"),
        key=lambda path: int(path.stem.rsplit("-", 1)[-1]),
    )
    if not pages:
        raise HTTPException(422, "文档没有可预览的页面")
    return [path.relative_to(root).as_posix() for path in pages]


@app.get("/api/workspace")
def list_workspace(
    conversation_id: str = Query(min_length=1, max_length=80),
    path: str = Query(default="", max_length=1000),
    user: dict[str, Any] = Depends(current_user),
):
    require_agent_conversation(conversation_id, str(user["id"]))
    root, target = _workspace_path(str(user["id"]), path)
    if not target.exists() or not target.is_dir():
        raise HTTPException(404, "目录不存在")
    entries = []
    for item in sorted(target.iterdir(), key=lambda value: (not value.is_dir(), value.name.lower()))[:500]:
        try:
            resolved = item.resolve()
            if resolved != root and root not in resolved.parents:
                continue
            item.stat()
        except OSError:
            continue
        entries.append(_public_workspace_entry(root, item))
    return ok({"path": target.relative_to(root).as_posix() if target != root else "", "entries": entries})


@app.post("/api/workspace/upload")
async def upload_workspace_file(
    conversation_id: str = Query(min_length=1, max_length=80),
    path: str = Query(default="", max_length=1000),
    file: UploadFile = File(...),
    user: dict[str, Any] = Depends(current_user),
):
    require_agent_conversation(conversation_id, str(user["id"]))
    filename = _safe_upload_filename(file.filename or "upload")
    content = await file.read(settings.upload_max_bytes + 1)
    await file.close()
    if not content:
        raise HTTPException(400, "文件不能为空")
    if len(content) > settings.upload_max_bytes:
        raise HTTPException(413, "文件超过大小限制")
    root, _directory = _workspace_path(str(user["id"]), path)
    target = await _store_workspace_upload(str(user["id"]), path, filename, content)
    return ok({"entry": _public_workspace_entry(root, target)})


@app.get("/api/workspace/mentions")
def workspace_mentions(
    conversation_id: str = Query(min_length=1, max_length=80),
    query: str = Query(default="", max_length=240),
    user: dict[str, Any] = Depends(current_user),
):
    require_agent_conversation(conversation_id, str(user["id"]))
    root, _target = _workspace_path(str(user["id"]), "")
    normalized = query.strip().lower()
    matches: list[dict[str, Any]] = []
    scanned = 0
    for item in root.rglob("*"):
        scanned += 1
        if scanned > 20_000:
            break
        try:
            resolved = item.resolve()
            if not item.is_file() or item.is_symlink() or root not in resolved.parents:
                continue
            relative = item.relative_to(root).as_posix()
            if normalized and normalized not in relative.lower():
                continue
            matches.append(_public_workspace_entry(root, item))
        except OSError:
            continue
    matches.sort(key=lambda item: (item["path"].count("/"), item["path"].lower()))
    return ok({"entries": matches[:100]})


@app.get("/api/workspace/download")
def download_workspace_file(
    conversation_id: str = Query(min_length=1, max_length=80),
    path: str = Query(min_length=1, max_length=1000),
    user: dict[str, Any] = Depends(current_user),
):
    require_agent_conversation(conversation_id, str(user["id"]))
    _root, target = _workspace_path(str(user["id"]), path)
    if not target.is_file():
        raise HTTPException(404, "文件不存在")
    return FileResponse(target, filename=target.name)


@app.post("/api/workspace/preview")
async def preview_workspace_file(
    conversation_id: str = Query(min_length=1, max_length=80),
    path: str = Query(min_length=1, max_length=1000),
    user: dict[str, Any] = Depends(current_user),
):
    require_agent_conversation(conversation_id, str(user["id"]))
    root, target = _workspace_path(str(user["id"]), path)
    if not target.is_file():
        raise HTTPException(404, "文件不存在")
    stat = target.stat()
    if stat.st_size > MAX_PREVIEW_BYTES:
        raise HTTPException(413, "文件超过 15MB，暂不提供在线预览")
    if stat.st_size <= 0:
        raise HTTPException(422, "空文件无法预览")
    suffix = target.suffix.casefold()
    mime_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    base = {
        "filename": target.name,
        "mimeType": mime_type,
        "sizeBytes": stat.st_size,
        "sourcePath": target.relative_to(root).as_posix(),
    }
    if suffix == ".mp4" or mime_type.casefold() == "video/mp4":
        raise HTTPException(415, "暂不支持 MP4 在线预览")
    if suffix in MARKDOWN_PREVIEW_SUFFIXES:
        text, truncated = await asyncio.to_thread(_read_text_preview, target)
        return ok({"preview": {**base, "kind": "markdown", "text": text, "truncated": truncated}})
    if suffix in TEXT_PREVIEW_SUFFIXES or mime_type.startswith("text/"):
        text, truncated = await asyncio.to_thread(_read_text_preview, target)
        return ok({"preview": {**base, "kind": "text", "text": text, "truncated": truncated}})
    if suffix in AUDIO_PREVIEW_SUFFIXES or mime_type.startswith("audio/"):
        return ok({"preview": {**base, "kind": "audio"}})
    if suffix in IMAGE_PREVIEW_SUFFIXES or mime_type.startswith("image/"):
        return ok({"preview": {**base, "kind": "image"}})
    if suffix == ".zip":
        try:
            with zipfile.ZipFile(target) as archive:
                names = archive.namelist()
        except (OSError, zipfile.BadZipFile):
            raise HTTPException(422, "ZIP 文件损坏或格式无效")
        listing = "\n".join(names[:2000])
        return ok({"preview": {**base, "kind": "text", "text": listing, "truncated": len(names) > 2000}})
    if suffix == ".pdf" or suffix in OFFICE_PREVIEW_SUFFIXES:
        pages = await _render_document_preview(str(user["id"]), root, target)
        return ok({"preview": {**base, "kind": "document", "pages": pages, "truncated": len(pages) >= 80}})
    raise HTTPException(415, "该文件格式暂不支持在线预览，可直接下载或分享到其他应用打开")


@app.get("/api/conversations/{conversation_id}/browser")
async def browser_state(conversation_id: str, user: dict[str, Any] = Depends(current_user)):
    require_agent_conversation(conversation_id, str(user["id"]))
    state = await runtime_manager.ensure_page(str(user["id"]), conversation_id)
    return ok({"browser": state})


@app.post("/api/conversations/{conversation_id}/browser/action")
async def browser_action(payload: BrowserActionRequest, conversation_id: str, user: dict[str, Any] = Depends(current_user)):
    require_agent_conversation(conversation_id, str(user["id"]))
    state = await runtime_manager.browser_action(str(user["id"]), conversation_id, payload.action)
    return ok({"browser": state})


@app.post("/api/conversations/{conversation_id}/browser/ticket")
def browser_ticket(conversation_id: str, user: dict[str, Any] = Depends(current_user)):
    require_agent_conversation(conversation_id, str(user["id"]))
    ticket = issue_browser_scope(settings.internal_browser_key, str(user["id"]), conversation_id, lifetime_seconds=120)
    return ok({"ticket": ticket, "expiresIn": 120})


@app.websocket("/api/v1/conversations/{conversation_id}/browser/vnc")
@app.websocket("/api/conversations/{conversation_id}/browser/vnc")
async def browser_vnc(websocket: WebSocket, conversation_id: str, ticket: str = Query(default="")):
    try:
        user_id = verify_browser_scope(settings.internal_browser_key, ticket, conversation_id)
        user = database.get_user_by_id(user_id)
        if not user or user.get("status") != "active":
            raise ValueError("inactive user")
        require_agent_conversation(conversation_id, user_id)
        upstream_url = await runtime_manager.vnc_url(user_id, conversation_id)
    except Exception:
        await websocket.close(code=4401)
        return
    requested_protocols = websocket.headers.get("sec-websocket-protocol", "")
    subprotocol = "binary" if "binary" in requested_protocols else None
    await websocket.accept(subprotocol=subprotocol)
    try:
        async with websockets.connect(
            upstream_url,
            subprotocols=["binary"] if subprotocol else None,
            max_size=None,
            open_timeout=15,
            proxy=None,
        ) as upstream:
            async def client_to_upstream():
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        return
                    if message.get("bytes") is not None:
                        await upstream.send(message["bytes"])
                    elif message.get("text") is not None:
                        await upstream.send(message["text"])

            async def upstream_to_client():
                async for message in upstream:
                    if isinstance(message, bytes):
                        await websocket.send_bytes(message)
                    else:
                        await websocket.send_text(message)

            tasks = {asyncio.create_task(client_to_upstream()), asyncio.create_task(upstream_to_client())}
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*done, *pending, return_exceptions=True)
    except (WebSocketDisconnect, websockets.WebSocketException, OSError):
        pass
    finally:
        try:
            await websocket.close()
        except RuntimeError:
            pass


@app.get("/api/schedules")
async def list_schedules(user: dict[str, Any] = Depends(current_vip_user)):
    user_id = str(user["id"])
    try:
        worker = await runtime_manager.ensure_worker(user_id, dispatcher.busy_user_ids())
        jobs = await hermes_client.list_jobs(worker, user_id)
    except RuntimeUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except HermesError as exc:
        raise HTTPException(502, str(exc)) from exc
    return ok({"schedules": jobs})


@app.post("/api/schedules/{job_id}/{action}")
async def update_schedule(
    job_id: str,
    action: str,
    user: dict[str, Any] = Depends(current_vip_user),
):
    if action not in {"pause", "resume", "run"}:
        raise HTTPException(404, "不支持的定时任务操作")
    if not job_id or len(job_id) > 128 or not all(character.isalnum() or character in "_-" for character in job_id):
        raise HTTPException(400, "无效的定时任务 ID")
    user_id = str(user["id"])
    try:
        worker = await runtime_manager.ensure_worker(user_id, dispatcher.busy_user_ids())
        result = await hermes_client.job_action(worker, user_id, job_id, action)
    except RuntimeUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except HermesError as exc:
        raise HTTPException(502, str(exc)) from exc
    return ok({"schedule": result.get("job", result)})


def _runtime_user(authorization: str | None) -> dict[str, Any]:
    try:
        user_id = verify_internal_runtime_token(settings.internal_hermes_key, bearer_value(authorization))
    except (ValueError, HTTPException):
        raise HTTPException(401, "运行时授权无效")
    user = database.get_user_by_id(user_id)
    if not user or user.get("status") != "active":
        raise HTTPException(403, "运行时用户已停用")
    require_vip(user)
    return user


async def _fixed_llm_proxy(request: Request, subpath: str, *, max_body_bytes: int):
    normalized = subpath.strip("/")
    if normalized == "models" and request.method == "GET":
        return {
            "object": "list",
            "data": [{"id": "mumu-execution", "object": "model", "owned_by": "mumu"}],
        }
    if normalized != "chat/completions" or request.method != "POST":
        raise HTTPException(404, "仅开放固定模型的 Chat Completions 接口")
    raw = await request.body()
    if len(raw) > max_body_bytes:
        raise HTTPException(413, "模型请求过大")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(400, "模型请求 JSON 无效")
    if not isinstance(payload, dict) or not isinstance(payload.get("messages"), list):
        raise HTTPException(400, "模型请求缺少 messages")
    requested_stream = bool(payload.get("stream"))
    bridge_tools = isinstance(payload.get("tools"), list) and bool(payload["tools"])
    try:
        endpoint, upstream_model, payload = await model_gateway.prepare_payload("executor", payload)
    except (httpx.HTTPError, LLMUpstreamExhausted, ValueError) as exc:
        raise HTTPException(502, f"执行模型准备失败: {str(exc)[:300]}") from exc
    allowed_tool_names: set[str] = set()
    if bridge_tools:
        payload, allowed_tool_names = prepare_upstream_payload(payload, upstream_model)
    upstream_headers = model_gateway._headers(endpoint)
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(120, connect=10),
        verify=settings.llm_verify_tls,
        proxy=settings.llm_proxy or None,
    )
    if bridge_tools:
        def validate_bridge_response(response: httpx.Response) -> None:
            completion = response.json()
            require_reported_model(completion, upstream_model)
            normalize_completion(completion, upstream_model, allowed_tool_names)

        try:
            upstream = await post_llm_upstream_with_retry(
                client,
                f"{endpoint.base_url}/chat/completions",
                headers=upstream_headers,
                payload=payload,
                stream=False,
                max_retries=settings.llm_max_retries,
                concurrency_limit=settings.llm_concurrency_limit,
                validate_response=validate_bridge_response,
            )
        except LLMUpstreamExhausted as exc:
            await client.aclose()
            raise HTTPException(
                502,
                f"上游模型连接失败: {str(exc)[:300]}",
                headers={RETRY_EXHAUSTED_HEADER: "1"},
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            await client.aclose()
            raise HTTPException(502, f"上游模型连接失败: {str(exc)[:300]}")
        await client.aclose()
        content_type = upstream.headers.get("content-type", "application/json").split(";", 1)[0]
        if upstream.status_code >= 400:
            return Response(content=upstream.content, status_code=upstream.status_code, media_type=content_type)
        try:
            completion = normalize_completion(upstream.json(), upstream_model, allowed_tool_names)
        except (TypeError, ValueError, KeyError):
            raise HTTPException(502, "上游模型返回了无效的 Chat Completions 数据")
        if requested_stream:
            return StreamingResponse(completion_sse(completion), media_type="text/event-stream")
        return JSONResponse(completion)
    def validate_direct_response(response: httpx.Response) -> None:
        media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().casefold()
        if media_type == "text/event-stream":
            reported = False
            for line in response.text.splitlines():
                raw_line = line.strip()
                if raw_line.startswith("data:"):
                    raw_line = raw_line[5:].strip()
                if not raw_line or raw_line == "[DONE]":
                    continue
                try:
                    chunk = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                if isinstance(chunk, dict) and str(chunk.get("model") or "").strip():
                    require_reported_model(chunk, upstream_model)
                    reported = True
            if not reported:
                require_reported_model({}, upstream_model)
            return
        require_reported_model(response.json(), upstream_model)

    try:
        upstream = await post_llm_upstream_with_retry(
            client,
            f"{endpoint.base_url}/chat/completions",
            headers=upstream_headers,
            payload=payload,
            stream=False,
            max_retries=settings.llm_max_retries,
            concurrency_limit=settings.llm_concurrency_limit,
            validate_response=validate_direct_response,
        )
    except LLMUpstreamExhausted as exc:
        await client.aclose()
        raise HTTPException(
            502,
            f"上游模型连接失败: {str(exc)[:300]}",
            headers={RETRY_EXHAUSTED_HEADER: "1"},
        ) from exc
    except (httpx.HTTPError, ValueError) as exc:
        await client.aclose()
        raise HTTPException(502, f"上游模型连接失败: {str(exc)[:300]}")

    async def relay():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    content_type = upstream.headers.get("content-type", "application/json")
    return StreamingResponse(relay(), status_code=upstream.status_code, media_type=content_type.split(";", 1)[0])


@app.api_route("/api/internal/llm/v1/{subpath:path}", methods=["GET", "POST"], include_in_schema=False)
async def internal_llm_proxy(request: Request, subpath: str, authorization: str | None = Header(default=None)):
    _runtime_user(authorization)
    return await _fixed_llm_proxy(request, subpath, max_body_bytes=settings.upload_max_bytes * 3)


def _control_llm_lease(authorization: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
    raw = control_credential(authorization)
    credential, separator, lease_scope = raw.partition("~")
    task_id, lease_separator, lease_id = lease_scope.partition("~")
    if not separator or not lease_separator or not task_id or not lease_id:
        raise HTTPException(401, "电脑任务模型授权无效")
    device = computer_store.authenticate_credential(credential)
    if not device:
        raise HTTPException(401, "电脑客户端授权已失效")
    user = database.get_user_by_id(str(device.get("user_id") or ""))
    if not user or user.get("status") != "active" or user.get("access_tier") != "vip":
        raise HTTPException(403, "该账户未开通电脑控制权限")
    task = computer_store.active_task_lease(str(device["id"]), task_id, lease_id)
    if not task:
        raise HTTPException(403, "电脑任务租约已失效")
    return device, task


@app.api_route("/api/control/llm/v1/{subpath:path}", methods=["GET", "POST"], include_in_schema=False)
async def control_llm_proxy(request: Request, subpath: str, authorization: str | None = Header(default=None)):
    _control_llm_lease(authorization)
    return await _fixed_llm_proxy(request, subpath, max_body_bytes=8 * 1024 * 1024)


@app.get("/api/admin/settings", include_in_schema=False)
def admin_settings(_admin: dict[str, Any] = Depends(current_admin)):
    return ok({
        "registrationEnabled": settings.registration_enabled,
        "models": model_store.public(),
    })


@app.patch("/api/admin/model-settings", include_in_schema=False)
def admin_update_model_settings(
    payload: AdminModelSettingsUpdateRequest,
    _admin: dict[str, Any] = Depends(current_admin),
):
    try:
        model_store.update(payload.model_dump())
        model_gateway._model_cache.clear()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return ok({"models": model_store.public()})


@app.post("/api/admin/model-settings/test", include_in_schema=False)
async def admin_test_model_settings(
    payload: AdminModelTestRequest,
    _admin: dict[str, Any] = Depends(current_admin),
):
    try:
        return ok(await model_gateway.test_connection(payload.role))
    except LLMUpstreamExhausted as exc:
        raise HTTPException(502, str(exc), headers={RETRY_EXHAUSTED_HEADER: "1"}) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, f"模型连接测试失败: {str(exc)[:500]}") from exc


@app.get("/api/admin/users", include_in_schema=False)
def admin_users(_admin: dict[str, Any] = Depends(current_admin)):
    rows = database.all("SELECT * FROM users ORDER BY created_at DESC")
    return ok({"users": [public_user(row) for row in rows]})


@app.post("/api/admin/users", include_in_schema=False)
def admin_create_user(payload: AdminUserCreateRequest, _admin: dict[str, Any] = Depends(current_admin)):
    try:
        email = normalize_email(payload.email)
        password = validate_password(payload.password)
        username = normalize_username(payload.username or email.split("@", 1)[0])
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if database.get_user_by_email(email):
        raise HTTPException(409, "该邮箱已存在")
    if database.get_user_by_username(username):
        raise HTTPException(409, "该用户名已存在")
    user = database.create_user(
        username=username,
        email=email,
        display_name=payload.display_name.strip() or email.split("@", 1)[0],
        password_hash=hash_password(password),
        status=payload.status,
        access_tier=payload.access_tier,
    )
    return ok({"user": public_user(user)})


@app.patch("/api/admin/users/{user_id}", include_in_schema=False)
async def admin_update_user(payload: AdminUserUpdateRequest, user_id: str, _admin: dict[str, Any] = Depends(current_admin)):
    user = database.get_user_by_id(user_id)
    if not user:
        raise HTTPException(404, "用户不存在")
    updates: list[str] = []
    parameters: list[Any] = []
    if payload.display_name is not None:
        updates.append("display_name = ?")
        parameters.append(payload.display_name.strip() or user["display_name"])
    if payload.status is not None:
        updates.append("status = ?")
        parameters.append(payload.status)
    if payload.access_tier is not None:
        updates.append("access_tier = ?")
        parameters.append(payload.access_tier)
    if payload.password:
        try:
            password = validate_password(payload.password)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        updates.append("password_hash = ?")
        parameters.append(hash_password(password))
    if updates:
        updates.append("updated_at = ?")
        parameters.extend([now_ms(), user_id])
        database.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", tuple(parameters))
    if payload.status == "disabled":
        await runtime_manager.stop_user_runtimes(user_id)
    return ok({"user": public_user(database.get_user_by_id(user_id) or user)})


@app.get("/api/admin/activation-codes", include_in_schema=False)
def admin_activation_codes(_admin: dict[str, Any] = Depends(current_admin)):
    rows = database.all("SELECT * FROM activation_codes ORDER BY created_at DESC LIMIT 1000")
    return ok({"activationCodes": [public_activation_code(row) for row in rows]})


@app.post("/api/admin/activation-codes", include_in_schema=False)
def admin_create_activation_code(
    payload: ActivationCodeCreateRequest,
    _admin: dict[str, Any] = Depends(current_admin),
):
    code_id = secrets.token_hex(16)
    code = ""
    digest = ""
    for _attempt in range(8):
        raw = secrets.token_hex(8).upper()
        candidate = "VIP-" + "-".join(raw[index : index + 4] for index in range(0, 16, 4))
        candidate_digest = activation_code_digest(settings.activation_secret, candidate)
        if not database.one("SELECT id FROM activation_codes WHERE code_hash = ?", (candidate_digest,)):
            code = candidate
            digest = candidate_digest
            break
    if not code:
        raise HTTPException(503, "暂时无法生成唯一激活码")
    current = now_ms()
    database.execute(
        """INSERT INTO activation_codes
        (id, code_hash, code_preview, note, status, max_uses, use_count, expires_at, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'active', ?, 0, ?, ?, ?)""",
        (
            code_id,
            digest,
            f"VIP-{code[4:8]}-****-****-{code[-4:]}",
            payload.note.strip(),
            payload.max_uses,
            payload.expires_at,
            current,
            current,
        ),
    )
    row = database.one("SELECT * FROM activation_codes WHERE id = ?", (code_id,)) or {}
    return ok({"activationCode": public_activation_code(row, code)})


@app.patch("/api/admin/activation-codes/{code_id}", include_in_schema=False)
def admin_update_activation_code(
    payload: ActivationCodeUpdateRequest,
    code_id: str,
    _admin: dict[str, Any] = Depends(current_admin),
):
    row = database.one("SELECT * FROM activation_codes WHERE id = ?", (code_id,))
    if not row:
        raise HTTPException(404, "激活码不存在")
    values = payload.model_dump(exclude_unset=True)
    if any(values.get(field) is None for field in ("note", "status", "max_uses") if field in values):
        raise HTTPException(400, "该字段不能设为空")
    if "max_uses" in values and int(values["max_uses"]) < int(row.get("use_count") or 0):
        raise HTTPException(400, "使用上限不能小于已使用次数")
    updates: list[str] = []
    parameters: list[Any] = []
    for field, column in {
        "note": "note", "status": "status", "max_uses": "max_uses", "expires_at": "expires_at"
    }.items():
        if field in values:
            updates.append(f"{column} = ?")
            parameters.append(values[field].strip() if field == "note" else values[field])
    if updates:
        updates.append("updated_at = ?")
        parameters.extend([now_ms(), code_id])
        database.execute(f"UPDATE activation_codes SET {', '.join(updates)} WHERE id = ?", tuple(parameters))
    return ok({"activationCode": public_activation_code(database.one("SELECT * FROM activation_codes WHERE id = ?", (code_id,)) or row)})


@app.delete("/api/admin/activation-codes/{code_id}", include_in_schema=False)
def admin_delete_activation_code(code_id: str, _admin: dict[str, Any] = Depends(current_admin)):
    if database.execute("DELETE FROM activation_codes WHERE id = ?", (code_id,)) != 1:
        raise HTTPException(404, "激活码不存在")
    return ok()


@app.delete("/api/admin/users/{user_id}", include_in_schema=False)
async def admin_delete_user(user_id: str, _admin: dict[str, Any] = Depends(current_admin)):
    user = database.get_user_by_id(user_id)
    if not user:
        raise HTTPException(404, "用户不存在")
    await runtime_manager.remove_user(user_id)
    await asyncio.to_thread(computer_store.remove_user_frames, user_id)
    database.execute("DELETE FROM users WHERE id = ?", (user_id,))
    return ok()


@app.get("/api/admin/runtimes", include_in_schema=False)
async def admin_runtimes(_admin: dict[str, Any] = Depends(current_admin)):
    summary = await asyncio.to_thread(runtime_manager.runtime_summary)
    queued = database.one("SELECT COUNT(*) AS count FROM tasks WHERE status = 'queued'") or {"count": 0}
    summary["queuedTasks"] = int(queued["count"])
    return ok(summary)


@app.get("/api/savepoints")
def list_savepoints(user: dict[str, Any] = Depends(current_vip_user)):
    rows = database.all(
        "SELECT * FROM savepoints WHERE user_id = ? ORDER BY created_at DESC",
        (user["id"],),
    )
    return ok({"savepoints": [public_savepoint(row) for row in rows]})


@app.post("/api/savepoints")
async def create_savepoint(payload: SavepointCreateRequest, user: dict[str, Any] = Depends(current_vip_user)):
    user_id = str(user["id"])
    if user_id in dispatcher.busy_user_ids():
        raise HTTPException(409, "Hermes 正在执行任务，请等待任务结束后再创建保存点")
    existing = database.one("SELECT COUNT(*) AS count FROM savepoints WHERE user_id = ?", (user_id,)) or {"count": 0}
    if int(existing["count"]) >= 10:
        raise HTTPException(409, "每个账户最多保留 10 个保存点")
    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "保存点名称不能为空")
    savepoint_id = secrets.token_hex(16)
    lock = savepoint_locks.setdefault(user_id, asyncio.Lock())
    async with lock:
        paths = await runtime_manager.ensure_user_dirs(user_id)
        await runtime_manager.stop_worker(user_id)
        try:
            stats = await asyncio.to_thread(savepoint_store.create, paths, savepoint_id)
        except SavepointError as exc:
            await asyncio.to_thread(savepoint_store.delete, paths, savepoint_id)
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            logger.exception("Savepoint creation failed for user %s", user_id, exc_info=exc)
            await asyncio.to_thread(savepoint_store.delete, paths, savepoint_id)
            raise HTTPException(500, "保存点创建失败") from exc
        current = now_ms()
        database.execute(
            """INSERT INTO savepoints
            (id, user_id, name, file_count, logical_bytes, stored_bytes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                savepoint_id,
                user_id,
                name,
                stats["file_count"],
                stats["logical_bytes"],
                stats["stored_bytes"],
                current,
            ),
        )
    row = database.one("SELECT * FROM savepoints WHERE id = ?", (savepoint_id,)) or {}
    return ok({"savepoint": public_savepoint(row)})


@app.post("/api/savepoints/{savepoint_id}/restore")
async def restore_savepoint(savepoint_id: str, user: dict[str, Any] = Depends(current_vip_user)):
    user_id = str(user["id"])
    row = database.one("SELECT * FROM savepoints WHERE id = ? AND user_id = ?", (savepoint_id, user_id))
    if not row:
        raise HTTPException(404, "保存点不存在")
    if user_id in dispatcher.busy_user_ids():
        raise HTTPException(409, "Hermes 正在执行任务，请等待任务结束后再恢复")
    lock = savepoint_locks.setdefault(user_id, asyncio.Lock())
    async with lock:
        paths = await runtime_manager.ensure_user_dirs(user_id)
        await runtime_manager.stop_worker(user_id)
        try:
            await asyncio.to_thread(savepoint_store.restore, paths, savepoint_id)
            await runtime_manager.ensure_user_dirs(user_id)
        except SavepointError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(500, "保存点恢复失败") from exc
    return ok({"savepoint": public_savepoint(row)})


@app.delete("/api/savepoints/{savepoint_id}")
async def delete_savepoint(savepoint_id: str, user: dict[str, Any] = Depends(current_vip_user)):
    user_id = str(user["id"])
    row = database.one("SELECT id FROM savepoints WHERE id = ? AND user_id = ?", (savepoint_id, user_id))
    if not row:
        raise HTTPException(404, "保存点不存在")
    lock = savepoint_locks.setdefault(user_id, asyncio.Lock())
    async with lock:
        paths = runtime_manager.user_paths(user_id)
        try:
            await asyncio.to_thread(savepoint_store.delete, paths, savepoint_id)
        except Exception as exc:
            raise HTTPException(500, "保存点文件清理失败") from exc
        database.execute("DELETE FROM savepoints WHERE id = ? AND user_id = ?", (savepoint_id, user_id))
    return ok()


@app.post("/api/terminal/ticket")
async def terminal_ticket(user: dict[str, Any] = Depends(current_vip_user)):
    user_id = str(user["id"])
    await runtime_manager.ensure_worker(user_id, dispatcher.busy_user_ids())
    ticket = issue_runtime_scope(settings.internal_hermes_key, user_id, "terminal", lifetime_seconds=120)
    return ok({"ticket": ticket, "expiresIn": 120})


@app.websocket("/api/v1/terminal/ws")
@app.websocket("/api/terminal/ws")
async def terminal_websocket(websocket: WebSocket, ticket: str = Query(default="")):
    try:
        user_id = verify_runtime_scope(settings.internal_hermes_key, ticket, "terminal")
        user = database.get_user_by_id(user_id)
        if not user or user.get("status") != "active" or user.get("access_tier") != "vip":
            raise ValueError("inactive user")
        await runtime_manager.ensure_worker(user_id, dispatcher.busy_user_ids())
        exec_id, terminal_stream = await asyncio.to_thread(runtime_manager.open_terminal, user_id)
    except (ValueError, RuntimeUnavailable):
        await websocket.close(code=4401)
        return

    await websocket.accept()

    async def client_to_terminal() -> None:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
            if message.get("bytes") is not None:
                await asyncio.to_thread(write_terminal_stream, terminal_stream, message["bytes"])
                continue
            text = message.get("text") or ""
            try:
                command = json.loads(text)
            except json.JSONDecodeError:
                await asyncio.to_thread(write_terminal_stream, terminal_stream, text.encode())
                continue
            if command.get("type") == "resize":
                columns = max(10, min(int(command.get("cols") or 100), 500))
                rows = max(2, min(int(command.get("rows") or 28), 300))
                await asyncio.to_thread(runtime_manager.resize_terminal, exec_id, columns, rows)

    async def terminal_to_client() -> None:
        while True:
            content = await asyncio.to_thread(terminal_stream.read, 16_384)
            if not content:
                return
            await websocket.send_bytes(content)

    try:
        tasks = {asyncio.create_task(client_to_terminal()), asyncio.create_task(terminal_to_client())}
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*done, *pending, return_exceptions=True)
    except (WebSocketDisconnect, OSError, RuntimeError):
        pass
    finally:
        await asyncio.to_thread(terminal_stream.close)
        try:
            await websocket.close()
        except RuntimeError:
            pass


@app.get("/api/ports")
async def list_preview_ports(request: Request, user: dict[str, Any] = Depends(current_vip_user)):
    user_id = str(user["id"])
    await runtime_manager.ensure_worker(user_id, dispatcher.busy_user_ids())
    detected = set(await runtime_manager.list_ports(user_id))
    configured = set(database.configured_ports(user_id))
    ports = sorted(detected | configured)
    prefix = str(request.base_url).rstrip("/")
    username = str(user.get("username") or "")
    return ok(
        {
            "ports": [
                {"port": port, "url": f"{prefix}/{username}/{port}/", "listening": port in detected, "configured": port in configured}
                for port in ports
            ]
        }
    )


@app.post("/api/ports/open")
async def open_preview_port(
    payload: PortOpenRequest,
    request: Request,
    user: dict[str, Any] = Depends(current_vip_user),
):
    if payload.port == 8642:
        raise HTTPException(400, "该端口为内部服务保留")
    user_id = str(user["id"])
    await runtime_manager.ensure_worker(user_id, dispatcher.busy_user_ids())
    database.add_configured_port(user_id, payload.port)
    detected = set(await runtime_manager.list_ports(user_id))
    prefix = str(request.base_url).rstrip("/")
    return ok({
        "port": {
            "port": payload.port,
            "url": f"{prefix}/{user['username']}/{payload.port}/",
            "listening": payload.port in detected,
            "configured": True,
        }
    })


@app.delete("/api/ports/{port}")
def remove_preview_port(port: int, user: dict[str, Any] = Depends(current_vip_user)):
    if port < 1024 or port > 65535 or port == 8642:
        raise HTTPException(400, "端口无效")
    database.remove_configured_port(str(user["id"]), port)
    return ok()


@app.get("/downloads/AIchatMUMU-arm64.apk", include_in_schema=False)
def download_android_app():
    target = ANDROID_APK_PATH if ANDROID_APK_PATH.is_file() else ANDROID_LEGACY_APK_PATH
    if not target.is_file():
        raise HTTPException(404, "Android 安装包尚未发布")
    return FileResponse(
        target,
        filename="MiaoxiangZhiDi-arm64.apk",
        media_type="application/vnd.android.package-archive",
        headers={"Cache-Control": "public, max-age=300"},
    )


@app.get("/downloads/MiaoxiangComputerAgent-x64.exe", include_in_schema=False)
def download_windows_agent():
    target = WINDOWS_AGENT_PATH
    if not target.is_file():
        raise HTTPException(404, "Windows 客户端尚未发布")
    return FileResponse(
        target,
        filename="MiaoxiangComputerAgent-x64.exe",
        media_type="application/vnd.microsoft.portable-executable",
        headers={"Cache-Control": "public, max-age=300", "X-Content-Type-Options": "nosniff"},
    )


def _preview_user(username: str, port: int) -> dict[str, Any]:
    try:
        normalized = normalize_username(username)
    except ValueError as exc:
        raise HTTPException(404, "预览不存在") from exc
    if port < 1024 or port > 65535 or port == 8642:
        raise HTTPException(404, "预览不存在")
    user = database.get_user_by_username(normalized)
    if not user or user.get("status") != "active":
        raise HTTPException(404, "预览不存在")
    return user


async def _preview_http(request: Request, username: str, port: int, preview_path: str):
    user = _preview_user(username, port)
    user_id = str(user["id"])
    worker = await runtime_manager.ensure_worker(user_id, dispatcher.busy_user_ids())
    target_path = "/" + preview_path.lstrip("/")
    query = request.url.query
    target_url = f"http://{worker}:{port}{target_path}{'?' + query if query else ''}"
    forwarded_headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in {
            "authorization", "cookie", "host", "connection", "upgrade", "proxy-authorization",
            "proxy-authenticate", "keep-alive", "te", "trailer", "transfer-encoding",
        }
    }
    forwarded_headers["x-forwarded-prefix"] = f"/{username}/{port}"
    forwarded_headers["x-forwarded-host"] = request.headers.get("host", "")
    forwarded_headers["x-forwarded-proto"] = request.url.scheme
    body = await request.body()
    client = httpx.AsyncClient(timeout=httpx.Timeout(30, read=None), trust_env=False, follow_redirects=False)
    try:
        upstream_request = client.build_request(request.method, target_url, headers=forwarded_headers, content=body)
        upstream = await client.send(upstream_request, stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        raise HTTPException(502, f"预览端口 {port} 暂时不可用") from exc

    response_headers = {
        key: value
        for key, value in upstream.headers.items()
        if key.lower() not in {
            "connection", "content-length", "content-encoding", "keep-alive", "proxy-authenticate",
            "proxy-authorization", "te", "trailer", "transfer-encoding", "upgrade",
            "x-frame-options", "content-security-policy",
        }
    }
    location = response_headers.get("location")
    internal_origin = f"http://{worker}:{port}"
    if location and location.startswith(internal_origin):
        response_headers["location"] = f"/{username}/{port}{location[len(internal_origin):]}"
    elif location and location.startswith("/") and not location.startswith("//"):
        response_headers["location"] = f"/{username}/{port}{location}"

    content_type = str(upstream.headers.get("content-type") or "").lower()
    content_length = int(upstream.headers.get("content-length") or 0)
    if ("text/html" in content_type or "text/css" in content_type) and content_length <= 8 * 1024 * 1024:
        content = await upstream.aread()
        await upstream.aclose()
        await client.aclose()
        if len(content) > 8 * 1024 * 1024:
            return Response(content=content, status_code=upstream.status_code, headers=response_headers)
        prefix = f"/{username}/{port}"
        text = content.decode("utf-8", errors="replace")
        if "text/html" in content_type:
            text = re.sub(
                r"(?i)(\b(?:src|href|action)\s*=\s*['\"])/(?!/)",
                lambda match: f"{match.group(1)}{prefix}/",
                text,
            )
        else:
            text = re.sub(
                r"(?i)(url\(\s*['\"]?)/(?!/)",
                lambda match: f"{match.group(1)}{prefix}/",
                text,
            )
        return Response(content=text.encode("utf-8"), status_code=upstream.status_code, headers=response_headers)

    async def relay():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(
        relay(),
        status_code=upstream.status_code,
        headers=response_headers,
        media_type=None,
    )


@app.api_route(
    "/{username}/{port:int}",
    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    include_in_schema=False,
)
async def preview_root(request: Request, username: str, port: int):
    return await _preview_http(request, username, port, "")


@app.api_route(
    "/{username}/{port:int}/{preview_path:path}",
    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    include_in_schema=False,
)
async def preview_path(request: Request, username: str, port: int, preview_path: str):
    return await _preview_http(request, username, port, preview_path)


@app.websocket("/{username}/{port:int}")
@app.websocket("/{username}/{port:int}/{preview_path:path}")
async def preview_websocket(
    websocket: WebSocket,
    username: str,
    port: int,
    preview_path: str = "",
):
    try:
        user = _preview_user(username, port)
        worker = await runtime_manager.ensure_worker(str(user["id"]), dispatcher.busy_user_ids())
    except (HTTPException, RuntimeUnavailable):
        await websocket.close(code=4404)
        return
    query = websocket.url.query
    upstream_path = "/" + preview_path.lstrip("/")
    upstream_url = f"ws://{worker}:{port}{upstream_path}{'?' + query if query else ''}"
    requested_protocols = [value.strip() for value in websocket.headers.get("sec-websocket-protocol", "").split(",") if value.strip()]
    try:
        async with websockets.connect(
            upstream_url,
            subprotocols=requested_protocols or None,
            max_size=None,
            open_timeout=20,
            proxy=None,
        ) as upstream:
            await websocket.accept(subprotocol=upstream.subprotocol)

            async def client_to_upstream():
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        return
                    if message.get("bytes") is not None:
                        await upstream.send(message["bytes"])
                    elif message.get("text") is not None:
                        await upstream.send(message["text"])

            async def upstream_to_client():
                async for message in upstream:
                    if isinstance(message, bytes):
                        await websocket.send_bytes(message)
                    else:
                        await websocket.send_text(message)

            tasks = {asyncio.create_task(client_to_upstream()), asyncio.create_task(upstream_to_client())}
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*done, *pending, return_exceptions=True)
    except (WebSocketDisconnect, websockets.WebSocketException, OSError):
        try:
            await websocket.close(code=1011)
        except RuntimeError:
            pass
