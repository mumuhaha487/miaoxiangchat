from __future__ import annotations

import asyncio
import base64
import hashlib
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import main
from app.capabilities import CapabilityManager
from app.conversation_title import summarize_conversation_title
from app.coordinator import Coordinator
from app.database import Database
from app.model_config import ModelConfigStore
from app.model_gateway import ModelGateway
from app.task_dispatcher import TaskDispatcher


class FakeRuntimeManager:
    def __init__(self, root: Path):
        self.root = root
        self.settings = main.settings

    def user_paths(self, user_id: str) -> dict[str, Path]:
        base = self.root / user_id
        result = {
            "container_root": base,
            "container_hermes": base / "hermes",
            "container_workspace": base / "WORKSPACE",
            "container_attachments": base / "attachments",
            "container_profile": base / "profile",
            "container_skills": base / "hermes" / "skills",
        }
        for path in result.values():
            path.mkdir(parents=True, exist_ok=True)
        return result

    async def ensure_user_dirs(self, user_id: str) -> dict[str, Path]:
        return self.user_paths(user_id)

    async def sync_builtin_skills_for_users(self, user_ids: list[str]) -> int:
        return len(set(user_ids))

    async def ensure_worker(self, _user_id: str, _busy: set[str]) -> str:
        return "fake-worker"

    async def list_ports(self, _user_id: str) -> list[int]:
        return [1443, 5173]

    async def ensure_page(self, _user_id: str, conversation_id: str):
        return {"conversationId": conversation_id, "title": "Browser", "url": "about:blank"}

    async def browser_action(self, _user_id: str, conversation_id: str, _action: str):
        return {"conversationId": conversation_id, "title": "Browser", "url": "about:blank"}

    async def close_page(self, _user_id: str, _conversation_id: str):
        return None

    async def cleanup_idle(self, _known: list[str], _busy: set[str]):
        return None

    async def stop_user_runtimes(self, _user_id: str):
        return None

    async def stop_worker(self, _user_id: str):
        return None

    async def remove_user(self, _user_id: str):
        return None

    def mark_used(self, _user_id: str):
        return None

    def runtime_summary(self):
        return {
            "workerLimit": 2,
            "workerMin": 2,
            "workerMax": 8,
            "dynamicWorkers": True,
            "resourceBasis": {},
            "workers": [],
            "browsers": [],
        }

    def current_worker_limit(self):
        return 2


class FakeHermes:
    def __init__(self):
        self.jobs = [{
            "id": "job-test",
            "name": "Hermes morning check",
            "prompt": "check status",
            "enabled": True,
            "state": "scheduled",
            "schedule_display": "0 9 * * *",
            "next_run_at": "2026-08-23T09:00:00+08:00",
            "last_run_at": None,
        }]

    async def start_run(self, **_kwargs):
        return {"run_id": "run-test", "status": "started"}

    async def events(self, _worker: str, _user: str, _run: str):
        yield {"event": "tool.started", "tool": "terminal", "preview": "pwd"}
        yield {"event": "message.delta", "delta": "任务"}
        yield {"event": "message.delta", "delta": "完成"}
        yield {"event": "run.completed", "output": "任务完成"}

    async def run_status(self, _worker: str, _user: str, _run: str):
        return {"status": "completed", "output": "任务完成"}

    async def approve(self, *_args):
        return {"resolved": 1}

    async def steer(self, *_args):
        return {"accepted": True}

    async def stop(self, *_args):
        return {"status": "stopping"}

    async def list_jobs(self, _worker: str, _user: str):
        return self.jobs

    async def job_action(self, _worker: str, _user: str, job_id: str, action: str):
        job = next(item for item in self.jobs if item["id"] == job_id)
        if action == "pause":
            job = {**job, "enabled": False, "state": "paused", "next_run_at": None}
        elif action == "resume":
            job = {**job, "enabled": True, "state": "scheduled"}
        self.jobs = [job if item["id"] == job_id else item for item in self.jobs]
        return {"job": job}


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch, tmp_path):
    database = main.Database(tmp_path / "app.db")
    runtime = FakeRuntimeManager(tmp_path / "users")
    hermes = FakeHermes()
    dispatcher = TaskDispatcher(database, runtime, hermes)
    model_store = ModelConfigStore(database, main.settings)
    model_gateway = ModelGateway(model_store, main.settings)
    monkeypatch.setattr(main, "database", database)
    monkeypatch.setattr(main, "computer_store", main.ComputerControlStore(database, main.settings.app_secret, tmp_path))
    monkeypatch.setattr(main, "computer_hub", main.ControlAgentHub())
    monkeypatch.setattr(main, "runtime_manager", runtime)
    monkeypatch.setattr(main, "hermes_client", hermes)
    monkeypatch.setattr(main, "dispatcher", dispatcher)
    monkeypatch.setattr(main, "capability_manager", CapabilityManager(database, runtime))
    monkeypatch.setattr(main, "model_store", model_store)
    monkeypatch.setattr(main, "model_gateway", model_gateway)
    monkeypatch.setattr(main, "coordinator", Coordinator(model_gateway))
    monkeypatch.setattr(main, "send_verification_email", lambda *_args: None)
    monkeypatch.setattr(main.secrets, "randbelow", lambda _limit: 123456)
    async def fake_completion(_messages, max_tokens=2048, *, role="chat"):
        assert role in {"chat", "coordinator", "executor"}
        return "测试回复", {"completion_tokens": min(max_tokens, 8)}
    monkeypatch.setattr(main, "fixed_chat_completion", fake_completion)
    monkeypatch.setattr(main, "admin_login_limiter", main.SlidingWindowLimiter(limit=100, window_seconds=300))
    monkeypatch.setattr(main, "verification_email_limiter", main.SlidingWindowLimiter(limit=100, window_seconds=300))
    monkeypatch.setattr(main, "verification_address_limiter", main.SlidingWindowLimiter(limit=100, window_seconds=300))
    monkeypatch.setattr(main, "guest_chat_global_limiter", main.SlidingWindowLimiter(limit=100, window_seconds=300))
    monkeypatch.setattr(main, "guest_chat_client_limiter", main.SlidingWindowLimiter(limit=100, window_seconds=300))
    monkeypatch.setattr(main, "user_chat_limiter", main.SlidingWindowLimiter(limit=100, window_seconds=300))
    monkeypatch.setattr(main, "trusted_login_limiter", main.SlidingWindowLimiter(limit=100, window_seconds=300))
    monkeypatch.setattr(
        main, "control_device_register_limiter", main.SlidingWindowLimiter(limit=100, window_seconds=300)
    )
    monkeypatch.setattr(main, "control_task_limiter", main.SlidingWindowLimiter(limit=100, window_seconds=300))
    monkeypatch.setattr(main, "wechat_login_limiter", main.SlidingWindowLimiter(limit=100, window_seconds=300))
    monkeypatch.setattr(main, "wechat_cloud_replay_guard", main.OneTimeReplayGuard())
    return database, runtime


def register(client: TestClient, *, vip: bool = True) -> tuple[str, dict[str, Any]]:
    credentials = {"email": "user@example.com", "password": "Password123!", "purpose": "register"}
    assert client.post("/api/auth/request-code", json=credentials).status_code == 200
    verified = client.post("/api/auth/verify", json={**credentials, "code": "123456", "display_name": "测试用户"})
    assert verified.status_code == 200
    data = verified.json()["data"]
    if vip:
        main.database.execute(
            "UPDATE users SET access_tier = 'vip', updated_at = ? WHERE id = ?",
            (main.now_ms(), data["user"]["id"]),
        )
        data["user"]["accessTier"] = "vip"
    return data["token"], data["user"]


def wait_for_task(client: TestClient, headers: dict[str, str], task_id: str) -> dict[str, Any]:
    for _ in range(50):
        task = client.get(f"/api/tasks/{task_id}", headers=headers).json()["data"]["task"]
        if task["status"] in {"completed", "failed", "cancelled"}:
            return task
        asyncio.run(asyncio.sleep(0.02))
    raise AssertionError("task did not complete")


def test_basic_registration_activation_and_password_only_admin_login():
    with TestClient(main.app) as client:
        token, user = register(client, vip=False)
        headers = {"Authorization": f"Bearer {token}"}
        assert user["accessTier"] == "basic"

        chat = client.post(
            "/api/v1/conversations",
            headers=headers,
            json={"title": "Basic Chat", "mode": "chat"},
        )
        assert chat.status_code == 200
        assert client.post(
            "/api/v1/conversations",
            headers=headers,
            json={"title": "Blocked Agent", "mode": "agent"},
        ).status_code == 403
        assert client.post(
            "/api/v1/control/devices/register",
            headers=headers,
            json={"installation_id": "basic-device-0001", "hostname": "BASIC-PC"},
        ).status_code == 403
        assert client.post(
            "/api/v1/savepoints", headers=headers, json={"name": "blocked"}
        ).status_code == 403
        assert client.get("/api/v1/devices", headers=headers).status_code == 403

        admin_login = client.post(
            "/api/v1/auth/admin-login",
            json={"password": main.settings.admin_password},
        )
        assert admin_login.status_code == 200
        admin_headers = {"Authorization": f"Bearer {admin_login.json()['data']['token']}"}
        created = client.post(
            "/api/v1/admin/activation-codes",
            headers=admin_headers,
            json={"note": "Basic upgrade", "max_uses": 1},
        )
        assert created.status_code == 200
        activation = created.json()["data"]["activationCode"]
        assert activation["code"].startswith("VIP-")
        listed = client.get("/api/v1/admin/activation-codes", headers=admin_headers).json()["data"]
        assert listed["activationCodes"][0]["code"] == ""

        redeemed = client.post(
            "/api/v1/auth/activation/redeem",
            headers=headers,
            json={"code": activation["code"]},
        )
        assert redeemed.status_code == 200
        assert redeemed.json()["data"]["user"]["accessTier"] == "vip"
        assert client.post(
            "/api/v1/conversations",
            headers=headers,
            json={"title": "Unlocked Agent", "mode": "agent"},
        ).status_code == 200


