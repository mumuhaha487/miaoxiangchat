from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .conversation_title import summarize_conversation_title


NOTIFICATION_PREFERENCE_KEYS = (
    "chat_completed",
    "agent_completed",
    "schedule_completed",
    "task_failed",
    "approval_required",
    "system",
)
NOTIFICATION_CATEGORY_PREFERENCES = {key: key for key in NOTIFICATION_PREFERENCE_KEYS}


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
    email TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    access_tier TEXT NOT NULL DEFAULT 'basic',
    status TEXT NOT NULL DEFAULT 'active',
    email_verified INTEGER NOT NULL DEFAULT 1,
    background_filename TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    last_login_at INTEGER
);

CREATE TABLE IF NOT EXISTS verification_codes (
    email TEXT NOT NULL,
    purpose TEXT NOT NULL,
    digest TEXT NOT NULL,
    nonce TEXT NOT NULL,
    sent_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (email, purpose)
);

CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    device_key_hash TEXT NOT NULL,
    name TEXT NOT NULL,
    platform TEXT NOT NULL DEFAULT 'web',
    trust_token_hash TEXT NOT NULL DEFAULT '',
    trusted_at INTEGER,
    created_at INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL,
    UNIQUE(user_id, device_key_hash)
);

CREATE INDEX IF NOT EXISTS idx_devices_user_seen
ON devices(user_id, last_seen_at DESC);

CREATE TABLE IF NOT EXISTS auth_sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    created_at INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    revoked_at INTEGER
);

CREATE INDEX IF NOT EXISTS idx_auth_sessions_user_active
ON auth_sessions(user_id, revoked_at, expires_at);

CREATE TABLE IF NOT EXISTS preview_ports (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    port INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (user_id, port)
);

