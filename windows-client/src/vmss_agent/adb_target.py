from __future__ import annotations

import io
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from defusedxml import ElementTree
from PIL import Image

from .observation import OCRParser, Observation, ObservationGuard, UIElement, normalize_box


_SERIAL_PATTERN = re.compile(r"^[A-Za-z0-9._:\-]{1,200}$")
_BOUNDS_PATTERN = re.compile(r"^\[(\d+),(\d+)\]\[(\d+),(\d+)\]$")
_SAFE_TEXT_PATTERN = re.compile(r"^[A-Za-z0-9 .,_@:/+\-]*$")
_KEY_EVENTS = {
    "back": 4,
    "home": 3,
    "enter": 66,
    "delete": 67,
    "tab": 61,
    "escape": 111,
    "volume_up": 24,
    "volume_down": 25,
}


class ADBError(RuntimeError):
    pass


def _creation_flags() -> int:
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0


def find_adb(explicit: str = "") -> Path | None:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    for value in (os.getenv("ANDROID_HOME"), os.getenv("ANDROID_SDK_ROOT")):
        if value:
            candidates.append(Path(value) / "platform-tools" / ("adb.exe" if os.name == "nt" else "adb"))
    if os.name == "nt":
        candidates.extend(
            [
                Path(r"D:\ruanjian\MuMuPlayer\nx_device\15.0\shell\adb.exe"),
                Path(r"C:\Program Files\Netease\MuMuPlayerGlobal-12.0\shell\adb.exe"),
                Path(r"C:\Program Files\Netease\MuMu Player 12\shell\adb.exe"),
            ]
        )
    path_value = os.getenv("PATH", "")
    for directory in path_value.split(os.pathsep):
        if directory:
            candidates.append(Path(directory) / ("adb.exe" if os.name == "nt" else "adb"))
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved.is_file():
            return resolved
    return None


