from __future__ import annotations

import logging

import pytest

from vmss_agent import desktop
from vmss_agent.config import AgentConfig


class FakeWindow:
    def __init__(self, url: str):
        self.url = url

    def get_current_url(self) -> str:
        return self.url


def test_desktop_url_reuses_public_workspace_and_marks_windows_client():
    assert desktop.desktop_url("https://example.com") == (
        "https://example.com/?client=windows-desktop&desktopVersion=0.6.2"
    )


def test_desktop_bridge_only_accepts_the_configured_origin(monkeypatch, tmp_path):
    monkeypatch.setenv("VMSS_AGENT_HOME", str(tmp_path))
    config = AgentConfig()
    bridge = desktop.DesktopBridge(config)
    bridge.attach(FakeWindow("https://example.com/"))

    with pytest.raises(PermissionError):
        bridge.set_auth_token("account-token")


def test_desktop_bridge_binds_once_and_clears_without_exposing_secrets(monkeypatch, tmp_path):
    monkeypatch.setenv("VMSS_AGENT_HOME", str(tmp_path))
    config = AgentConfig()
    bridge = desktop.DesktopBridge(config)
    bridge.attach(FakeWindow("https://example.com/?client=windows-desktop"))
    calls = []

    def bind(current, token):
        calls.append((current, token))
        current.credential_ciphertext = "protected-device"
        return {"device": {"name": "测试电脑"}}

    def clear(current):
        calls.append((current, "clear"))
        current.credential_ciphertext = ""

    monkeypatch.setattr(desktop, "bind_account_token", bind)
    monkeypatch.setattr(desktop, "clear_account_binding", clear)

    first = bridge.set_auth_token("account-token")
    second = bridge.set_auth_token("account-token")
    cleared = bridge.clear_auth_token()

    assert first == {"ok": True, "deviceName": "测试电脑"}
    assert second == {"ok": True, "deviceName": config.device_name}
    assert cleared == {"ok": True}
    assert [value for _config, value in calls] == ["account-token", "clear"]


def test_desktop_bridge_keeps_internal_objects_private():
    bridge = desktop.DesktopBridge(AgentConfig())

    assert all(name.startswith("_") for name in vars(bridge))


def test_desktop_bridge_downloads_authenticated_file_and_selects_it(monkeypatch, tmp_path):
    monkeypatch.setenv("VMSS_AGENT_HOME", str(tmp_path))
    bridge = desktop.DesktopBridge(AgentConfig())
    bridge.attach(FakeWindow("https://example.com/?client=windows-desktop"))
    bridge._auth_token = "account-token"
    launched = []

    class FakeResponse:
        content = b"valid-document"

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, url, headers):
            assert url.startswith("https://example.com/api/v1/workspace/download?")
            assert headers == {"Authorization": "Bearer account-token"}
            return FakeResponse()

    monkeypatch.setattr(desktop.httpx, "Client", FakeClient)
    monkeypatch.setattr(desktop.subprocess, "Popen", lambda command, **kwargs: launched.append((command, kwargs)))
    result = bridge.share_authenticated_file(
        "https://example.com/api/v1/workspace/download?conversation_id=c1&path=report.docx",
        "report.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    target = tmp_path / "shared-files" / "report.docx"
    assert result == {"ok": True, "path": str(target)}
    assert target.read_bytes() == b"valid-document"
    assert launched[0][0] == ["explorer.exe", f"/select,{target}"]

    with pytest.raises(PermissionError):
        bridge.share_authenticated_file("https://evil.example/file", "bad.txt", "text/plain")


class FakeWorkspaceWindow:
    def __init__(self, readiness: list[bool]):
        self.readiness = readiness
        self.scripts: list[str] = []

    def evaluate_js(self, script: str) -> bool:
        self.scripts.append(script)
        if script == "location.reload(); true":
            return True
        return self.readiness.pop(0)


def test_blank_workspace_is_reloaded_only_once():
    window = FakeWorkspaceWindow([False, True])

    desktop._recover_blank_workspace(
        window,
        "https://example.com/?client=windows-desktop",
        logging.getLogger("test.desktop"),
        wait=lambda _seconds: None,
    )

    assert window.scripts.count("location.reload(); true") == 1
