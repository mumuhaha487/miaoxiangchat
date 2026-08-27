from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import threading
import time
from collections import deque
from typing import Any

import jwt


EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
USERNAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]{2,31}$")
ACTIVATION_CODE_PATTERN = re.compile(r"^VIP(?:-[A-Z0-9]{4}){4}$")
ACTIVATION_LINK_PATTERN = re.compile(r"^([0-9a-f]{32})\.([0-9a-f]{64})$")
RESERVED_USERNAMES = frozenset(
    {
        "admin", "administrator", "api", "assets", "contact", "downloads", "help", "info",
        "mail", "mmhh", "moderator", "official", "owner", "postmaster", "root", "security",
        "service", "staff", "support", "system", "webmaster", "www",
    }
)


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: int, max_keys: int = 10_000):
        self.limit = limit
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self._entries: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def consume(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            if len(self._entries) >= self.max_keys and key not in self._entries:
                stale = [name for name, events in self._entries.items() if not events or events[-1] <= cutoff]
                for name in stale:
                    self._entries.pop(name, None)
                if len(self._entries) >= self.max_keys:
                    return False
            events = self._entries.setdefault(key, deque())
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= self.limit:
                return False
            events.append(now)
            return True


class OneTimeReplayGuard:
    def __init__(self, max_keys: int = 20_000):
        self.max_keys = max_keys
        self._entries: dict[str, float] = {}
        self._lock = threading.Lock()

    def consume(self, key: str, ttl_seconds: int) -> bool:
        now = time.monotonic()
        with self._lock:
            expired = [name for name, expires_at in self._entries.items() if expires_at <= now]
            for name in expired:
                self._entries.pop(name, None)
            if key in self._entries or len(self._entries) >= self.max_keys:
                return False
            self._entries[key] = now + max(1, ttl_seconds)
            return True


def normalize_email(value: str) -> str:
    email = str(value or "").strip().lower()
    if len(email) > 254 or not EMAIL_PATTERN.match(email):
        raise ValueError("请输入有效邮箱地址")
    return email


def normalize_username(value: str) -> str:
    username = str(value or "").strip().casefold()
    if not USERNAME_PATTERN.fullmatch(username):
        raise ValueError("用户名须为 3 至 32 位字母、数字、点、横线或下划线")
    if username in RESERVED_USERNAMES:
        raise ValueError("该用户名为系统保留名称")
    return username


def validate_password(value: str) -> str:
    password = str(value or "")
    if len(password) < 8 or len(password) > 128:
        raise ValueError("密码长度必须为 8 至 128 个字符")
    return password


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return "scrypt$" + base64.urlsafe_b64encode(salt).decode() + "$" + base64.urlsafe_b64encode(derived).decode()


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, salt_text, hash_text = encoded.split("$", 2)
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_text)
        expected = base64.urlsafe_b64decode(hash_text)
        actual = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def issue_access_token(
    secret: str,
    subject: str,
    role: str,
    email: str,
    lifetime_hours: int = 24,
    session_id: str = "",
) -> str:
    now = int(time.time())
    payload = {"sub": subject, "role": role, "email": email, "iat": now, "exp": now + lifetime_hours * 3600}
    if session_id:
        payload["sid"] = session_id
    return jwt.encode(
        payload,
        secret,
        algorithm="HS256",
    )


def decode_access_token(secret: str, token: str) -> dict[str, Any]:
    payload = jwt.decode(token, secret, algorithms=["HS256"])
    if not payload.get("sub") or payload.get("role") not in {"user", "admin"}:
        raise jwt.InvalidTokenError("Invalid token claims")
    return payload


def code_digest(secret: str, email: str, purpose: str, code: str, nonce: str) -> str:
    message = f"{email}:{purpose}:{code}:{nonce}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def opaque_digest(secret: str, namespace: str, value: str) -> str:
    return hmac.new(secret.encode("utf-8"), f"{namespace}:{value}".encode("utf-8"), hashlib.sha256).hexdigest()


