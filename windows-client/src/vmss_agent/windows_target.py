from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from .credentials import is_interactive_desktop
from .cursor_overlay import NullRemoteCursorOverlay, RemoteCursorOverlay
from .input_motion import pointer_duration, pointer_path, typing_delay
from .observation import OCRParser, Observation, ObservationGuard, UIElement, normalize_box


if os.name == "nt":
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        ctypes.windll.user32.SetProcessDPIAware()


class KeyboardInput(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class InputUnion(ctypes.Union):
    _fields_ = [("ki", KeyboardInput)]


class Input(ctypes.Structure):
    _anonymous_ = ("union",)
    _fields_ = [("type", wintypes.DWORD), ("union", InputUnion)]


@dataclass(frozen=True)
class WindowRegion:
    left: int
    top: int
    width: int
    height: int


class WindowsTarget:
    """Windows capture/input adapter derived from Enikk's MIT-licensed game services."""

    def __init__(
        self,
        ocr: OCRParser,
        allowed_apps: dict[str, str],
        cursor: RemoteCursorOverlay | None = None,
    ):
        if os.name != "nt":
            raise RuntimeError("WindowsTarget 只能在 Windows 上运行")
        import pyautogui

        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.06
        self.pyautogui = pyautogui
        self.ocr = ocr
        self.allowed_apps = dict(allowed_apps)
        self.guard = ObservationGuard()
        self._input_lock = threading.Lock()
        self.cursor = cursor or NullRemoteCursorOverlay()

    def close(self) -> None:
        pass

    def set_control_active(self, active: bool) -> None:
        self.cursor.set_control_active(active)

    @staticmethod
    def _modules():
        import psutil
        import win32gui
        import win32process

        return psutil, win32gui, win32process

    def list_windows(self) -> list[dict[str, Any]]:
        psutil, win32gui, win32process = self._modules()
        windows: list[dict[str, Any]] = []

        def collect(hwnd: int, _context: Any) -> None:
            try:
                if not win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd):
                    return
                left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                if right - left < 100 or bottom - top < 100:
                    return
                title = win32gui.GetWindowText(hwnd).strip()
                if not title:
                    return
                _thread, pid = win32process.GetWindowThreadProcessId(hwnd)
                if pid == os.getpid():
                    return
                try:
                    executable = psutil.Process(pid).name()
                except (psutil.AccessDenied, psutil.NoSuchProcess):
                    executable = ""
                windows.append(
                    {
                        "windowId": int(hwnd),
                        "title": title[:300],
                        "executable": executable[:120],
                        "pid": int(pid),
                        "width": right - left,
                        "height": bottom - top,
                    }
                )
            except Exception:
                return

        win32gui.EnumWindows(collect, None)
        windows.sort(key=lambda item: (item["title"].lower(), item["windowId"]))
        return windows[:300]

    def _region(self, window_id: int) -> WindowRegion:
        _psutil, win32gui, _win32process = self._modules()
        if not window_id or not win32gui.IsWindow(window_id) or not win32gui.IsWindowVisible(window_id):
            raise ValueError("目标窗口不存在或不可见")
        left, top = win32gui.ClientToScreen(window_id, (0, 0))
        client_left, client_top, client_right, client_bottom = win32gui.GetClientRect(window_id)
        width = client_right - client_left
        height = client_bottom - client_top
        if width <= 0 or height <= 0:
            raise ValueError("目标窗口没有可操作的客户区")
        return WindowRegion(left=left, top=top, width=width, height=height)

    def _title(self, window_id: int) -> str:
        _psutil, win32gui, _win32process = self._modules()
        return str(win32gui.GetWindowText(window_id) or "")[:300]

    def _capture_region(self, region: WindowRegion) -> Image.Image:
        import mss

        with mss.mss() as capture:
            shot = capture.grab(
                {"left": region.left, "top": region.top, "width": region.width, "height": region.height}
            )
        return Image.frombytes("RGB", shot.size, shot.rgb)

    def _capture_desktop(self) -> Image.Image:
        import mss

        with mss.mss() as capture:
            monitor = capture.monitors[0]
            shot = capture.grab(monitor)
        return Image.frombytes("RGB", shot.size, shot.rgb)

    def _uia_elements(self, window_id: int, region: WindowRegion) -> list[UIElement]:
        try:
            from pywinauto import Desktop

            root = Desktop(backend="uia").window(handle=window_id)
            wrappers = [root, *root.descendants()]
        except Exception:
            return []
        elements: list[UIElement] = []
        for wrapper in wrappers[:500]:
            try:
                rectangle = wrapper.rectangle()
                x1 = max(0, rectangle.left - region.left)
                y1 = max(0, rectangle.top - region.top)
                x2 = min(region.width, rectangle.right - region.left)
                y2 = min(region.height, rectangle.bottom - region.top)
                if x2 <= x1 or y2 <= y1:
                    continue
                text = str(wrapper.window_text() or "").strip()
                role = str(wrapper.element_info.control_type or "")
                if not text and role not in {"Button", "Edit", "CheckBox", "ComboBox", "MenuItem", "TabItem"}:
                    continue
                elements.append(
                    UIElement(
                        source="uia",
                        bbox=normalize_box((x1, y1, x2, y2), region.width, region.height),
                        text=text[:300],
                        role=role[:80],
                    )
                )
            except Exception:
                continue
        return elements

    def observe(self, window_id: int | None = None) -> Observation:
        self._ensure_safe_desktop()
        if window_id:
            try:
                self._activate(window_id)
            except RuntimeError:
                # Capturing remains useful when Windows foreground-lock rules deny focus.
                pass
            region = self._region(window_id)
            image = self._capture_region(region)
            with ThreadPoolExecutor(max_workers=2) as executor:
                ocr_future = executor.submit(self.ocr.parse, image)
                uia_future = executor.submit(self._uia_elements, window_id, region)
                elements = uia_future.result() + ocr_future.result()
            observation = Observation(
                target_id="desktop",
                target_kind="windows",
                image=image,
                elements=elements,
                window_id=window_id,
                title=self._title(window_id),
            )
        else:
            image = self._capture_desktop()
            observation = Observation(
                target_id="desktop",
                target_kind="windows",
                image=image,
                elements=self.ocr.parse(image),
                title="Windows 桌面",
            )
        return self.guard.replace(observation)

    def last_observation_window_id(self) -> int | None:
        return self.guard.current.window_id if self.guard.current else None

    def _ensure_safe_desktop(self) -> None:
        if not is_interactive_desktop():
            raise RuntimeError("Windows 已锁屏或处于安全桌面，已拒绝远程输入")
        psutil, win32gui, win32process = self._modules()
        foreground = win32gui.GetForegroundWindow()
        if not foreground:
            raise RuntimeError("当前没有可交互桌面")
        try:
            _thread, pid = win32process.GetWindowThreadProcessId(foreground)
            if psutil.Process(pid).name().lower() in {"lockapp.exe", "logonui.exe", "consent.exe"}:
                raise RuntimeError("锁屏、登录或 UAC 安全界面不允许远程控制")
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            pass

    def _activate(self, window_id: int | None) -> None:
        if not window_id:
            return
        psutil, win32gui, win32process = self._modules()
        foreground = win32gui.GetForegroundWindow()
        if foreground and foreground != window_id:
            try:
                _thread, foreground_pid = win32process.GetWindowThreadProcessId(foreground)
                foreground_process = psutil.Process(foreground_pid).name().casefold()
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                foreground_process = ""
            if foreground_process in {
                "searchhost.exe",
                "shellexperiencehost.exe",
                "startmenuexperiencehost.exe",
                "textinputhost.exe",
            }:
                self.pyautogui.press("esc")
                time.sleep(0.15)
                foreground = win32gui.GetForegroundWindow()

        if win32gui.IsIconic(window_id):
            win32gui.ShowWindow(window_id, 9)
        current_thread = int(ctypes.windll.kernel32.GetCurrentThreadId())
        target_thread = int(win32process.GetWindowThreadProcessId(window_id)[0])
        foreground_thread = int(win32process.GetWindowThreadProcessId(foreground)[0]) if foreground else 0
        attached_threads: list[int] = []
        try:
            for thread_id in (foreground_thread, target_thread):
                if thread_id and thread_id != current_thread and thread_id not in attached_threads:
                    win32process.AttachThreadInput(current_thread, thread_id, True)
                    attached_threads.append(thread_id)
            win32gui.ShowWindow(window_id, 5)
            win32gui.BringWindowToTop(window_id)
            win32gui.SetForegroundWindow(window_id)
            try:
                win32gui.SetActiveWindow(window_id)
            except Exception:
                pass
        except Exception:
            try:
                from pywinauto import Desktop

                Desktop(backend="uia").window(handle=window_id).set_focus()
            except Exception as focus_exc:
                raise RuntimeError("无法在不额外点击的情况下激活目标窗口") from focus_exc
        finally:
            for thread_id in reversed(attached_threads):
                try:
                    win32process.AttachThreadInput(current_thread, thread_id, False)
                except Exception:
                    pass
        time.sleep(0.18)
        if win32gui.GetForegroundWindow() != window_id:
            try:
                from pywinauto import Desktop

                Desktop(backend="uia").window(handle=window_id).set_focus()
            except Exception as focus_exc:
                raise RuntimeError("目标窗口未能成为前台窗口") from focus_exc
            time.sleep(0.15)

    def _move_pointer(self, point: tuple[int, int], *, duration: float | None = None) -> None:
        raw_start = self.pyautogui.position()
        start = (int(raw_start[0]), int(raw_start[1]))
        points = pointer_path(start, point)
        total_duration = pointer_duration(start, point) if duration is None else max(0.08, duration)
        delay = total_duration / max(1, len(points))
        for x, y in points:
            self.pyautogui.moveTo(x, y, duration=0)
            self.cursor.show(x, y)
            time.sleep(delay)

    def _click_pointer(self, clicks: int) -> None:
        count = max(1, min(int(clicks), 2))
        time.sleep(0.09)
        for index in range(count):
            point = self.pyautogui.position()
            self.cursor.show(int(point[0]), int(point[1]), click=True)
            self.pyautogui.mouseDown(button="left")
            time.sleep(0.075)
            self.pyautogui.mouseUp(button="left")
            if index + 1 < count:
                time.sleep(0.115)

    @staticmethod
    def _unicode_input(character: str) -> None:
        if not character:
            return
        units = character.encode("utf-16-le", errors="surrogatepass")
        inputs: list[Input] = []
        for offset in range(0, len(units), 2):
            scan = int.from_bytes(units[offset : offset + 2], "little")
            inputs.extend(
                (
                    Input(type=1, ki=KeyboardInput(0, scan, 0x0004, 0, 0)),
                    Input(type=1, ki=KeyboardInput(0, scan, 0x0004 | 0x0002, 0, 0)),
                )
            )
        array = (Input * len(inputs))(*inputs)
        sent = ctypes.windll.user32.SendInput(len(inputs), array, ctypes.sizeof(Input))
        if sent != len(inputs):
            raise ctypes.WinError()

    def _point(self, observation: Observation, x: int, y: int) -> tuple[int, int]:
        if not 0 <= x <= 1000 or not 0 <= y <= 1000:
            raise ValueError("坐标必须位于 0 到 1000")
        if observation.window_id:
            region = self._region(observation.window_id)
            return region.left + int(x / 1000 * region.width), region.top + int(y / 1000 * region.height)
        import mss

        with mss.mss() as capture:
            monitor = capture.monitors[0]
        return monitor["left"] + int(x / 1000 * monitor["width"]), monitor["top"] + int(y / 1000 * monitor["height"])

    def click(self, observation_id: str, x: int, y: int, clicks: int = 1) -> dict[str, Any]:
        self._ensure_safe_desktop()
        observation = self.guard.consume(observation_id)
        point = self._point(observation, x, y)
        with self._input_lock:
            self._activate(observation.window_id)
            self._move_pointer(point)
            self._click_pointer(clicks)
        return {"success": True, "x": x, "y": y, "clicks": clicks}

    def type_text(self, observation_id: str, text: str) -> dict[str, Any]:
        self._ensure_safe_desktop()
        observation = self.guard.consume(observation_id)
        clean = str(text)[:10_000]
        with self._input_lock:
            self._activate(observation.window_id)
            for index, character in enumerate(clean):
                self._unicode_input(character)
                time.sleep(typing_delay(character, index, len(clean)))
        return {"success": True, "characters": len(clean), "inputMethod": "unicode-key-events"}

    def _paste_restoring_clipboard(self, text: str) -> None:
        import win32clipboard
        import win32con

        previous: str | None = None
        win32clipboard.OpenClipboard()
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                previous = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
        finally:
            win32clipboard.CloseClipboard()
        try:
            self.pyautogui.hotkey("ctrl", "v")
            # SendInput returns before slower UI loops have consumed WM_PASTE.
            time.sleep(0.25)
        finally:
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                if previous is not None:
                    win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, previous)
            finally:
                win32clipboard.CloseClipboard()

    def press_key(self, observation_id: str, key: str) -> dict[str, Any]:
        self._ensure_safe_desktop()
        observation = self.guard.consume(observation_id)
        normalized = str(key).lower().strip()
        allowed = set(self.pyautogui.KEYBOARD_KEYS) - {"win", "winleft", "winright"}
        if normalized not in allowed:
            raise ValueError("不支持的按键")
        with self._input_lock:
            self._activate(observation.window_id)
            self.pyautogui.keyDown(normalized)
            time.sleep(0.055)
            self.pyautogui.keyUp(normalized)
        return {"success": True, "key": normalized}

    def hotkey(self, observation_id: str, keys: list[str]) -> dict[str, Any]:
        self._ensure_safe_desktop()
        observation = self.guard.consume(observation_id)
        normalized = [str(item).lower().strip() for item in keys[:4]]
        allowed = set(self.pyautogui.KEYBOARD_KEYS) - {"win", "winleft", "winright"}
        if not normalized or any(item not in allowed for item in normalized):
            raise ValueError("不支持的组合键")
        with self._input_lock:
            self._activate(observation.window_id)
            try:
                for key in normalized:
                    self.pyautogui.keyDown(key)
                    time.sleep(0.035)
                time.sleep(0.06)
            finally:
                for key in reversed(normalized):
                    self.pyautogui.keyUp(key)
                    time.sleep(0.025)
        return {"success": True, "keys": normalized}

    def scroll(self, observation_id: str, x: int, y: int, amount: int) -> dict[str, Any]:
        self._ensure_safe_desktop()
        observation = self.guard.consume(observation_id)
        point = self._point(observation, x, y)
        with self._input_lock:
            self._activate(observation.window_id)
            self._move_pointer(point)
            bounded = max(-20, min(20, int(amount)))
            direction = 1 if bounded > 0 else -1
            for _ in range(abs(bounded)):
                self.pyautogui.scroll(direction)
                time.sleep(0.035)
        return {"success": True, "amount": amount}

    def drag(self, observation_id: str, x1: int, y1: int, x2: int, y2: int) -> dict[str, Any]:
        self._ensure_safe_desktop()
        observation = self.guard.consume(observation_id)
        start = self._point(observation, x1, y1)
        end = self._point(observation, x2, y2)
        with self._input_lock:
            self._activate(observation.window_id)
            self._move_pointer(start)
            self.pyautogui.mouseDown(button="left")
            try:
                self._move_pointer(end, duration=max(0.45, pointer_duration(start, end)))
            finally:
                self.pyautogui.mouseUp(button="left")
            self.cursor.show(*end, click=True)
        return {"success": True, "from": [x1, y1], "to": [x2, y2]}

    def launch_app(self, name: str) -> dict[str, Any]:
        self._ensure_safe_desktop()
        command = self.allowed_apps.get(str(name))
        if not command:
            raise ValueError("应用不在本机允许列表中")
        executable = self._resolve_executable(command)
        subprocess.Popen([executable], close_fds=True)
        return {"success": True, "app": name}

    @staticmethod
    def _resolve_executable(command: str) -> str:
        candidate = str(command).strip()
        if not candidate or any(character in candidate for character in "\r\n\0"):
            raise ValueError("应用启动命令无效")
        direct = Path(candidate)
        if direct.is_file():
            return str(direct)
        located = shutil.which(candidate)
        if located:
            return located
        if direct.name != candidate:
            raise ValueError("应用程序不存在")
        try:
            import winreg

            subkey = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{candidate}"
            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    with winreg.OpenKey(hive, subkey) as key:
                        value = str(winreg.QueryValue(key, None) or "")
                    if Path(value).is_file():
                        return value
                except OSError:
                    continue
        except ImportError:
            pass
        raise ValueError(f"未找到应用程序：{candidate}")
