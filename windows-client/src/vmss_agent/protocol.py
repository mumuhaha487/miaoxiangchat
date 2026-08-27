from __future__ import annotations

import asyncio
import json
import platform
import random
import socket
import ssl
import time
import uuid
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
import websockets
from websockets.exceptions import ConnectionClosed

from .config import AgentConfig
from .credentials import protect_credential, unprotect_credential
from .runner import TaskRunner


AGENT_VERSION = "0.6.2"
StatusCallback = Callable[[str], None]
TargetSupplier = Callable[[], list[dict[str, Any]]]


def _data(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise RuntimeError("服务器返回的数据格式无效")
    value = payload.get("data")
    if payload.get("success") is False:
        raise RuntimeError(str(payload.get("message") or "请求失败"))
    return value if isinstance(value, dict) else payload


def _endpoint(config: AgentConfig, path: str) -> str:
    base = config.server_url.rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("服务地址必须是有效的 HTTP(S) 地址")
    return base + path


def _post_data(
    config: AgentConfig,
    path: str,
    payload: dict[str, Any],
    *,
    token: str = "",
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}"} if token else None
    with httpx.Client(timeout=20, follow_redirects=False) as client:
        response = client.post(_endpoint(config, path), json=payload, headers=headers)
    if response.status_code >= 400:
        try:
            body = response.json()
            message = body.get("detail") or body.get("message")
        except Exception:
            message = response.text
        raise RuntimeError(str(message or f"请求失败 ({response.status_code})")[:500])
    return _data(response.json())


def _get_data(config: AgentConfig, path: str, *, token: str) -> dict[str, Any]:
    with httpx.Client(timeout=20, follow_redirects=False) as client:
        response = client.get(_endpoint(config, path), headers={"Authorization": f"Bearer {token}"})
    if response.status_code >= 400:
        try:
            body = response.json()
            message = body.get("detail") or body.get("message")
        except Exception:
            message = response.text
        raise RuntimeError(str(message or f"请求失败 ({response.status_code})")[:500])
    return _data(response.json())


def _trust_token(config: AgentConfig) -> str:
    if not config.trust_token_ciphertext:
        return ""
    try:
        return unprotect_credential(config.trust_token_ciphertext)
    except Exception:
        return ""


def _login_payload(config: AgentConfig, identifier: str, password: str) -> dict[str, Any]:
    return {
        "identifier": str(identifier or "").strip()[:254],
        "password": str(password or ""),
        "purpose": "login",
        "device_id": config.installation_id,
        "device_name": config.device_name,
        "client_platform": "windows",
        "trust_token": _trust_token(config),
    }


def _register_account_device(config: AgentConfig, token: str) -> dict[str, Any]:
    return _post_data(
        config,
        "/api/v1/control/devices/register",
        {
            "installation_id": config.installation_id,
            "hostname": socket.gethostname(),
            "name": config.device_name,
            "agent_version": AGENT_VERSION,
        },
        token=token,
    )


def _complete_account_login(
    config: AgentConfig,
    identifier: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    token = str(result.get("token") or "")
    if not token:
        raise RuntimeError("服务器没有返回账号会话")
    registered = _register_account_device(config, token)
    credential = str(registered.get("credential") or "")
    if not credential:
        raise RuntimeError("服务器没有返回电脑设备凭据")
    account_token_ciphertext = protect_credential(token)
    credential_ciphertext = protect_credential(credential)
    trust_credential = str(result.get("deviceCredential") or "")
    trust_token_ciphertext = (
        protect_credential(trust_credential) if trust_credential else config.trust_token_ciphertext
    )
    config.account_identifier = str(identifier or "").strip()[:254]
    config.account_token_ciphertext = account_token_ciphertext
    config.trust_token_ciphertext = trust_token_ciphertext
    config.credential_ciphertext = credential_ciphertext
    device = registered.get("device") if isinstance(registered.get("device"), dict) else {}
    if device.get("name"):
        config.device_name = str(device["name"])[:120]
    config.save()
    return {**result, "device": device}


def bind_account_token(config: AgentConfig, token: str) -> dict[str, Any]:
    clean_token = str(token or "").strip()
    if not clean_token:
        raise RuntimeError("账号会话无效")
    profile = _get_data(config, "/api/v1/auth/me", token=clean_token)
    user = profile.get("user") if isinstance(profile.get("user"), dict) else {}
    if user.get("role") != "user" or user.get("status") != "active":
        raise RuntimeError("只有正常用户账号可以绑定电脑")
    registered = _register_account_device(config, clean_token)
    credential = str(registered.get("credential") or "")
    if not credential:
        raise RuntimeError("服务器没有返回电脑设备凭据")
    config.account_identifier = str(user.get("username") or user.get("email") or "")[:254]
    config.account_token_ciphertext = protect_credential(clean_token)
    config.credential_ciphertext = protect_credential(credential)
    device = registered.get("device") if isinstance(registered.get("device"), dict) else {}
    if device.get("name"):
        config.device_name = str(device["name"])[:120]
    config.save()
    return {"user": user, "device": device}


def clear_account_binding(config: AgentConfig) -> None:
    config.account_identifier = ""
    config.account_token_ciphertext = ""
    config.credential_ciphertext = ""
    config.trust_token_ciphertext = ""
    config.save()


def request_account_login(config: AgentConfig, identifier: str, password: str) -> dict[str, Any]:
    payload = _login_payload(config, identifier, password)
    result = _post_data(config, "/api/v1/auth/request-code", payload)
    if result.get("token"):
        return _complete_account_login(config, identifier, result)
    return result


def verify_account_login(config: AgentConfig, identifier: str, password: str, code: str) -> dict[str, Any]:
    payload = {
        **_login_payload(config, identifier, password),
        "code": str(code or "").strip(),
        "trust_device": True,
    }
    result = _post_data(config, "/api/v1/auth/verify", payload)
    return _complete_account_login(config, identifier, result)


def websocket_url(server_url: str) -> str:
    parsed = urlsplit(server_url.rstrip("/"))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("服务地址必须是有效的 HTTP(S) 地址")
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunsplit((scheme, parsed.netloc, "/api/v1/control/agent/ws", "", ""))


class AgentConnection:
    def __init__(
        self,
        config: AgentConfig,
        runner: TaskRunner,
        target_supplier: TargetSupplier,
        status: StatusCallback | None = None,
    ):
        self.config = config
        self.runner = runner
        self.target_supplier = target_supplier
        self.status = status or (lambda _value: None)
        self._stop = asyncio.Event()
        self._socket: Any = None
        self._send_lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._task_cancel: asyncio.Event | None = None
        self._task_id = ""
        self._lease_id = ""
        self._pending_task: tuple[dict[str, Any], str, str] | None = None
        self._approvals: dict[str, tuple[str, str, asyncio.Future[bool]]] = {}

    @property
    def active_task_id(self) -> str:
        return self._task_id

    async def stop(self) -> None:
        self._stop.set()
        if self._task_cancel:
            self._task_cancel.set()
        if self._task:
            self._task.cancel()
        socket_value = self._socket
        if socket_value:
            await socket_value.close(code=1000, reason="客户端退出")

    async def run_forever(self) -> None:
        credential = unprotect_credential(self.config.credential_ciphertext)
        backoff = 1.0
        while not self._stop.is_set():
            try:
                await self._connected_session(credential)
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self._stop.is_set():
                    break
                self.status(f"连接断开：{str(exc)[:180]}")
            if self._stop.is_set():
                break
            delay = min(30.0, backoff) + random.random()
            await asyncio.sleep(delay)
            backoff = min(30.0, backoff * 2)
        self.status("已停止")

    async def _connected_session(self, credential: str) -> None:
        uri = websocket_url(self.config.server_url)
        ssl_context = ssl.create_default_context() if uri.startswith("wss:") else None
        self.status("正在连接…")
        async with websockets.connect(
            uri,
            additional_headers={"Authorization": f"Bearer {credential}"},
            ssl=ssl_context,
            open_timeout=20,
            close_timeout=5,
            ping_interval=25,
            ping_timeout=20,
            max_size=4 * 1024 * 1024,
            compression=None,
        ) as websocket:
            self._socket = websocket
            targets = await asyncio.to_thread(self.target_supplier)
            await self._send(
                {
                    "type": "hello",
                    "hostname": socket.gethostname(),
                    "agentVersion": AGENT_VERSION,
                    "capabilities": [
                        "windows.capture",
                        "windows.uia",
                        "ocr.rapidocr",
                        "input.normalized",
                        "approval.action_hash",
                        "adb.fixed_commands",
                    ],
                    "targets": targets,
                    "platform": platform.platform()[:200],
                }
            )
            self.status("在线")
            heartbeat = asyncio.create_task(self._heartbeat())
            try:
                async for raw in websocket:
                    if not isinstance(raw, str):
                        continue
                    await self._handle(json.loads(raw), credential)
            finally:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
                if self._task_cancel:
                    self._task_cancel.set()
                if self._task:
                    self._task.cancel()
                    await asyncio.gather(self._task, return_exceptions=True)
                self._clear_task()
                self._socket = None

    async def _heartbeat(self) -> None:
        while True:
            await asyncio.sleep(20)
            await self._send(
                {
                    "type": "heartbeat",
                    "runningTaskIds": [self._task_id] if self._task_id else [],
                    "time": int(time.time() * 1000),
                }
            )

    async def _handle(self, message: dict[str, Any], credential: str) -> None:
        if not isinstance(message, dict):
            return
        kind = str(message.get("type") or "")
        if kind in {"hello.request", "hello.ack", "heartbeat.ack"}:
            return
        if kind == "event.ack":
            if (
                self._pending_task
                and not self._task
                and str(message.get("taskId") or "") == self._task_id
            ):
                task, lease_id, pending_credential = self._pending_task
                self._pending_task = None
                self._task = asyncio.create_task(self._run_task(task, lease_id, pending_credential))
            return
        if kind == "protocol.error":
            self.status("协议错误：" + str(message.get("message") or "未知错误")[:160])
            if self._pending_task and not self._task:
                self._clear_task()
                await self._send({"type": "task.ready"})
            return
        if kind == "task.assign":
            task = message.get("task") if isinstance(message.get("task"), dict) else {}
            lease_id = str(message.get("leaseId") or "")
            task_id = str(task.get("id") or "")
            if self._task_id or (self._task and not self._task.done()):
                return
            if not task_id or len(lease_id) < 16:
                return
            self._task_id, self._lease_id = task_id, lease_id
            self._task_cancel = asyncio.Event()
            await self._send(
                {
                    "type": "task.accepted",
                    "taskId": task_id,
                    "leaseId": lease_id,
                    "sequence": 1,
                    "clientEventId": f"accepted-{lease_id}",
                }
            )
            self.status(f"正在执行：{str(task.get('instruction') or '')[:80]}")
            self._pending_task = (task, lease_id, credential)
            return
        if kind == "task.cancel":
            if str(message.get("taskId") or "") == self._task_id and str(message.get("leaseId") or "") == self._lease_id:
                if self._task_cancel:
                    self._task_cancel.set()
            return
        if kind == "approval.response":
            task_id = str(message.get("taskId") or "")
            lease_id = str(message.get("leaseId") or "")
            action_hash = str(message.get("actionHash") or "")
            pending = self._approvals.get(task_id)
            if pending and pending[0] == lease_id and pending[1] == action_hash and not pending[2].done():
                pending[2].set_result(message.get("decision") == "approve")

    async def _run_task(self, task: dict[str, Any], lease_id: str, credential: str) -> None:
        task_id = str(task.get("id") or "")
        cancel = self._task_cancel or asyncio.Event()
        try:
            await self.runner.run(task, lease_id, credential, self, cancel)
        except asyncio.CancelledError:
            raise
        finally:
            self._approvals.pop(task_id, None)
            self._clear_task()
            try:
                await self._send({"type": "task.ready"})
                self.status("在线")
            except ConnectionClosed:
                pass

    def _clear_task(self) -> None:
        self._task = None
        self._task_cancel = None
        self._task_id = ""
        self._lease_id = ""
        self._pending_task = None

    async def _send(self, payload: dict[str, Any]) -> None:
        if not self._socket:
            raise ConnectionError("控制连接未建立")
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        async with self._send_lock:
            await self._socket.send(encoded)

    async def send_event(
        self,
        task_id: str,
        lease_id: str,
        sequence: int,
        event_type: str,
        payload: dict[str, Any],
        frame_base64: str = "",
    ) -> None:
        message: dict[str, Any] = {
            "type": "task.event",
            "taskId": task_id,
            "leaseId": lease_id,
            "sequence": sequence,
            "clientEventId": uuid.uuid4().hex,
            "eventType": event_type,
            "payload": payload,
        }
        if frame_base64:
            message["frameBase64"] = frame_base64
        await self._send(message)

    async def wait_for_approval(
        self, task_id: str, lease_id: str, expected_hash: str, cancel: asyncio.Event
    ) -> bool:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[bool] = loop.create_future()
        self._approvals[task_id] = (lease_id, expected_hash, future)
        cancel_wait = asyncio.create_task(cancel.wait())
        try:
            done, _pending = await asyncio.wait({future, cancel_wait}, return_when=asyncio.FIRST_COMPLETED)
            if cancel_wait in done and cancel.is_set():
                return False
            return bool(future.result())
        finally:
            cancel_wait.cancel()
            self._approvals.pop(task_id, None)