def test_wechat_login_binds_existing_email_and_merges_conversations(monkeypatch):
    async def fake_exchange(_code: str) -> tuple[str, str]:
        return "openid-test-bind", "unionid-test-bind"

    monkeypatch.setattr(main, "exchange_wechat_code", fake_exchange)
    with TestClient(main.app) as client:
        email_token, email_user = register(client)
        email_headers = {"Authorization": f"Bearer {email_token}"}
        email_conversation = client.post(
            "/api/v1/conversations",
            headers=email_headers,
            json={"title": "邮箱对话", "mode": "chat"},
        ).json()["data"]["conversation"]

        wechat = client.post(
            "/api/v1/auth/wechat",
            json={"code": "one-time-code", "device_id": "wechat-device-test", "device_name": "微信测试"},
        )
        assert wechat.status_code == 200
        wechat_data = wechat.json()["data"]
        assert wechat_data["user"]["accessTier"] == "basic"
        assert wechat_data["user"]["emailBound"] is False
        provisional_id = wechat_data["user"]["id"]
        wechat_headers = {"Authorization": f"Bearer {wechat_data['token']}"}
        wechat_conversation = client.post(
            "/api/v1/conversations",
            headers=wechat_headers,
            json={"title": "微信对话", "mode": "chat"},
        ).json()["data"]["conversation"]
        synced_chat = client.post(
            f"/api/v1/conversations/{wechat_conversation['id']}/chat",
            headers=wechat_headers,
            json={"content": "微信端待同步消息"},
        )
        assert synced_chat.status_code == 200

        bind_payload = {"email": "user@example.com", "password": "Password123!"}
        assert client.post(
            "/api/v1/auth/wechat/bind-email/request-code",
            headers=wechat_headers,
            json=bind_payload,
        ).status_code == 403

        admin_login = client.post(
            "/api/v1/auth/admin-login",
            json={"password": main.settings.admin_password},
        )
        admin_headers = {"Authorization": f"Bearer {admin_login.json()['data']['token']}"}
        activation = client.post(
            "/api/v1/admin/activation-codes",
            headers=admin_headers,
            json={"note": "微信绑定测试", "max_uses": 1},
        ).json()["data"]["activationCode"]["code"]
        activated = client.post(
            "/api/v1/auth/activation/redeem",
            headers=wechat_headers,
            json={"code": activation},
        )
        assert activated.status_code == 200
        assert activated.json()["data"]["user"]["accessTier"] == "vip"

        requested = client.post(
            "/api/v1/auth/wechat/bind-email/request-code",
            headers=wechat_headers,
            json=bind_payload,
        )
        assert requested.status_code == 200
        assert requested.json()["data"]["existingAccount"] is True
        bound = client.post(
            "/api/v1/auth/wechat/bind-email/verify",
            headers=wechat_headers,
            json={**bind_payload, "code": "123456", "device_id": "wechat-device-test"},
        )
        assert bound.status_code == 200
        bound_data = bound.json()["data"]
        assert bound_data["user"]["id"] == email_user["id"]
        assert bound_data["user"]["accessTier"] == "vip"
        assert bound_data["user"]["emailBound"] is True
        assert main.database.get_user_by_id(provisional_id) is None

        merged_headers = {"Authorization": f"Bearer {bound_data['token']}"}
        conversations = client.get("/api/v1/conversations", headers=merged_headers).json()["data"]["conversations"]
        assert {item["id"] for item in conversations} >= {email_conversation["id"], wechat_conversation["id"]}
        merged_messages = client.get(
            f"/api/v1/conversations/{wechat_conversation['id']}/messages", headers=merged_headers
        ).json()["data"]["messages"]
        assert [item["content"] for item in merged_messages] == ["微信端待同步消息", "测试回复"]
        email_messages = client.get(
            f"/api/v1/conversations/{wechat_conversation['id']}/messages", headers=email_headers
        ).json()["data"]["messages"]
        assert [item["content"] for item in email_messages] == ["微信端待同步消息", "测试回复"]
        assert client.get("/api/v1/auth/me", headers=wechat_headers).status_code == 403


def test_wechat_cloud_login_rejects_forgery_expiry_and_replay():
    def signed_payload(**overrides):
        payload = {
            "app_id": main.settings.wechat_app_id,
            "open_id": "openid-cloud-login",
            "union_id": "unionid-cloud-login",
            "timestamp": int(time.time()),
            "nonce": "abcdef0123456789abcdef0123456789abcdef0123456789",
            "device_id": "wechat-cloud-device",
            "device_name": "微信云登录测试",
        }
        payload.update(overrides)
        payload["signature"] = main.wechat_cloud_login_signature(
            main.settings.wechat_cloud_bridge_secret,
            app_id=payload["app_id"],
            open_id=payload["open_id"],
            union_id=payload["union_id"],
            timestamp=payload["timestamp"],
            nonce=payload["nonce"],
            device_id=payload["device_id"],
            device_name=payload["device_name"],
        )
        return payload

    with TestClient(main.app) as client:
        payload = signed_payload()
        logged_in = client.post("/api/v1/auth/wechat/cloud", json=payload)
        assert logged_in.status_code == 200
        assert logged_in.json()["data"]["user"]["accessTier"] == "basic"
        assert logged_in.json()["data"]["user"]["emailBound"] is False

        replay = client.post("/api/v1/auth/wechat/cloud", json=payload)
        assert replay.status_code == 409

        forged = signed_payload(nonce="bbbbbb0123456789abcdef0123456789abcdef0123456789")
        forged["signature"] = "0" * 64
        assert client.post("/api/v1/auth/wechat/cloud", json=forged).status_code == 401

        expired = signed_payload(
            timestamp=int(time.time()) - 300,
            nonce="cccccc0123456789abcdef0123456789abcdef0123456789",
        )
        assert client.post("/api/v1/auth/wechat/cloud", json=expired).status_code == 401

        wrong_app = signed_payload(
            app_id="wx0000000000000000",
            nonce="dddddd0123456789abcdef0123456789abcdef0123456789",
        )
        assert client.post("/api/v1/auth/wechat/cloud", json=wrong_app).status_code == 401


def test_conversation_title_uses_only_latest_instruction_and_sqlite_memory(isolated_state):
    database, _runtime = isolated_state
    assert summarize_conversation_title("你好") == "用户问候"
    assert summarize_conversation_title("帮我看看最近有哪些新闻热点") == "查询最近的新闻热点"
    with TestClient(main.app) as client:
        token, _user = register(client)
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post("/api/conversations", headers=headers, json={"title": "新对话", "mode": "chat"})
        conversation_id = created.json()["data"]["conversation"]["id"]

        greeting = client.post(
            f"/api/conversations/{conversation_id}/chat", headers=headers, json={"content": "你好"}
        )
        assert greeting.status_code == 200
        assert greeting.json()["data"]["conversation"]["title"] == "用户问候"

        news = client.post(
            f"/api/conversations/{conversation_id}/chat",
            headers=headers,
            json={"content": "帮我看看最近有哪些新闻热点"},
        )
        assert news.status_code == 200
        assert news.json()["data"]["conversation"]["title"] == "查询最近的新闻热点"

        row = database.one("SELECT * FROM conversations WHERE id = ?", (conversation_id,))
        assert row and row["last_user_instruction"] == "帮我看看最近有哪些新闻热点"
        memories = database.conversation_memories(conversation_id)
        assert [item["source"] for item in memories] == ["chat", "chat"]
        assert memories[-1]["user_content"] == "帮我看看最近有哪些新闻热点"


def test_chat_and_agent_endpoints_emit_ordered_sse_events(monkeypatch, isolated_state):
    database, _runtime = isolated_state

    async def fake_stream(_messages, max_tokens=2048, *, role="chat"):
        assert role == "chat"
        assert max_tokens >= 64
        yield {"type": "delta", "content": "流"}
        yield {"type": "delta", "content": "式"}
        yield {"type": "done", "usage": {"completion_tokens": 2}, "model": "stream-test-model"}

    monkeypatch.setattr(main, "fixed_chat_completion_stream", fake_stream)
    with TestClient(main.app) as client:
        token, user = register(client)
        headers = {"Authorization": f"Bearer {token}"}
        chat = client.post(
            "/api/conversations",
            headers=headers,
            json={"title": "流式聊天", "mode": "chat"},
        ).json()["data"]["conversation"]
        with client.stream(
            "POST",
            f"/api/conversations/{chat['id']}/chat/stream",
            headers=headers,
            json={"content": "测试流式输出"},
        ) as response:
            response.read()
            chat_body = response.text

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["x-accel-buffering"] == "no"
        assert chat_body.index('event: delta\ndata: {"content":"流"}') < chat_body.index(
            'event: delta\ndata: {"content":"式"}'
        ) < chat_body.index("event: done")
        messages = database.all(
            "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id",
            (chat["id"],),
        )
        assert [(row["role"], row["content"]) for row in messages] == [
            ("user", "测试流式输出"),
            ("assistant", "流式"),
        ]

        agent = client.post(
            "/api/conversations",
            headers=headers,
            json={"title": "流式 Agent", "mode": "agent"},
        ).json()["data"]["conversation"]
        task = database.create_task(
            user_id=user["id"],
            conversation_id=agent["id"],
            prompt="测试 Agent 流",
            attachment_ids=[],
        )
        database.add_task_event(task["id"], "message.delta", {"delta": "第一段"})
        database.add_task_event(task["id"], "message.delta", {"delta": "第二段"})
        database.finish_task(task["id"], status="completed", output="第一段第二段")
        with client.stream("GET", f"/api/tasks/{task['id']}/stream", headers=headers) as response:
            response.read()
            agent_body = response.text

        assert response.status_code == 200
        assert agent_body.count("event: task-event") == 2
        assert agent_body.index("第一段") < agent_body.index("第二段") < agent_body.index("event: done")


def test_hermes_history_uses_long_sqlite_context(isolated_state):
    database, _runtime = isolated_state
    with TestClient(main.app) as client:
        token, user = register(client)
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post("/api/conversations", headers=headers, json={"title": "长上下文", "mode": "agent"})
        conversation_id = created.json()["data"]["conversation"]["id"]
        for index in range(60):
            database.add_message(conversation_id, "user", f"历史指令 {index}")
            database.add_message(conversation_id, "assistant", f"历史结果 {index}")
        task = database.create_task(
            user_id=user["id"],
            conversation_id=conversation_id,
            prompt="继续处理",
            attachment_ids=[],
        )
        history = main.dispatcher._conversation_history(task)
        assert len(history) == 120
        assert history[0]["content"] == "历史指令 0"
        assert history[-1]["content"] == "历史结果 59"


