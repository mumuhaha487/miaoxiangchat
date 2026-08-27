from __future__ import annotations

import hashlib
import logging
import os
import re
import subprocess
import threading
import webbrowser
import httpx
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .config import AgentConfig, agent_home
from .process_lock import NamedMutex, activate_window
from .protocol import AGENT_VERSION, bind_account_token, clear_account_binding
from .worker import application_command


WINDOW_TITLE = "妙想之地"
SHELL_MUTEX = r"Local\MiaoxiangZhiDi.DesktopShell"
WORKSPACE_RECOVERY_DELAY_SECONDS = 7


def _desktop_logger() -> logging.Logger:
    log_dir = agent_home() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("miaoxiang.desktop")
    if not logger.handlers:
        handler = logging.FileHandler(log_dir / "desktop.log", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


def _same_origin(url: str, expected: str) -> bool:
    actual_url = urlsplit(url)
    expected_url = urlsplit(expected)
    return (
        actual_url.scheme.lower() == expected_url.scheme.lower()
        and actual_url.hostname == expected_url.hostname
        and (actual_url.port or (443 if actual_url.scheme == "https" else 80))
        == (expected_url.port or (443 if expected_url.scheme == "https" else 80))
    )


def desktop_url(server_url: str) -> str:
    parsed = urlsplit(server_url.rstrip("/") + "/")
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update({"client": "windows-desktop", "desktopVersion": AGENT_VERSION})
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))


class DesktopBridge:
    def __init__(self, config: AgentConfig):
        self._config = config
        self._window: Any = None
        self._lock = threading.Lock()
        self._last_token_digest = ""
        self._auth_token = ""

    def attach(self, window: Any) -> None:
        self._window = window

    def _assert_trusted_page(self) -> None:
        current_url = self._window.get_current_url() if self._window else ""
        if not current_url or not _same_origin(current_url, self._config.server_url):
            raise PermissionError("本机桥接只允许妙想之地页面调用")

    def set_auth_token(self, token: str) -> dict[str, Any]:
        self._assert_trusted_page()
        clean_token = str(token or "").strip()
        if not clean_token or len(clean_token) > 8192:
            raise ValueError("账号会话无效")
        digest = hashlib.sha256(clean_token.encode("utf-8")).hexdigest()
        with self._lock:
            if digest == self._last_token_digest and self._config.credential_ciphertext:
                return {"ok": True, "deviceName": self._config.device_name}
            result = bind_account_token(self._config, clean_token)
            self._last_token_digest = digest
            self._auth_token = clean_token
        device = result.get("device") if isinstance(result.get("device"), dict) else {}
        return {"ok": True, "deviceName": device.get("name") or self._config.device_name}

    def clear_auth_token(self) -> dict[str, bool]:
        self._assert_trusted_page()
        with self._lock:
            clear_account_binding(self._config)
            self._last_token_digest = ""
            self._auth_token = ""
        return {"ok": True}

    def get_app_version(self) -> str:
        self._assert_trusted_page()
        return AGENT_VERSION

    def share_authenticated_file(self, url: str, filename: str, mime_type: str = "") -> dict[str, Any]:
        self._assert_trusted_page()
        parsed = urlsplit(str(url or ""))
        if not _same_origin(url, self._config.server_url) or parsed.path not in {
            "/api/workspace/download", "/api/v1/workspace/download",
        }:
            raise PermissionError("文件分享地址无效")
        with self._lock:
            token = self._auth_token
        if not token:
            raise PermissionError("账号会话已失效")
        safe_name = re.sub(r"[^\w.()\-\u4e00-\u9fff ]", "_", str(filename or "file")).strip(" .")[:180] or "file"
        target_dir = agent_home() / "shared-files"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / safe_name
        with httpx.Client(timeout=120, follow_redirects=False) as client:
            response = client.get(url, headers={"Authorization": f"Bearer {token}"})
            response.raise_for_status()
            if len(response.content) > 100 * 1024 * 1024:
                raise ValueError("文件超过桌面端分享大小限制")
            target.write_bytes(response.content)
        subprocess.Popen(
            ["explorer.exe", f"/select,{target}"],
            close_fds=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return {"ok": True, "path": str(target)}


def _start_worker() -> None:
    subprocess.Popen(
        application_command("--agent-worker"),
        close_fds=True,
        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
    )


def _recover_blank_workspace(
    window: Any,
    app_url: str,
    logger: logging.Logger,
    wait: Any = None,
) -> None:
    wait = wait or threading.Event().wait
    wait(WORKSPACE_RECOVERY_DELAY_SECONDS)
    try:
        if window.evaluate_js("Boolean(document.querySelector('#root > *'))"):
            logger.info("workspace_ready")
            return
        logger.warning("workspace_blank reloading_once")
        window.evaluate_js("location.reload(); true")
        wait(WORKSPACE_RECOVERY_DELAY_SECONDS)
        if window.evaluate_js("Boolean(document.querySelector('#root > *'))"):
            logger.info("workspace_ready_after_reload")
        else:
            logger.error("workspace_still_blank_after_reload")
    except Exception:
        logger.exception("workspace_recovery_failed")


def run_desktop(config: AgentConfig) -> int:
    if os.name != "nt":
        raise RuntimeError("Windows 桌面端只能在 Windows 上运行")
    mutex = NamedMutex(SHELL_MUTEX)
    if mutex.already_exists:
        activate_window(WINDOW_TITLE)
        mutex.close()
        return 0

    logger = _desktop_logger()
    logger.info("desktop_start version=%s", AGENT_VERSION)
    _start_worker()
    try:
        import webview

        pywebview_logger = logging.getLogger("pywebview")
        for handler in logger.handlers:
            if handler not in pywebview_logger.handlers:
                pywebview_logger.addHandler(handler)
        pywebview_logger.setLevel(logging.INFO)
        pywebview_logger.propagate = False

        webview.settings["ALLOW_DOWNLOADS"] = True
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
        debug_port = os.environ.get("VMSS_WEBVIEW_DEBUG_PORT", "").strip()
        if debug_port:
            webview.settings["REMOTE_DEBUGGING_PORT"] = int(debug_port)
        bridge = DesktopBridge(config)
        app_url = desktop_url(config.server_url)
        window = webview.create_window(
            WINDOW_TITLE,
            app_url,
            js_api=bridge,
            width=1440,
            height=900,
            min_size=(960, 640),
            resizable=True,
            background_color="#f5f6f7",
            text_select=True,
        )
        bridge.attach(window)

        def keep_app_origin(window: Any) -> None:
            current_url = window.get_current_url() or ""
            parsed_url = urlsplit(current_url)
            logger.info("page_loaded origin=%s://%s path=%s", parsed_url.scheme, parsed_url.netloc, parsed_url.path)
            if current_url and not _same_origin(current_url, config.server_url):
                webbrowser.open(current_url)
                window.load_url(app_url)

        window.events.shown += lambda: logger.info("window_shown")
        window.events.before_load += lambda: logger.info("navigation_started")
        window.events.loaded += keep_app_origin
        storage_path = agent_home() / "webview"
        storage_path.mkdir(parents=True, exist_ok=True)
        webview.start(
            _recover_blank_workspace,
            (window, app_url, logger),
            gui="edgechromium",
            private_mode=False,
            storage_path=str(storage_path),
        )
        logger.info("desktop_closed")
    except Exception:
        logger.exception("desktop_failed")
        raise
    finally:
        mutex.close()
    return 0
