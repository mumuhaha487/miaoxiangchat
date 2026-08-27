from __future__ import annotations

import ctypes
import os
import queue
from ctypes import wintypes
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageFont


if os.name == "nt":
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        ctypes.windll.user32.SetProcessDPIAware()


WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
GWL_EXSTYLE = -20
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
HWND_TOPMOST = -1
MONITOR_DEFAULTTOPRIMARY = 0x00000001


class Size(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class BitmapInfoHeader(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class RgbQuad(ctypes.Structure):
    _fields_ = [
        ("rgbBlue", ctypes.c_ubyte),
        ("rgbGreen", ctypes.c_ubyte),
        ("rgbRed", ctypes.c_ubyte),
        ("rgbReserved", ctypes.c_ubyte),
    ]


class BitmapInfo(ctypes.Structure):
    _fields_ = [("bmiHeader", BitmapInfoHeader), ("bmiColors", RgbQuad * 1)]


class BlendFunction(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_ubyte),
        ("BlendFlags", ctypes.c_ubyte),
        ("SourceConstantAlpha", ctypes.c_ubyte),
        ("AlphaFormat", ctypes.c_ubyte),
    ]


class MonitorInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


class NullRemoteCursorOverlay:
    def set_control_active(self, _active: bool) -> None:
        pass

    def show(self, _x: int, _y: int, *, click: bool = False) -> None:
        pass


class RemoteCursorOverlay:
    """Click-through, per-pixel-alpha Computer Use overlay for the virtual desktop."""

    EDGE_GLOW_SIZE = 64
    BANNER_WIDTH = 380
    BANNER_HEIGHT = 46
    BANNER_SHADOW = 10
    BANNER_TOP = 58
    POINTER_SIZE = 112

    def __init__(self, root: Any) -> None:
        self.root = root
        self._events: queue.Queue[tuple[str, object]] = queue.Queue()
        self._active = False
        self._cursor_position: tuple[int, int] | None = None
        self._click_generation = 0
        self._banner_monitor: tuple[int, int, int, int] | None = None
        self._user32 = ctypes.windll.user32
        self._gdi32 = ctypes.windll.gdi32
        self._user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        self._user32.GetAncestor.restype = wintypes.HWND
        self._user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        self._user32.GetWindowLongW.restype = ctypes.c_long
        self._user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
        self._user32.SetWindowLongW.restype = ctypes.c_long
        self._user32.SetWindowPos.argtypes = [
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        self._user32.SetWindowPos.restype = wintypes.BOOL
        self._user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        self._user32.GetCursorPos.restype = wintypes.BOOL
        self._user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
        self._user32.MonitorFromPoint.restype = wintypes.HANDLE
        self._user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
        self._user32.GetMonitorInfoW.restype = wintypes.BOOL
        self._user32.GetDC.argtypes = [wintypes.HWND]
        self._user32.GetDC.restype = wintypes.HDC
        self._user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        self._user32.ReleaseDC.restype = ctypes.c_int
        self._user32.UpdateLayeredWindow.argtypes = [
            wintypes.HWND,
            wintypes.HDC,
            ctypes.POINTER(wintypes.POINT),
            ctypes.POINTER(Size),
            wintypes.HDC,
            ctypes.POINTER(wintypes.POINT),
            wintypes.COLORREF,
            ctypes.POINTER(BlendFunction),
            wintypes.DWORD,
        ]
        self._user32.UpdateLayeredWindow.restype = wintypes.BOOL
        self._gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
        self._gdi32.CreateCompatibleDC.restype = wintypes.HDC
        self._gdi32.DeleteDC.argtypes = [wintypes.HDC]
        self._gdi32.DeleteDC.restype = wintypes.BOOL
        self._gdi32.CreateDIBSection.argtypes = [
            wintypes.HDC,
            ctypes.POINTER(BitmapInfo),
            wintypes.UINT,
            ctypes.POINTER(ctypes.c_void_p),
            wintypes.HANDLE,
            wintypes.DWORD,
        ]
        self._gdi32.CreateDIBSection.restype = ctypes.c_void_p
        self._gdi32.SelectObject.argtypes = [wintypes.HDC, ctypes.c_void_p]
        self._gdi32.SelectObject.restype = ctypes.c_void_p
        self._gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
        self._gdi32.DeleteObject.restype = wintypes.BOOL
        self._virtual_x = self._user32.GetSystemMetrics(76)
        self._virtual_y = self._user32.GetSystemMetrics(77)
        self._virtual_width = max(1, self._user32.GetSystemMetrics(78))
        self._virtual_height = max(1, self._user32.GetSystemMetrics(79))

        thickness = self.EDGE_GLOW_SIZE
        self._edge_layers = [
            (*self._make_layer(self._virtual_width, thickness, self._virtual_x, self._virtual_y),
             self.edge_image(self._virtual_width, thickness, "top"), self._virtual_x, self._virtual_y),
            (*self._make_layer(
                self._virtual_width,
                thickness,
                self._virtual_x,
                self._virtual_y + self._virtual_height - thickness,
            ), self.edge_image(self._virtual_width, thickness, "bottom"),
             self._virtual_x, self._virtual_y + self._virtual_height - thickness),
            (*self._make_layer(thickness, self._virtual_height, self._virtual_x, self._virtual_y),
             self.edge_image(thickness, self._virtual_height, "left"), self._virtual_x, self._virtual_y),
            (*self._make_layer(
                thickness,
                self._virtual_height,
                self._virtual_x + self._virtual_width - thickness,
                self._virtual_y,
            ), self.edge_image(thickness, self._virtual_height, "right"),
             self._virtual_x + self._virtual_width - thickness, self._virtual_y),
        ]
        self._banner_window, self._banner_hwnd = self._make_layer(
            self.BANNER_WIDTH, self.BANNER_HEIGHT + self.BANNER_SHADOW, 0, 0,
        )
        self._pointer_window, self._pointer_hwnd = self._make_layer(self.POINTER_SIZE, self.POINTER_SIZE, 0, 0)
        self._edges_uploaded = False

    def _make_layer(self, width: int, height: int, x: int, y: int) -> tuple[Any, int]:
        import tkinter as tk

        window = tk.Toplevel(self.root)
        window.withdraw()
        window.overrideredirect(True)
        window.geometry(f"{width}x{height}{x:+d}{y:+d}")
        window.attributes("-topmost", True)
        window.update_idletasks()
        hwnd = self._user32.GetAncestor(window.winfo_id(), 2) or window.winfo_id()
        ex_style = self._user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        self._user32.SetWindowLongW(
            hwnd,
            GWL_EXSTYLE,
            ex_style | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
        )
        return window, hwnd

    @staticmethod
    def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
        for filename in ("msyh.ttc", "msyhbd.ttc", "seguisym.ttf", "arial.ttf"):
            candidate = windir / "Fonts" / filename
            if candidate.is_file():
                return ImageFont.truetype(str(candidate), size=size)
        return ImageFont.load_default()

    @classmethod
    def edge_image(cls, width: int, height: int, side: str) -> Image.Image:
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image, "RGBA")
        thickness = height if side in {"top", "bottom"} else width
        for distance in range(thickness - 1, -1, -1):
            progress = 1.0 - distance / max(1, thickness - 1)
            alpha = int(10 + 184 * progress**2.35)
            color = (int(8 + 20 * progress), int(92 + 82 * progress), 255, alpha)
            coordinate = distance if side in {"top", "left"} else thickness - 1 - distance
            if side in {"top", "bottom"}:
                draw.line((0, coordinate, width - 1, coordinate), fill=color)
            else:
                draw.line((coordinate, 0, coordinate, height - 1), fill=color)
        core = (58, 188, 255, 232)
        if side == "top":
            draw.rectangle((0, 0, width - 1, 1), fill=core)
        elif side == "bottom":
            draw.rectangle((0, height - 2, width - 1, height - 1), fill=core)
        elif side == "left":
            draw.rectangle((0, 0, 1, height - 1), fill=core)
        else:
            draw.rectangle((width - 2, 0, width - 1, height - 1), fill=core)
        return image

    @classmethod
    def banner_image(cls) -> Image.Image:
        scale = 2
        image = Image.new(
            "RGBA",
            (cls.BANNER_WIDTH * scale, (cls.BANNER_HEIGHT + cls.BANNER_SHADOW) * scale),
            (0, 0, 0, 0),
        )
        draw = ImageDraw.Draw(image, "RGBA")
        draw.rounded_rectangle(
            (6 * scale, 7 * scale, (cls.BANNER_WIDTH - 6) * scale, (cls.BANNER_HEIGHT + 3) * scale),
            radius=7 * scale,
            fill=(0, 50, 86, 48),
        )
        draw.rounded_rectangle(
            (0, 0, cls.BANNER_WIDTH * scale - 1, cls.BANNER_HEIGHT * scale - 1),
            radius=6 * scale,
            fill=(5, 122, 204, 255),
            outline=(58, 174, 238, 255),
            width=scale,
        )
        text = "电脑正在被 Agent 远程控制"
        font = cls._load_font(16 * scale)
        bounds = draw.textbbox((0, 0), text, font=font)
        text_width = bounds[2] - bounds[0]
        text_height = bounds[3] - bounds[1]
        draw.text(
            (
                (cls.BANNER_WIDTH * scale - text_width) / 2,
                (cls.BANNER_HEIGHT * scale - text_height) / 2 - bounds[1],
            ),
            text,
            font=font,
            fill=(255, 255, 255, 255),
        )
        return image.resize(
            (cls.BANNER_WIDTH, cls.BANNER_HEIGHT + cls.BANNER_SHADOW), Image.Resampling.LANCZOS,
        )

    @classmethod
    def pointer_image(cls, *, click: bool = False) -> Image.Image:
        scale = 2
        size = cls.POINTER_SIZE * scale
        image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        pixels = image.load()
        center_x = 56 * scale
        center_y = 49 * scale
        radius = (56 if click else 50) * scale
        for y in range(size):
            for x in range(size):
                distance = ((x - center_x) ** 2 + (y - center_y) ** 2) ** 0.5
                if distance >= radius:
                    continue
                strength = (1.0 - distance / radius) ** 2.15
                pixels[x, y] = (37, 171, 255, int((155 if click else 118) * strength))
        draw = ImageDraw.Draw(image, "RGBA")
        arrow = [
            (52 * scale, 57 * scale),
            (52 * scale, 78 * scale),
            (56 * scale, 74 * scale),
            (61 * scale, 83 * scale),
            (65 * scale, 81 * scale),
            (59 * scale, 72 * scale),
            (67 * scale, 71 * scale),
        ]
        draw.polygon(arrow, fill=(12, 15, 18, 255), outline=(247, 250, 252, 255), width=2 * scale)
        return image.resize((cls.POINTER_SIZE, cls.POINTER_SIZE), Image.Resampling.LANCZOS)

    def set_control_active(self, active: bool) -> None:
        self._events.put(("active", bool(active)))

    def show(self, x: int, y: int, *, click: bool = False) -> None:
        self._events.put(("cursor", (int(x), int(y), bool(click))))

    def process(self) -> None:
        try:
            while True:
                kind, payload = self._events.get_nowait()
                if kind == "active":
                    self._set_active(bool(payload))
                elif kind == "cursor" and isinstance(payload, tuple):
                    x, y, clicked = payload
                    self._cursor_position = (int(x), int(y))
                    if self._active:
                        self._show_banner_for_cursor()
                        self._show_pointer(click=bool(clicked))
                    if clicked:
                        self._click_generation += 1
                        generation = self._click_generation

                        def soften_click(expected: int = generation) -> None:
                            if expected == self._click_generation and self._active:
                                self._show_pointer(click=False)

                        self.root.after(180, soften_click)
        except queue.Empty:
            pass

    def _set_active(self, active: bool) -> None:
        self._active = active
        if not active:
            self._hide_all()
            return
        if self._cursor_position is None:
            self._cursor_position = self._current_cursor()
        if not self._edges_uploaded:
            for _window, hwnd, image, x, y in self._edge_layers:
                self._upload(hwnd, image, x, y)
            self._edges_uploaded = True
        for window, hwnd, image, x, y in self._edge_layers:
            self._show_layer(window, hwnd, image.width, image.height, x, y)
        self._show_banner_for_cursor(force=True)
        self._show_pointer(click=False)

    def _current_cursor(self) -> tuple[int, int]:
        point = wintypes.POINT()
        if self._user32.GetCursorPos(ctypes.byref(point)):
            return point.x, point.y
        return 0, 0

    def _monitor_for_cursor(self) -> wintypes.RECT:
        x, y = self._cursor_position or self._current_cursor()
        monitor = self._user32.MonitorFromPoint(wintypes.POINT(x, y), MONITOR_DEFAULTTOPRIMARY)
        info = MonitorInfo(cbSize=ctypes.sizeof(MonitorInfo))
        if monitor and self._user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return info.rcMonitor
        return wintypes.RECT(0, 0, self._user32.GetSystemMetrics(0), self._user32.GetSystemMetrics(1))

    @classmethod
    def banner_position(cls, monitor: wintypes.RECT) -> tuple[int, int]:
        width = monitor.right - monitor.left
        return monitor.left + (width - cls.BANNER_WIDTH) // 2, monitor.top + cls.BANNER_TOP

    def _show_banner_for_cursor(self, *, force: bool = False) -> None:
        monitor = self._monitor_for_cursor()
        marker = (monitor.left, monitor.top, monitor.right, monitor.bottom)
        if not force and marker == self._banner_monitor:
            return
        self._banner_monitor = marker
        x, y = self.banner_position(monitor)
        image = self.banner_image()
        self._upload(self._banner_hwnd, image, x, y)
        self._show_layer(self._banner_window, self._banner_hwnd, image.width, image.height, x, y)

    def _show_pointer(self, *, click: bool) -> None:
        if self._cursor_position is None:
            return
        x = self._cursor_position[0] - 52
        y = self._cursor_position[1] - 57
        image = self.pointer_image(click=click)
        self._upload(self._pointer_hwnd, image, x, y)
        self._show_layer(self._pointer_window, self._pointer_hwnd, image.width, image.height, x, y)

    def _show_layer(self, window: Any, hwnd: int, width: int, height: int, x: int, y: int) -> None:
        window.deiconify()
        self._user32.SetWindowPos(
            hwnd, HWND_TOPMOST, x, y, width, height, SWP_NOACTIVATE | SWP_SHOWWINDOW,
        )

    def _hide_all(self) -> None:
        self._banner_monitor = None
        for window, _hwnd, _image, _x, _y in self._edge_layers:
            window.withdraw()
        self._banner_window.withdraw()
        self._pointer_window.withdraw()

    def _upload(self, hwnd: int, image: Image.Image, x: int, y: int) -> None:
        red, green, blue, alpha = image.split()
        premultiplied = Image.merge(
            "RGBA",
            (
                ImageChops.multiply(blue, alpha),
                ImageChops.multiply(green, alpha),
                ImageChops.multiply(red, alpha),
                alpha,
            ),
        ).tobytes()
        width, height = image.size
        screen_dc = self._user32.GetDC(0)
        memory_dc = self._gdi32.CreateCompatibleDC(screen_dc)
        bits = ctypes.c_void_p()
        info = BitmapInfo()
        info.bmiHeader.biSize = ctypes.sizeof(BitmapInfoHeader)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        bitmap = self._gdi32.CreateDIBSection(screen_dc, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
        if not bitmap or not bits.value:
            if memory_dc:
                self._gdi32.DeleteDC(memory_dc)
            self._user32.ReleaseDC(0, screen_dc)
            raise OSError("无法创建远控覆盖层位图")
        old_bitmap = self._gdi32.SelectObject(memory_dc, bitmap)
        try:
            ctypes.memmove(bits.value, premultiplied, len(premultiplied))
            destination = wintypes.POINT(x, y)
            source = wintypes.POINT(0, 0)
            size = Size(width, height)
            blend = BlendFunction(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
            if not self._user32.UpdateLayeredWindow(
                hwnd,
                screen_dc,
                ctypes.byref(destination),
                ctypes.byref(size),
                memory_dc,
                ctypes.byref(source),
                0,
                ctypes.byref(blend),
                ULW_ALPHA,
            ):
                raise ctypes.WinError()
        finally:
            self._gdi32.SelectObject(memory_dc, old_bitmap)
            self._gdi32.DeleteObject(bitmap)
            self._gdi32.DeleteDC(memory_dc)
            self._user32.ReleaseDC(0, screen_dc)

    def close(self) -> None:
        self._active = False
        self._hide_all()
        windows = [layer[0] for layer in self._edge_layers]
        windows.extend((self._banner_window, self._pointer_window))
        for window in windows:
            try:
                window.destroy()
            except Exception:
                pass
