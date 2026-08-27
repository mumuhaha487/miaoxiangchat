from __future__ import annotations

import json
import os
import tempfile
import sqlite3
import time
from dataclasses import replace
from pathlib import Path

import pytest
from docker.errors import NotFound

from app.database import Database
from app.context_manager import context_messages, estimate_tokens, plan_compression
from app.main import choose_automatic_model, settings, write_terminal_stream
from app.runtime_manager import GIB, RuntimeManager, WORKER_SPEC_VERSION, calculate_worker_limit, parse_listening_ports
from app.security import (
    SlidingWindowLimiter,
    decode_access_token,
    hash_password,
    internal_runtime_token,
    issue_access_token,
    issue_browser_scope,
    issue_runtime_scope,
    verify_browser_scope,
    verify_runtime_scope,
    verify_internal_runtime_token,
    verify_password,
)
from app.task_dispatcher import canonical_office_output, collect_task_artifacts, next_schedule_ms


@pytest.mark.asyncio
async def test_close_page_does_not_start_a_browser_for_conversation_deletion(monkeypatch):
    class Containers:
        def get(self, _name):
            raise NotFound("browser is not running")

    class DockerClient:
        containers = Containers()

    manager = RuntimeManager(settings, docker_client=DockerClient())

    async def fail_if_started(_user_id):
        raise AssertionError("deleting a conversation must not start a browser runtime")

    monkeypatch.setattr(manager, "ensure_browser", fail_if_started)
    await manager.close_page("user-without-browser", "conversation-to-delete")


def test_password_tokens_and_scoped_runtime_credentials():
    encoded = hash_password("CorrectHorse123!")
    assert verify_password("CorrectHorse123!", encoded)
    assert not verify_password("wrong-password", encoded)

    secret = "s" * 40
    token = issue_access_token(secret, "user-1", "user", "user@example.com")
    assert decode_access_token(secret, token)["sub"] == "user-1"

    ticket = issue_browser_scope(secret, "user-1", "conversation-1", lifetime_seconds=60)
    assert verify_browser_scope(secret, ticket, "conversation-1") == "user-1"
    with pytest.raises(ValueError):
        verify_browser_scope(secret, ticket, "conversation-2")

    runtime_token = internal_runtime_token(secret, "user-1")
    assert verify_internal_runtime_token(secret, runtime_token) == "user-1"
    with pytest.raises(ValueError):
        verify_internal_runtime_token(secret, runtime_token + "x")

    terminal_ticket = issue_runtime_scope(secret, "user-1", "terminal", lifetime_seconds=60)
    assert verify_runtime_scope(secret, terminal_ticket, "terminal") == "user-1"
    with pytest.raises(ValueError):
        verify_runtime_scope(secret, terminal_ticket, "different-scope")


def test_sliding_window_limiter_enforces_rate_and_key_caps():
    limiter = SlidingWindowLimiter(limit=2, window_seconds=60, max_keys=1)
    assert limiter.consume("login")
    assert limiter.consume("login")
    assert not limiter.consume("login")
    assert not limiter.consume("different-key")


def test_database_task_events_completion_and_cascade():
    with tempfile.TemporaryDirectory() as directory:
        database = Database(Path(directory) / "app.db")
        user = database.create_user(
            email="person@example.com",
            display_name="Person",
            password_hash=hash_password("Password123!"),
        )
        conversation = database.create_conversation(user["id"])
        task = database.create_task(
            user_id=user["id"],
            conversation_id=conversation["id"],
            prompt="inspect the workspace",
            attachment_ids=[],
        )
        assert database.claim_task(task["id"])["status"] == "starting"
        event = database.add_task_event(task["id"], "tool.started", {"tool": "terminal"})
        assert event["event_type"] == "tool.started"
        finished = database.finish_task(task["id"], status="completed", output="done")
        assert finished["status"] == "completed"
        messages = database.all("SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id", (conversation["id"],))
        assert [(row["role"], row["content"]) for row in messages] == [
            ("user", "inspect the workspace"),
            ("assistant", "done"),
        ]
        database.execute("DELETE FROM users WHERE id = ?", (user["id"],))
        assert database.get_task(task["id"]) is None


