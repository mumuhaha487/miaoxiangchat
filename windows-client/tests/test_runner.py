from __future__ import annotations

import asyncio

from PIL import Image

from vmss_agent.config import AgentConfig
from vmss_agent.llm import AssistantTurn, ToolCall
from vmss_agent.observation import Observation, ObservationGuard
from vmss_agent.runner import TaskRunner, safe_window_capture_intent
from vmss_agent.safety import EmergencyStop


class FakeLLM:
    def __init__(self, turns):
        self.turns = list(turns)
        self.closed = False

    async def complete(self, _messages, _tools):
        return self.turns.pop(0)

    async def close(self):
        self.closed = True


class FailingLLM(FakeLLM):
    async def complete(self, _messages, _tools):
        raise RuntimeError("模拟模型故障")


class CancellingLLM(FakeLLM):
    def __init__(self, cancel):
        super().__init__([])
        self.cancel = cancel

    async def complete(self, _messages, _tools):
        self.cancel.set()
        return turn("observe", {}, "call-observe")


def turn(name: str, arguments: dict, call_id: str) -> AssistantTurn:
    call = ToolCall(call_id, name, arguments)
    return AssistantTurn(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": call_id, "type": "function", "function": {"name": name, "arguments": "{}"}}
            ],
        },
        "",
        [call],
    )


class FakeWindows:
    def __init__(self):
        self.guard = ObservationGuard()
        self.clicks = []
        self.overlay_states = []
        self.observed_window_ids = []
        self.launched_apps = []

    def set_control_active(self, active):
        self.overlay_states.append(bool(active))

    def observe(self, window_id=None):
        self.observed_window_ids.append(window_id)
        return self.guard.replace(
            Observation("desktop", "windows", Image.new("RGB", (200, 100), "white"), window_id=window_id)
        )

    def last_observation_window_id(self):
        return self.guard.current.window_id if self.guard.current else None

    def click(self, observation_id, x, y, clicks=1):
        self.guard.consume(observation_id)
        self.clicks.append((x, y, clicks))
        return {"success": True}

    def launch_app(self, name):
        self.launched_apps.append(name)
        return {"success": True, "app": name}

    def list_windows(self):
        return [
            {
                "windowId": 7788,
                "title": "新标签页 - Microsoft Edge",
                "executable": "msedge.exe",
                "width": 1600,
                "height": 900,
            }
        ]


class FakeTransport:
    def __init__(self):
        self.events = []

    async def send_event(self, task_id, lease_id, sequence, event_type, payload, frame_base64=""):
        self.events.append((sequence, event_type, payload, bool(frame_base64)))

    async def wait_for_approval(self, *_args):
        return True


async def run_flow():
    config = AgentConfig(require_local_task_confirmation=False, max_steps=10)
    windows = FakeWindows()
    observed = windows.observe()
    llm = FakeLLM(
        [
            turn("observe", {}, "call-observe"),
            turn(
                "click",
                {"observationId": observed.observation_id, "x": 500, "y": 500, "clicks": 1},
                "call-click",
            ),
            turn("observe", {}, "call-observe-final"),
            turn("finish", {"output": "测试完成"}, "call-finish"),
        ]
    )
    original_observe = windows.observe
    first = True

    def deterministic_observe(window_id=None):
        nonlocal first
        if first:
            first = False
            windows.guard.replace(observed)
            return observed
        return original_observe(window_id)

    windows.observe = deterministic_observe
    transport = FakeTransport()
    runner = TaskRunner(config, EmergencyStop(), windows, None, lambda *_args: True, lambda *_args: llm)
    await runner.run(
        {
            "id": "task-123",
            "deviceName": "TEST-PC",
            "instruction": "点击测试按钮",
            "targetKind": "windows",
            "targetId": "desktop",
        },
        "lease-1234567890123456",
        "device.secret",
        transport,
        asyncio.Event(),
    )
    return windows, transport, llm


def test_runner_observes_acts_and_finishes_with_monotonic_events():
    windows, transport, llm = asyncio.run(run_flow())
    assert windows.clicks == [(500, 500, 1)]
    assert windows.overlay_states == [True, False]
    assert [item[0] for item in transport.events] == [2, 3, 4, 5, 6, 7]
    assert [item[1] for item in transport.events] == [
        "observation",
        "action.started",
        "action.completed",
        "observation",
        "observation",
        "task.completed",
    ]
    assert transport.events[0][3] is True
    assert transport.events[-2][2]["purpose"] == "result"
    assert transport.events[-2][3] is True
    assert llm.closed is True


def test_runner_executes_only_first_tool_call_in_each_model_turn():
    config = AgentConfig(require_local_task_confirmation=False, max_steps=4)
    windows = FakeWindows()
    observed = windows.observe()

    def deterministic_observe(window_id=None):
        windows.guard.replace(observed)
        return observed

    windows.observe = deterministic_observe
    observe_call = ToolCall("call-observe", "observe", {})
    click_call = ToolCall(
        "call-click",
        "click",
        {"observationId": observed.observation_id, "x": 500, "y": 500, "clicks": 1},
    )
    llm = FakeLLM(
        [
            AssistantTurn(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {"id": observe_call.id, "type": "function", "function": {"name": "observe", "arguments": "{}"}},
                        {"id": click_call.id, "type": "function", "function": {"name": "click", "arguments": "{}"}},
                    ],
                },
                "",
                [observe_call, click_call],
            ),
            turn("finish", {"output": "原子测试完成"}, "call-finish"),
        ]
    )
    transport = FakeTransport()
    runner = TaskRunner(config, EmergencyStop(), windows, None, lambda *_args: True, lambda *_args: llm)

    asyncio.run(
        runner.run(
            {
                "id": "task-atomic",
                "deviceName": "TEST-PC",
                "instruction": "观察后点击",
                "targetKind": "windows",
                "targetId": "desktop",
            },
            "lease-1234567890123456",
            "device.secret",
            transport,
            asyncio.Event(),
        )
    )

    assert windows.clicks == []
    assert [item[1] for item in transport.events] == ["observation", "observation", "task.completed"]


