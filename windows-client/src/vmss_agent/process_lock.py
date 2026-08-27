from __future__ import annotations

import ctypes
from ctypes import wintypes


ERROR_ALREADY_EXISTS = 183


class NamedMutex:
    def __init__(self, name: str) -> None:
        self.handle = ctypes.windll.kernel32.CreateMutexW(None, False, name)
        if not self.handle:
            raise ctypes.WinError()
        self.already_exists = ctypes.windll.kernel32.GetLastError() == ERROR_ALREADY_EXISTS

    def close(self) -> None:
        if self.handle:
            ctypes.windll.kernel32.CloseHandle(self.handle)
            self.handle = None


def activate_window(title: str) -> bool:
    hwnd = ctypes.windll.user32.FindWindowW(None, title)
    if not hwnd:
        return False
    ctypes.windll.user32.ShowWindow(hwnd, 9)
    ctypes.windll.user32.SetForegroundWindow(hwnd)
    return True


def window_process_id(title: str) -> int:
    hwnd = ctypes.windll.user32.FindWindowW(None, title)
    if not hwnd:
        return 0
    process_id = wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
    return int(process_id.value)