def test_database_migration_backfills_latest_instruction_titles_and_remote_memory(tmp_path: Path):
    database_path = tmp_path / "legacy-title.db"
    legacy = Database(database_path)
    user = legacy.create_user(
        username="legacy_titles",
        email="legacy-titles@example.com",
        display_name="Legacy Titles",
        password_hash="hash",
    )
    conversation = legacy.create_conversation(user["id"], "你好", "agent")
    original_updated_at = conversation["updated_at"]
    with legacy.connection() as connection:
        connection.execute(
            "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, 'user', ?, ?)",
            (conversation["id"], "你好", original_updated_at + 1),
        )
        connection.execute(
            """INSERT INTO control_devices
            (id, user_id, installation_hash, credential_hash, name, hostname, platform,
             agent_version, capabilities_json, targets_json, created_at, updated_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, 'windows', '0.1.0', '[]', '[]', ?, ?, ?)""",
            (
                "legacy-device",
                user["id"],
                "legacy-installation",
                "legacy-credential",
                "Legacy PC",
                "legacy-pc",
                original_updated_at,
                original_updated_at,
                original_updated_at,
            ),
        )
        connection.execute(
            """INSERT INTO control_tasks
            (id, user_id, device_id, conversation_id, target_id, target_kind, instruction,
             status, output, created_at, updated_at, completed_at)
            VALUES (?, ?, ?, ?, 'desktop', 'windows', ?, 'completed', ?, ?, ?, ?)""",
            (
                "legacy-remote-task",
                user["id"],
                "legacy-device",
                conversation["id"],
                "帮我看看最近有哪些新闻热点",
                "已整理新闻热点",
                original_updated_at + 2,
                original_updated_at + 3,
                original_updated_at + 3,
            ),
        )

    migrated = Database(database_path)
    refreshed = migrated.get_conversation(conversation["id"], user["id"])
    assert refreshed is not None
    assert refreshed["title"] == "查询最近的新闻热点"
    assert refreshed["last_user_instruction"] == "帮我看看最近有哪些新闻热点"
    assert refreshed["updated_at"] == original_updated_at
    memories = migrated.conversation_memories(conversation["id"])
    assert [(item["source"], item["source_id"]) for item in memories] == [
        ("remote", "legacy-remote-task")
    ]

    migrated_again = Database(database_path)
    assert len(migrated_again.conversation_memories(conversation["id"])) == 1


def register_control_client(
    client: TestClient,
    headers: dict[str, str],
    installation_id: str = "installation-test-0001",
):
    registered = client.post(
        "/api/v1/control/devices/register",
        headers=headers,
        json={
            "installation_id": installation_id,
            "hostname": "TEST-PC",
            "name": "",
            "agent_version": "0.1.0-test",
        },
    )
    assert registered.status_code == 200
    return registered.json()["data"]


def test_control_device_registers_from_same_account_without_pairing_code():
    payload = {
        "installation_id": "windows-installation-0001",
        "hostname": "ACCOUNT-PC",
        "name": "书房电脑",
        "agent_version": "0.2.0-test",
    }
    with TestClient(main.app) as client:
        assert client.post("/api/v1/control/devices/register", json=payload).status_code == 401
        token, _user = register(client)
        login = {
            "identifier": "user@example.com",
            "password": "Password123!",
            "purpose": "login",
            "device_id": payload["installation_id"],
            "device_name": payload["name"],
            "client_platform": "windows",
        }
        assert client.post("/api/v1/auth/request-code", json=login).status_code == 200
        windows_login = client.post(
            "/api/v1/auth/verify",
            json={**login, "code": "123456", "trust_device": True},
        )
        assert windows_login.status_code == 200
        token = windows_login.json()["data"]["token"]
        headers = {"Authorization": f"Bearer {token}"}

        first = client.post("/api/v1/control/devices/register", headers=headers, json=payload)
        assert first.status_code == 200
        first_data = first.json()["data"]
        device_id = first_data["device"]["id"]
        assert first_data["device"]["name"] == "书房电脑"
        assert first_data["credential"].startswith(device_id + ".")

        second = client.post(
            "/api/v1/control/devices/register",
            headers=headers,
            json={**payload, "name": "更新后的书房电脑"},
        )
        assert second.status_code == 200
        second_data = second.json()["data"]
        assert second_data["device"]["id"] == device_id
        assert second_data["device"]["name"] == "更新后的书房电脑"
        assert second_data["credential"] != first_data["credential"]
        assert main.computer_store.authenticate_credential(first_data["credential"]) is None
        assert main.computer_store.authenticate_credential(second_data["credential"])["user_id"] == _user["id"]

        other = main.database.create_user(
            email="other-account-device@example.com",
            display_name="Other",
            password_hash="hash",
            username="other-account-device",
            access_tier="vip",
        )
        other_token = main.issue_access_token(main.settings.app_secret, str(other["id"]), "user", str(other["email"]))
        other_result = client.post(
            "/api/v1/control/devices/register",
            headers={"Authorization": f"Bearer {other_token}"},
            json=payload,
        )
        assert other_result.status_code == 200
        assert other_result.json()["data"]["device"]["id"] != device_id
        assert [item["id"] for item in client.get("/api/v1/control/devices", headers=headers).json()["data"]["devices"]] == [device_id]


def test_control_device_management_and_user_isolation_without_pairing_api():
    with TestClient(main.app) as client:
        assert client.get("/api/v1/control/devices").status_code == 401
        token, user = register(client)
        headers = {"Authorization": f"Bearer {token}"}
        registered = register_control_client(client, headers)
        device = registered["device"]
        assert device["name"] == "TEST-PC"
        assert device["hostname"] == "TEST-PC"
        assert device["online"] is False
        assert registered["credential"].startswith(device["id"] + ".")
        assert client.post("/api/v1/control/pairing-codes", headers=headers, json={}).status_code == 404
        assert client.post("/api/v1/control/pair", json={}).status_code == 404

        listed = client.get("/api/v1/control/devices", headers=headers).json()["data"]["devices"]
        assert [item["id"] for item in listed] == [device["id"]]
        renamed = client.patch(
            f"/api/v1/control/devices/{device['id']}", headers=headers, json={"name": "书房电脑"}
        )
        assert renamed.status_code == 200
        assert renamed.json()["data"]["device"]["name"] == "书房电脑"

        other = main.database.create_user(
            email="other-control@example.com", display_name="Other", password_hash="hash",
            username="other-control", access_tier="vip",
        )
        other_token = main.issue_access_token(main.settings.app_secret, str(other["id"]), "user", str(other["email"]))
        other_headers = {"Authorization": f"Bearer {other_token}"}
        assert client.get("/api/v1/control/devices", headers=other_headers).json()["data"]["devices"] == []
        assert client.patch(
            f"/api/v1/control/devices/{device['id']}", headers=other_headers, json={"name": "越权"}
        ).status_code == 404
        assert client.post(
            "/api/v1/control/tasks",
            headers=other_headers,
            json={"device_id": device["id"], "target_id": "desktop", "instruction": "越权任务"},
        ).status_code == 404

        assert client.delete(f"/api/v1/control/devices/{device['id']}", headers=headers).status_code == 200
        assert main.computer_store.authenticate_credential(registered["credential"]) is None
        assert client.get("/api/v1/control/devices", headers=headers).json()["data"]["devices"] == []


def test_conversations_bind_distinct_same_account_devices_and_remote_is_explicit():
    with TestClient(main.app) as client:
        token, user = register(client)
        headers = {"Authorization": f"Bearer {token}"}
        first = register_control_client(client, headers, "installation-binding-0001")["device"]
        second = register_control_client(client, headers, "installation-binding-0002")["device"]
        main.computer_store.update_hello(
            second["id"],
            hostname="SECOND-PC",
            agent_version="0.3.2-test",
            capabilities=["windows", "adb", "ocr"],
            targets=[
                {"id": "desktop", "kind": "windows", "name": "Windows 桌面", "state": "device"},
                {
                    "id": "adb:127.0.0.1:16384",
                    "kind": "adb",
                    "name": "MuMu",
                    "serial": "127.0.0.1:16384",
                    "state": "device",
                },
            ],
        )
        first_conversation = client.post(
            "/api/v1/conversations", headers=headers, json={"title": "电脑一", "mode": "agent"}
        ).json()["data"]["conversation"]
        second_conversation = client.post(
            "/api/v1/conversations", headers=headers, json={"title": "电脑二", "mode": "agent"}
        ).json()["data"]["conversation"]

        bound_first = client.put(
            f"/api/v1/conversations/{first_conversation['id']}/control-binding",
            headers=headers,
            json={"device_id": first["id"], "target_id": "desktop"},
        )
        assert bound_first.status_code == 200
        assert {
            key: bound_first.json()["data"]["conversation"][key]
            for key in ("controlDeviceId", "controlTargetId", "controlTargetKind")
        } == {
            "controlDeviceId": first["id"],
            "controlTargetId": "desktop",
            "controlTargetKind": "windows",
        }

        bound_second = client.put(
            f"/api/v1/conversations/{second_conversation['id']}/control-binding",
            headers=headers,
            json={"device_id": second["id"], "target_id": "adb:127.0.0.1:16384"},
        )
        assert bound_second.status_code == 200
        assert bound_second.json()["data"]["conversation"]["controlTargetKind"] == "adb"

        other = main.database.create_user(
            email="binding-other@example.com", display_name="Other", password_hash="hash", username="binding-other"
        )
        other_device = main.computer_store.register_account_device(
            user_id=str(other["id"]),
            installation_id="installation-binding-other",
            hostname="OTHER-PC",
            name="其他账号电脑",
            agent_version="test",
        )["device"]
        assert client.put(
            f"/api/v1/conversations/{first_conversation['id']}/control-binding",
            headers=headers,
            json={"device_id": other_device["id"], "target_id": "desktop"},
        ).status_code == 404

        mismatched = client.post(
            "/api/v1/control/tasks",
            headers=headers,
            json={
                "conversation_id": first_conversation["id"],
                "device_id": second["id"],
                "target_id": "adb:127.0.0.1:16384",
                "instruction": "不应发送到另一台电脑",
            },
        )
        assert mismatched.status_code == 409

        remote = client.post(
            "/api/v1/control/tasks",
            headers=headers,
            json={
                "conversation_id": first_conversation["id"],
                "device_id": first["id"],
                "target_id": "desktop",
                "instruction": "只执行这一次远程任务",
            },
        )
        assert remote.status_code == 200
        remote_task = remote.json()["data"]["task"]
        assert remote_task["conversationId"] == first_conversation["id"]

        local = client.post(
            f"/api/v1/conversations/{first_conversation['id']}/tasks",
            headers=headers,
            json={"content": "这条仍由本地 Hermes 执行", "attachment_ids": []},
        )
        assert local.status_code == 200
        assert main.database.one(
            "SELECT COUNT(*) AS count FROM control_tasks WHERE user_id = ?", (user["id"],)
        )["count"] == 1

        first_tasks = client.get(
            f"/api/v1/control/tasks?conversation_id={first_conversation['id']}", headers=headers
        ).json()["data"]["tasks"]
        second_tasks = client.get(
            f"/api/v1/control/tasks?conversation_id={second_conversation['id']}", headers=headers
        ).json()["data"]["tasks"]
        assert [item["id"] for item in first_tasks] == [remote_task["id"]]
        assert second_tasks == []

        cleared = client.put(
            f"/api/v1/conversations/{first_conversation['id']}/control-binding",
            headers=headers,
            json={"device_id": None, "target_id": None},
        )
        assert cleared.status_code == 200
        assert cleared.json()["data"]["conversation"]["controlDeviceId"] is None
        assert client.post(
            "/api/v1/control/tasks",
            headers=headers,
            json={
                "conversation_id": first_conversation["id"],
                "device_id": first["id"],
                "target_id": "desktop",
                "instruction": "解绑后不可执行",
            },
        ).status_code == 409

        assert client.delete(f"/api/v1/control/devices/{second['id']}", headers=headers).status_code == 200
        conversations = client.get("/api/v1/conversations", headers=headers).json()["data"]["conversations"]
        revoked_conversation = next(item for item in conversations if item["id"] == second_conversation["id"])
        assert revoked_conversation["controlDeviceId"] is None


