from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import re
import secrets
import shutil
import uuid
from pathlib import Path
from typing import Any

from fastapi import WebSocket

from .database import Database, now_ms


AGENT_LEASE_MS = 90 * 1000
MAX_FRAME_BYTES = 2 * 1024 * 1024
MAX_EVENT_PAYLOAD_BYTES = 64 * 1024
FRAME_RETENTION_MS = 14 * 24 * 60 * 60 * 1000
ACTIVE_TASK_STATUSES = {"queued", "dispatched", "running", "waiting_approval", "stopping"}
TERMINAL_TASK_STATUSES = {"completed", "failed", "cancelled"}
ALLOWED_EVENT_TYPES = {
    "task.started",
    "observation",
    "reasoning",
    "action.started",
    "action.completed",
    "action.failed",
    "approval.required",
    "task.completed",
    "task.failed",
    "task.cancelled",
    "task.stopped",
}
_ACTION_HASH_PATTERN = re.compile(r"^[a-f0-9]{64}$")


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def public_control_device(row: dict[str, Any], *, online: bool) -> dict[str, Any]:
    return {
        "id": str(row.get("id") or ""),
        "name": str(row.get("name") or "Windows 电脑"),
        "hostname": str(row.get("hostname") or ""),
        "platform": str(row.get("platform") or "windows"),
        "online": online and not bool(row.get("revoked_at")),
        "agentVersion": str(row.get("agent_version") or ""),
        "capabilities": [str(item) for item in _json_list(row.get("capabilities_json")) if isinstance(item, str)],
        "targets": [item for item in _json_list(row.get("targets_json")) if isinstance(item, dict)],
        "createdAt": int(row.get("created_at") or 0),
        "lastSeenAt": int(row.get("last_seen_at") or 0),
    }


def public_control_task(row: dict[str, Any]) -> dict[str, Any]:
    status = str(row.get("status") or "queued")
    return {
        "id": str(row.get("id") or ""),
        "conversationId": str(row.get("conversation_id") or "") or None,
        "deviceId": str(row.get("device_id") or ""),
        "deviceName": str(row.get("device_name") or ""),
        "targetId": str(row.get("target_id") or "desktop"),
        "targetKind": str(row.get("target_kind") or "windows"),
        "instruction": str(row.get("instruction") or ""),
        "status": "assigned" if status == "dispatched" else status,
        "output": str(row.get("output") or ""),
        "error": str(row.get("error") or ""),
        "createdAt": int(row.get("created_at") or 0),
        "updatedAt": int(row.get("updated_at") or 0),
        "startedAt": int(row["started_at"]) if row.get("started_at") is not None else None,
        "completedAt": int(row["completed_at"]) if row.get("completed_at") is not None else None,
    }


def public_control_event(row: dict[str, Any]) -> dict[str, Any]:
    frame_id = str(row.get("frame_id") or "")
    return {
        "id": int(row.get("id") or 0),
        "type": str(row.get("event_type") or "event"),
        "payload": _json_object(row.get("payload_json")),
        "frameId": frame_id or None,
        "frameUrl": f"/api/v1/control/frames/{frame_id}" if frame_id else None,
        "createdAt": int(row.get("created_at") or 0),
    }


