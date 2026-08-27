from __future__ import annotations

import asyncio
import threading
from typing import Any

from .adb_target import ADBError, ADBTarget
from .config import AgentConfig
from .credentials import CredentialError
from .observation import OCRParser
from .protocol import AgentConnection
from .runner import TaskRunner
from .safety import EmergencyStop
from .windows_target import WindowsTarget


class AgentRuntime:
    def __init__(self, config: AgentConfig, emergency: EmergencyStop, host: Any):
        self.config = config
        self.emergency = emergency
        self.host = host
        self.thread: threading.Thread | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.connection: AgentConnection | None = None
        self.windows: WindowsTarget | None = None
        self.adb: ADBTarget | None = None

    def targets(self) -> list[dict[str, Any]]:
        targets: list[dict[str, Any]] = [
            {"id": "desktop", "kind": "windows", "name": "Windows 桌面", "status": "online"}
        ]
        if self.adb:
            try:
                targets.extend(self.adb.list_devices(connect=True))
            except Exception as exc:
                self.host.set_status(f"ADB 发现失败：{str(exc)[:120]}")
        return targets

    def start(self) -> None:
        if not self.config.credential_ciphertext or (self.thread and self.thread.is_alive()):
            return
        self.thread = threading.Thread(target=self._thread_main, name="vmss-agent-network", daemon=True)
        self.thread.start()

    def _thread_main(self) -> None:
        try:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            ocr = OCRParser()
            self.windows = WindowsTarget(ocr, self.config.allowed_apps, self.host.control_overlay)
            try:
                self.adb = ADBTarget(ocr, self.config.adb_path, self.config.auto_connect_adb)
            except ADBError:
                self.adb = None
            runner = TaskRunner(
                self.config,
                self.emergency,
                self.windows,
                self.adb,
                self.host.confirm_task,
                activity=self.host.activity,
            )
            self.connection = AgentConnection(self.config, runner, self.targets, self.host.set_status)
            self.loop.run_until_complete(self.connection.run_forever())
        except CredentialError as exc:
            self.host.set_status(str(exc))
        except Exception as exc:
            self.host.set_status(f"后台 Agent 启动失败：{str(exc)[:200]}")
        finally:
            if self.windows:
                self.windows.close()
            if self.loop:
                self.loop.close()
            self.connection = None
            self.windows = None
            self.adb = None
            self.loop = None

    def stop(self) -> None:
        if self.loop and self.connection:
            try:
                future = asyncio.run_coroutine_threadsafe(self.connection.stop(), self.loop)
                future.result(timeout=5)
            except Exception:
                pass
        if self.thread and self.thread.is_alive() and self.thread is not threading.current_thread():
            self.thread.join(timeout=6)
        self.thread = None
