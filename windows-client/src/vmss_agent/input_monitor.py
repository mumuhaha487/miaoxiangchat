from __future__ import annotations

import os
import threading
import time


class HumanActivityMonitor:
    """Tracks physical Windows input while ignoring SendInput-style injected events."""

    def __init__(self):
        self._lock = threading.Lock()
        self._last_activity = 0.0
        self._keyboard = None
        self._mouse = None

    def _mark(self) -> None:
        with self._lock:
            self._last_activity = time.monotonic()

    def reset(self) -> None:
        with self._lock:
            self._last_activity = 0.0

    def recent(self, seconds: float = 1.5) -> bool:
        with self._lock:
            value = self._last_activity
        return bool(value and time.monotonic() - value < seconds)

    def start(self) -> None:
        if os.name != "nt" or self._keyboard or self._mouse:
            return
        from pynput import keyboard, mouse

        def keyboard_filter(_message, data):
            if not int(getattr(data, "flags", 0)) & 0x10:  # LLKHF_INJECTED
                self._mark()
            return True

        def mouse_filter(_message, data):
            if not int(getattr(data, "flags", 0)) & 0x01:  # LLMHF_INJECTED
                self._mark()
            return True

        self._keyboard = keyboard.Listener(win32_event_filter=keyboard_filter)
        self._mouse = mouse.Listener(win32_event_filter=mouse_filter)
        self._keyboard.start()
        self._mouse.start()

    def stop(self) -> None:
        for listener in (self._keyboard, self._mouse):
            if listener:
                listener.stop()
        self._keyboard = None
        self._mouse = None