def test_runner_final_screenshot_reuses_last_confirmed_window():
    config = AgentConfig(require_local_task_confirmation=False, max_steps=4)
    windows = FakeWindows()
    llm = FakeLLM(
        [
            turn("observe", {"windowId": 321}, "call-observe-window"),
            turn("finish", {"output": "截图完成"}, "call-finish"),
        ]
    )
    transport = FakeTransport()
    runner = TaskRunner(config, EmergencyStop(), windows, None, lambda *_args: True, lambda *_args: llm)

    asyncio.run(
        runner.run(
            {
                "id": "task-window-frame",
                "deviceName": "TEST-PC",
                "instruction": "截图",
                "targetKind": "windows",
                "targetId": "desktop",
            },
            "lease-1234567890123456",
            "device.secret",
            transport,
            asyncio.Event(),
        )
    )

    assert windows.observed_window_ids == [321, 321]
    final_frame = [item for item in transport.events if item[2].get("purpose") == "result"]
    assert final_frame[0][2]["windowId"] == 321


def test_runner_hides_control_overlay_after_failure():
    config = AgentConfig(require_local_task_confirmation=False, max_steps=2)
    windows = FakeWindows()
    llm = FailingLLM([])
    transport = FakeTransport()
    runner = TaskRunner(config, EmergencyStop(), windows, None, lambda *_args: True, lambda *_args: llm)

    asyncio.run(
        runner.run(
            {
                "id": "task-failure",
                "deviceName": "TEST-PC",
                "instruction": "触发失败",
                "targetKind": "windows",
                "targetId": "desktop",
            },
            "lease-1234567890123456",
            "device.secret",
            transport,
            asyncio.Event(),
        )
    )

    assert windows.overlay_states == [True, False]
    assert transport.events[-1][1] == "task.failed"
    assert llm.closed is True


def test_runner_hides_control_overlay_after_cancellation():
    config = AgentConfig(require_local_task_confirmation=False, max_steps=2)
    windows = FakeWindows()
    cancel = asyncio.Event()
    llm = CancellingLLM(cancel)
    transport = FakeTransport()
    runner = TaskRunner(config, EmergencyStop(), windows, None, lambda *_args: True, lambda *_args: llm)

    asyncio.run(
        runner.run(
            {
                "id": "task-cancelled",
                "deviceName": "TEST-PC",
                "instruction": "触发取消",
                "targetKind": "windows",
                "targetId": "desktop",
            },
            "lease-1234567890123456",
            "device.secret",
            transport,
            cancel,
        )
    )

    assert windows.overlay_states == [True, False]
    assert llm.closed is True


def test_safe_window_capture_intent_is_strict_and_allowlisted():
    allowed = AgentConfig().allowed_apps
    assert safe_window_capture_intent("打开我电脑的浏览器，查看界面截个图给我", "windows", allowed) == (
        "浏览器",
        True,
    )
    assert safe_window_capture_intent("查看当前 Microsoft Edge 浏览器窗口并截个图给我", "windows", allowed) == (
        "Microsoft Edge",
        False,
    )
    assert safe_window_capture_intent("打开浏览器搜索最近热点并截图", "windows", allowed) is None
    assert safe_window_capture_intent("打开浏览器并删除文件再截图", "windows", allowed) is None
    assert safe_window_capture_intent("打开浏览器并截图", "adb", allowed) is None


def test_runner_captures_allowlisted_browser_without_creating_llm():
    config = AgentConfig(require_local_task_confirmation=False, max_steps=2)
    windows = FakeWindows()
    transport = FakeTransport()

    def forbidden_llm_factory(*_args):
        raise AssertionError("安全截图快路径不应创建或调用 LLM")

    runner = TaskRunner(config, EmergencyStop(), windows, None, lambda *_args: True, forbidden_llm_factory)
    asyncio.run(
        runner.run(
            {
                "id": "task-safe-browser-capture",
                "deviceName": "TEST-PC",
                "instruction": "打开我电脑的浏览器，查看界面截个图给我",
                "targetKind": "windows",
                "targetId": "desktop",
            },
            "lease-1234567890123456",
            "device.secret",
            transport,
            asyncio.Event(),
        )
    )

    assert windows.launched_apps == ["浏览器"]
    assert windows.observed_window_ids == [7788]
    assert windows.overlay_states == [True, False]
    assert [item[1] for item in transport.events] == [
        "action.started",
        "action.completed",
        "observation",
        "task.completed",
    ]
    assert transport.events[-2][2]["purpose"] == "result"
    assert transport.events[-2][3] is True
