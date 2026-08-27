from __future__ import annotations

import os
import subprocess
import sys
import time
from typing import Any

from PIL import Image, ImageDraw

from .config import AgentConfig
from .cursor_overlay import RemoteCursorOverlay
from .input_monitor import HumanActivityMonitor
from .process_lock import NamedMutex
from .runtime import AgentRuntime
from .safety import EmergencyStop


WORKER_MUTEX = r"Local\MiaoxiangZhiDi.ComputerAgent.Worker"


def application_command(*arguments: str) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, *arguments]
    return [sys.executable, "-m", "vmss_agent.main", *arguments]


class AgentHost:
    def __init__(self, config: AgentConfig):
        if os.name != "nt":
            raise RuntimeError("后台 Agent 只能在 Windows 上运行")
        import tkinter as tk

        self.root = tk.Tk()
        self.root.withdraw()
        self.config = config
        self.emergency = EmergencyStop()
        self.activity = HumanActivityMonitor()
        self.control_overlay = RemoteCursorOverlay(self.root)
        self.runtime = AgentRuntime(config, self.emergency, self)
        self.status = "等待网页端登录"
        self._binding_marker = self._marker(config)
        self._last_config_check = 0.0
        self._hotkeys: Any = None
        self._tray: Any = None
        self._closed = False

        self.activity.start()
        self._start_hotkey()
        self._start_tray()
        if config.credential_ciphertext:
            self.runtime.start()
        self.root.after(50, self._tick)

    @staticmethod
    def _marker(config: AgentConfig) -> tuple[str, str, str]:
        return config.credential_ciphertext, config.account_token_ciphertext, config.server_url

    def set_status(self, value: str) -> None:
        self.status = str(value)

    @staticmethod
    def confirm_task(_title: str, _instruction: str, _target: str) -> bool:
        return True

    def _tick(self) -> None:
        if self._closed:
            return
        self.control_overlay.process()
        now = time.monotonic()
        if now - self._last_config_check >= 1.0:
            self._last_config_check = now
            latest = AgentConfig.load()
            marker = self._marker(latest)
            if marker != self._binding_marker:
                self.runtime.stop()
                self.config = latest
                self._binding_marker = marker
                self.emergency = EmergencyStop()
                self.runtime = AgentRuntime(latest, self.emergency, self)
                if latest.credential_ciphertext:
                    self.runtime.start()
                else:
                    self.status = "等待网页端登录"
        self.root.after(50, self._tick)

    def _emergency_stop(self) -> None:
        self.emergency.stop()
        self.control_overlay.set_control_active(False)
        self.status = "已急停"

    def _start_hotkey(self) -> None:
        try:
            from pynput import keyboard

            self._hotkeys = keyboard.GlobalHotKeys({"<ctrl>+<alt>+<pause>": self._emergency_stop})
            self._hotkeys.start()
        except Exception:
            self._hotkeys = None

    @staticmethod
    def _tray_image() -> Image.Image:
        image = Image.new("RGB", (64, 64), "#f4f5f6")
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((7, 7, 57, 57), radius=10, fill="#596168")
        draw.rectangle((19, 20, 45, 38), fill="#ffffff")
        draw.rectangle((27, 41, 37, 46), fill="#ffffff")
        return image

    @staticmethod
    def _open_workspace() -> None:
        subprocess.Popen(
            application_command("--web-shell"),
            close_fds=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

    def _start_tray(self) -> None:
        try:
            import pystray

            self._tray = pystray.Icon(
                "miaoxiang-computer-agent",
                self._tray_image(),
                "妙想之地",
                menu=pystray.Menu(
                    pystray.MenuItem("打开妙想之地", lambda _icon, _item: self._open_workspace(), default=True),
                    pystray.MenuItem("急停远程控制", lambda _icon, _item: self.root.after(0, self._emergency_stop)),
                    pystray.MenuItem("退出后台 Agent", lambda _icon, _item: self.root.after(0, self.close)),
                ),
            )
            import threading

            threading.Thread(target=self._tray.run, name="vmss-agent-tray", daemon=True).start()
        except Exception:
            self._tray = None

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.runtime.stop()
        self.activity.stop()
        if self._hotkeys:
            self._hotkeys.stop()
        if self._tray:
            self._tray.stop()
        self.control_overlay.close()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def run_worker(config: AgentConfig) -> int:
    mutex = NamedMutex(WORKER_MUTEX)
    if mutex.already_exists:
        mutex.close()
        return 0
    try:
        AgentHost(config).run()
    finally:
        mutex.close()
    return 0