CREATE TABLE IF NOT EXISTS guest_imports (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    client_import_id TEXT NOT NULL,
    imported_count INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (user_id, client_import_id)
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS activation_codes (
    id TEXT PRIMARY KEY,
    code_hash TEXT NOT NULL UNIQUE,
    code_preview TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    max_uses INTEGER NOT NULL DEFAULT 1,
    use_count INTEGER NOT NULL DEFAULT 0,
    expires_at INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_activation_codes_status_created
ON activation_codes(status, created_at DESC);

CREATE TABLE IF NOT EXISTS webview_login_tickets (
    digest TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    session_id TEXT NOT NULL REFERENCES auth_sessions(id) ON DELETE CASCADE,
    expires_at INTEGER NOT NULL,
    used_at INTEGER,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_webview_login_tickets_expiry
ON webview_login_tickets(expires_at, used_at);

CREATE TABLE IF NOT EXISTS wechat_accounts (
    app_id TEXT NOT NULL,
    open_id TEXT NOT NULL,
    union_id TEXT NOT NULL DEFAULT '',
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (app_id, open_id),
    UNIQUE(app_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_wechat_accounts_user
ON wechat_accounts(user_id);

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'chat',
    agent_profile TEXT NOT NULL DEFAULT 'expert',
    summary TEXT NOT NULL DEFAULT '',
    summary_through_message_id INTEGER,
    last_user_instruction TEXT NOT NULL DEFAULT '',
    control_device_id TEXT NOT NULL DEFAULT '',
    control_target_id TEXT NOT NULL DEFAULT '',
    control_target_kind TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_conversations_user_updated
ON conversations(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation
ON messages(conversation_id, id ASC);

CREATE TABLE IF NOT EXISTS conversation_memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    user_content TEXT NOT NULL,
    assistant_content TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE(source, source_id)
);

CREATE INDEX IF NOT EXISTS idx_conversation_memories_conversation
ON conversation_memories(conversation_id, id ASC);

CREATE TABLE IF NOT EXISTS attachments (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    conversation_id TEXT REFERENCES conversations(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_attachments_user_created
ON attachments(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    source TEXT NOT NULL DEFAULT 'user',
    schedule_id TEXT,
    prompt TEXT NOT NULL,
    attachment_ids TEXT NOT NULL DEFAULT '[]',
    agent_profile TEXT NOT NULL DEFAULT 'expert',
    status TEXT NOT NULL DEFAULT 'queued',
    hermes_run_id TEXT,
    worker_name TEXT,
    output TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    coordination_status TEXT NOT NULL DEFAULT '',
    coordination_plan_json TEXT NOT NULL DEFAULT '{}',
    quality_status TEXT NOT NULL DEFAULT '',
    quality_score INTEGER,
    quality_attempt INTEGER NOT NULL DEFAULT 0,
    quality_selected_attempt INTEGER,
    quality_report_json TEXT NOT NULL DEFAULT '{}',
    assistant_message_id INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    started_at INTEGER,
    completed_at INTEGER
);

CREATE INDEX IF NOT EXISTS idx_tasks_user_status_created
ON tasks(user_id, status, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_tasks_conversation_created
ON tasks(conversation_id, created_at ASC);

CREATE TABLE IF NOT EXISTS workflow_categories (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(user_id, name COLLATE NOCASE)
);

CREATE TABLE IF NOT EXISTS workflows (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    category_id TEXT REFERENCES workflow_categories(id) ON DELETE SET NULL,
    source_conversation_id TEXT REFERENCES conversations(id) ON DELETE SET NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    instructions TEXT NOT NULL,
    triggers_json TEXT NOT NULL DEFAULT '[]',
    relative_path TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'validated',
    validation_report_json TEXT NOT NULL DEFAULT '{}',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(user_id, name COLLATE NOCASE)
);

CREATE INDEX IF NOT EXISTS idx_workflows_user_updated
ON workflows(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS skill_records (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'local',
    source_ref TEXT NOT NULL DEFAULT '',
    relative_path TEXT NOT NULL,
    triggers_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'installed',
    validation_report_json TEXT NOT NULL DEFAULT '{}',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(user_id, name COLLATE NOCASE)
);

CREATE INDEX IF NOT EXISTS idx_skill_records_user_updated
ON skill_records(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS capability_shares (
    id TEXT PRIMARY KEY,
    owner_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    item_id TEXT NOT NULL,
    code_hash TEXT NOT NULL UNIQUE,
    code_preview TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    archive_relative_path TEXT NOT NULL DEFAULT '',
    sha256 TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    import_count INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS capability_imports (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    share_id TEXT NOT NULL REFERENCES capability_shares(id) ON DELETE CASCADE,
    imported_item_id TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (user_id, share_id)
);

CREATE TABLE IF NOT EXISTS task_artifacts (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    relative_path TEXT NOT NULL,
    filename TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE(task_id, relative_path)
);

CREATE INDEX IF NOT EXISTS idx_task_artifacts_task
ON task_artifacts(task_id, created_at ASC);

CREATE TABLE IF NOT EXISTS task_quality_attempts (
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    attempt INTEGER NOT NULL,
    score INTEGER NOT NULL,
    passed INTEGER NOT NULL DEFAULT 0,
    output TEXT NOT NULL DEFAULT '',
    report_json TEXT NOT NULL DEFAULT '{}',
    artifacts_json TEXT NOT NULL DEFAULT '[]',
    created_at INTEGER NOT NULL,
    PRIMARY KEY (task_id, attempt)
);

CREATE INDEX IF NOT EXISTS idx_task_quality_attempts_score
ON task_quality_attempts(task_id, score DESC, attempt DESC);

CREATE TABLE IF NOT EXISTS task_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_task_events_task_id
ON task_events(task_id, id ASC);

CREATE TABLE IF NOT EXISTS schedules (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    prompt TEXT NOT NULL,
    cron_expr TEXT NOT NULL,
    timezone TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    next_run_at INTEGER,
    last_run_at INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_schedules_due
ON schedules(status, next_run_at ASC);

CREATE TABLE IF NOT EXISTS savepoints (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    file_count INTEGER NOT NULL,
    logical_bytes INTEGER NOT NULL,
    stored_bytes INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_savepoints_user_created
ON savepoints(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS notification_preferences (
    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    chat_completed INTEGER NOT NULL DEFAULT 1,
    agent_completed INTEGER NOT NULL DEFAULT 1,
    schedule_completed INTEGER NOT NULL DEFAULT 1,
    task_failed INTEGER NOT NULL DEFAULT 1,
    approval_required INTEGER NOT NULL DEFAULT 1,
    system INTEGER NOT NULL DEFAULT 1,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    entity_type TEXT NOT NULL DEFAULT '',
    entity_id TEXT NOT NULL DEFAULT '',
    dedupe_key TEXT NOT NULL,
    read_at INTEGER,
    created_at INTEGER NOT NULL,
    UNIQUE(user_id, dedupe_key)
);

CREATE INDEX IF NOT EXISTS idx_notifications_user_id
ON notifications(user_id, id ASC);

CREATE TABLE IF NOT EXISTS control_devices (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    installation_hash TEXT NOT NULL,
    credential_hash TEXT NOT NULL,
    name TEXT NOT NULL,
    hostname TEXT NOT NULL,
    platform TEXT NOT NULL DEFAULT 'windows',
    agent_version TEXT NOT NULL DEFAULT '',
    capabilities_json TEXT NOT NULL DEFAULT '[]',
    targets_json TEXT NOT NULL DEFAULT '[]',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL,
    revoked_at INTEGER,
    UNIQUE(user_id, installation_hash)
);

CREATE INDEX IF NOT EXISTS idx_control_devices_user_seen
ON control_devices(user_id, revoked_at, last_seen_at DESC);

CREATE TABLE IF NOT EXISTS control_tasks (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    device_id TEXT NOT NULL REFERENCES control_devices(id) ON DELETE CASCADE,
    conversation_id TEXT REFERENCES conversations(id) ON DELETE SET NULL,
    target_id TEXT NOT NULL DEFAULT 'desktop',
    target_kind TEXT NOT NULL DEFAULT 'windows',
    instruction TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    output TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    lease_id TEXT,
    lease_expires_at INTEGER,
    approval_hash TEXT NOT NULL DEFAULT '',
    last_seq INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    started_at INTEGER,
    completed_at INTEGER
);

CREATE INDEX IF NOT EXISTS idx_control_tasks_device_status_created
ON control_tasks(device_id, status, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_control_tasks_user_created
ON control_tasks(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS control_task_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES control_tasks(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    client_event_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    frame_id TEXT,
    created_at INTEGER NOT NULL,
    UNIQUE(task_id, client_event_id)
);

CREATE INDEX IF NOT EXISTS idx_control_task_events_task
ON control_task_events(task_id, id ASC);

CREATE TABLE IF NOT EXISTS control_frames (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES control_tasks(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    relative_path TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_control_frames_task_created
ON control_frames(task_id, created_at DESC);
"""


def now_ms() -> int:
    return int(time.time() * 1000)


def _notification_excerpt(value: str, fallback: str = "") -> str:
    compact = re.sub(r"\s+", " ", value).strip() or fallback
    return compact[:180]


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self.connection() as connection:
            connection.executescript(SCHEMA)
            self._migrate(connection)

    @staticmethod
    def _migrate(connection: sqlite3.Connection) -> None:
        user_columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(users)").fetchall()}
        if "username" not in user_columns:
            connection.execute("ALTER TABLE users ADD COLUMN username TEXT")
        if "background_filename" not in user_columns:
            connection.execute("ALTER TABLE users ADD COLUMN background_filename TEXT NOT NULL DEFAULT ''")
        if "access_tier" not in user_columns:
            # Rows that existed before access tiers were introduced retain their full feature set.
            connection.execute("ALTER TABLE users ADD COLUMN access_tier TEXT NOT NULL DEFAULT 'vip'")
        connection.execute("DROP TABLE IF EXISTS invitations")
        users = connection.execute("SELECT id, email, username FROM users ORDER BY created_at ASC").fetchall()
        used = {str(row["username"] or "").lower() for row in users if row["username"]}
        for row in users:
            if row["username"]:
                continue
            local = str(row["email"] or "user").split("@", 1)[0].lower()
            base = re.sub(r"[^a-z0-9_.-]", "_", local).strip("._-")[:24]
            if len(base) < 3:
                base = f"user_{str(row['id'])[:8]}"
            candidate = base
            index = 2
            while candidate in used:
                candidate = f"{base[:27]}_{index}"
                index += 1
            connection.execute("UPDATE users SET username = ? WHERE id = ?", (candidate, row["id"]))
            used.add(candidate)
        connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username_nocase ON users(username COLLATE NOCASE)")
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(conversations)").fetchall()}
        if "mode" not in columns:
            connection.execute("ALTER TABLE conversations ADD COLUMN mode TEXT NOT NULL DEFAULT 'agent'")
        if "agent_profile" not in columns:
            connection.execute("ALTER TABLE conversations ADD COLUMN agent_profile TEXT NOT NULL DEFAULT 'expert'")
        if "summary" not in columns:
            connection.execute("ALTER TABLE conversations ADD COLUMN summary TEXT NOT NULL DEFAULT ''")
        if "summary_through_message_id" not in columns:
            connection.execute("ALTER TABLE conversations ADD COLUMN summary_through_message_id INTEGER")
        if "last_user_instruction" not in columns:
            connection.execute("ALTER TABLE conversations ADD COLUMN last_user_instruction TEXT NOT NULL DEFAULT ''")
        if "control_device_id" not in columns:
            connection.execute("ALTER TABLE conversations ADD COLUMN control_device_id TEXT NOT NULL DEFAULT ''")
        if "control_target_id" not in columns:
            connection.execute("ALTER TABLE conversations ADD COLUMN control_target_id TEXT NOT NULL DEFAULT ''")
        if "control_target_kind" not in columns:
            connection.execute("ALTER TABLE conversations ADD COLUMN control_target_kind TEXT NOT NULL DEFAULT ''")
        task_columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(tasks)").fetchall()}
        if "agent_profile" not in task_columns:
            connection.execute("ALTER TABLE tasks ADD COLUMN agent_profile TEXT NOT NULL DEFAULT 'expert'")
        if "quality_status" not in task_columns:
            connection.execute("ALTER TABLE tasks ADD COLUMN quality_status TEXT NOT NULL DEFAULT ''")
        if "coordination_status" not in task_columns:
            connection.execute("ALTER TABLE tasks ADD COLUMN coordination_status TEXT NOT NULL DEFAULT ''")
        if "coordination_plan_json" not in task_columns:
            connection.execute("ALTER TABLE tasks ADD COLUMN coordination_plan_json TEXT NOT NULL DEFAULT '{}'")
        if "quality_score" not in task_columns:
            connection.execute("ALTER TABLE tasks ADD COLUMN quality_score INTEGER")
        if "quality_attempt" not in task_columns:
            connection.execute("ALTER TABLE tasks ADD COLUMN quality_attempt INTEGER NOT NULL DEFAULT 0")
        if "quality_selected_attempt" not in task_columns:
            connection.execute("ALTER TABLE tasks ADD COLUMN quality_selected_attempt INTEGER")
        if "quality_report_json" not in task_columns:
            connection.execute("ALTER TABLE tasks ADD COLUMN quality_report_json TEXT NOT NULL DEFAULT '{}'")
        control_task_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(control_tasks)").fetchall()
        }
        if control_task_columns and "approval_hash" not in control_task_columns:
            connection.execute("ALTER TABLE control_tasks ADD COLUMN approval_hash TEXT NOT NULL DEFAULT ''")
        if control_task_columns and "conversation_id" not in control_task_columns:
            connection.execute("ALTER TABLE control_tasks ADD COLUMN conversation_id TEXT")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_control_tasks_conversation_created ON control_tasks(conversation_id, created_at DESC)"
        )
        connection.execute(
            """INSERT OR IGNORE INTO conversation_memories
            (conversation_id, user_id, source, source_id, user_content, assistant_content, created_at)
            SELECT conversation_id, user_id, 'remote', id, instruction, output,
                   COALESCE(completed_at, updated_at, created_at)
            FROM control_tasks
            WHERE conversation_id IS NOT NULL AND status = 'completed' AND output != ''"""
        )
        latest_instructions = connection.execute(
            """SELECT conversation_id, instruction, created_at, source_order, sort_key
            FROM (
                SELECT conversation_id, content AS instruction, created_at,
                       1 AS source_order, CAST(id AS TEXT) AS sort_key
                FROM messages WHERE role = 'user'
                UNION ALL
                SELECT conversation_id, instruction, created_at,
                       2 AS source_order, id AS sort_key
                FROM control_tasks WHERE conversation_id IS NOT NULL
            )
            ORDER BY conversation_id ASC, created_at DESC, source_order DESC, sort_key DESC"""
        ).fetchall()
        seen_conversations: set[str] = set()
        for row in latest_instructions:
            conversation_id = str(row["conversation_id"] or "")
            instruction = str(row["instruction"] or "").strip()
            if not conversation_id or not instruction or conversation_id in seen_conversations:
                continue
            seen_conversations.add(conversation_id)
            connection.execute(
                """UPDATE conversations SET title = ?, last_user_instruction = ?
                WHERE id = ? AND last_user_instruction = ''""",
                (
                    summarize_conversation_title(instruction)[:80],
                    instruction[:50_000],
                    conversation_id,
                ),
            )
        connection.execute("DELETE FROM app_settings WHERE key = 'invite_only_registration'")

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = sqlite3.connect(self.path, timeout=15, check_same_thread=False)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    def one(self, query: str, parameters: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = connection.execute(query, parameters).fetchone()
            return dict(row) if row else None

    def all(self, query: str, parameters: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.connection() as connection:
            return [dict(row) for row in connection.execute(query, parameters).fetchall()]

    def execute(self, query: str, parameters: tuple[Any, ...] = ()) -> int:
        with self.connection() as connection:
            cursor = connection.execute(query, parameters)
            return cursor.rowcount

    def get_app_setting(self, key: str) -> str | None:
        row = self.one("SELECT value FROM app_settings WHERE key = ?", (key,))
        return str(row["value"]) if row else None

    def set_app_setting(self, key: str, value: str) -> None:
        current = now_ms()
        with self.connection() as connection:
            connection.execute(
                """INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
                (key, value, current),
            )

    def create_user(
        self,
        *,
        email: str,
        display_name: str,
        password_hash: str,
        username: str = "",
        role: str = "user",
        status: str = "active",
        access_tier: str = "basic",
        activation_code_id: str | None = None,
    ) -> dict[str, Any]:
        current = now_ms()
        user_id = str(uuid.uuid4())
        if not username:
            local = re.sub(r"[^a-z0-9_.-]", "_", email.split("@", 1)[0].lower()).strip("._-")[:24]
            username = local if len(local) >= 3 else f"user_{user_id[:8]}"
        with self.connection() as connection:
            if activation_code_id:
                consumed = connection.execute(
                    """UPDATE activation_codes SET use_count = use_count + 1, updated_at = ?
                    WHERE id = ? AND status = 'active' AND use_count < max_uses
                    AND (expires_at IS NULL OR expires_at > ?)""",
                    (current, activation_code_id, current),
                )
                if consumed.rowcount != 1:
                    raise ValueError("激活码不可用或已达到使用上限")
            connection.execute(
                """INSERT INTO users
                (id, username, email, display_name, password_hash, role, access_tier, status,
                 email_verified, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                (user_id, username, email, display_name, password_hash, role, access_tier, status, current, current),
            )
        return self.get_user_by_id(user_id) or {}

    def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        return self.one("SELECT * FROM users WHERE email = ?", (email,))

    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        return self.one("SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,))

    def get_user_by_identifier(self, identifier: str) -> dict[str, Any] | None:
        primary = self.one(
            "SELECT * FROM users WHERE email = ? COLLATE NOCASE OR username = ? COLLATE NOCASE",
            (identifier, identifier),
        )
        if primary:
            return primary
        legacy_matches = self.all(
            "SELECT * FROM users WHERE TRIM(display_name) = ? COLLATE NOCASE LIMIT 2",
            (identifier,),
        )
        return legacy_matches[0] if len(legacy_matches) == 1 else None

    def get_user_by_id(self, user_id: str) -> dict[str, Any] | None:
        return self.one("SELECT * FROM users WHERE id = ?", (user_id,))

    def get_wechat_account(self, app_id: str, open_id: str) -> dict[str, Any] | None:
        return self.one(
            """SELECT wa.*, u.status AS user_status FROM wechat_accounts wa
            JOIN users u ON u.id = wa.user_id WHERE wa.app_id = ? AND wa.open_id = ?""",
            (app_id, open_id),
        )

    def link_wechat_account(self, app_id: str, open_id: str, union_id: str, user_id: str) -> None:
        current = now_ms()
        self.execute(
            """INSERT INTO wechat_accounts (app_id, open_id, union_id, user_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(app_id, open_id) DO UPDATE SET union_id=excluded.union_id,
            user_id=excluded.user_id, updated_at=excluded.updated_at""",
            (app_id, open_id, union_id, user_id, current, current),
        )

    def merge_wechat_user(self, source_user_id: str, target_user_id: str, app_id: str, open_id: str) -> None:
        if source_user_id == target_user_id:
            return
        with self.connection() as connection:
            source = connection.execute("SELECT id, access_tier FROM users WHERE id = ?", (source_user_id,)).fetchone()
            target = connection.execute("SELECT id, access_tier FROM users WHERE id = ?", (target_user_id,)).fetchone()
            if not source or not target:
                raise ValueError("待绑定账户不存在")

            # Login sessions from the provisional identity cannot remain valid after the
            # identity is merged. The binding response creates a fresh target session.
            connection.execute("DELETE FROM auth_sessions WHERE user_id = ?", (source_user_id,))
            connection.execute("DELETE FROM devices WHERE user_id = ?", (source_user_id,))
            connection.execute(
                """INSERT OR IGNORE INTO preview_ports (user_id, port, created_at)
                SELECT ?, port, created_at FROM preview_ports WHERE user_id = ?""",
                (target_user_id, source_user_id),
            )
            connection.execute("DELETE FROM preview_ports WHERE user_id = ?", (source_user_id,))
            connection.execute(
                """INSERT INTO guest_imports (user_id, client_import_id, imported_count, created_at)
                SELECT ?, client_import_id, imported_count, created_at FROM guest_imports WHERE user_id = ?
                ON CONFLICT(user_id, client_import_id) DO UPDATE SET
                imported_count = MAX(guest_imports.imported_count, excluded.imported_count)""",
                (target_user_id, source_user_id),
            )
            connection.execute("DELETE FROM guest_imports WHERE user_id = ?", (source_user_id,))

            source_categories = connection.execute(
                "SELECT id, name FROM workflow_categories WHERE user_id = ?",
                (source_user_id,),
            ).fetchall()
            for category in source_categories:
                duplicate = connection.execute(
                    "SELECT id FROM workflow_categories WHERE user_id = ? AND name = ? COLLATE NOCASE",
                    (target_user_id, category["name"]),
                ).fetchone()
                if duplicate:
                    connection.execute(
                        "UPDATE workflows SET category_id = ? WHERE category_id = ?",
                        (duplicate["id"], category["id"]),
                    )
                    connection.execute("DELETE FROM workflow_categories WHERE id = ?", (category["id"],))
                else:
                    connection.execute(
                        "UPDATE workflow_categories SET user_id = ? WHERE id = ?",
                        (target_user_id, category["id"]),
                    )

            for table in ("workflows", "skill_records"):
                rows = connection.execute(
                    f"SELECT id, name FROM {table} WHERE user_id = ? ORDER BY created_at",
                    (source_user_id,),
                ).fetchall()
                for row in rows:
                    base_name = str(row["name"])
                    name = base_name
                    suffix = 2
                    while connection.execute(
                        f"SELECT 1 FROM {table} WHERE user_id = ? AND name = ? COLLATE NOCASE",
                        (target_user_id, name),
                    ).fetchone():
                        name = f"{base_name} ({suffix})"
                        suffix += 1
                    connection.execute(
                        f"UPDATE {table} SET user_id = ?, name = ? WHERE id = ?",
                        (target_user_id, name, row["id"]),
                    )

            connection.execute(
                "DELETE FROM capability_imports WHERE user_id = ? AND share_id IN "
                "(SELECT share_id FROM capability_imports WHERE user_id = ?)",
                (source_user_id, target_user_id),
            )
            connection.execute(
                "UPDATE capability_imports SET user_id = ? WHERE user_id = ?",
                (target_user_id, source_user_id),
            )
            connection.execute(
                "UPDATE capability_shares SET owner_user_id = ? WHERE owner_user_id = ?",
                (target_user_id, source_user_id),
            )

            # A reinstalled Windows agent may exist on both identities. Keep the target
            # device and re-point the source task history before moving the remainder.
            source_devices = connection.execute(
                "SELECT id, installation_hash FROM control_devices WHERE user_id = ?",
                (source_user_id,),
            ).fetchall()
            for source_device in source_devices:
                target_device = connection.execute(
                    "SELECT id FROM control_devices WHERE user_id = ? AND installation_hash = ?",
                    (target_user_id, source_device["installation_hash"]),
                ).fetchone()
                if target_device:
                    connection.execute(
                        "UPDATE control_tasks SET device_id = ? WHERE device_id = ?",
                        (target_device["id"], source_device["id"]),
                    )
                    connection.execute(
                        "UPDATE conversations SET control_device_id = ? WHERE control_device_id = ?",
                        (target_device["id"], source_device["id"]),
                    )
                    connection.execute("DELETE FROM control_devices WHERE id = ?", (source_device["id"],))
                else:
                    connection.execute(
                        "UPDATE control_devices SET user_id = ? WHERE id = ?",
                        (target_user_id, source_device["id"]),
                    )

            connection.execute(
                """DELETE FROM notifications WHERE user_id = ? AND dedupe_key IN
                (SELECT dedupe_key FROM notifications WHERE user_id = ?)""",
                (source_user_id, target_user_id),
            )
            for table in (
                "conversations",
                "conversation_memories",
                "attachments",
                "tasks",
                "task_artifacts",
                "schedules",
                "savepoints",
                "notifications",
                "control_tasks",
                "control_task_events",
                "control_frames",
            ):
                connection.execute(f"UPDATE {table} SET user_id = ? WHERE user_id = ?", (target_user_id, source_user_id))
            source_preferences = connection.execute(
                "SELECT * FROM notification_preferences WHERE user_id = ?",
                (source_user_id,),
            ).fetchone()
            if source_preferences:
                connection.execute(
                    """INSERT OR IGNORE INTO notification_preferences
                    (user_id, chat_completed, agent_completed, schedule_completed, task_failed,
                     approval_required, system, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        target_user_id,
                        source_preferences["chat_completed"],
                        source_preferences["agent_completed"],
                        source_preferences["schedule_completed"],
                        source_preferences["task_failed"],
                        source_preferences["approval_required"],
                        source_preferences["system"],
                        source_preferences["updated_at"],
                    ),
                )
                connection.execute("DELETE FROM notification_preferences WHERE user_id = ?", (source_user_id,))
            connection.execute(
                "UPDATE wechat_accounts SET user_id = ?, updated_at = ? WHERE app_id = ? AND open_id = ?",
                (target_user_id, now_ms(), app_id, open_id),
            )
            if source["access_tier"] == "vip" and target["access_tier"] != "vip":
                connection.execute(
                    "UPDATE users SET access_tier = 'vip', updated_at = ? WHERE id = ?",
                    (now_ms(), target_user_id),
                )
            connection.execute("DELETE FROM users WHERE id = ?", (source_user_id,))

    def redeem_activation_code(self, code_hash: str, user_id: str) -> dict[str, Any]:
        current = now_ms()
        with self.connection() as connection:
            code = connection.execute("SELECT * FROM activation_codes WHERE code_hash = ?", (code_hash,)).fetchone()
            if (
                not code
                or code["status"] != "active"
                or int(code["use_count"] or 0) >= int(code["max_uses"] or 1)
                or (code["expires_at"] and int(code["expires_at"]) <= current)
            ):
                raise ValueError("激活码不可用或已过期")
            consumed = connection.execute(
                """UPDATE activation_codes SET use_count = use_count + 1, updated_at = ?
                WHERE id = ? AND status = 'active' AND use_count < max_uses
                AND (expires_at IS NULL OR expires_at > ?)""",
                (current, code["id"], current),
            )
            if consumed.rowcount != 1:
                raise ValueError("激活码不可用或已达到使用上限")
            updated = connection.execute(
                "UPDATE users SET access_tier = 'vip', updated_at = ? WHERE id = ?",
                (current, user_id),
            )
            if updated.rowcount != 1:
                raise ValueError("账户不存在")
            return dict(code)

    def trusted_device(self, user_id: str, device_key_hash: str, trust_token_hash: str) -> dict[str, Any] | None:
        if not device_key_hash or not trust_token_hash:
            return None
        return self.one(
            """SELECT * FROM devices WHERE user_id = ? AND device_key_hash = ?
            AND trust_token_hash = ? AND trusted_at IS NOT NULL""",
            (user_id, device_key_hash, trust_token_hash),
        )

    def upsert_device(
        self,
        *,
        user_id: str,
        device_key_hash: str,
        name: str,
        platform: str,
        trust_token_hash: str = "",
    ) -> dict[str, Any]:
        current = now_ms()
        device_id = str(uuid.uuid4())
        with self.connection() as connection:
            connection.execute(
                """INSERT INTO devices
                (id, user_id, device_key_hash, name, platform, trust_token_hash, trusted_at, created_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, device_key_hash) DO UPDATE SET
                    name = excluded.name,
                    platform = excluded.platform,
                    trust_token_hash = CASE WHEN excluded.trust_token_hash != '' THEN excluded.trust_token_hash ELSE devices.trust_token_hash END,
                    trusted_at = CASE WHEN excluded.trust_token_hash != '' THEN excluded.trusted_at ELSE devices.trusted_at END,
                    last_seen_at = excluded.last_seen_at""",
                (
                    device_id, user_id, device_key_hash, name[:120] or "未知设备", platform,
                    trust_token_hash, current if trust_token_hash else None, current, current,
                ),
            )
            row = connection.execute(
                "SELECT * FROM devices WHERE user_id = ? AND device_key_hash = ?",
                (user_id, device_key_hash),
            ).fetchone()
        return dict(row) if row else {}

    def create_auth_session(self, user_id: str, device_id: str, expires_at: int) -> dict[str, Any]:
        session_id = str(uuid.uuid4())
        current = now_ms()
        with self.connection() as connection:
            connection.execute(
                """INSERT INTO auth_sessions
                (id, user_id, device_id, created_at, last_seen_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (session_id, user_id, device_id, current, current, expires_at),
            )
            row = connection.execute("SELECT * FROM auth_sessions WHERE id = ?", (session_id,)).fetchone()
        return dict(row) if row else {}

    def create_webview_login_ticket(
        self, digest: str, user_id: str, session_id: str, expires_at: int
    ) -> None:
        current = now_ms()
        with self.connection() as connection:
            connection.execute(
                "DELETE FROM webview_login_tickets WHERE expires_at <= ? OR used_at IS NOT NULL",
                (current,),
            )
            connection.execute(
                """INSERT INTO webview_login_tickets
                (digest, user_id, session_id, expires_at, created_at) VALUES (?, ?, ?, ?, ?)""",
                (digest, user_id, session_id, expires_at, current),
            )

    def consume_webview_login_ticket(self, digest: str) -> dict[str, Any] | None:
        current = now_ms()
        with self.connection() as connection:
            row = connection.execute(
                """SELECT t.*, s.revoked_at AS session_revoked_at,
                          s.expires_at AS session_expires_at, u.email, u.status, u.role
                FROM webview_login_tickets t
                JOIN auth_sessions s ON s.id = t.session_id AND s.user_id = t.user_id
                JOIN users u ON u.id = t.user_id
                WHERE t.digest = ? AND t.used_at IS NULL AND t.expires_at > ?
                  AND s.revoked_at IS NULL AND s.expires_at > ?""",
                (digest, current, current),
            ).fetchone()
            if not row:
                return None
            consumed = connection.execute(
                "UPDATE webview_login_tickets SET used_at = ? WHERE digest = ? AND used_at IS NULL",
                (current, digest),
            )
            return dict(row) if consumed.rowcount == 1 else None

    def get_auth_session(self, session_id: str, user_id: str) -> dict[str, Any] | None:
        current = now_ms()
        return self.one(
            """SELECT s.*, d.name AS device_name, d.platform AS device_platform
            FROM auth_sessions s JOIN devices d ON d.id = s.device_id
            WHERE s.id = ? AND s.user_id = ? AND s.revoked_at IS NULL AND s.expires_at > ?""",
            (session_id, user_id, current),
        )

    def touch_auth_session(self, session_id: str, device_id: str) -> None:
        current = now_ms()
        with self.connection() as connection:
            connection.execute("UPDATE auth_sessions SET last_seen_at = ? WHERE id = ?", (current, session_id))
            connection.execute("UPDATE devices SET last_seen_at = ? WHERE id = ?", (current, device_id))

    def revoke_session(self, session_id: str, user_id: str) -> bool:
        return self.execute(
            "UPDATE auth_sessions SET revoked_at = ? WHERE id = ? AND user_id = ? AND revoked_at IS NULL",
            (now_ms(), session_id, user_id),
        ) > 0

    def revoke_device_sessions(self, device_id: str, user_id: str) -> int:
        return self.execute(
            "UPDATE auth_sessions SET revoked_at = ? WHERE device_id = ? AND user_id = ? AND revoked_at IS NULL",
            (now_ms(), device_id, user_id),
        )

    def revoke_all_sessions(self, user_id: str) -> int:
        return self.execute(
            "UPDATE auth_sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
            (now_ms(), user_id),
        )

    def untrust_all_devices(self, user_id: str) -> int:
        return self.execute(
            "UPDATE devices SET trust_token_hash = '', trusted_at = NULL WHERE user_id = ?",
            (user_id,),
        )

    def list_devices(self, user_id: str) -> list[dict[str, Any]]:
        current = now_ms()
        return self.all(
            """SELECT d.*,
                SUM(CASE WHEN s.revoked_at IS NULL AND s.expires_at > ? THEN 1 ELSE 0 END) AS active_sessions
            FROM devices d LEFT JOIN auth_sessions s ON s.device_id = d.id
            WHERE d.user_id = ?
            GROUP BY d.id ORDER BY d.last_seen_at DESC""",
            (current, user_id),
        )

    def delete_device(self, device_id: str, user_id: str) -> bool:
        return self.execute("DELETE FROM devices WHERE id = ? AND user_id = ?", (device_id, user_id)) > 0

    def configured_ports(self, user_id: str) -> list[int]:
        return [int(row["port"]) for row in self.all(
            "SELECT port FROM preview_ports WHERE user_id = ? ORDER BY port", (user_id,)
        )]

    def add_configured_port(self, user_id: str, port: int) -> None:
        self.execute(
            "INSERT OR IGNORE INTO preview_ports (user_id, port, created_at) VALUES (?, ?, ?)",
            (user_id, port, now_ms()),
        )

    def remove_configured_port(self, user_id: str, port: int) -> bool:
        return self.execute("DELETE FROM preview_ports WHERE user_id = ? AND port = ?", (user_id, port)) > 0

    def import_guest_archive(
        self,
        user_id: str,
        client_import_id: str,
        conversations: list[dict[str, Any]],
    ) -> int:
        current = now_ms()
        with self.connection() as connection:
            existing = connection.execute(
                "SELECT imported_count FROM guest_imports WHERE user_id = ? AND client_import_id = ?",
                (user_id, client_import_id),
            ).fetchone()
            if existing:
                return int(existing["imported_count"])
            imported = 0
            for item in conversations:
                messages = list(item.get("messages") or [])
                if not messages:
                    continue
                conversation_id = str(uuid.uuid4())
                created_at = int(item.get("created_at") or current)
                title = str(item.get("title") or "访客对话")[:80]
                connection.execute(
                    """INSERT INTO conversations
                    (id, user_id, title, mode, created_at, updated_at) VALUES (?, ?, ?, 'chat', ?, ?)""",
                    (conversation_id, user_id, title, created_at, current),
                )
                for message in messages:
                    connection.execute(
                        "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                        (
                            conversation_id,
                            str(message.get("role") or "user"),
                            str(message.get("content") or ""),
                            int(message.get("created_at") or created_at),
                        ),
                    )
                imported += 1
            connection.execute(
                "INSERT INTO guest_imports (user_id, client_import_id, imported_count, created_at) VALUES (?, ?, ?, ?)",
                (user_id, client_import_id, imported, current),
            )
        return imported

    def create_conversation(
        self,
        user_id: str,
        title: str = "新对话",
        mode: str = "chat",
        agent_profile: str = "expert",
    ) -> dict[str, Any]:
        conversation_id = str(uuid.uuid4())
        current = now_ms()
        with self.connection() as connection:
            connection.execute(
                """INSERT INTO conversations
                (id, user_id, title, mode, agent_profile, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (conversation_id, user_id, title[:80], mode, agent_profile, current, current),
            )
        return self.get_conversation(conversation_id, user_id) or {}

    def get_conversation(self, conversation_id: str, user_id: str) -> dict[str, Any] | None:
        return self.one(
            "SELECT * FROM conversations WHERE id = ? AND user_id = ?",
            (conversation_id, user_id),
        )

    def add_message(self, conversation_id: str, role: str, content: str) -> dict[str, Any]:
        current = now_ms()
        with self.connection() as connection:
            cursor = connection.execute(
                "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (conversation_id, role, content, current),
            )
            connection.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (current, conversation_id),
            )
            row = connection.execute("SELECT * FROM messages WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return dict(row) if row else {}

    def record_latest_instruction(self, conversation_id: str, user_id: str, instruction: str, title: str) -> None:
        self.execute(
            """UPDATE conversations SET title = ?, last_user_instruction = ?, updated_at = ?
            WHERE id = ? AND user_id = ?""",
            (title[:80], instruction[:50_000], now_ms(), conversation_id, user_id),
        )

    def add_conversation_memory(
        self,
        *,
        conversation_id: str,
        user_id: str,
        source: str,
        source_id: str,
        user_content: str,
        assistant_content: str,
    ) -> None:
        self.execute(
            """INSERT OR IGNORE INTO conversation_memories
            (conversation_id, user_id, source, source_id, user_content, assistant_content, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                conversation_id,
                user_id,
                source[:24],
                source_id[:120],
                user_content[:100_000],
                assistant_content[:200_000],
                now_ms(),
            ),
        )

    def conversation_memories(self, conversation_id: str) -> list[dict[str, Any]]:
        return self.all(
            "SELECT * FROM conversation_memories WHERE conversation_id = ? ORDER BY id ASC",
            (conversation_id,),
        )

    def user_memories(self, user_id: str, *, limit: int = 80) -> list[dict[str, Any]]:
        rows = self.all(
            """SELECT m.*, c.title AS conversation_title
            FROM conversation_memories AS m
            JOIN conversations AS c ON c.id = m.conversation_id
            WHERE m.user_id = ?
            ORDER BY m.id DESC
            LIMIT ?""",
            (user_id, max(1, min(200, int(limit)))),
        )
        return list(reversed(rows))

    def create_workflow_category(self, user_id: str, name: str, description: str = "") -> dict[str, Any]:
        category_id = str(uuid.uuid4())
        current = now_ms()
        with self.connection() as connection:
            connection.execute(
                """INSERT INTO workflow_categories (id, user_id, name, description, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (category_id, user_id, name[:60], description[:500], current, current),
            )
        return self.one("SELECT * FROM workflow_categories WHERE id = ?", (category_id,)) or {}

    def list_workflow_categories(self, user_id: str) -> list[dict[str, Any]]:
        return self.all(
            "SELECT * FROM workflow_categories WHERE user_id = ? ORDER BY name COLLATE NOCASE",
            (user_id,),
        )

    def create_workflow(
        self,
        *,
        user_id: str,
        name: str,
        description: str,
        instructions: str,
        triggers: list[str],
        relative_path: str,
        category_id: str | None = None,
        source_conversation_id: str | None = None,
        validation_report: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        workflow_id = str(uuid.uuid4())
        current = now_ms()
        with self.connection() as connection:
            connection.execute(
                """INSERT INTO workflows
                (id, user_id, category_id, source_conversation_id, name, description, instructions,
                 triggers_json, relative_path, status, validation_report_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'validated', ?, ?, ?)""",
                (
                    workflow_id, user_id, category_id, source_conversation_id, name[:80], description[:1000],
                    instructions[:100_000], json.dumps(triggers, ensure_ascii=False), relative_path,
                    json.dumps(validation_report or {"ok": True}, ensure_ascii=False), current, current,
                ),
            )
        return self.one("SELECT * FROM workflows WHERE id = ?", (workflow_id,)) or {}

    def list_workflows(self, user_id: str) -> list[dict[str, Any]]:
        return self.all(
            """SELECT w.*, c.name AS category_name FROM workflows w
            LEFT JOIN workflow_categories c ON c.id = w.category_id
            WHERE w.user_id = ? ORDER BY w.updated_at DESC""",
            (user_id,),
        )

    def get_workflow(self, workflow_id: str, user_id: str) -> dict[str, Any] | None:
        return self.one("SELECT * FROM workflows WHERE id = ? AND user_id = ?", (workflow_id, user_id))

    def update_workflow(self, workflow_id: str, user_id: str, **values: Any) -> dict[str, Any] | None:
        allowed = {"name", "description", "instructions", "triggers_json", "category_id", "relative_path", "status", "validation_report_json"}
        selected = {key: value for key, value in values.items() if key in allowed}
        if selected:
            selected["updated_at"] = now_ms()
            fields = ", ".join(f"{key} = ?" for key in selected)
            self.execute(
                f"UPDATE workflows SET {fields} WHERE id = ? AND user_id = ?",
                (*selected.values(), workflow_id, user_id),
            )
        return self.get_workflow(workflow_id, user_id)

    def delete_workflow(self, workflow_id: str, user_id: str) -> dict[str, Any] | None:
        item = self.get_workflow(workflow_id, user_id)
        if item:
            self.execute("DELETE FROM workflows WHERE id = ? AND user_id = ?", (workflow_id, user_id))
        return item

    def delete_workflow_category(self, category_id: str, user_id: str) -> bool:
        deleted = self.execute(
            "DELETE FROM workflow_categories WHERE id = ? AND user_id = ?",
            (category_id, user_id),
        )
        return bool(deleted)

    def upsert_skill_record(
        self,
        *,
        user_id: str,
        name: str,
        description: str,
        source: str,
        source_ref: str,
        relative_path: str,
        triggers: list[str],
        status: str,
        validation_report: dict[str, Any],
    ) -> dict[str, Any]:
        current = now_ms()
        record_id = str(uuid.uuid4())
        with self.connection() as connection:
            connection.execute(
                """INSERT INTO skill_records
                (id, user_id, name, description, source, source_ref, relative_path, triggers_json,
                 status, validation_report_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT DO UPDATE SET description=excluded.description,
                source=excluded.source, source_ref=excluded.source_ref, relative_path=excluded.relative_path,
                triggers_json=excluded.triggers_json, status=excluded.status,
                validation_report_json=excluded.validation_report_json, updated_at=excluded.updated_at""",
                (
                    record_id, user_id, name[:80], description[:1000], source[:32], source_ref[:1000],
                    relative_path, json.dumps(triggers, ensure_ascii=False), status[:32],
                    json.dumps(validation_report, ensure_ascii=False), current, current,
                ),
            )
        return self.one(
            "SELECT * FROM skill_records WHERE user_id = ? AND name = ? COLLATE NOCASE",
            (user_id, name),
        ) or {}

    def list_skill_records(self, user_id: str) -> list[dict[str, Any]]:
        return self.all("SELECT * FROM skill_records WHERE user_id = ? ORDER BY updated_at DESC", (user_id,))

    def get_skill_record(self, skill_id: str, user_id: str) -> dict[str, Any] | None:
        return self.one("SELECT * FROM skill_records WHERE id = ? AND user_id = ?", (skill_id, user_id))

    def delete_skill_record(self, skill_id: str, user_id: str) -> dict[str, Any] | None:
        item = self.get_skill_record(skill_id, user_id)
        if item:
            self.execute("DELETE FROM skill_records WHERE id = ? AND user_id = ?", (skill_id, user_id))
        return item

    def create_capability_share(
        self,
        *,
        owner_user_id: str,
        kind: str,
        item_id: str,
        code_hash: str,
        code_preview: str,
        payload: dict[str, Any],
        archive_relative_path: str,
        sha256: str,
    ) -> dict[str, Any]:
        share_id = str(uuid.uuid4())
        current = now_ms()
        with self.connection() as connection:
            connection.execute(
                """INSERT INTO capability_shares
                (id, owner_user_id, kind, item_id, code_hash, code_preview, payload_json,
                 archive_relative_path, sha256, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    share_id, owner_user_id, kind, item_id, code_hash, code_preview,
                    json.dumps(payload, ensure_ascii=False), archive_relative_path, sha256, current, current,
                ),
            )
        return self.one("SELECT * FROM capability_shares WHERE id = ?", (share_id,)) or {}

    def get_capability_share(self, code_hash: str) -> dict[str, Any] | None:
        return self.one(
            "SELECT * FROM capability_shares WHERE code_hash = ? AND status = 'active'",
            (code_hash,),
        )

    def record_capability_import(self, user_id: str, share_id: str, imported_item_id: str) -> None:
        current = now_ms()
        with self.connection() as connection:
            cursor = connection.execute(
                """INSERT INTO capability_imports (user_id, share_id, imported_item_id, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, share_id) DO NOTHING""",
                (user_id, share_id, imported_item_id, current),
            )
            if cursor.rowcount:
                connection.execute(
                    "UPDATE capability_shares SET import_count = import_count + 1, updated_at = ? WHERE id = ?",
                    (current, share_id),
                )

    def create_attachment(
        self,
        *,
        user_id: str,
        conversation_id: str | None,
        filename: str,
        relative_path: str,
        mime_type: str,
        size_bytes: int,
    ) -> dict[str, Any]:
        attachment_id = str(uuid.uuid4())
        current = now_ms()
        with self.connection() as connection:
            connection.execute(
                """INSERT INTO attachments
                (id, user_id, conversation_id, filename, relative_path, mime_type, size_bytes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (attachment_id, user_id, conversation_id, filename, relative_path, mime_type, size_bytes, current),
            )
        return self.one("SELECT * FROM attachments WHERE id = ?", (attachment_id,)) or {}

    def create_task(
        self,
        *,
        user_id: str,
        conversation_id: str,
        prompt: str,
        attachment_ids: list[str],
        source: str = "user",
        schedule_id: str | None = None,
        agent_profile: str | None = None,
    ) -> dict[str, Any]:
        task_id = str(uuid.uuid4())
        current = now_ms()
        with self.connection() as connection:
            if agent_profile is None:
                row = connection.execute(
                    "SELECT agent_profile FROM conversations WHERE id = ? AND user_id = ?",
                    (conversation_id, user_id),
                ).fetchone()
                agent_profile = str(row["agent_profile"] if row else "expert")
            connection.execute(
                """INSERT INTO tasks
                (id, user_id, conversation_id, source, schedule_id, prompt, attachment_ids, agent_profile,
                 status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)""",
                (
                    task_id, user_id, conversation_id, source, schedule_id, prompt,
                    json.dumps(attachment_ids), agent_profile, current, current,
                ),
            )
            connection.execute(
                "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, 'user', ?, ?)",
                (conversation_id, prompt, current),
            )
            connection.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (current, conversation_id))
        return self.get_task(task_id, user_id) or {}

    def get_task(self, task_id: str, user_id: str | None = None) -> dict[str, Any] | None:
        if user_id is None:
            return self.one("SELECT * FROM tasks WHERE id = ?", (task_id,))
        return self.one("SELECT * FROM tasks WHERE id = ? AND user_id = ?", (task_id, user_id))

    def claim_task(self, task_id: str) -> dict[str, Any] | None:
        current = now_ms()
        with self.connection() as connection:
            cursor = connection.execute(
                "UPDATE tasks SET status = 'starting', started_at = COALESCE(started_at, ?), updated_at = ? WHERE id = ? AND status = 'queued'",
                (current, current, task_id),
            )
            if cursor.rowcount != 1:
                return None
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            return dict(row) if row else None

    def update_task(self, task_id: str, **values: Any) -> None:
        if not values:
            return
        notification_status = values.get("status")
        values["updated_at"] = now_ms()
        fields = ", ".join(f"{key} = ?" for key in values)
        self.execute(f"UPDATE tasks SET {fields} WHERE id = ?", (*values.values(), task_id))
        if notification_status == "waiting_approval":
            task = self.get_task(task_id)
            if task:
                self.create_notification(
                    user_id=str(task["user_id"]),
                    category="approval_required",
                    title="Hermes 等待审批",
                    body=_notification_excerpt(str(task.get("prompt") or "任务需要你的确认")),
                    entity_type="task",
                    entity_id=task_id,
                    dedupe_key=f"task:{task_id}:approval",
                )

    def add_task_event(self, task_id: str, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        current = now_ms()
        with self.connection() as connection:
            cursor = connection.execute(
                "INSERT INTO task_events (task_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?)",
                (task_id, event_type[:100], json.dumps(payload, ensure_ascii=False), current),
            )
            row = connection.execute("SELECT * FROM task_events WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return dict(row) if row else {}

    def replace_task_artifacts(self, task_id: str, artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        current = now_ms()
        with self.connection() as connection:
            task = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if not task:
                return []
            connection.execute("DELETE FROM task_artifacts WHERE task_id = ?", (task_id,))
            for artifact in artifacts:
                connection.execute(
                    """INSERT INTO task_artifacts
                    (id, task_id, user_id, conversation_id, relative_path, filename, mime_type, size_bytes, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        str(uuid.uuid4()), task_id, task["user_id"], task["conversation_id"],
                        str(artifact["relative_path"]), str(artifact["filename"]),
                        str(artifact["mime_type"]), int(artifact["size_bytes"]), current,
                    ),
                )
            rows = connection.execute(
                "SELECT * FROM task_artifacts WHERE task_id = ? ORDER BY created_at, filename",
                (task_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_task_artifacts(self, task_id: str) -> list[dict[str, Any]]:
        return self.all(
            "SELECT * FROM task_artifacts WHERE task_id = ? ORDER BY created_at, filename",
            (task_id,),
        )

    def record_quality_attempt(
        self,
        task_id: str,
        *,
        attempt: int,
        score: int,
        passed: bool,
        output: str,
        report: dict[str, Any],
        artifacts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        current = now_ms()
        with self.connection() as connection:
            connection.execute(
                """INSERT INTO task_quality_attempts
                (task_id, attempt, score, passed, output, report_json, artifacts_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id, attempt) DO UPDATE SET
                score = excluded.score, passed = excluded.passed, output = excluded.output,
                report_json = excluded.report_json, artifacts_json = excluded.artifacts_json,
                created_at = excluded.created_at""",
                (
                    task_id,
                    int(attempt),
                    max(1, min(100, int(score))),
                    1 if passed else 0,
                    output,
                    json.dumps(report, ensure_ascii=False),
                    json.dumps(artifacts, ensure_ascii=False),
                    current,
                ),
            )
            row = connection.execute(
                "SELECT * FROM task_quality_attempts WHERE task_id = ? AND attempt = ?",
                (task_id, int(attempt)),
            ).fetchone()
        return dict(row) if row else {}

    def list_quality_attempts(self, task_id: str) -> list[dict[str, Any]]:
        return self.all(
            "SELECT * FROM task_quality_attempts WHERE task_id = ? ORDER BY attempt ASC",
            (task_id,),
        )

    def best_quality_attempt(self, task_id: str) -> dict[str, Any] | None:
        return self.one(
            """SELECT * FROM task_quality_attempts WHERE task_id = ?
            ORDER BY score DESC, attempt DESC LIMIT 1""",
            (task_id,),
        )

    def finish_task(self, task_id: str, *, status: str, output: str = "", error: str = "") -> dict[str, Any] | None:
        current = now_ms()
        result: dict[str, Any] | None = None
        with self.connection() as connection:
            task = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if not task:
                return None
            assistant_message_id = task["assistant_message_id"]
            if status == "completed" and output and not assistant_message_id:
                cursor = connection.execute(
                    "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, 'assistant', ?, ?)",
                    (task["conversation_id"], output, current),
                )
                assistant_message_id = cursor.lastrowid
                connection.execute(
                    "UPDATE conversations SET updated_at = ? WHERE id = ?",
                    (current, task["conversation_id"]),
                )
            connection.execute(
                """UPDATE tasks SET status = ?, output = ?, error = ?, assistant_message_id = ?,
                completed_at = ?, updated_at = ? WHERE id = ?""",
                (status, output, error, assistant_message_id, current, current, task_id),
            )
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            result = dict(row) if row else None
        if result and status in {"completed", "failed", "cancelled"}:
            source = str(result.get("source") or "user")
            if status == "completed":
                if source != "schedule":
                    self.add_conversation_memory(
                        conversation_id=str(result["conversation_id"]),
                        user_id=str(result["user_id"]),
                        source="agent",
                        source_id=str(result["id"]),
                        user_content=str(result.get("prompt") or ""),
                        assistant_content=output,
                    )
                category = "schedule_completed" if source == "schedule" else "agent_completed"
                title = "定时任务已完成" if source == "schedule" else "Hermes 任务已完成"
                body = _notification_excerpt(output, str(result.get("prompt") or "任务已完成"))
            else:
                category = "task_failed"
                title = "定时任务执行失败" if source == "schedule" else "Hermes 任务未完成"
                body = _notification_excerpt(error, "任务已取消" if status == "cancelled" else "请打开应用查看详情")
            self.create_notification(
                user_id=str(result["user_id"]),
                category=category,
                title=title,
                body=body,
                entity_type="task",
                entity_id=task_id,
                dedupe_key=f"task:{task_id}:{status}",
            )
        return result

    def notification_preferences(self, user_id: str) -> dict[str, Any]:
        current = now_ms()
        with self.connection() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO notification_preferences (user_id, updated_at) VALUES (?, ?)",
                (user_id, current),
            )
            row = connection.execute(
                "SELECT * FROM notification_preferences WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            return dict(row) if row else {}

    def update_notification_preferences(self, user_id: str, values: dict[str, bool]) -> dict[str, Any]:
        selected = {key: value for key, value in values.items() if key in NOTIFICATION_PREFERENCE_KEYS}
        with self.connection() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO notification_preferences (user_id, updated_at) VALUES (?, ?)",
                (user_id, now_ms()),
            )
            if selected:
                fields = ", ".join(f"{key} = ?" for key in selected)
                connection.execute(
                    f"UPDATE notification_preferences SET {fields}, updated_at = ? WHERE user_id = ?",
                    (*[1 if value else 0 for value in selected.values()], now_ms(), user_id),
                )
            row = connection.execute(
                "SELECT * FROM notification_preferences WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            return dict(row) if row else {}

    def create_notification(
        self,
        *,
        user_id: str,
        category: str,
        title: str,
        body: str = "",
        entity_type: str = "",
        entity_id: str = "",
        dedupe_key: str,
    ) -> dict[str, Any] | None:
        preference = NOTIFICATION_CATEGORY_PREFERENCES.get(category)
        if not preference:
            raise ValueError("unknown notification category")
        current = now_ms()
        with self.connection() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO notification_preferences (user_id, updated_at) VALUES (?, ?)",
                (user_id, current),
            )
            enabled = connection.execute(
                f"SELECT {preference} FROM notification_preferences WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if not enabled or not bool(enabled[preference]):
                return None
            cursor = connection.execute(
                """INSERT OR IGNORE INTO notifications
                (user_id, category, title, body, entity_type, entity_id, dedupe_key, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, category, title[:120], body[:500], entity_type[:40], entity_id[:120], dedupe_key[:200], current),
            )
            if cursor.rowcount != 1:
                return None
            row = connection.execute("SELECT * FROM notifications WHERE id = ?", (cursor.lastrowid,)).fetchone()
            return dict(row) if row else None

    def list_notifications(self, user_id: str, *, after: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        preferences = self.notification_preferences(user_id)
        categories = [
            category for category, preference in NOTIFICATION_CATEGORY_PREFERENCES.items()
            if bool(preferences.get(preference, 1))
        ]
        if not categories:
            return []
        placeholders = ",".join("?" for _ in categories)
        return self.all(
            f"""SELECT * FROM notifications WHERE user_id = ? AND id > ?
            AND category IN ({placeholders}) ORDER BY id ASC LIMIT ?""",
            (user_id, after, *categories, max(1, min(limit, 200))),
        )

    def unread_notification_count(self, user_id: str) -> int:
        preferences = self.notification_preferences(user_id)
        categories = [
            category for category, preference in NOTIFICATION_CATEGORY_PREFERENCES.items()
            if bool(preferences.get(preference, 1))
        ]
        if not categories:
            return 0
        placeholders = ", ".join("?" for _ in categories)
        row = self.one(
            f"""SELECT COUNT(*) AS count FROM notifications
            WHERE user_id = ? AND read_at IS NULL AND category IN ({placeholders})""",
            (user_id, *categories),
        )
        return int(row.get("count") or 0) if row else 0

    def mark_notification_read(self, user_id: str, notification_id: int) -> bool:
        return self.execute(
            "UPDATE notifications SET read_at = COALESCE(read_at, ?) WHERE id = ? AND user_id = ?",
            (now_ms(), notification_id, user_id),
        ) == 1

    def mark_all_notifications_read(self, user_id: str) -> int:
        return self.execute(
            "UPDATE notifications SET read_at = ? WHERE user_id = ? AND read_at IS NULL",
            (now_ms(), user_id),
        )

    def create_schedule(
        self,
        *,
        user_id: str,
        conversation_id: str,
        title: str,
        prompt: str,
        cron_expr: str,
        timezone: str,
        next_run_at: int,
    ) -> dict[str, Any]:
        schedule_id = str(uuid.uuid4())
        current = now_ms()
        with self.connection() as connection:
            connection.execute(
                """INSERT INTO schedules
                (id, user_id, conversation_id, title, prompt, cron_expr, timezone, status, next_run_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)""",
                (schedule_id, user_id, conversation_id, title, prompt, cron_expr, timezone, next_run_at, current, current),
            )
        return self.one("SELECT * FROM schedules WHERE id = ?", (schedule_id,)) or {}