def test_database_migrates_legacy_conversation_binding_and_control_task_columns():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "legacy.db"
        connection = sqlite3.connect(path)
        try:
            connection.execute(
                """CREATE TABLE conversations (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    mode TEXT NOT NULL DEFAULT 'agent',
                    summary TEXT NOT NULL DEFAULT '',
                    summary_through_message_id INTEGER,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                )"""
            )
            connection.execute(
                """CREATE TABLE control_tasks (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    target_id TEXT NOT NULL DEFAULT 'desktop',
                    target_kind TEXT NOT NULL DEFAULT 'windows',
                    instruction TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    output TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    lease_id TEXT,
                    lease_expires_at INTEGER,
                    last_seq INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    started_at INTEGER,
                    completed_at INTEGER
                )"""
            )
            connection.commit()
        finally:
            connection.close()
        database = Database(path)
        task_columns = {row["name"] for row in database.all("PRAGMA table_info(control_tasks)")}
        assert {"approval_hash", "conversation_id"} <= task_columns
        conversation_columns = {row["name"] for row in database.all("PRAGMA table_info(conversations)")}
        assert {"control_device_id", "control_target_id", "control_target_kind"} <= conversation_columns


def test_database_migration_keeps_pre_tier_users_vip():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "legacy-users.db"
        connection = sqlite3.connect(path)
        try:
            connection.execute(
                """CREATE TABLE users (
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'user',
                    status TEXT NOT NULL DEFAULT 'active',
                    email_verified INTEGER NOT NULL DEFAULT 1,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    last_login_at INTEGER
                )"""
            )
            connection.execute(
                """INSERT INTO users
                (id, email, display_name, password_hash, created_at, updated_at)
                VALUES ('legacy-user', 'legacy@example.com', 'Legacy', 'hash', 1, 1)"""
            )
            connection.commit()
        finally:
            connection.close()

        database = Database(path)
        legacy = database.get_user_by_id("legacy-user")
        assert legacy and legacy["access_tier"] == "vip"
        created = database.create_user(
            email="new@example.com",
            username="new-user",
            display_name="New",
            password_hash="hash",
        )
        # Once the tier column exists, all new users use the new Basic default.
        assert created["access_tier"] == "basic"


def test_task_artifact_markers_are_workspace_scoped_and_persisted():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory) / "workspace"
        root.mkdir()
        (root / "report.docx").write_bytes(b"valid-office-placeholder")
        (root / "empty.pdf").write_bytes(b"")
        outside = Path(directory) / "secret.txt"
        outside.write_text("secret", encoding="utf-8")
        output = "完成\n[[artifact:/workspace/report.docx]]\n[[artifact:/workspace/empty.pdf]]\n[[artifact:/workspace/../secret.txt]]"
        cleaned, artifacts, rejected = collect_task_artifacts(root, output)
        assert cleaned == "完成"
        assert [item["relative_path"] for item in artifacts] == ["report.docx"]
        assert len(rejected) == 2

        database = Database(Path(directory) / "app.db")
        user = database.create_user(email="files@example.com", display_name="Files", password_hash="hash")
        conversation = database.create_conversation(user["id"], mode="agent")
        task = database.create_task(user_id=user["id"], conversation_id=conversation["id"], prompt="生成报告", attachment_ids=[])
        stored = database.replace_task_artifacts(task["id"], artifacts)
        assert stored[0]["filename"] == "report.docx"
        assert database.list_task_artifacts(task["id"])[0]["size_bytes"] == len(b"valid-office-placeholder")


def test_task_artifact_local_markdown_images_are_recorded_for_inline_display():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory) / "workspace"
        root.mkdir()
        (root / "screen.png").write_bytes(b"complete-screenshot")
        (root / "second screen.jpg").write_bytes(b"second-screenshot")
        output = (
            "截图如下：\n![完整截图](sandbox:/workspace/screen.png)\n"
            "另一张截图：`/workspace/second screen.jpg`"
        )

        cleaned, artifacts, rejected = collect_task_artifacts(root, output)

        assert cleaned == "截图如下：\n\n另一张截图："
        assert rejected == []
        assert artifacts == [
            {
                "relative_path": "screen.png",
                "filename": "screen.png",
                "mime_type": "image/png",
                "size_bytes": len(b"complete-screenshot"),
            },
            {
                "relative_path": "second screen.jpg",
                "filename": "second screen.jpg",
                "mime_type": "image/jpeg",
                "size_bytes": len(b"second-screenshot"),
            },
        ]


def test_task_artifacts_recover_from_quality_submission_when_final_reply_is_empty(tmp_path: Path):
    workspace = tmp_path / "workspace"
    previews = workspace / "rendered-slides"
    previews.mkdir(parents=True)
    (workspace / "hotspots.pptx").write_bytes(b"non-empty-pptx")
    (previews / "slide-1.png").write_bytes(b"non-empty-preview")
    (workspace / "quality-submission.json").write_text(json.dumps({
        "deliverables": ["/workspace/hotspots.pptx"],
        "previews": [{"page": 1, "path": "/workspace/rendered-slides/slide-1.png"}],
    }), encoding="utf-8")

    cleaned, artifacts, rejected = collect_task_artifacts(
        workspace,
        "(empty)",
        modified_after_ms=1,
    )

    assert cleaned == "(empty)"
    assert rejected == []
    assert {item["relative_path"] for item in artifacts} == {
        "quality-submission.json",
        "hotspots.pptx",
        "rendered-slides/slide-1.png",
    }