def test_agent_dispatch_uses_llm_to_choose_hermes_or_bound_device(monkeypatch):
    decisions = iter(['{"route":"local"}', '{"route":"remote"}'])
    prompts: list[str] = []

    async def route_completion(messages, max_tokens=2048, *, role="chat"):
        assert role == "coordinator"
        prompts.append(messages[-1]["content"])
        return next(decisions), {"completion_tokens": min(max_tokens, 8)}

    monkeypatch.setattr(main, "fixed_chat_completion", route_completion)
    with TestClient(main.app) as client:
        token, user = register(client)
        headers = {"Authorization": f"Bearer {token}"}
        device = register_control_client(client, headers, "installation-auto-route")['device']
        conversation = client.post(
            "/api/v1/conversations", headers=headers, json={"title": "智能路由", "mode": "agent"}
        ).json()["data"]["conversation"]
        assert client.put(
            f"/api/v1/conversations/{conversation['id']}/control-binding",
            headers=headers,
            json={"device_id": device["id"], "target_id": "desktop"},
        ).status_code == 200

        research = client.post(
            f"/api/v1/conversations/{conversation['id']}/dispatch",
            headers=headers,
            json={"content": "帮我在浏览器上面查找一下最近热点", "attachment_ids": []},
        )
        assert research.status_code == 200
        assert research.json()["data"]["execution"] == "local"

        open_browser = client.post(
            f"/api/v1/conversations/{conversation['id']}/dispatch",
            headers=headers,
            json={"content": "帮我打开浏览器", "attachment_ids": []},
        )
        assert open_browser.status_code == 200
        assert open_browser.json()["data"]["execution"] == "remote"
        assert open_browser.json()["data"]["task"]["deviceId"] == device["id"]
        assert open_browser.json()["data"]["task"]["conversationId"] == conversation["id"]
        assert len(prompts) == 2
        assert "网页搜索、新闻热点" in prompts[0]
        assert "用户指令" in prompts[1]
        assert main.database.one(
            "SELECT COUNT(*) AS count FROM tasks WHERE user_id = ?", (user["id"],)
        )["count"] == 1
        assert main.database.one(
            "SELECT COUNT(*) AS count FROM control_tasks WHERE user_id = ?", (user["id"],)
        )["count"] == 1


def test_agent_dispatch_fallback_routes_explicit_app_launch_remote_and_research_local(monkeypatch):
    async def malformed_completion(_messages, max_tokens=2048, *, role="chat"):
        assert role == "coordinator"
        return "无法提供 JSON", {"completion_tokens": min(max_tokens, 8)}

    monkeypatch.setattr(main, "fixed_chat_completion", malformed_completion)
    assert main.fallback_agent_execution_route("帮我打开浏览器") == "remote"
    assert main.fallback_agent_execution_route("操作我的电脑主机打开微信") == "remote"
    assert main.fallback_agent_execution_route("帮我在浏览器中搜索最近热点") == "local"
    assert asyncio.run(main.classify_agent_execution("帮我打开浏览器", "windows")) == "remote"
    assert asyncio.run(main.classify_agent_execution("帮我在浏览器中搜索最近热点", "windows")) == "local"


def test_agent_dispatch_routes_explicit_device_capture_without_calling_classifier_model(monkeypatch):
    async def unavailable_completion(_messages, max_tokens=2048, *, role="chat"):
        raise AssertionError(f"明确的设备截图不应调用模型：{max_tokens}")

    monkeypatch.setattr(main, "fixed_chat_completion", unavailable_completion)
    assert main.explicit_remote_capture_route("打开我电脑的浏览器，查看界面截个图给我") is True
    assert main.explicit_remote_capture_route("查看当前 Microsoft Edge 浏览器窗口并截个图给我") is True
    assert main.explicit_remote_capture_route("在浏览器搜索最近热点并截图给我") is False
    assert asyncio.run(main.classify_agent_execution("打开我电脑的浏览器，查看界面截个图给我", "windows")) == "remote"
    assert asyncio.run(main.classify_agent_execution("查看当前 Microsoft Edge 浏览器窗口并截个图给我", "windows")) == "remote"


def test_control_websocket_task_frames_replay_approval_and_llm_lease():
    tiny_png = base64.b64encode(
        base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
    ).decode()
    with TestClient(main.app) as client:
        token, _user = register(client)
        headers = {"Authorization": f"Bearer {token}"}
        registered = register_control_client(client, headers)
        credential = registered["credential"]
        device_id = registered["device"]["id"]
        with client.websocket_connect(
            "/api/v1/control/agent/ws", headers={"Authorization": f"Bearer {credential}"}
        ) as socket:
            assert socket.receive_json()["type"] == "hello.request"
            socket.send_json(
                {
                    "type": "hello",
                    "hostname": "TEST-PC",
                    "agentVersion": "0.1.0-test",
                    "capabilities": ["windows", "adb", "ocr"],
                    "targets": [
                        {"id": "desktop", "kind": "windows", "name": "Windows 桌面", "state": "device"},
                        {"id": "adb:127.0.0.1:16384", "kind": "adb", "name": "MuMu", "serial": "127.0.0.1:16384", "state": "device"},
                    ],
                }
            )
            assert socket.receive_json()["type"] == "hello.ack"
            online = client.get("/api/v1/control/devices", headers=headers).json()["data"]["devices"][0]
            assert online["online"] is True
            assert online["targets"][1]["kind"] == "adb"

            created = client.post(
                "/api/v1/control/tasks",
                headers=headers,
                json={
                    "device_id": device_id,
                    "target_id": "adb:127.0.0.1:16384",
                    "target_kind": "adb",
                    "instruction": "观察测试页面并等待批准",
                },
            )
            assert created.status_code == 200
            task_id = created.json()["data"]["task"]["id"]
            assignment = socket.receive_json()
            assert assignment["type"] == "task.assign"
            assert assignment["task"]["status"] == "assigned"
            lease_id = assignment["leaseId"]

            socket.send_json(
                {
                    "type": "task.accepted",
                    "taskId": task_id,
                    "leaseId": lease_id,
                    "sequence": 1,
                    "clientEventId": "accepted-1",
                }
            )
            assert socket.receive_json()["type"] == "event.ack"
            scoped_credential = f"{credential}~{task_id}~{lease_id}"
            assert client.get(
                "/api/control/llm/v1/models", headers={"Authorization": f"Bearer {scoped_credential}"}
            ).status_code == 200
            assert client.get(
                "/api/v1/control/llm/v1/models", headers={"Authorization": f"Bearer {scoped_credential}"}
            ).status_code == 200
            assert client.get(
                "/api/control/llm/v1/models", headers={"Authorization": f"Bearer {credential}~bad~lease"}
            ).status_code == 403

            socket.send_json(
                {
                    "type": "task.event",
                    "taskId": task_id,
                    "leaseId": lease_id,
                    "sequence": 2,
                    "clientEventId": "observation-2",
                    "eventType": "observation",
                    "payload": {"observationId": "obs-2", "summary": "已读取画面"},
                    "frameBase64": tiny_png,
                }
            )
            frame_ack = socket.receive_json()
            assert frame_ack["type"] == "event.ack"
            assert frame_ack["frameId"]

            socket.send_json(
                {
                    "type": "task.event",
                    "taskId": task_id,
                    "leaseId": lease_id,
                    "sequence": 2,
                    "clientEventId": "replay-with-different-id",
                    "eventType": "reasoning",
                    "payload": {"message": "重放"},
                }
            )
            protocol_error = socket.receive_json()
            assert protocol_error["type"] == "protocol.error"
            assert "严格递增" in protocol_error["message"]

            action_hash = "a" * 64
            socket.send_json(
                {
                    "type": "task.event",
                    "taskId": task_id,
                    "leaseId": lease_id,
                    "sequence": 3,
                    "clientEventId": "approval-3",
                    "eventType": "approval.required",
                    "payload": {"summary": "准备点击测试按钮", "actionHash": action_hash},
                }
            )
            assert socket.receive_json()["type"] == "event.ack"
            waiting = client.get(f"/api/v1/control/tasks/{task_id}", headers=headers).json()["data"]["task"]
            assert waiting["status"] == "waiting_approval"
            approved = client.post(
                f"/api/v1/control/tasks/{task_id}/approval", headers=headers, json={"decision": "approve"}
            )
            assert approved.status_code == 200
            approval = socket.receive_json()
            assert approval == {
                "type": "approval.response",
                "taskId": task_id,
                "leaseId": lease_id,
                "decision": "approve",
                "actionHash": action_hash,
            }

            socket.send_json(
                {
                    "type": "task.event",
                    "taskId": task_id,
                    "leaseId": lease_id,
                    "sequence": 4,
                    "clientEventId": "completed-4",
                    "eventType": "task.completed",
                    "payload": {"output": "测试任务完成"},
                }
            )
            assert socket.receive_json()["type"] == "event.ack"

            task = client.get(f"/api/v1/control/tasks/{task_id}", headers=headers).json()["data"]["task"]
            assert task["status"] == "completed"
            assert task["output"] == "测试任务完成"
            events = client.get(
                f"/api/v1/control/tasks/{task_id}/events", headers=headers
            ).json()["data"]["events"]
            assert [event["type"] for event in events] == ["task.started", "observation", "approval.required", "task.completed"]
            assert events[1]["frameId"] == frame_ack["frameId"]
            frame = client.get(f"/api/v1/control/frames/{frame_ack['frameId']}", headers=headers)
            assert frame.status_code == 200
            assert frame.headers["content-type"].startswith("image/png")
            assert client.get(
                "/api/control/llm/v1/models", headers={"Authorization": f"Bearer {scoped_credential}"}
            ).status_code == 403
            main.database.execute("UPDATE control_frames SET created_at = 0 WHERE id = ?", (frame_ack["frameId"],))
            pruned = main.computer_store.prune_expired_data()
            assert pruned["frames"] == 1
            assert client.get(f"/api/v1/control/frames/{frame_ack['frameId']}", headers=headers).status_code == 404