class ComputerControlStore:
    def __init__(self, database: Database, app_secret: str, data_dir: Path):
        self.database = database
        self._secret = app_secret.encode("utf-8")
        self.frame_root = data_dir / "control-use"
        self.frame_root.mkdir(parents=True, exist_ok=True)

    def _digest(self, namespace: str, value: str) -> str:
        return hmac.new(self._secret, f"{namespace}:{value}".encode("utf-8"), hashlib.sha256).hexdigest()

    def credential_hash(self, device_id: str, secret: str) -> str:
        return self._digest("control-device", f"{device_id}:{secret}")

    def register_account_device(
        self,
        *,
        user_id: str,
        installation_id: str,
        hostname: str,
        name: str,
        agent_version: str,
    ) -> dict[str, Any] | None:
        installation = str(installation_id or "").strip()[:200]
        if len(installation) < 12:
            return None
        clean_hostname = re.sub(r"[\x00-\x1f\x7f]", "", str(hostname or "")).strip()[:120] or "Windows-PC"
        clean_name = re.sub(r"[\x00-\x1f\x7f]", "", str(name or "")).strip()[:120] or clean_hostname
        current = now_ms()
        installation_hash = self._digest("control-installation", installation)
        credential_secret = secrets.token_urlsafe(48)
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM control_devices WHERE user_id = ? AND installation_hash = ?",
                (user_id, installation_hash),
            ).fetchone()
            device_id = str(existing["id"]) if existing else str(uuid.uuid4())
            digest = self.credential_hash(device_id, credential_secret)
            if existing:
                connection.execute(
                    """UPDATE control_devices SET credential_hash = ?, name = ?, hostname = ?,
                    platform = 'windows', agent_version = ?, updated_at = ?, last_seen_at = ?, revoked_at = NULL
                    WHERE id = ?""",
                    (digest, clean_name, clean_hostname, str(agent_version or "")[:40], current, current, device_id),
                )
            else:
                connection.execute(
                    """INSERT INTO control_devices
                    (id, user_id, installation_hash, credential_hash, name, hostname, platform,
                    agent_version, capabilities_json, targets_json, created_at, updated_at, last_seen_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'windows', ?, '[]', '[]', ?, ?, ?)""",
                    (
                        device_id,
                        user_id,
                        installation_hash,
                        digest,
                        clean_name,
                        clean_hostname,
                        str(agent_version or "")[:40],
                        current,
                        current,
                        current,
                    ),
                )
            row = connection.execute("SELECT * FROM control_devices WHERE id = ?", (device_id,)).fetchone()
        return {
            "device": dict(row) if row else {},
            "credential": f"{device_id}.{credential_secret}",
        }

    def authenticate_credential(self, credential: str) -> dict[str, Any] | None:
        raw = str(credential or "").strip()
        device_id, separator, secret = raw.partition(".")
        if not separator or len(device_id) > 64 or len(secret) < 32:
            return None
        row = self.database.one(
            "SELECT * FROM control_devices WHERE id = ? AND revoked_at IS NULL",
            (device_id,),
        )
        if not row:
            return None
        expected = self.credential_hash(device_id, secret)
        return row if hmac.compare_digest(expected, str(row.get("credential_hash") or "")) else None

    def list_devices(self, user_id: str) -> list[dict[str, Any]]:
        return self.database.all(
            """SELECT * FROM control_devices WHERE user_id = ? AND revoked_at IS NULL
            ORDER BY last_seen_at DESC, created_at DESC""",
            (user_id,),
        )

    def get_device(self, device_id: str, user_id: str | None = None) -> dict[str, Any] | None:
        if user_id is None:
            return self.database.one("SELECT * FROM control_devices WHERE id = ? AND revoked_at IS NULL", (device_id,))
        return self.database.one(
            "SELECT * FROM control_devices WHERE id = ? AND user_id = ? AND revoked_at IS NULL",
            (device_id, user_id),
        )

    def rename_device(self, device_id: str, user_id: str, name: str) -> dict[str, Any] | None:
        clean = re.sub(r"[\x00-\x1f\x7f]", "", str(name or "")).strip()[:120]
        if not clean:
            return None
        changed = self.database.execute(
            "UPDATE control_devices SET name = ?, updated_at = ? WHERE id = ? AND user_id = ? AND revoked_at IS NULL",
            (clean, now_ms(), device_id, user_id),
        )
        return self.get_device(device_id, user_id) if changed else None

    def revoke_device(self, device_id: str, user_id: str) -> bool:
        current = now_ms()
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                """UPDATE control_devices SET revoked_at = ?, updated_at = ?
                WHERE id = ? AND user_id = ? AND revoked_at IS NULL""",
                (current, current, device_id, user_id),
            )
            if changed.rowcount != 1:
                return False
            connection.execute(
                """UPDATE control_tasks SET status = 'cancelled', error = '设备已撤销',
                approval_hash = '', updated_at = ?, completed_at = ? WHERE device_id = ?
                AND status IN ('queued','dispatched','running','waiting_approval','stopping')""",
                (current, current, device_id),
            )
            connection.execute(
                """UPDATE conversations SET control_device_id = '', control_target_id = '',
                control_target_kind = '', updated_at = ? WHERE user_id = ? AND control_device_id = ?""",
                (current, user_id, device_id),
            )
        return True

    def update_hello(
        self,
        device_id: str,
        *,
        hostname: str,
        agent_version: str,
        capabilities: list[Any],
        targets: list[Any],
    ) -> dict[str, Any] | None:
        clean_capabilities = [str(item)[:80] for item in capabilities[:50] if isinstance(item, str)]
        clean_targets: list[dict[str, Any]] = []
        for item in targets[:30]:
            if not isinstance(item, dict):
                continue
            target_id = str(item.get("id") or "")[:200]
            kind = str(item.get("kind") or "")[:20]
            if not target_id or kind not in {"windows", "adb"}:
                continue
            clean_targets.append(
                {
                    "id": target_id,
                    "kind": kind,
                    "name": str(item.get("name") or target_id)[:120],
                    "serial": str(item.get("serial") or "")[:200] or None,
                    "state": str(item.get("state") or "device")[:30],
                }
            )
        if not any(item["id"] == "desktop" for item in clean_targets):
            clean_targets.insert(0, {"id": "desktop", "kind": "windows", "name": "Windows 桌面", "serial": None, "state": "device"})
        current = now_ms()
        changed = self.database.execute(
            """UPDATE control_devices SET hostname = ?, agent_version = ?, capabilities_json = ?,
            targets_json = ?, updated_at = ?, last_seen_at = ? WHERE id = ? AND revoked_at IS NULL""",
            (
                str(hostname or "")[:120], str(agent_version or "")[:40],
                json.dumps(clean_capabilities, ensure_ascii=False), json.dumps(clean_targets, ensure_ascii=False),
                current, current, device_id,
            ),
        )
        return self.get_device(device_id) if changed else None

    def touch_device(self, device_id: str) -> None:
        current = now_ms()
        self.database.execute(
            "UPDATE control_devices SET last_seen_at = ?, updated_at = ? WHERE id = ? AND revoked_at IS NULL",
            (current, current, device_id),
        )

    @staticmethod
    def resolve_target_kind(device: dict[str, Any], target_id: str) -> str:
        requested_target = str(target_id or "desktop")[:200]
        if requested_target == "desktop":
            return "windows"
        target = next(
            (
                item for item in _json_list(device.get("targets_json"))
                if isinstance(item, dict) and str(item.get("id")) == requested_target
            ),
            None,
        )
        if not target or target.get("kind") not in {"windows", "adb"} or target.get("state") not in {None, "device"}:
            raise ValueError("目标设备当前不可用")
        return str(target["kind"])

    def create_task(
        self,
        user_id: str,
        device_id: str,
        target_id: str,
        instruction: str,
        conversation_id: str | None = None,
    ) -> dict[str, Any] | None:
        device = self.get_device(device_id, user_id)
        if not device:
            return None
        clean_instruction = str(instruction or "").strip()
        if not clean_instruction:
            raise ValueError("任务内容不能为空")
        requested_target = str(target_id or "desktop")[:200]
        target_kind = self.resolve_target_kind(device, requested_target)
        current = now_ms()
        task_id = str(uuid.uuid4())
        self.database.execute(
            """INSERT INTO control_tasks
            (id, user_id, device_id, conversation_id, target_id, target_kind, instruction, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)""",
            (
                task_id, user_id, device_id, conversation_id, requested_target, target_kind,
                clean_instruction[:8000], current, current,
            ),
        )
        return self.get_task(task_id, user_id)

    def get_task(self, task_id: str, user_id: str | None = None) -> dict[str, Any] | None:
        query = """SELECT t.*, d.name AS device_name FROM control_tasks t
        JOIN control_devices d ON d.id = t.device_id WHERE t.id = ?"""
        parameters: tuple[Any, ...] = (task_id,)
        if user_id is not None:
            query += " AND t.user_id = ?"
            parameters = (task_id, user_id)
        return self.database.one(query, parameters)

    def list_tasks(
        self,
        user_id: str,
        limit: int = 100,
        conversation_id: str | None = None,
    ) -> list[dict[str, Any]]:
        query = """SELECT t.*, d.name AS device_name FROM control_tasks t
        JOIN control_devices d ON d.id = t.device_id WHERE t.user_id = ?"""
        parameters: list[Any] = [user_id]
        if conversation_id is not None:
            query += " AND t.conversation_id = ?"
            parameters.append(conversation_id)
        query += " ORDER BY t.created_at DESC LIMIT ?"
        parameters.append(max(1, min(limit, 200)))
        return self.database.all(query, tuple(parameters))

    def claim_next_task(self, device_id: str) -> dict[str, Any] | None:
        current = now_ms()
        lease_id = secrets.token_urlsafe(24)
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            active = connection.execute(
                """SELECT 1 FROM control_tasks WHERE device_id = ?
                AND status IN ('dispatched','running','waiting_approval','stopping') LIMIT 1""",
                (device_id,),
            ).fetchone()
            if active:
                return None
            row = connection.execute(
                """SELECT id FROM control_tasks WHERE device_id = ? AND status = 'queued'
                ORDER BY created_at ASC LIMIT 1""",
                (device_id,),
            ).fetchone()
            if not row:
                return None
            changed = connection.execute(
                """UPDATE control_tasks SET status = 'dispatched', lease_id = ?, lease_expires_at = ?,
                updated_at = ? WHERE id = ? AND status = 'queued'""",
                (lease_id, current + AGENT_LEASE_MS, current, row["id"]),
            )
            if changed.rowcount != 1:
                return None
        task = self.get_task(str(row["id"]))
        if task:
            task["lease_id"] = lease_id
        return task

    def accept_task(self, device_id: str, task_id: str, lease_id: str) -> dict[str, Any] | None:
        current = now_ms()
        changed = self.database.execute(
            """UPDATE control_tasks SET status = 'running', started_at = COALESCE(started_at, ?),
            updated_at = ?, lease_expires_at = ? WHERE id = ? AND device_id = ? AND lease_id = ?
            AND status IN ('dispatched','running')""",
            (current, current, current + AGENT_LEASE_MS, task_id, device_id, lease_id),
        )
        return self.get_task(task_id) if changed else None

    def active_task_lease(self, device_id: str, task_id: str, lease_id: str) -> dict[str, Any] | None:
        return self.database.one(
            """SELECT * FROM control_tasks WHERE id = ? AND device_id = ? AND lease_id = ?
            AND status IN ('running','waiting_approval','stopping') AND lease_expires_at > ?""",
            (task_id, device_id, lease_id, now_ms()),
        )

    def touch_task_leases(self, device_id: str, task_ids: list[str]) -> None:
        current = now_ms()
        for task_id in task_ids[:5]:
            self.database.execute(
                """UPDATE control_tasks SET lease_expires_at = ?, updated_at = ?
                WHERE id = ? AND device_id = ? AND status IN ('running','waiting_approval','stopping')""",
                (current + AGENT_LEASE_MS, current, str(task_id), device_id),
            )

    def requeue_unaccepted(self, device_id: str) -> None:
        current = now_ms()
        self.database.execute(
            """UPDATE control_tasks SET status = 'queued', lease_id = NULL, lease_expires_at = NULL,
            updated_at = ? WHERE device_id = ? AND status = 'dispatched' AND started_at IS NULL""",
            (current, device_id),
        )

    def expire_stale_tasks(self, device_id: str | None = None) -> list[dict[str, Any]]:
        current = now_ms()
        suffix = " AND device_id = ?" if device_id else ""
        parameters: tuple[Any, ...] = (current, device_id) if device_id else (current,)
        stale = self.database.all(
            """SELECT * FROM control_tasks WHERE lease_expires_at IS NOT NULL AND lease_expires_at < ?
            AND status IN ('dispatched','running','waiting_approval','stopping')""" + suffix,
            parameters,
        )
        for task in stale:
            task_id = str(task["id"])
            if task.get("status") == "dispatched" and task.get("started_at") is None:
                self.database.execute(
                    """UPDATE control_tasks SET status = 'queued', lease_id = NULL, lease_expires_at = NULL,
                    approval_hash = '', updated_at = ? WHERE id = ? AND status = 'dispatched'""",
                    (current, task_id),
                )
                continue
            changed = self.database.execute(
                """UPDATE control_tasks SET status = 'failed', error = '电脑客户端连接超时，为避免重复操作未自动重试',
                approval_hash = '', updated_at = ?, completed_at = ? WHERE id = ?
                AND status IN ('running','waiting_approval','stopping')""",
                (current, current, task_id),
            )
            if changed:
                self.database.create_notification(
                    user_id=str(task["user_id"]),
                    category="task_failed",
                    title="电脑任务连接超时",
                    body="为避免重复执行，任务没有自动重试",
                    entity_type="control_task",
                    entity_id=task_id,
                    dedupe_key=f"control-timeout:{task_id}",
                )
        return stale

    def request_stop(self, task_id: str, user_id: str) -> dict[str, Any] | None:
        task = self.get_task(task_id, user_id)
        if not task:
            return None
        status = str(task.get("status") or "")
        current = now_ms()
        if status == "queued" or (status == "dispatched" and task.get("started_at") is None):
            self.database.execute(
                """UPDATE control_tasks SET status = 'cancelled', error = '用户已取消',
                lease_id = NULL, lease_expires_at = NULL, approval_hash = '', updated_at = ?, completed_at = ?
                WHERE id = ? AND user_id = ? AND status IN ('queued','dispatched') AND started_at IS NULL""",
                (current, current, task_id, user_id),
            )
        elif status in {"dispatched", "running", "waiting_approval"}:
            self.database.execute(
                """UPDATE control_tasks SET status = 'stopping', updated_at = ?
                WHERE id = ? AND user_id = ? AND status IN ('dispatched','running','waiting_approval')""",
                (current, task_id, user_id),
            )
        return self.get_task(task_id, user_id)

    def respond_approval(self, task_id: str, user_id: str, decision: str) -> dict[str, Any] | None:
        task = self.get_task(task_id, user_id)
        approval_hash = str(task.get("approval_hash") or "") if task else ""
        if not task or task.get("status") != "waiting_approval" or not _ACTION_HASH_PATTERN.fullmatch(approval_hash):
            return None
        current = now_ms()
        if decision == "approve":
            self.database.execute(
                "UPDATE control_tasks SET status = 'running', updated_at = ? WHERE id = ? AND user_id = ? AND status = 'waiting_approval'",
                (current, task_id, user_id),
            )
        else:
            self.database.execute(
                """UPDATE control_tasks SET status = 'cancelled', error = '用户拒绝了操作',
                updated_at = ?, completed_at = ? WHERE id = ? AND user_id = ? AND status = 'waiting_approval'""",
                (current, current, task_id, user_id),
            )
        result = self.get_task(task_id, user_id)
        if result:
            result["approval_hash"] = approval_hash
        return result

    def add_event(
        self,
        *,
        device_id: str,
        task_id: str,
        lease_id: str,
        sequence: int,
        client_event_id: str,
        event_type: str,
        payload: dict[str, Any],
        frame_id: str | None = None,
    ) -> dict[str, Any] | None:
        if event_type not in ALLOWED_EVENT_TYPES:
            raise ValueError("不支持的任务事件类型")
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_EVENT_PAYLOAD_BYTES:
            raise ValueError("事件内容过大")
        current = now_ms()
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = connection.execute(
                "SELECT * FROM control_tasks WHERE id = ? AND device_id = ? AND lease_id = ?",
                (task_id, device_id, lease_id),
            ).fetchone()
            if not task or str(task["status"]) in TERMINAL_TASK_STATUSES:
                return None
            if sequence <= int(task["last_seq"] or 0):
                event_key = str(client_event_id or "")[:120]
                if event_key:
                    existing = connection.execute(
                        "SELECT * FROM control_task_events WHERE task_id = ? AND client_event_id = ?",
                        (task_id, event_key),
                    ).fetchone()
                    if existing:
                        return dict(existing)
                raise ValueError("任务事件序号必须严格递增")
            event_key = str(client_event_id or "")[:120] or f"seq-{sequence}-{event_type}"
            inserted = connection.execute(
                """INSERT OR IGNORE INTO control_task_events
                (task_id, user_id, client_event_id, event_type, payload_json, frame_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (task_id, task["user_id"], event_key, str(event_type or "event")[:60], encoded, frame_id, current),
            )
            if inserted.rowcount != 1:
                existing = connection.execute(
                    "SELECT * FROM control_task_events WHERE task_id = ? AND client_event_id = ?",
                    (task_id, event_key),
                ).fetchone()
                return dict(existing) if existing else None
            values: dict[str, Any] = {
                "last_seq": max(int(task["last_seq"] or 0), max(0, sequence)),
                "lease_expires_at": current + AGENT_LEASE_MS,
                "updated_at": current,
            }
            if event_type == "approval.required":
                action_hash = str(payload.get("actionHash") or "").lower()
                if not _ACTION_HASH_PATTERN.fullmatch(action_hash):
                    raise ValueError("批准请求缺少有效的动作哈希")
                values["status"] = "waiting_approval"
                values["approval_hash"] = action_hash
            elif event_type == "task.completed":
                values.update(status="completed", output=str(payload.get("output") or "")[:100_000], error="", approval_hash="", completed_at=current)
            elif event_type == "task.failed":
                values.update(status="failed", error=str(payload.get("error") or "任务执行失败")[:20_000], approval_hash="", completed_at=current)
            elif event_type in {"task.cancelled", "task.stopped"}:
                values.update(status="cancelled", error=str(payload.get("reason") or "任务已停止")[:20_000], approval_hash="", completed_at=current)
            assignments = ", ".join(f"{key} = ?" for key in values)
            connection.execute(
                f"UPDATE control_tasks SET {assignments} WHERE id = ?",
                (*values.values(), task_id),
            )
            row = connection.execute("SELECT * FROM control_task_events WHERE id = ?", (inserted.lastrowid,)).fetchone()
            user_id = str(task["user_id"])
        if event_type == "task.completed":
            if task["conversation_id"]:
                self.database.add_conversation_memory(
                    conversation_id=str(task["conversation_id"]),
                    user_id=user_id,
                    source="remote",
                    source_id=task_id,
                    user_content=str(task["instruction"] or ""),
                    assistant_content=str(payload.get("output") or ""),
                )
            self.database.create_notification(
                user_id=user_id,
                category="agent_completed",
                title="电脑任务已完成",
                body=str(payload.get("output") or "任务执行完成")[:500],
                entity_type="control_task",
                entity_id=task_id,
                dedupe_key=f"control-completed:{task_id}",
            )
        elif event_type == "task.failed":
            self.database.create_notification(
                user_id=user_id,
                category="task_failed",
                title="电脑任务执行失败",
                body=str(payload.get("error") or "任务执行失败")[:500],
                entity_type="control_task",
                entity_id=task_id,
                dedupe_key=f"control-failed:{task_id}",
            )
        elif event_type == "approval.required":
            self.database.create_notification(
                user_id=user_id,
                category="approval_required",
                title="电脑操作等待批准",
                body=str(payload.get("summary") or payload.get("action") or "请确认下一步操作")[:500],
                entity_type="control_task",
                entity_id=task_id,
                dedupe_key=f"control-approval:{task_id}:{sequence}",
            )
        return dict(row) if row else None

    def store_frame(
        self,
        *,
        device_id: str,
        task_id: str,
        lease_id: str,
        sequence: int,
        encoded: str,
    ) -> dict[str, Any]:
        task = self.database.one(
            """SELECT * FROM control_tasks WHERE id = ? AND device_id = ? AND lease_id = ?
            AND status IN ('running','waiting_approval','stopping') AND last_seq < ?""",
            (task_id, device_id, lease_id, max(0, sequence)),
        )
        if not task:
            raise ValueError("任务租约无效")
        try:
            content = base64.b64decode(str(encoded or ""), validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("截图编码无效") from None
        if not content or len(content) > MAX_FRAME_BYTES:
            raise ValueError("截图大小超出限制")
        if content.startswith(b"\xff\xd8\xff"):
            mime_type, extension = "image/jpeg", ".jpg"
        elif content.startswith(b"\x89PNG\r\n\x1a\n"):
            mime_type, extension = "image/png", ".png"
        else:
            raise ValueError("仅支持 JPEG 或 PNG 截图")
        frame_id = str(uuid.uuid4())
        relative = Path(str(task["user_id"])) / task_id / f"{frame_id}{extension}"
        destination = (self.frame_root / relative).resolve()
        if self.frame_root.resolve() not in destination.parents:
            raise ValueError("截图路径无效")
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        destination.write_bytes(content)
        destination.chmod(0o600)
        current = now_ms()
        digest = hashlib.sha256(content).hexdigest()
        try:
            self.database.execute(
                """INSERT INTO control_frames
                (id, task_id, user_id, sequence, relative_path, mime_type, size_bytes, sha256, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    frame_id, task_id, task["user_id"], max(0, sequence), relative.as_posix(),
                    mime_type, len(content), digest, current,
                ),
            )
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        return {
            "id": frame_id,
            "taskId": task_id,
            "sequence": max(0, sequence),
            "mimeType": mime_type,
            "sizeBytes": len(content),
            "sha256": digest,
            "createdAt": current,
        }

    def list_events(self, task_id: str, user_id: str, after: int = 0, limit: int = 300) -> list[dict[str, Any]]:
        if not self.get_task(task_id, user_id):
            return []
        return self.database.all(
            """SELECT * FROM control_task_events WHERE task_id = ? AND user_id = ? AND id > ?
            ORDER BY id ASC LIMIT ?""",
            (task_id, user_id, max(0, after), max(1, min(limit, 500))),
        )

    def get_frame(self, frame_id: str, user_id: str) -> dict[str, Any] | None:
        return self.database.one(
            "SELECT * FROM control_frames WHERE id = ? AND user_id = ?",
            (frame_id, user_id),
        )

    def frame_path(self, row: dict[str, Any]) -> Path | None:
        candidate = (self.frame_root / str(row.get("relative_path") or "")).resolve()
        return candidate if self.frame_root.resolve() in candidate.parents and candidate.is_file() else None

    def remove_user_frames(self, user_id: str) -> bool:
        root = self.frame_root.resolve()
        clean_user_id = str(user_id or "").strip()
        candidate = (root / clean_user_id).resolve()
        if not clean_user_id or candidate.parent != root:
            raise ValueError("用户截图目录无效")
        if not candidate.exists():
            return False
        if not candidate.is_dir():
            raise ValueError("用户截图目录无效")
        shutil.rmtree(candidate)
        return True

    def prune_expired_data(self) -> dict[str, int]:
        current = now_ms()
        frames = self.database.all(
            "SELECT id, relative_path FROM control_frames WHERE created_at < ? ORDER BY created_at ASC LIMIT 5000",
            (current - FRAME_RETENTION_MS,),
        )
        removed = 0
        for frame in frames:
            candidate = (self.frame_root / str(frame.get("relative_path") or "")).resolve()
            if self.frame_root.resolve() not in candidate.parents:
                continue
            try:
                candidate.unlink(missing_ok=True)
            except OSError:
                continue
            removed += self.database.execute("DELETE FROM control_frames WHERE id = ?", (frame["id"],))
            parent = candidate.parent
            for _ in range(2):
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent
        return {"frames": removed}