def test_task_artifacts_reject_stale_explicit_and_submission_references(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    stale = workspace / "previous-task.docx"
    stale.write_bytes(b"stale-office-output")
    cutoff_ms = int(time.time() * 1000)
    old_seconds = cutoff_ms / 1000 - 30
    os.utime(stale, (old_seconds, old_seconds))
    submission = workspace / "quality-submission.json"
    submission.write_text(json.dumps({
        "deliverables": ["/workspace/previous-task.docx"],
        "previews": [],
    }), encoding="utf-8")

    cleaned, artifacts, rejected = collect_task_artifacts(
        workspace,
        "已完成\n[[artifact:/workspace/previous-task.docx]]",
        modified_after_ms=cutoff_ms,
    )

    assert cleaned == "已完成"
    assert [item["relative_path"] for item in artifacts] == ["quality-submission.json"]
    assert rejected == ["/workspace/previous-task.docx"]

    stale.touch()
    _, refreshed_artifacts, refreshed_rejected = collect_task_artifacts(
        workspace,
        "[[artifact:/workspace/previous-task.docx]]",
        modified_after_ms=cutoff_ms,
    )
    assert "previous-task.docx" in {item["relative_path"] for item in refreshed_artifacts}
    assert refreshed_rejected == []


def test_office_delivery_output_never_repeats_unverified_model_summary():
    artifacts = [{"filename": "report.docx"}]
    assert canonical_office_output("模型重新编写的事实摘要", artifacts) == "文件已生成并完成验证，请在下方下载。"
    assert canonical_office_output("普通文本结果", [{"filename": "report.txt"}]) == "普通文本结果"


def test_cron_next_run_uses_requested_timezone():
    value = next_schedule_ms("0 9 * * *", "Asia/Shanghai", 1_767_225_600_000)
    assert value > 1_767_225_600_000
    with pytest.raises(ValueError):
        next_schedule_ms("not cron", "Asia/Shanghai")
    with pytest.raises(ValueError):
        next_schedule_ms("0 9 * * *", "Mars/Olympus")


def test_context_compression_keeps_recent_turns_and_summary():
    messages = [
        {"id": index, "role": "user" if index % 2 else "assistant", "content": "上下文" * 8_000}
        for index in range(1, 13)
    ]
    plan = plan_compression(messages, 64_000)
    assert plan.compressed
    assert plan.older
    assert len(plan.recent) >= 4
    assert estimate_tokens(plan.recent) < estimate_tokens(messages)
    prepared = context_messages("已经确认固定模型", plan.recent)
    assert prepared[0]["role"] == "system"
    assert "已经确认固定模型" in prepared[0]["content"]
    assert plan.threshold_tokens == 32_000
    assert plan.target_tokens == 6_400


def test_listening_port_parser_handles_tools_and_proc_fallback():
    output = "\n".join(
        [
            "LISTEN 0 128 0.0.0.0:1443 0.0.0.0:*",
            "[::]:4173",
            "LISTEN 0 128 127.0.0.1:9000 0.0.0.0:*",
            "0.0.0.0:8642",
            "0.0.0.0:1443",
        ]
    )
    assert parse_listening_ports(output) == [1443, 4173]


def test_dynamic_worker_limit_respects_resources_floor_and_ceiling():
    nominal = calculate_worker_limit(
        minimum=2,
        maximum=8,
        dynamic=True,
        cpu_count=20,
        load_one=3.0,
        cpu_reserve=2.0,
        cpu_per_worker=3.0,
        memory_available_bytes=11 * GIB,
        memory_reserve_bytes=4 * GIB,
        memory_per_worker_bytes=int(1.5 * GIB),
    )
    assert nominal == (4, 5, 4)

    pressured = calculate_worker_limit(
        minimum=2,
        maximum=8,
        dynamic=True,
        cpu_count=20,
        load_one=18.0,
        cpu_reserve=2.0,
        cpu_per_worker=3.0,
        memory_available_bytes=2 * GIB,
        memory_reserve_bytes=4 * GIB,
        memory_per_worker_bytes=int(1.5 * GIB),
    )
    assert pressured[0] == 2

    fixed = calculate_worker_limit(
        minimum=2,
        maximum=8,
        dynamic=False,
        cpu_count=1,
        load_one=100.0,
        cpu_reserve=2.0,
        cpu_per_worker=3.0,
        memory_available_bytes=0,
        memory_reserve_bytes=4 * GIB,
        memory_per_worker_bytes=int(1.5 * GIB),
    )
    assert fixed == (8, 8, 8)


def test_worker_config_persists_learning_state_under_hermes_home():
    manager = RuntimeManager(settings, docker_client=object())
    paths = manager.user_paths("persistent-user")
    assert paths["host_skills"] == paths["host_hermes"] / "skills"
    assert paths["host_memories"] == paths["host_hermes"] / "memories"
    assert paths["host_sessions"] == paths["host_hermes"] / "sessions"
    config = manager._worker_config()
    assert "curator:\n  enabled: true" in config
    assert "  consolidate: true" in config
    assert "    keep: 5" in config
    assert "_config_version: 40" in config
    assert "  api_max_retries: 1" in config
    assert "reasoning_effort: max" in config
    assert 'approvals:\n  mode: "off"' in config
    assert "compression:\n  enabled: true\n  threshold: 0.50" in config
    assert "  proactive_prune_tokens: 48000" in config
    assert "security:\n  tirith_enabled: true\n  allow_private_urls: true" in config
    assert "browser:\n  allow_private_urls: true" in config
    assert "memory:\n" in config
    assert "  memory_enabled: false" in config
    assert "  user_profile_enabled: false" in config
    assert WORKER_SPEC_VERSION == "worker-v3.8.7-memory-gate"


def test_builtin_skills_sync_all_users_without_overwriting_user_changes(tmp_path: Path):
    source_root = tmp_path / "builtins"
    source_skill = source_root / "office-skill"
    source_skill.mkdir(parents=True)
    (source_skill / "SKILL.md").write_text("version one", encoding="utf-8")
    local_settings = replace(
        settings,
        data_dir=tmp_path / "data",
        project_host_dir=tmp_path / "host",
        builtin_skills_dir=source_root,
    )
    manager = RuntimeManager(local_settings, docker_client=object())

    user_c_skill = manager.user_paths("user-c")["container_skills"] / "office-skill"
    user_c_skill.mkdir(parents=True)
    (user_c_skill / "notes.txt").write_text("keep this", encoding="utf-8")

    assert manager._sync_builtin_skills_for_users_sync(["user-a", "user-a", "user-b", "user-c"]) == 3
    user_a_skill = manager.user_paths("user-a")["container_skills"] / "office-skill"
    user_b_skill = manager.user_paths("user-b")["container_skills"] / "office-skill"
    assert (user_a_skill / "SKILL.md").read_text(encoding="utf-8") == "version one"
    assert (user_b_skill / "SKILL.md").read_text(encoding="utf-8") == "version one"
    assert (user_c_skill / "SKILL.md").read_text(encoding="utf-8") == "version one"
    assert (user_c_skill / "notes.txt").read_text(encoding="utf-8") == "keep this"

    (source_skill / "SKILL.md").write_text("version two", encoding="utf-8")
    manager._sync_builtin_skills_for_users_sync(["user-a", "user-b", "user-c"])
    assert (user_a_skill / "SKILL.md").read_text(encoding="utf-8") == "version two"
    assert (user_c_skill / "SKILL.md").read_text(encoding="utf-8") == "version one"

    (user_b_skill / "SKILL.md").write_text("user customization", encoding="utf-8")
    (source_skill / "SKILL.md").write_text("version three", encoding="utf-8")
    manager._sync_builtin_skills_for_users_sync(["user-a", "user-b"])
    assert (user_a_skill / "SKILL.md").read_text(encoding="utf-8") == "version three"
    assert (user_b_skill / "SKILL.md").read_text(encoding="utf-8") == "user customization"


def test_terminal_stream_writes_to_docker_socket_wrapper():
    class Socket:
        def __init__(self):
            self.content = b""

        def sendall(self, content: bytes):
            self.content += content

    class SocketWrapper:
        def __init__(self):
            self._sock = Socket()

        def write(self, _content: bytes):
            raise AssertionError("the read-only wrapper must not be used for writes")

    stream = SocketWrapper()
    write_terminal_stream(stream, b"pwd\n")
    assert stream._sock.content == b"pwd\n"


def test_automatic_model_uses_first_general_chat_model():
    assert choose_automatic_model([
        "text-embedding-3-large",
        "legacy-3.7-flash-high",
        "gemini-3-flash-agent",
        "gemini-3-flash",
    ]) == "gemini-3-flash"