def test_control_stop_before_accept_prevents_execution():
    with TestClient(main.app) as client:
        token, _user = register(client)
        headers = {"Authorization": f"Bearer {token}"}
        registered = register_control_client(client, headers)
        credential = registered["credential"]
        device_id = registered["device"]["id"]
        with client.websocket_connect(
            "/api/v1/control/agent/ws", headers={"Authorization": f"Bearer {credential}"}
        ) as socket:
            assert socket.receive_json()["type"] == "hello.request"
            socket.send_json({"type": "hello", "hostname": "TEST-PC", "targets": []})
            assert socket.receive_json()["type"] == "hello.ack"
            created = client.post(
                "/api/v1/control/tasks",
                headers=headers,
                json={"device_id": device_id, "target_id": "desktop", "instruction": "不应开始"},
            ).json()["data"]["task"]
            assignment = socket.receive_json()
            stopped = client.post(f"/api/v1/control/tasks/{created['id']}/stop", headers=headers)
            assert stopped.status_code == 200
            assert stopped.json()["data"]["task"]["status"] == "cancelled"
            socket.send_json(
                {
                    "type": "task.accepted",
                    "taskId": created["id"],
                    "leaseId": assignment["leaseId"],
                    "sequence": 1,
                    "clientEventId": "late-accept",
                }
            )
            response = socket.receive_json()
            assert response["type"] == "protocol.error"
            assert "租约无效" in response["message"]


def test_control_serializes_tasks_expires_started_lease_and_revokes_all_active_tasks():
    with TestClient(main.app) as client:
        token, user = register(client)
        headers = {"Authorization": f"Bearer {token}"}
        registered = register_control_client(client, headers)
        device_id = registered["device"]["id"]
        first = main.computer_store.create_task(str(user["id"]), device_id, "desktop", "任务一")
        second = main.computer_store.create_task(str(user["id"]), device_id, "desktop", "任务二")
        claimed = main.computer_store.claim_next_task(device_id)
        assert claimed and claimed["id"] == first["id"]
        assert main.computer_store.accept_task(device_id, first["id"], claimed["lease_id"])
        assert main.computer_store.claim_next_task(device_id) is None

        main.database.execute(
            "UPDATE control_tasks SET lease_expires_at = ? WHERE id = ?",
            (main.now_ms() - 1, first["id"]),
        )
        main.computer_store.expire_stale_tasks(device_id)
        expired = main.computer_store.get_task(first["id"], str(user["id"]))
        assert expired["status"] == "failed"
        assert "未自动重试" in expired["error"]

        next_claim = main.computer_store.claim_next_task(device_id)
        assert next_claim and next_claim["id"] == second["id"]
        assert main.computer_store.accept_task(device_id, second["id"], next_claim["lease_id"])
        third = main.computer_store.create_task(str(user["id"]), device_id, "desktop", "任务三")
        revoked = client.delete(f"/api/v1/control/devices/{device_id}", headers=headers)
        assert revoked.status_code == 200
        assert main.computer_store.get_task(second["id"], str(user["id"]))["status"] == "cancelled"
        assert main.computer_store.get_task(third["id"], str(user["id"]))["status"] == "cancelled"
        assert main.computer_store.authenticate_credential(registered["credential"]) is None


def test_registration_task_queue_events_and_persistence():
    with TestClient(main.app) as client:
        token, _user = register(client)
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post("/api/conversations", headers=headers, json={"title": "新任务", "mode": "agent"})
        conversation_id = created.json()["data"]["conversation"]["id"]
        submitted = client.post(
            f"/api/conversations/{conversation_id}/tasks",
            headers=headers,
            json={"content": "检查工作区并回复", "attachment_ids": []},
        )
        assert submitted.status_code == 200
        task_id = submitted.json()["data"]["task"]["id"]
        task = wait_for_task(client, headers, task_id)
        assert task["status"] == "completed"
        assert task["output"] == "任务完成"
        events = client.get(f"/api/tasks/{task_id}/events", headers=headers).json()["data"]["events"]
        assert any(event["type"] == "tool.started" for event in events)
        messages = client.get(f"/api/conversations/{conversation_id}/messages", headers=headers).json()["data"]["messages"]
        assert [message["role"] for message in messages] == ["user", "assistant"]
        notifications = client.get("/api/v1/notifications", headers=headers).json()["data"]["notifications"]
        assert any(item["category"] == "agent_completed" and item["entityId"] == task_id for item in notifications)


def test_notification_preferences_chat_events_and_read_state(isolated_state):
    database, _runtime = isolated_state
    with TestClient(main.app) as client:
        token, user = register(client)
        headers = {"Authorization": f"Bearer {token}"}

        defaults = client.get("/api/v1/notifications/preferences", headers=headers)
        assert defaults.status_code == 200
        assert defaults.json()["data"]["preferences"] == {
            "chatCompleted": True,
            "agentCompleted": True,
            "scheduleCompleted": True,
            "taskFailed": True,
            "approvalRequired": True,
            "system": True,
        }

        conversation_id = client.post(
            "/api/v1/conversations",
            headers=headers,
            json={"title": "通知测试", "mode": "chat"},
        ).json()["data"]["conversation"]["id"]
        assert client.post(
            f"/api/v1/conversations/{conversation_id}/chat",
            headers=headers,
            json={"content": "完成后通知"},
        ).status_code == 200

        listed = client.get("/api/v1/notifications", headers=headers).json()["data"]
        assert listed["unreadCount"] == 1
        assert listed["notifications"][0]["category"] == "chat_completed"
        notification_id = listed["notifications"][0]["id"]
        assert client.post(f"/api/v1/notifications/{notification_id}/read", headers=headers, json={}).status_code == 200
        assert client.get("/api/v1/notifications", headers=headers).json()["data"]["unreadCount"] == 0

        updated = client.patch(
            "/api/v1/notifications/preferences",
            headers=headers,
            json={"chat_completed": False, "schedule_completed": False},
        )
        assert updated.json()["data"]["preferences"]["chatCompleted"] is False
        assert updated.json()["data"]["preferences"]["scheduleCompleted"] is False
        assert client.post(
            f"/api/v1/conversations/{conversation_id}/chat",
            headers=headers,
            json={"content": "这次不要通知"},
        ).status_code == 200

        agent_conversation = client.post(
            "/api/v1/conversations",
            headers=headers,
            json={"title": "Agent 通知", "mode": "agent"},
        ).json()["data"]["conversation"]["id"]
        scheduled = database.create_task(
            user_id=user["id"], conversation_id=agent_conversation, prompt="计划任务",
            attachment_ids=[], source="schedule", schedule_id="schedule-test",
        )
        database.finish_task(str(scheduled["id"]), status="completed", output="计划完成")
        categories = {
            item["category"] for item in client.get("/api/v1/notifications", headers=headers).json()["data"]["notifications"]
        }
        assert categories == set()

        approval = database.create_task(
            user_id=user["id"], conversation_id=agent_conversation, prompt="需要审批",
            attachment_ids=[], source="user",
        )
        database.update_task(str(approval["id"]), status="waiting_approval")
        database.finish_task(str(approval["id"]), status="failed", error="审批超时")
        categories = {
            item["category"] for item in client.get("/api/v1/notifications", headers=headers).json()["data"]["notifications"]
        }
        assert {"approval_required", "task_failed"} <= categories