class ControlAgentHub:
    def __init__(self):
        self._connections: dict[str, WebSocket] = {}
        self._lock = asyncio.Lock()

    def is_online(self, device_id: str) -> bool:
        return device_id in self._connections

    async def attach(self, device_id: str, websocket: WebSocket) -> None:
        previous: WebSocket | None = None
        async with self._lock:
            previous = self._connections.get(device_id)
            self._connections[device_id] = websocket
        if previous and previous is not websocket:
            try:
                await previous.close(code=4001, reason="另一连接已接管此设备")
            except RuntimeError:
                pass

    async def detach(self, device_id: str, websocket: WebSocket) -> bool:
        async with self._lock:
            if self._connections.get(device_id) is websocket:
                self._connections.pop(device_id, None)
                return True
        return False

    async def send(self, device_id: str, payload: dict[str, Any]) -> bool:
        websocket = self._connections.get(device_id)
        if not websocket:
            return False
        try:
            await websocket.send_json(payload)
            return True
        except RuntimeError:
            await self.detach(device_id, websocket)
            return False

    async def revoke(self, device_id: str) -> None:
        websocket = self._connections.get(device_id)
        if not websocket:
            return
        await self.detach(device_id, websocket)
        try:
            await websocket.close(code=4003, reason="设备授权已撤销")
        except RuntimeError:
            pass

    async def close(self) -> None:
        async with self._lock:
            connections = list(self._connections.items())
            self._connections.clear()
        for _device_id, websocket in connections:
            try:
                await websocket.close(code=1001, reason="服务正在重启")
            except RuntimeError:
                pass
