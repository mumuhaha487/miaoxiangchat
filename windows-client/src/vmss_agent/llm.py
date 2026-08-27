from __future__ import annotations

import json
import asyncio
from dataclasses import dataclass
from typing import Any

import httpx


SYSTEM_PROMPT = """你是妙想之地 Computer Use 执行器。你只能通过提供的受限工具操作当前目标。
规则：
1. 先 observe；每次点击、输入、按键、滚动或拖动后，旧 observationId 立即失效，必须重新 observe。
2. 坐标统一为截图范围内 0..1000。优先使用 UIA/UIAutomator/OCR 元素的中心坐标，图像判断只作后备。
3. 不得猜测动作已经成功；动作后重新截图确认。
4. Windows 桌面任务可先 list_windows，再用 observe(windowId) 锁定唯一窗口。不要操作登录、UAC、锁屏或安全桌面。
5. 不得索取或处理密码、验证码、支付信息，不得绕过批准。
6. 完成后调用 finish；无法安全完成时调用 finish 并说明原因。
7. 每次回复只能调用一个工具。任何界面动作之后，下一次工具调用必须是 observe，确保动作原子化且不使用旧画面。
8. 打开应用时优先使用 launch_app，不要先在桌面或开始菜单中寻找图标。
9. 用户要求截图时，完成最终 observe 后再调用 finish；客户端会把最新画面作为结果回传。
"""

RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524}
MAX_LLM_ATTEMPTS = 4
RETRY_EXHAUSTED_HEADER = "X-Mumu-LLM-Retry-Exhausted"


def _function(name: str, description: str, properties: dict[str, Any], required: list[str] | None = None):
    schema: dict[str, Any] = {"type": "object", "properties": properties, "additionalProperties": False}
    if required:
        schema["required"] = required
    return {"type": "function", "function": {"name": name, "description": description, "parameters": schema}}


def tools_for_target(target_kind: str, allowed_app_names: list[str] | None = None) -> list[dict[str, Any]]:
    coordinate = {"type": "integer", "minimum": 0, "maximum": 1000}
    observation = {"type": "string", "minLength": 16, "maxLength": 80}
    common = [
        _function("finish", "结束任务并返回用户可读的结果。", {"output": {"type": "string", "maxLength": 10000}}, ["output"])
    ]
    if target_kind == "adb":
        return [
            _function("observe", "获取当前 Android 截图、UIAutomator 和 OCR 元素。", {}),
            _function(
                "adb_tap",
                "点击 Android 截图坐标。",
                {"observationId": observation, "x": coordinate, "y": coordinate},
                ["observationId", "x", "y"],
            ),
            _function(
                "adb_swipe",
                "在 Android 上滑动。",
                {
                    "observationId": observation,
                    "x1": coordinate,
                    "y1": coordinate,
                    "x2": coordinate,
                    "y2": coordinate,
                    "durationMs": {"type": "integer", "minimum": 100, "maximum": 2000},
                },
                ["observationId", "x1", "y1", "x2", "y2"],
            ),
            _function(
                "adb_type_text",
                "在 Android 当前输入框中输入受限 ASCII 文本。",
                {"observationId": observation, "text": {"type": "string", "maxLength": 1000}},
                ["observationId", "text"],
            ),
            _function(
                "adb_press_key",
                "发送 Android 固定按键。",
                {
                    "observationId": observation,
                    "key": {
                        "type": "string",
                        "enum": ["back", "home", "enter", "delete", "tab", "escape", "volume_up", "volume_down"],
                    },
                },
                ["observationId", "key"],
            ),
            *common,
        ]
    return [
        _function("list_windows", "列出可见 Windows 窗口，用于选择唯一目标窗口。", {}),
        _function(
            "observe",
            "获取 Windows 桌面或指定窗口截图、UI Automation 和 OCR 元素。",
            {"windowId": {"type": "integer", "minimum": 1}},
        ),
        _function(
            "click",
            "点击 Windows 截图坐标。",
            {
                "observationId": observation,
                "x": coordinate,
                "y": coordinate,
                "clicks": {"type": "integer", "minimum": 1, "maximum": 2},
            },
            ["observationId", "x", "y"],
        ),
        _function(
            "type_text",
            "向 Windows 当前焦点输入文本。",
            {"observationId": observation, "text": {"type": "string", "maxLength": 10000}},
            ["observationId", "text"],
        ),
        _function(
            "press_key",
            "发送单个 Windows 非系统按键。",
            {"observationId": observation, "key": {"type": "string", "maxLength": 30}},
            ["observationId", "key"],
        ),
        _function(
            "hotkey",
            "发送不包含 Windows 键的组合键。",
            {
                "observationId": observation,
                "keys": {"type": "array", "items": {"type": "string", "maxLength": 30}, "minItems": 1, "maxItems": 4},
            },
            ["observationId", "keys"],
        ),
        _function(
            "scroll",
            "在 Windows 指定位置滚动，正数向上、负数向下。",
            {"observationId": observation, "x": coordinate, "y": coordinate, "amount": {"type": "integer", "minimum": -20, "maximum": 20}},
            ["observationId", "x", "y", "amount"],
        ),
        _function(
            "drag",
            "在 Windows 上拖动。",
            {"observationId": observation, "x1": coordinate, "y1": coordinate, "x2": coordinate, "y2": coordinate},
            ["observationId", "x1", "y1", "x2", "y2"],
        ),
        _function(
            "launch_app",
            "启动本机允许的应用。可用名称：" + "、".join((allowed_app_names or [])[:30]),
            {
                "name": {
                    "type": "string",
                    "enum": (allowed_app_names or ["Microsoft Edge"])[:30],
                }
            },
            ["name"],
        ),
        *common,
    ]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class AssistantTurn:
    message: dict[str, Any]
    content: str
    tool_calls: list[ToolCall]