def wechat_cloud_login_signature(
    secret: str,
    *,
    app_id: str,
    open_id: str,
    union_id: str,
    timestamp: int,
    nonce: str,
    device_id: str,
    device_name: str,
) -> str:
    values = (app_id, open_id, union_id, str(timestamp), nonce, device_id, device_name)
    canonical = "|".join(f"{len(value.encode('utf-8'))}:{value}" for value in values)
    return hmac.new(secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def normalize_activation_code(value: str) -> str:
    code = str(value or "").strip().upper()
    if not ACTIVATION_CODE_PATTERN.fullmatch(code):
        raise ValueError("激活码格式不正确")
    return code


def activation_code_digest(secret: str, code: str) -> str:
    normalized = normalize_activation_code(code)
    return hmac.new(secret.encode("utf-8"), f"activation:{normalized}".encode("utf-8"), hashlib.sha256).hexdigest()


def activation_registration_token(secret: str, code_id: str) -> str:
    normalized = str(code_id or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{32}", normalized):
        raise ValueError("激活注册链接无效")
    signature = hmac.new(
        secret.encode("utf-8"), f"activation-registration:{normalized}".encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"{normalized}.{signature}"


def verify_activation_registration_token(secret: str, token: str) -> str:
    match = ACTIVATION_LINK_PATTERN.fullmatch(str(token or "").strip().lower())
    if not match:
        raise ValueError("激活注册链接无效")
    code_id, supplied = match.groups()
    expected = activation_registration_token(secret, code_id).rsplit(".", 1)[1]
    if not hmac.compare_digest(supplied, expected):
        raise ValueError("激活注册链接无效")
    return code_id


def browser_controller_token(secret: str, user_id: str) -> str:
    return hmac.new(secret.encode("utf-8"), f"browser:{user_id}".encode(), hashlib.sha256).hexdigest()


def internal_runtime_token(secret: str, user_id: str) -> str:
    encoded = base64.urlsafe_b64encode(user_id.encode("utf-8")).decode().rstrip("=")
    signature = hmac.new(secret.encode("utf-8"), f"runtime:{encoded}".encode(), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


def verify_internal_runtime_token(secret: str, token: str) -> str:
    try:
        encoded, signature = token.split(".", 1)
        expected = hmac.new(secret.encode("utf-8"), f"runtime:{encoded}".encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("invalid signature")
        padded = encoded + "=" * (-len(encoded) % 4)
        user_id = base64.urlsafe_b64decode(padded).decode("utf-8")
        if not user_id:
            raise ValueError("missing user")
        return user_id
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("invalid runtime token") from exc


def issue_browser_scope(secret: str, user_id: str, conversation_id: str, lifetime_seconds: int = 600) -> str:
    payload = {"u": user_id, "c": conversation_id, "e": int(time.time()) + lifetime_seconds}
    encoded = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    signature = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


def verify_browser_scope(secret: str, token: str, conversation_id: str) -> str:
    try:
        encoded, signature = token.split(".", 1)
        expected = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("invalid signature")
        padded = encoded + "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode())
        if payload.get("c") != conversation_id or int(payload.get("e") or 0) < int(time.time()):
            raise ValueError("scope expired")
        user_id = str(payload.get("u") or "")
        if not user_id:
            raise ValueError("missing user")
        return user_id
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise ValueError("浏览器授权无效或已过期") from exc


def issue_runtime_scope(secret: str, user_id: str, scope: str, lifetime_seconds: int = 120) -> str:
    payload = {"u": user_id, "s": scope, "e": int(time.time()) + lifetime_seconds}
    encoded = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    signature = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


def verify_runtime_scope(secret: str, token: str, scope: str) -> str:
    try:
        encoded, signature = token.split(".", 1)
        expected = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("invalid signature")
        padded = encoded + "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode())
        if payload.get("s") != scope or int(payload.get("e") or 0) < int(time.time()):
            raise ValueError("scope expired")
        user_id = str(payload.get("u") or "")
        if not user_id:
            raise ValueError("missing user")
        return user_id
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise ValueError("运行时授权无效或已过期") from exc
