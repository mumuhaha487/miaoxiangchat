from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .config import AgentConfig


def arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="妙想之地 Windows 桌面端")
    parser.add_argument("--server", help="覆盖服务地址（默认读取 MIAOXIANG_SERVER_URL）")
    parser.add_argument("--self-test", metavar="REPORT", help="执行离线打包自检并将结果写入 JSON")
    parser.add_argument("--agent-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--web-shell", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def run_self_test(report_path: str) -> int:
    checks: dict[str, object] = {"platform": sys.platform}
    ok = False
    error = ""
    try:
        import cv2
        import importlib.metadata
        import mss
        import numpy
        import onnxruntime
        import psutil
        import pynput
        import pystray
        import pywinauto
        import webview
        from PIL import Image

        from .adb_target import ADBTarget
        from .credentials import protect_credential, unprotect_credential
        from .desktop import DesktopBridge, desktop_url
        from .observation import OCRParser
        from .windows_target import WindowsTarget

        checks.update(
            {
                "cv2": cv2.__version__,
                "mss": mss.__version__,
                "numpy": numpy.__version__,
                "onnxProviders": onnxruntime.get_available_providers(),
                "psutil": psutil.__version__,
                "pynput": getattr(pynput, "__version__", "available"),
                "pystray": getattr(pystray, "__version__", "available"),
                "pywinauto": pywinauto.__version__,
                "pywebview": importlib.metadata.version("pywebview"),
                "webviewModule": webview.__name__,
                "targets": [WindowsTarget.__name__, ADBTarget.__name__],
                "desktop": [DesktopBridge.__name__, desktop_url("https://example.com")],
            }
        )
        checks["ocrElementsOnBlankImage"] = len(OCRParser().parse(Image.new("RGB", (320, 120), "white")))
        dpapi_probe = protect_credential("miaoxiang-self-test")
        checks["dpapiRoundTrip"] = unprotect_credential(dpapi_probe) == "miaoxiang-self-test"
        if not checks["dpapiRoundTrip"]:
            raise RuntimeError("DPAPI round trip failed")
        ok = True
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"[:1000]

    destination = Path(report_path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps({"ok": ok, "checks": checks, "error": error}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    options = arguments(argv)
    if os.name != "nt":
        print("妙想之地 Computer Agent 只能在 Windows 上运行。", file=sys.stderr)
        return 2
    if options.self_test:
        return run_self_test(options.self_test)
    config = AgentConfig.load()
    if options.server:
        config.server_url = options.server.rstrip("/")
        config.save()
    if options.agent_worker:
        from .worker import run_worker

        return run_worker(config)
    from .desktop import run_desktop

    return run_desktop(config)


if __name__ == "__main__":
    raise SystemExit(main())