class ControlLLM:
    def __init__(self, server_url: str, scoped_credential: str):
        self.endpoint = server_url.rstrip("/") + "/api/v1/control/llm/v1/chat/completions"
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(90, connect=20),
            follow_redirects=False,
            headers={"Authorization": f"Bearer {scoped_credential}"},
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> AssistantTurn:
        request_payload = {
            "model": "auto",
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "parallel_tool_calls": False,
            "stream": False,
            "temperature": 0.1,
            "max_tokens": 512,
        }
        response: httpx.Response | None = None
        last_error: httpx.TransportError | None = None
        for attempt in range(MAX_LLM_ATTEMPTS):
            response = None
            try:
                response = await self.client.post(self.endpoint, json=request_payload)
            except httpx.TransportError as exc:
                last_error = exc
            else:
                upstream_exhausted = response.headers.get(RETRY_EXHAUSTED_HEADER) == "1"
                if (
                    response.status_code not in RETRYABLE_STATUS_CODES
                    or upstream_exhausted
                    or attempt + 1 >= MAX_LLM_ATTEMPTS
                ):
                    break
            if attempt + 1 < MAX_LLM_ATTEMPTS:
                retry_after = response.headers.get("retry-after", "") if response is not None else ""
                try:
                    delay = max(0.0, min(float(retry_after), 4.0))
                except ValueError:
                    delay = (0.8, 1.8, 3.2)[attempt]
                await asyncio.sleep(delay)
        if response is None:
            detail = str(last_error or "未收到响应")[:240]
            raise RuntimeError(f"模型服务暂时不可用，已自动重试 {MAX_LLM_ATTEMPTS} 次：{detail}")
        if response.headers.get(RETRY_EXHAUSTED_HEADER) == "1":
            try:
                detail = str(response.json().get("detail") or "")
            except (ValueError, AttributeError):
                detail = ""
            raise RuntimeError(detail or "模型服务暂时不可用，后端自动重连已达到上限")
        if response.status_code in RETRYABLE_STATUS_CODES:
            raise RuntimeError(
                f"模型服务暂时不可用（HTTP {response.status_code}），已自动重试 {MAX_LLM_ATTEMPTS} 次"
            )
        response.raise_for_status()
        payload = response.json()
        choices = payload.get("choices") if isinstance(payload, dict) else None
        message = choices[0].get("message") if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None
        if not isinstance(message, dict):
            raise RuntimeError("模型返回缺少 assistant message")
        content = str(message.get("content") or "").strip()
        calls: list[ToolCall] = []
        for raw in message.get("tool_calls") if isinstance(message.get("tool_calls"), list) else []:
            function = raw.get("function") if isinstance(raw, dict) else None
            if not isinstance(function, dict):
                continue
            name = str(function.get("name") or "")
            call_id = str(raw.get("id") or "")[:100]
            try:
                arguments = json.loads(str(function.get("arguments") or "{}"))
            except json.JSONDecodeError:
                arguments = {}
            if name and call_id and isinstance(arguments, dict):
                calls.append(ToolCall(call_id, name, arguments))
        normalized = {"role": "assistant", "content": content or None}
        if calls:
            normalized["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": json.dumps(call.arguments, ensure_ascii=False)},
                }
                for call in calls
            ]
        return AssistantTurn(normalized, content, calls)
