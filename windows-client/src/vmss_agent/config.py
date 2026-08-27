from __future__ import annotations

import json
import os
import socket
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


def agent_home() -> Path:
    override = os.getenv("VMSS_AGENT_HOME", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    base = Path(os.getenv("LOCALAPPDATA") or Path.home())
    return base / "MiaoxiangZhiDi" / "ComputerAgent"


@dataclass
class AgentConfig:
    server_url: str = field(default_factory=lambda: os.getenv("MIAOXIANG_SERVER_URL", "https://example.com"))
    device_name: str = field(default_factory=socket.gethostname)
    installation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    credential_ciphertext: str = ""
    account_token_ciphertext: str = ""
    trust_token_ciphertext: str = ""
    account_identifier: str = ""
    require_local_task_confirmation: bool = False
    pause_on_user_input: bool = True
    auto_connect_adb: list[str] = field(default_factory=lambda: ["127.0.0.1:16384"])
    adb_path: str = ""
    allowed_apps: dict[str, str] = field(
        default_factory=lambda: {
            "记事本": "notepad.exe",
            "计算器": "calc.exe",
            "文件资源管理器": "explorer.exe",
            "浏览器": "msedge.exe",
            "Microsoft Edge": "msedge.exe",
            "Google Chrome": "chrome.exe",
            "Mozilla Firefox": "firefox.exe",
            "画图": "mspaint.exe",
            "Word": "winword.exe",
            "Excel": "excel.exe",
            "PowerPoint": "powerpnt.exe",
        }
    )
    max_steps: int = 60

    @property
    def path(self) -> Path:
        return agent_home() / "config.json"

    @classmethod
    def load(cls) -> "AgentConfig":
        path = agent_home() / "config.json"
        if not path.is_file():
            config = cls()
            config.save()
            return config
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(value, dict):
            return cls()
        defaults = cls()
        for key in asdict(defaults):
            if key in value:
                setattr(defaults, key, value[key])
        defaults.server_url = str(defaults.server_url).rstrip("/")
        defaults.device_name = str(defaults.device_name or socket.gethostname())[:120]
        defaults.installation_id = str(defaults.installation_id or uuid.uuid4())[:200]
        defaults.account_identifier = str(defaults.account_identifier or "")[:254]
        defaults.max_steps = max(1, min(int(defaults.max_steps), 120))
        defaults.auto_connect_adb = [str(item)[:200] for item in defaults.auto_connect_adb[:10]]
        saved_allowed_apps = {
            str(name)[:80]: str(command)[:260]
            for name, command in dict(defaults.allowed_apps).items()
            if name and command
        }
        defaults.allowed_apps = {**cls().allowed_apps, **saved_allowed_apps}
        return defaults

    def save(self) -> None:
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def update(self, values: dict[str, Any]) -> None:
        for key in (
            "server_url",
            "device_name",
            "require_local_task_confirmation",
            "pause_on_user_input",
            "adb_path",
            "auto_connect_adb",
            "allowed_apps",
            "max_steps",
        ):
            if key in values:
                setattr(self, key, values[key])
        self.save()