class ADBTarget:
    """A fixed-command ADB adapter. User/model supplied shell fragments are never executed."""

    def __init__(self, ocr: OCRParser, adb_path: str = "", auto_connect: list[str] | None = None):
        located = find_adb(adb_path)
        if not located:
            raise ADBError("未找到 ADB；请在设置中选择 adb.exe")
        self.adb = located
        self.ocr = ocr
        self.auto_connect = [item for item in (auto_connect or []) if _SERIAL_PATTERN.fullmatch(item)]
        self.guard = ObservationGuard()
        self._known_serials: set[str] = set()

    def _run(self, arguments: list[str], *, timeout: float = 20, binary: bool = False) -> bytes | str:
        process = subprocess.run(
            [str(self.adb), *arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
            creationflags=_creation_flags(),
        )
        if process.returncode != 0:
            error = process.stderr.decode("utf-8", errors="replace").strip()
            raise ADBError((error or "ADB 命令执行失败")[:500])
        return process.stdout if binary else process.stdout.decode("utf-8", errors="replace")

    def connect_configured(self) -> None:
        for endpoint in self.auto_connect[:10]:
            try:
                self._run(["connect", endpoint], timeout=8)
            except (ADBError, subprocess.TimeoutExpired):
                continue

    def list_devices(self, *, connect: bool = True) -> list[dict[str, Any]]:
        if connect:
            self.connect_configured()
        output = str(self._run(["devices", "-l"], timeout=10))
        devices: list[dict[str, Any]] = []
        known: set[str] = set()
        for line in output.splitlines()[1:]:
            columns = line.strip().split()
            if len(columns) < 2 or not _SERIAL_PATTERN.fullmatch(columns[0]):
                continue
            serial, state = columns[0], columns[1]
            if state != "device":
                continue
            attributes = {
                key: value
                for column in columns[2:]
                if ":" in column
                for key, value in [column.split(":", 1)]
            }
            known.add(serial)
            model = attributes.get("model", "Android").replace("_", " ")[:120]
            devices.append(
                {
                    "id": f"adb:{serial}",
                    "kind": "adb",
                    "name": f"{model} ({serial})",
                    "serial": serial,
                    "status": "online",
                }
            )
        self._known_serials = known
        return devices[:30]

    def _serial(self, target_id: str) -> str:
        serial = str(target_id or "")
        if serial.startswith("adb:"):
            serial = serial[4:]
        if not _SERIAL_PATTERN.fullmatch(serial) or serial not in self._known_serials:
            raise ADBError("ADB 设备未授权或已经离线，请重新发现设备")
        return serial

    def _device(self, serial: str, arguments: list[str], *, timeout: float = 20, binary: bool = False):
        return self._run(["-s", serial, *arguments], timeout=timeout, binary=binary)

    def _uia_elements(self, serial: str, width: int, height: int) -> list[UIElement]:
        try:
            self._device(serial, ["shell", "uiautomator", "dump", "/sdcard/vmss-window.xml"], timeout=12)
            encoded = self._device(serial, ["exec-out", "cat", "/sdcard/vmss-window.xml"], timeout=8, binary=True)
            root = ElementTree.fromstring(bytes(encoded))
        except Exception:
            return []
        elements: list[UIElement] = []
        for node in root.iter("node"):
            match = _BOUNDS_PATTERN.fullmatch(str(node.attrib.get("bounds") or ""))
            if not match:
                continue
            x1, y1, x2, y2 = (int(value) for value in match.groups())
            if x2 <= x1 or y2 <= y1:
                continue
            text = str(node.attrib.get("text") or node.attrib.get("content-desc") or "").strip()
            role = str(node.attrib.get("class") or "").rsplit(".", 1)[-1]
            clickable = node.attrib.get("clickable") == "true"
            if not text and not clickable and role not in {"EditText", "Button", "CheckBox", "Switch"}:
                continue
            elements.append(
                UIElement(
                    source="uiautomator",
                    bbox=normalize_box((x1, y1, x2, y2), width, height),
                    text=text[:300],
                    role=role[:80],
                )
            )
        return elements[:500]

    def observe(self, target_id: str) -> Observation:
        serial = self._serial(target_id)
        raw = bytes(self._device(serial, ["exec-out", "screencap", "-p"], timeout=15, binary=True))
        try:
            image = Image.open(io.BytesIO(raw)).convert("RGB")
        except Exception as exc:
            raise ADBError(f"ADB 截图无效: {exc}") from exc
        elements = self._uia_elements(serial, image.width, image.height)
        elements.extend(self.ocr.parse(image))
        observation = Observation(
            target_id=f"adb:{serial}",
            target_kind="adb",
            image=image,
            elements=elements,
            title=f"Android {serial}",
        )
        return self.guard.replace(observation)

    @staticmethod
    def _point(observation: Observation, x: int, y: int) -> tuple[int, int]:
        if not 0 <= int(x) <= 1000 or not 0 <= int(y) <= 1000:
            raise ValueError("坐标必须位于 0 到 1000")
        return int(int(x) / 1000 * observation.image.width), int(int(y) / 1000 * observation.image.height)

    def tap(self, observation_id: str, x: int, y: int) -> dict[str, Any]:
        observation = self.guard.consume(observation_id)
        serial = self._serial(observation.target_id)
        px, py = self._point(observation, x, y)
        self._device(serial, ["shell", "input", "tap", str(px), str(py)], timeout=8)
        return {"success": True, "x": int(x), "y": int(y)}

    def swipe(self, observation_id: str, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 350):
        observation = self.guard.consume(observation_id)
        serial = self._serial(observation.target_id)
        start = self._point(observation, x1, y1)
        end = self._point(observation, x2, y2)
        duration = max(100, min(int(duration_ms), 2000))
        self._device(
            serial,
            ["shell", "input", "swipe", str(start[0]), str(start[1]), str(end[0]), str(end[1]), str(duration)],
            timeout=10,
        )
        return {"success": True, "from": [int(x1), int(y1)], "to": [int(x2), int(y2)]}

    def type_ascii(self, observation_id: str, text: str) -> dict[str, Any]:
        observation = self.guard.consume(observation_id)
        serial = self._serial(observation.target_id)
        clean = str(text)[:1000]
        if not _SAFE_TEXT_PATTERN.fullmatch(clean):
            raise ValueError("ADB 安全输入仅支持常用 ASCII 字母、数字和标点")
        encoded = clean.replace(" ", "%s")
        self._device(serial, ["shell", "input", "text", encoded], timeout=12)
        return {"success": True, "characters": len(clean)}

    def press_key(self, observation_id: str, key: str) -> dict[str, Any]:
        observation = self.guard.consume(observation_id)
        serial = self._serial(observation.target_id)
        normalized = str(key).lower().strip()
        if normalized not in _KEY_EVENTS:
            raise ValueError("不支持的 ADB 按键")
        self._device(serial, ["shell", "input", "keyevent", str(_KEY_EVENTS[normalized])], timeout=8)
        return {"success": True, "key": normalized}