def test_attachment_workspace_and_native_hermes_schedules(isolated_state):
    _database, runtime = isolated_state
    with TestClient(main.app) as client:
        token, user = register(client)
        headers = {"Authorization": f"Bearer {token}"}
        conversation_id = client.post("/api/conversations", headers=headers, json={"title": "文件任务", "mode": "agent"}).json()["data"]["conversation"]["id"]
        uploaded = client.post(
            f"/api/conversations/{conversation_id}/attachments",
            headers=headers,
            files={"file": ("pixel.png", b"fake-png", "image/png")},
        )
        assert uploaded.status_code == 200
        attachment_id = uploaded.json()["data"]["attachment"]["id"]
        assert client.get(f"/api/attachments/{attachment_id}", headers=headers).content == b"fake-png"

        workspace = runtime.user_paths(user["id"])["container_workspace"]
        assert (workspace / "pixel.png").read_bytes() == b"fake-png"
        (workspace / "packages").mkdir()
        archive = client.post(
            f"/api/workspace/upload?conversation_id={conversation_id}&path=packages",
            headers=headers,
            files={"file": ("source.zip", b"zip-content", "application/zip")},
        )
        assert archive.status_code == 200
        assert archive.json()["data"]["entry"]["path"] == "packages/source.zip"
        assert (workspace / "packages" / "source.zip").read_bytes() == b"zip-content"
        mentions = client.get(
            f"/api/workspace/mentions?conversation_id={conversation_id}&query=source",
            headers=headers,
        ).json()["data"]["entries"]
        assert [item["path"] for item in mentions] == ["packages/source.zip"]
        prepared = main.dispatcher._build_user_input({
            "prompt": "检查 @<packages/source.zip>",
            "attachment_ids": "[]",
            "user_id": user["id"],
        })
        assert "/workspace/packages/source.zip" in prepared

        (workspace / "result.txt").write_text("done", encoding="utf-8")
        listing = client.get(f"/api/workspace?conversation_id={conversation_id}", headers=headers).json()["data"]["entries"]
        assert {item["name"] for item in listing} >= {"packages", "pixel.png", "result.txt"}
        assert next(item for item in listing if item["name"] == "pixel.png")["mimeType"] == "image/png"
        assert next(item for item in listing if item["name"] == "packages")["mimeType"] == ""
        assert client.get(f"/api/workspace?conversation_id={conversation_id}&path=../../", headers=headers).status_code == 400

        (workspace / "generated.docx").write_bytes(b"generated-document")
        (workspace / "screen.png").write_bytes(b"complete-screenshot")
        task_id = "artifact-api-test"
        current = main.now_ms()
        _database.execute(
            """INSERT INTO tasks
            (id, user_id, conversation_id, prompt, attachment_ids, status, created_at, updated_at, started_at)
            VALUES (?, ?, ?, ?, '[]', 'running', ?, ?, ?)""",
            (task_id, user["id"], conversation_id, "生成文档", current, current, current),
        )
        main.dispatcher._finish_completed_task(
            task_id,
            "文档和截图已验证。\n[[artifact:/workspace/generated.docx]]\n"
            "截图文件：`/workspace/screen.png`",
        )
        public = client.get(f"/api/tasks/{task_id}", headers=headers).json()["data"]["task"]
        assert public["output"] == "文件已生成并完成验证，请在下方下载。"
        assert {artifact["path"] for artifact in public["artifacts"]} == {"generated.docx", "screen.png"}
        assert next(
            artifact for artifact in public["artifacts"] if artifact["path"] == "screen.png"
        )["mimeType"] == "image/png"
        downloaded = client.get(
            f"/api/workspace/download?conversation_id={conversation_id}&path=generated.docx",
            headers=headers,
        )
        assert downloaded.status_code == 200
        assert downloaded.content == b"generated-document"
        screenshot = client.get(
            f"/api/workspace/download?conversation_id={conversation_id}&path=screen.png",
            headers=headers,
        )
        assert screenshot.status_code == 200
        assert screenshot.headers["content-type"] == "image/png"
        assert screenshot.content == b"complete-screenshot"

        (workspace / "notes.md").write_text("# 标题\n\n预览内容", encoding="utf-8")
        markdown_preview = client.post(
            f"/api/workspace/preview?conversation_id={conversation_id}&path=notes.md",
            headers=headers,
            json={},
        )
        assert markdown_preview.status_code == 200
        assert markdown_preview.json()["data"]["preview"]["kind"] == "markdown"
        assert "预览内容" in markdown_preview.json()["data"]["preview"]["text"]

        (workspace / "sound.mp3").write_bytes(b"ID3" + b"\0" * 64)
        audio_preview = client.post(
            f"/api/workspace/preview?conversation_id={conversation_id}&path=sound.mp3",
            headers=headers,
            json={},
        )
        assert audio_preview.status_code == 200
        assert audio_preview.json()["data"]["preview"]["kind"] == "audio"

        (workspace / "movie.mp4").write_bytes(b"not-a-video")
        assert client.post(
            f"/api/workspace/preview?conversation_id={conversation_id}&path=movie.mp4",
            headers=headers,
            json={},
        ).status_code == 415

        (workspace / "too-large.txt").write_bytes(b"x" * (15 * 1024 * 1024 + 1))
        too_large = client.post(
            f"/api/workspace/preview?conversation_id={conversation_id}&path=too-large.txt",
            headers=headers,
            json={},
        )
        assert too_large.status_code == 413
        assert "15MB" in too_large.json()["detail"]

        schedules = client.get("/api/schedules", headers=headers)
        assert schedules.status_code == 200
        assert schedules.json()["data"]["schedules"][0]["name"] == "Hermes morning check"
        paused = client.post("/api/schedules/job-test/pause", headers=headers, json={})
        assert paused.status_code == 200
        assert paused.json()["data"]["schedule"]["enabled"] is False
        assert client.post("/api/schedules/job-test/run", headers=headers, json={}).status_code == 200


def test_admin_crud_runtime_and_regular_user_has_no_model_or_knowledge_routes():
    with TestClient(main.app) as client:
        user_token, _user = register(client)
        user_headers = {"Authorization": f"Bearer {user_token}"}
        assert client.post("/api/knowledge-bases", headers=user_headers, json={"name": "forbidden"}).status_code == 404
        assert client.put("/api/user/model", headers=user_headers, json={"model": "custom"}).status_code == 404

        login = client.post("/api/auth/admin-login", json={"username": main.settings.admin_username, "password": main.settings.admin_password})
        admin_headers = {"Authorization": f"Bearer {login.json()['data']['token']}"}
        created = client.post("/api/admin/users", headers=admin_headers, json={
            "email": "managed@example.com", "display_name": "Managed", "password": "Password123!", "status": "active",
        })
        user_id = created.json()["data"]["user"]["id"]
        assert client.patch(f"/api/admin/users/{user_id}", headers=admin_headers, json={"status": "disabled"}).status_code == 200
        runtimes = client.get("/api/admin/runtimes", headers=admin_headers)
        assert runtimes.json()["data"]["workerLimit"] == 2
        assert client.delete(f"/api/admin/users/{user_id}", headers=admin_headers).status_code == 200


def test_admin_can_configure_and_test_split_models(monkeypatch):
    with TestClient(main.app) as client:
        login = client.post(
            "/api/auth/admin-login",
            json={"username": main.settings.admin_username, "password": main.settings.admin_password},
        )
        headers = {"Authorization": f"Bearer {login.json()['data']['token']}"}
        payload = {
            "split_enabled": True,
            "chat": {
                "base_url": "https://chat.example.test/v1",
                "api_key": "chat-api-secret",
                "model": "chat-basic-model",
                "supports_vision": True,
            },
            "coordinator": {
                "base_url": "https://coordinator.example.test/v1",
                "api_key": "coordinator-api-secret",
                "model": "coordinator-model",
                "supports_vision": True,
            },
            "executor": {
                "base_url": "https://executor.example.test/v1",
                "api_key": "executor-api-secret",
                "model": "executor-text-model",
                "supports_vision": False,
                "vision_base_url": "https://vision.example.test/v1",
                "vision_api_key": "vision-api-secret",
                "vision_model": "vision-model",
            },
        }
        updated = client.patch("/api/admin/model-settings", headers=headers, json=payload)
        assert updated.status_code == 200
        models = updated.json()["data"]["models"]
        assert models["splitEnabled"] is True
        assert models["chat"]["model"] == "chat-basic-model"
        assert models["coordinator"]["model"] == "coordinator-model"
        assert models["executor"]["visionApiKeyConfigured"] is True
        assert "apiKey" not in models["coordinator"]

        async def successful_test(role):
            return {"ok": True, "role": role, "model": role + "-model", "latencyMs": 12, "stages": []}

        monkeypatch.setattr(main.model_gateway, "test_connection", successful_test)
        tested = client.post("/api/admin/model-settings/test", headers=headers, json={"role": "chat"})
        assert tested.status_code == 200
        assert tested.json()["data"]["role"] == "chat"


def test_guest_chat_is_public_versioned_and_fixed_model_only():
    with TestClient(main.app) as client:
        response = client.post(
            "/api/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "你好"}]},
        )
        assert response.status_code == 200
        assert response.headers["x-api-version"] == "1"
        assert response.json()["data"]["model"] == main.settings.llm_model
        assert response.json()["data"]["message"]["content"] == "测试回复"
        assert client.get("/api/v1/conversations").status_code == 401
        assert client.post("/api/v1/knowledge-bases", json={"name": "guest"}).status_code == 404
        assert client.post(
            "/api/v1/chat/completions",
            json={"messages": [{"role": "assistant", "content": "不能作为最后一条"}]},
        ).status_code == 400


def test_unique_username_login_reset_and_custom_background():
    with TestClient(main.app) as client:
        credentials = {
            "email": "mumu@example.com",
            "username": "mumu_user",
            "password": "Password123!",
            "purpose": "register",
        }
        assert client.post("/api/v1/auth/request-code", json=credentials).status_code == 200
        registered = client.post(
            "/api/v1/auth/verify",
            json={**credentials, "code": "123456", "display_name": "Mumu"},
        )
        assert registered.status_code == 200
        assert registered.json()["data"]["user"]["username"] == "mumu_user"

        duplicate = client.post(
            "/api/v1/auth/request-code",
            json={
                "email": "different@example.com",
                "username": "MUMU_USER",
                "password": "Password123!",
                "purpose": "register",
            },
        )
        assert duplicate.status_code == 409
        reserved = client.post(
            "/api/v1/auth/request-code",
            json={
                "email": "reserved@example.com",
                "username": "AdMiN",
                "password": "Password123!",
                "purpose": "register",
            },
        )
        assert reserved.status_code == 400
        assert "保留" in reserved.json()["detail"]

        login = {
            "identifier": "mumu_user",
            "password": "Password123!",
            "purpose": "login",
        }
        assert client.post("/api/v1/auth/request-code", json=login).status_code == 200
        verified_login = client.post("/api/v1/auth/verify", json={**login, "code": "123456"})
        assert verified_login.status_code == 200
        token = verified_login.json()["data"]["token"]
        headers = {"Authorization": f"Bearer {token}"}

        image = b"\x89PNG\r\n\x1a\n" + b"test-image"
        uploaded = client.put(
            "/api/v1/profile/background",
            headers=headers,
            files={"file": ("wallpaper.png", image, "image/png")},
        )
        assert uploaded.status_code == 200
        assert client.get("/api/v1/profile/background", headers=headers).content == image
        assert client.put(
            "/api/v1/profile/background",
            headers=headers,
            files={"file": ("invalid.png", b"not-an-image", "image/png")},
        ).status_code == 400
        assert client.delete("/api/v1/profile/background", headers=headers).status_code == 200
        assert client.get("/api/v1/profile/background", headers=headers).status_code == 404

        reset = {"identifier": "mumu@example.com", "purpose": "reset"}
        assert client.post("/api/v1/auth/request-code", json=reset).status_code == 200
        reset_verified = client.post(
            "/api/v1/auth/verify",
            json={**reset, "password": "NewPassword456!", "code": "123456"},
        )
        assert reset_verified.status_code == 200
        assert client.post("/api/v1/auth/request-code", json=login).status_code == 401
        assert client.post(
            "/api/v1/auth/request-code",
            json={**login, "password": "NewPassword456!"},
        ).status_code == 200


