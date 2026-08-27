import asyncio

import pytest

from vmss_agent.config import AgentConfig
from vmss_agent import protocol
from vmss_agent.protocol import (
    AgentConnection,
    bind_account_token,
    clear_account_binding,
    request_account_login,
    verify_account_login,
    websocket_url,
)


def test_websocket_url_uses_outbound_tls_endpoint():
    assert websocket_url("https://example.com") == "wss://example.com/api/v1/control/agent/ws"
    assert websocket_url("http://127.0.0.1:8000/") == "ws://127.0.0.1:8000/api/v1/control/agent/ws"


@pytest.mark.parametrize("value", ["file:///tmp/socket", "https://user:pass@example.com", "example.com"])
def test_websocket_url_rejects_unsafe_or_ambiguous_addresses(value):
    with pytest.raises(ValueError):
        websocket_url(value)


class FakeResponse:
    status_code = 200
    text = ""

    def __init__(self, data):
        self._data = data

    def json(self):
        return {"ok": True, "data": self._data}


class FakeHttpClient:
    responses = []
    requests = []

    def __init__(self, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def post(self, url, json, headers=None):
        self.requests.append({"method": "POST", "url": url, "json": json, "headers": headers})
        return FakeResponse(self.responses.pop(0))

    def get(self, url, headers=None):
        self.requests.append({"method": "GET", "url": url, "headers": headers})
        return FakeResponse(self.responses.pop(0))


def test_account_login_verifies_email_and_registers_current_installation(monkeypatch, tmp_path):
    monkeypatch.setenv("VMSS_AGENT_HOME", str(tmp_path))
    monkeypatch.setattr(protocol.httpx, "Client", FakeHttpClient)
    monkeypatch.setattr(protocol, "protect_credential", lambda value: "protected:" + value)
    monkeypatch.setattr(protocol, "unprotect_credential", lambda value: value.removeprefix("protected:"))
    FakeHttpClient.requests = []
    FakeHttpClient.responses = [
        {"verificationRequired": True, "sent": True},
        {"token": "account-token", "deviceCredential": "trusted-device", "user": {"id": "user-1"}},
        {"credential": "device-id.control-secret", "device": {"id": "device-id", "name": "测试电脑"}},
    ]
    config = AgentConfig(installation_id="installation-123456", device_name="测试电脑")

    requested = request_account_login(config, "user@example.com", "Password123!")
    completed = verify_account_login(config, "user@example.com", "Password123!", "123456")

    assert requested["verificationRequired"] is True
    assert completed["device"]["id"] == "device-id"
    assert FakeHttpClient.requests[0]["json"]["client_platform"] == "windows"
    assert FakeHttpClient.requests[1]["json"]["trust_device"] is True
    assert FakeHttpClient.requests[2]["url"].endswith("/api/v1/control/devices/register")
    assert FakeHttpClient.requests[2]["headers"] == {"Authorization": "Bearer account-token"}
    assert config.account_identifier == "user@example.com"
    assert config.account_token_ciphertext == "protected:account-token"
    assert config.trust_token_ciphertext == "protected:trusted-device"
    assert config.credential_ciphertext == "protected:device-id.control-secret"


def test_webview_account_token_binds_and_clears_hidden_agent(monkeypatch, tmp_path):
    monkeypatch.setenv("VMSS_AGENT_HOME", str(tmp_path))
    monkeypatch.setattr(protocol.httpx, "Client", FakeHttpClient)
    monkeypatch.setattr(protocol, "protect_credential", lambda value: "protected:" + value)
    FakeHttpClient.requests = []
    FakeHttpClient.responses = [
        {"user": {"id": "user-1", "username": "mumu", "role": "user", "status": "active"}},
        {"credential": "device-id.control-secret", "device": {"id": "device-id", "name": "桌面电脑"}},
    ]
    config = AgentConfig(installation_id="installation-123456", device_name="桌面电脑")

    result = bind_account_token(config, "webview-account-token")

    assert result["device"]["id"] == "device-id"
    assert FakeHttpClient.requests[0] == {
        "method": "GET",
        "url": "https://example.com/api/v1/auth/me",
        "headers": {"Authorization": "Bearer webview-account-token"},
    }
    assert FakeHttpClient.requests[1]["url"].endswith("/api/v1/control/devices/register")
    assert config.account_identifier == "mumu"
    assert config.account_token_ciphertext == "protected:webview-account-token"
    assert config.credential_ciphertext == "protected:device-id.control-secret"

    clear_account_binding(config)

    assert config.account_identifier == ""
    assert config.account_token_ciphertext == ""
    assert config.credential_ciphertext == ""


class FakeSocket:
    def __init__(self):
        self.messages = []

    async def send(self, payload):
        self.messages.append(payload)


class GatedRunner:
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def run(self, *_args):
        self.started.set()
        await self.release.wait()


async def accepted_ack_flow():
    runner = GatedRunner()
    connection = AgentConnection(AgentConfig(), runner, lambda: [])
    connection._socket = FakeSocket()
    assignment = {
        "type": "task.assign",
        "leaseId": "lease-1234567890123456",
        "task": {"id": "task-123", "instruction": "测试确认门闩"},
    }
    await connection._handle(assignment, "device.secret")
    await asyncio.sleep(0)
    assert not runner.started.is_set()
    await connection._handle({"type": "event.ack", "taskId": "task-123"}, "device.secret")
    await asyncio.wait_for(runner.started.wait(), timeout=1)
    runner.release.set()
    await asyncio.sleep(0)


def test_agent_does_not_start_task_before_server_accept_ack():
    asyncio.run(accepted_ack_flow())
