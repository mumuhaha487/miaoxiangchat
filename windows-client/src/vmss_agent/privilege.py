from __future__ import annotations

import ctypes
import subprocess
import sys


def is_elevated() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_elevated() -> None:
    if getattr(sys, "frozen", False):
        executable = sys.executable
        arguments = sys.argv[1:]
    else:
        executable = sys.executable
        arguments = ["-m", "vmss_agent.main", *sys.argv[1:]]
    result = ctypes.windll.shell32.ShellExecuteW(
        None,
        "runas",
        executable,
        subprocess.list2cmdline(arguments),
        None,
        1,
    )
    if int(result) <= 32:
        raise RuntimeError(f"管理员启动失败 ({int(result)})")