def test_legacy_display_name_login_alias_requires_unique_match():
    password = "Password123!"
    main.database.create_user(
        email="legacy@example.com",
        username="legacy_email",
        display_name="LegacyOwner",
        password_hash=main.hash_password(password),
    )
    login = {"identifier": "legacyowner", "password": password, "purpose": "login"}
    with TestClient(main.app) as client:
        requested = client.post("/api/v1/auth/request-code", json=login)
        assert requested.status_code == 200
        verified = client.post("/api/v1/auth/verify", json={**login, "code": "123456"})
        assert verified.status_code == 200
        assert verified.json()["data"]["user"]["username"] == "legacy_email"

        main.database.create_user(
            email="duplicate@example.com",
            username="duplicate_user",
            display_name="LegacyOwner",
            password_hash=main.hash_password(password),
        )
        ambiguous = client.post("/api/v1/auth/request-code", json=login)
        assert ambiguous.status_code == 401


def test_vip_wechat_login_session_can_manage_devices():
    with TestClient(main.app) as client:
        credentials = {
            "email": "wechat@example.com",
            "username": "wechat_user",
            "password": "Password123!",
            "purpose": "register",
            "device_id": "wechat-device-1",
            "device_name": "微信 iPhone",
            "client_platform": "wechat",
        }
        assert client.post("/api/v1/auth/request-code", json=credentials).status_code == 200
        verified = client.post(
            "/api/v1/auth/verify",
            json={**credentials, "code": "123456", "display_name": "微信用户", "trust_device": True},
        )
        assert verified.status_code == 200
        main.database.execute(
            "UPDATE users SET access_tier = 'vip', updated_at = ? WHERE id = ?",
            (main.now_ms(), verified.json()["data"]["user"]["id"]),
        )
        headers = {"Authorization": f"Bearer {verified.json()['data']['token']}"}
        devices = client.get("/api/v1/devices", headers=headers)
        assert devices.status_code == 200

        ticket = client.post("/api/v1/auth/webview-ticket", headers=headers, json={})
        assert ticket.status_code == 200
        raw_ticket = ticket.json()["data"]["ticket"]
        exchanged = client.post(
            "/api/v1/auth/webview-ticket/exchange", json={"ticket": raw_ticket}
        )
        assert exchanged.status_code == 200
        exchanged_headers = {"Authorization": f"Bearer {exchanged.json()['data']['token']}"}
        assert client.get("/api/v1/auth/me", headers=exchanged_headers).status_code == 200
        assert client.post(
            "/api/v1/auth/webview-ticket/exchange", json={"ticket": raw_ticket}
        ).status_code == 400
        assert devices.json()["data"]["devices"] == [
            {
                "id": devices.json()["data"]["devices"][0]["id"],
                "name": "微信 iPhone",
                "platform": "wechat",
                "trusted": True,
                "activeSessions": 1,
                "current": True,
                "createdAt": devices.json()["data"]["devices"][0]["createdAt"],
                "lastSeenAt": devices.json()["data"]["devices"][0]["lastSeenAt"],
            }
        ]


def test_content_addressed_savepoint_create_restore_and_delete(isolated_state):
    _database, runtime = isolated_state
    with TestClient(main.app) as client:
        token, user = register(client)
        headers = {"Authorization": f"Bearer {token}"}
        paths = runtime.user_paths(user["id"])
        workspace = paths["container_workspace"]
        hermes_home = paths["container_hermes"]
        (workspace / "project.txt").write_text("version one", encoding="utf-8")
        (hermes_home / "preferences.txt").write_text("stable", encoding="utf-8")

        created = client.post("/api/v1/savepoints", headers=headers, json={"name": "稳定环境"})
        assert created.status_code == 200
        savepoint = created.json()["data"]["savepoint"]
        assert savepoint["fileCount"] == 2
        assert savepoint["storedBytes"] > 0

        duplicate = client.post("/api/v1/savepoints", headers=headers, json={"name": "未变化"})
        assert duplicate.status_code == 200
        assert duplicate.json()["data"]["savepoint"]["storedBytes"] == 0

        (workspace / "project.txt").write_text("broken", encoding="utf-8")
        (workspace / "extra.txt").write_text("remove me", encoding="utf-8")
        restored = client.post(f"/api/v1/savepoints/{savepoint['id']}/restore", headers=headers, json={})
        assert restored.status_code == 200
        assert (workspace / "project.txt").read_text(encoding="utf-8") == "version one"
        assert not (workspace / "extra.txt").exists()
        assert (hermes_home / "preferences.txt").read_text(encoding="utf-8") == "stable"

        listed = client.get("/api/v1/savepoints", headers=headers).json()["data"]["savepoints"]
        assert [item["name"] for item in listed] == ["稳定环境", "未变化"] or [item["name"] for item in listed] == ["未变化", "稳定环境"]
        assert client.delete(f"/api/v1/savepoints/{savepoint['id']}", headers=headers).status_code == 200


def test_chat_mode_cannot_invoke_agent_browser_workspace_or_schedule():
    with TestClient(main.app) as client:
        token, _user = register(client)
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post("/api/v1/conversations", headers=headers, json={"title": "普通对话"})
        conversation = created.json()["data"]["conversation"]
        assert conversation["mode"] == "chat"
        conversation_id = conversation["id"]

        chat = client.post(
            f"/api/v1/conversations/{conversation_id}/chat",
            headers=headers,
            json={"content": "只使用默认模型"},
        )
        assert chat.status_code == 200
        assert chat.json()["data"]["model"] == main.settings.llm_model
        assert client.post(
            f"/api/v1/conversations/{conversation_id}/tasks",
            headers=headers,
            json={"content": "禁止调用 Hermes", "attachment_ids": []},
        ).status_code == 409
        assert client.get(f"/api/v1/conversations/{conversation_id}/browser", headers=headers).status_code == 409
        assert client.get(
            f"/api/v1/workspace?conversation_id={conversation_id}", headers=headers
        ).status_code == 409
        assert client.post(
            "/api/v1/schedules",
            headers=headers,
            json={
                "title": "禁止计划",
                "prompt": "不能运行",
                "cron": "0 9 * * *",
                "timezone": "Asia/Shanghai",
                "conversation_id": conversation_id,
            },
        ).status_code == 405
        messages = client.get(
            f"/api/v1/conversations/{conversation_id}/messages", headers=headers
        ).json()["data"]["messages"]
        assert [message["role"] for message in messages] == ["user", "assistant"]


def test_activation_code_registration_link_consumption_and_user_delete():
    with TestClient(main.app) as client:
        login = client.post("/api/v1/auth/admin-login", json={"username": main.settings.admin_username, "password": main.settings.admin_password})
        admin_headers = {"Authorization": f"Bearer {login.json()['data']['token']}"}

        settings_response = client.get("/api/v1/admin/settings", headers=admin_headers)
        settings_data = settings_response.json()["data"]
        assert settings_data["registrationEnabled"] is True
        assert settings_data["models"]["splitEnabled"] is False
        assert settings_data["models"]["chat"]["model"] == main.settings.llm_model
        assert settings_data["models"]["executor"]["apiKeyConfigured"] is True
        assert "apiKey" not in settings_data["models"]["executor"]
        assert client.patch("/api/v1/admin/settings", headers=admin_headers, json={}).status_code == 405
        assert client.get("/api/v1/admin/invitations", headers=admin_headers).status_code == 404

        created = client.post(
            "/api/v1/admin/activation-codes",
            headers=admin_headers,
            json={"note": "首批测试", "max_uses": 2, "expires_at": main.now_ms() + 600_000},
        )
        assert created.status_code == 200
        activation = created.json()["data"]["activationCode"]
        activation_id = activation["id"]
        activation_code = activation["code"]
        activation_token = activation["registrationPath"].split("activation=", 1)[1]
        assert activation_code.startswith("VIP-") and activation["codePreview"]

        listed = client.get("/api/v1/admin/activation-codes", headers=admin_headers).json()["data"]["activationCodes"]
        assert listed[0]["id"] == activation_id
        assert listed[0]["code"] == ""
        assert listed[0]["registrationPath"] == activation["registrationPath"]
        updated = client.patch(
            f"/api/v1/admin/activation-codes/{activation_id}",
            headers=admin_headers,
            json={"note": "已更新", "expires_at": None},
        )
        assert updated.status_code == 200
        assert updated.json()["data"]["activationCode"]["note"] == "已更新"
        assert updated.json()["data"]["activationCode"]["expiresAt"] is None

        code_credentials = {
            "email": "activated-code@example.com", "username": "activated_code",
            "password": "Password123!", "purpose": "register", "activation_code": activation_code,
        }
        assert client.post("/api/v1/auth/request-code", json=code_credentials).status_code == 200
        code_verified = client.post(
            "/api/v1/auth/verify",
            json={**code_credentials, "code": "123456", "display_name": "激活码用户"},
        )
        assert code_verified.status_code == 200
        assert code_verified.json()["data"]["user"]["accessTier"] == "vip"

        link_credentials = {
            "email": "activated-link@example.com", "username": "activated_link",
            "password": "Password123!", "purpose": "register", "activation_token": activation_token,
        }
        assert client.post("/api/v1/auth/request-code", json=link_credentials).status_code == 200
        link_verified = client.post(
            "/api/v1/auth/verify",
            json={**link_credentials, "code": "123456", "display_name": "链接用户"},
        )
        assert link_verified.status_code == 200
        link_data = link_verified.json()["data"]
        assert link_data["user"]["accessTier"] == "vip"

        consumed = client.get("/api/v1/admin/activation-codes", headers=admin_headers).json()["data"]["activationCodes"][0]
        assert consumed["useCount"] == 2
        assert client.post(
            "/api/v1/auth/request-code",
            json={
                "email": "second@example.com",
                "username": "second_user",
                "password": "Password123!",
                "purpose": "register",
                "activation_token": activation_token,
            },
        ).status_code == 400

        assert client.delete(
            f"/api/v1/admin/users/{link_data['user']['id']}", headers=admin_headers
        ).status_code == 200
        assert client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {link_data['token']}"}
        ).status_code == 403
        assert client.delete(
            f"/api/v1/admin/activation-codes/{activation_id}", headers=admin_headers
        ).status_code == 200
        assert client.delete(
            f"/api/v1/admin/activation-codes/{activation_id}", headers=admin_headers
        ).status_code == 404


def test_admin_user_delete_removes_only_its_control_frames():
    with TestClient(main.app) as client:
        _token, user = register(client)
        user_root = main.computer_store.frame_root / user["id"]
        frame = user_root / "task-id" / "frame.png"
        frame.parent.mkdir(parents=True)
        frame.write_bytes(b"test-frame")
        protected = main.computer_store.frame_root / "protected-neighbor"
        protected.mkdir()
        (protected / "keep.txt").write_text("keep", encoding="utf-8")

        login = client.post(
            "/api/v1/auth/admin-login",
            json={"username": main.settings.admin_username, "password": main.settings.admin_password},
        )
        admin_headers = {"Authorization": f"Bearer {login.json()['data']['token']}"}
        assert client.delete(f"/api/v1/admin/users/{user['id']}", headers=admin_headers).status_code == 200
        assert not user_root.exists()
        assert (protected / "keep.txt").read_text(encoding="utf-8") == "keep"

        (protected / "keep.txt").unlink()
        protected.rmdir()


def test_openapi_exposes_public_v1_surface_without_admin_routes():
    with TestClient(main.app) as client:
        response = client.get("/api/v1/openapi.json")
        assert response.status_code == 200
        paths = response.json()["paths"]
        assert "/api/v1/chat/completions" in paths
        assert not any(path.startswith("/api/v1/admin/") for path in paths)
        assert not any(path.startswith("/api/") and not path.startswith("/api/v1/") for path in paths)


def test_android_release_metadata_and_appassets_cors(tmp_path, monkeypatch):
    apk = tmp_path / "AIchatMUMU-arm64.apk"
    apk.write_bytes(b"signed-apk-fixture")
    windows_agent = tmp_path / "MiaoxiangComputerAgent-x64.exe"
    windows_agent.write_bytes(b"MZ-windows-agent-fixture")
    monkeypatch.setattr(main, "ANDROID_APK_PATH", apk)
    monkeypatch.setattr(main, "WINDOWS_AGENT_PATH", windows_agent)
    with TestClient(main.app) as client:
        response = client.get("/api/v1/app/android-release")
        assert response.status_code == 200
        release = response.json()["data"]
        assert release == {
            "available": True,
            "versionCode": 24,
            "versionName": "3.8.7",
            "sha256": hashlib.sha256(b"signed-apk-fixture").hexdigest(),
            "sizeBytes": len(b"signed-apk-fixture"),
                "downloadUrl": "https://example.com/downloads/AIchatMUMU-arm64.apk?v=24",
        }
        runtime = client.get("/api/v1/runtime").json()["data"]
        assert runtime["appDownloadUrl"] == "/downloads/AIchatMUMU-arm64.apk?v=24"
        assert runtime["windowsAgentVersion"] == "0.6.2"
        assert runtime["windowsAgentDownloadUrl"] == "/downloads/MiaoxiangComputerAgent-x64.exe?v=17"
        windows_release = client.get("/api/v1/app/windows-release")
        assert windows_release.status_code == 200
        assert windows_release.json()["data"] == {
            "available": True,
            "version": "0.6.2",
            "sha256": hashlib.sha256(b"MZ-windows-agent-fixture").hexdigest(),
            "sizeBytes": len(b"MZ-windows-agent-fixture"),
            "downloadUrl": "https://example.com/downloads/MiaoxiangComputerAgent-x64.exe?v=17",
        }
        downloaded_agent = client.get("/downloads/MiaoxiangComputerAgent-x64.exe")
        assert downloaded_agent.status_code == 200
        assert downloaded_agent.content == b"MZ-windows-agent-fixture"
        assert downloaded_agent.headers["content-type"].startswith("application/vnd.microsoft.portable-executable")
        preflight = client.options(
            "/api/v1/runtime",
            headers={
                "Origin": "https://appassets.androidplatform.net",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == "https://appassets.androidplatform.net"
        denied = client.options(
            "/api/v1/runtime",
            headers={"Origin": "https://untrusted.example", "Access-Control-Request-Method": "GET"},
        )
        assert "access-control-allow-origin" not in denied.headers


def test_legacy_android_apk_remains_downloadable_but_is_not_advertised_as_new_release(tmp_path, monkeypatch):
    release_apk = tmp_path / "MiaoxiangZhiDi-arm64-v3.8.7.apk"
    legacy_apk = tmp_path / "AIchatMUMU-arm64.apk"
    legacy_apk.write_bytes(b"legacy-apk-fixture")
    monkeypatch.setattr(main, "ANDROID_APK_PATH", release_apk)
    monkeypatch.setattr(main, "ANDROID_LEGACY_APK_PATH", legacy_apk)

    with TestClient(main.app) as client:
        release = client.get("/api/v1/app/android-release")
        assert release.status_code == 200
        assert release.json()["data"] == {"available": False}

        download = client.get("/downloads/AIchatMUMU-arm64.apk")
        assert download.status_code == 200
        assert download.content == b"legacy-apk-fixture"
        assert download.headers["content-type"].startswith("application/vnd.android.package-archive")


def test_trusted_vip_device_skips_email_and_android_can_manage_devices():
    with TestClient(main.app) as client:
        credentials = {
            "email": "device@example.com",
            "username": "device_user",
            "password": "Password123!",
            "purpose": "register",
            "device_id": "android-device-1",
            "device_name": "测试手机",
            "client_platform": "android",
            "trust_token": "",
        }
        assert client.post("/api/auth/request-code", json=credentials).status_code == 200
        registered = client.post(
            "/api/auth/verify",
            json={**credentials, "code": "123456", "display_name": "设备用户", "trust_device": True},
        ).json()["data"]
        main.database.execute(
            "UPDATE users SET access_tier = 'vip', updated_at = ? WHERE id = ?",
            (main.now_ms(), registered["user"]["id"]),
        )
        trust_token = registered["deviceCredential"]
        first_headers = {"Authorization": f"Bearer {registered['token']}"}
        assert client.post("/api/auth/logout", headers=first_headers, json={}).status_code == 200
        direct = client.post("/api/auth/request-code", json={
            **credentials,
            "purpose": "login",
            "identifier": "device_user",
            "trust_token": trust_token,
        })
        assert direct.status_code == 200
        assert direct.json()["data"]["verificationRequired"] is False
        android_token = direct.json()["data"]["token"]
        android_headers = {"Authorization": f"Bearer {android_token}"}

        web_login = {
            "identifier": "device_user",
            "password": "Password123!",
            "purpose": "login",
            "device_id": "web-device-1",
            "device_name": "测试浏览器",
            "client_platform": "web",
            "trust_token": "",
        }
        requested = client.post("/api/auth/request-code", json=web_login)
        assert requested.json()["data"]["verificationRequired"] is True
        web = client.post("/api/auth/verify", json={**web_login, "code": "123456", "trust_device": False}).json()["data"]
        assert client.get("/api/devices", headers={"Authorization": f"Bearer {web['token']}"}).status_code == 403

        devices = client.get("/api/devices", headers=android_headers).json()["data"]["devices"]
        web_device = next(item for item in devices if item["platform"] == "web")
        assert client.delete(f"/api/devices/{web_device['id']}", headers=android_headers).status_code == 200
        assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {web['token']}"}).status_code == 401


def test_guest_archive_import_is_idempotent_and_custom_ports_are_user_scoped():
    with TestClient(main.app) as client:
        token, _user = register(client)
        headers = {"Authorization": f"Bearer {token}"}
        payload = {
            "client_import_id": "import-client-123",
            "conversations": [{
                "client_id": "guest-1",
                "title": "登录前的对话",
                "created_at": 1000,
                "messages": [
                    {"role": "user", "content": "登录前问题", "created_at": 1001},
                    {"role": "assistant", "content": "登录前回答", "created_at": 1002},
                ],
            }],
        }
        assert client.post("/api/conversations/import-guest", headers=headers, json=payload).status_code == 200
        assert client.post("/api/conversations/import-guest", headers=headers, json=payload).status_code == 200
        conversations = client.get("/api/conversations", headers=headers).json()["data"]["conversations"]
        imported = [item for item in conversations if item["title"] == "登录前的对话"]
        assert len(imported) == 1
        messages = client.get(f"/api/conversations/{imported[0]['id']}/messages", headers=headers).json()["data"]["messages"]
        assert [item["content"] for item in messages] == ["登录前问题", "登录前回答"]

        opened = client.post("/api/ports/open", headers=headers, json={"port": 21374})
        assert opened.status_code == 200
        assert opened.json()["data"]["port"]["url"].endswith("/user/21374/")
        ports = client.get("/api/ports", headers=headers).json()["data"]["ports"]
        custom = next(item for item in ports if item["port"] == 21374)
        assert custom["configured"] is True
        assert custom["listening"] is False
        assert client.delete("/api/ports/21374", headers=headers).status_code == 200
