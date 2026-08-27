from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from .adb_target import ADBTarget
from .config import AgentConfig
from .input_monitor import HumanActivityMonitor
from .llm import ControlLLM, SYSTEM_PROMPT, ToolCall, tools_for_target
from .observation import Observation
from .safety import EmergencyStop, LocalConfirmation, assess_action
from .windows_target import WindowsTarget


class TaskTransport(Protocol):
    async def send_event(
        self,
        task_id: str,
        lease_id: str,
        sequence: int,
        event_type: str,
        payload: dict[str, Any],
        frame_base64: str = "",
    ) -> None: ...

    async def wait_for_approval(
        self, task_id: str, lease_id: str, expected_hash: str, cancel: asyncio.Event
    ) -> bool: ...


_ACTION_TOOLS = {
    "click",
    "type_text",
    "press_key",
    "hotkey",
    "scroll",
    "drag",
    "launch_app",
    "adb_tap",
    "adb_swipe",
    "adb_type_text",
    "adb_press_key",
}

_CAPTURE_MARKERS = ("截一张图", "截个图", "截图", "截屏")
_CAPTURE_FILLERS = (
    "microsoftedge浏览器",
    "googlechrome浏览器",
    "mozillafirefox浏览器",
    "回传给我",
    "发送给我",
    "发给我",
    "我电脑的",
    "我的电脑",
    "这台电脑",
    "电脑上的",
    "当前的",
    "现在的",
    "打开",
    "启动",
    "运行",
    "查看",
    "看看",
    "观察",
    "获取",
    "电脑",
    "windows",
    "主机",
    "当前",
    "现在",
    "浏览器",
    "窗口",
    "界面",
    "画面",
    "屏幕",
    "软件",
    "应用",
    "一下",
    "然后",
    "并且",
    "并",
    "请",
    "帮我",
    "麻烦",
    "给我",
    "它的",
    "其",
    "的",
)


def _compact_instruction(value: str) -> str:
    return re.sub(r"[\s，,。.!！?？、:：;；]+", "", str(value or "")).casefold()


def safe_window_capture_intent(
    instruction: str,
    target_kind: str,
    allowed_apps: dict[str, str],
) -> tuple[str, bool] | None:
    """Recognize only a launch/view allowlisted-app-and-capture instruction."""
    if target_kind != "windows":
        return None
    normalized = _compact_instruction(instruction)
    marker = next((item for item in _CAPTURE_MARKERS if item in normalized), "")
    if not marker:
        return None

    aliases = {_compact_instruction(name): name for name in allowed_apps if _compact_instruction(name)}
    if "浏览器" in allowed_apps:
        aliases.update({"微软edge": "Microsoft Edge", "edge": "Microsoft Edge", "浏览器": "浏览器"})
    if "Google Chrome" in allowed_apps:
        aliases.update({"谷歌浏览器": "Google Chrome", "chrome": "Google Chrome"})
    if "Mozilla Firefox" in allowed_apps:
        aliases.update({"火狐浏览器": "Mozilla Firefox", "firefox": "Mozilla Firefox"})
    matched_alias = next((alias for alias in sorted(aliases, key=len, reverse=True) if alias in normalized), "")
    if not matched_alias:
        return None

    remainder = normalized.replace(marker, "", 1).replace(matched_alias, "", 1)
    for filler in sorted(_CAPTURE_FILLERS, key=len, reverse=True):
        remainder = remainder.replace(filler, "")
    if remainder:
        return None
    launch = any(item in normalized for item in ("打开", "启动", "运行"))
    app_name = aliases[matched_alias]
    if app_name not in allowed_apps:
        app_name = "浏览器"
    return app_name, launch


class TaskRunner:
    def __init__(
        self,
        config: AgentConfig,
        emergency: EmergencyStop,
        windows: WindowsTarget | None,
        adb: ADBTarget | None,
        confirm_local: LocalConfirmation,
        llm_factory: Callable[[str, str], ControlLLM] = ControlLLM,
        activity: HumanActivityMonitor | None = None,
    ):
        self.config = config
        self.emergency = emergency
        self.windows = windows
        self.adb = adb
        self.confirm_local = confirm_local
        self.llm_factory = llm_factory
        self.activity = activity

    async def run(
        self,
        task: dict[str, Any],
        lease_id: str,
        credential: str,
        transport: TaskTransport,
        cancel: asyncio.Event,
    ) -> None:
        task_id = str(task.get("id") or "")
        instruction = str(task.get("instruction") or "").strip()
        target_kind = str(task.get("targetKind") or "windows")
        target_id = str(task.get("targetId") or "desktop")
        sequence = 1

        async def event(event_type: str, payload: dict[str, Any], frame: str = "") -> None:
            nonlocal sequence
            sequence += 1
            await transport.send_event(task_id, lease_id, sequence, event_type, payload, frame)

        if self.config.require_local_task_confirmation:
            accepted = await asyncio.to_thread(
                self.confirm_local,
                str(task.get("deviceName") or self.config.device_name),
                instruction,
                target_id,
            )
            if not accepted:
                await event("task.cancelled", {"reason": "Windows 电脑本机拒绝了远程任务"})
                return
        if cancel.is_set():
            await event("task.stopped", {"reason": "任务在执行前已取消"})
            return

        if self.activity:
            self.activity.reset()

        try:
            self.emergency.prepare_task()
        except InterruptedError as exc:
            await event("task.stopped", {"reason": str(exc)})
            return
        llm: ControlLLM | None = None
        control_overlay_active = target_kind == "windows" and self.windows is not None
        if control_overlay_active:
            self.windows.set_control_active(True)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"目标类型：{target_kind}\n目标 ID：{target_id}\n用户任务：{instruction}",
            },
        ]
        tools = tools_for_target(target_kind, list(self.config.allowed_apps))
        requires_observation = True
        try:
            if await self._run_safe_window_capture(instruction, target_kind, cancel, event):
                return
            llm = self.llm_factory(self.config.server_url, f"{credential}~{task_id}~{lease_id}")
            for _step in range(self.config.max_steps):
                self._check(cancel)
                turn = await llm.complete(messages, tools)
                messages.append(turn.message)
                if turn.content:
                    await event("reasoning", {"message": turn.content[:1000]})
                if not turn.tool_calls:
                    await self._send_final_observation(target_kind, target_id, event)
                    await event("task.completed", {"output": turn.content or "任务已完成"})
                    return
                finished = False
                for call_index, call in enumerate(turn.tool_calls):
                    if call_index > 0:
                        result: Any = {"error": "原子执行要求每轮只调用一个工具；本调用未执行"}
                    elif requires_observation and call.name not in {"observe", "list_windows"}:
                        result = {"error": "上一个界面状态尚未观察；请先调用 observe"}
                    else:
                        result, did_change, did_finish = await self._execute(
                            call,
                            instruction,
                            target_kind,
                            target_id,
                            task_id,
                            lease_id,
                            transport,
                            cancel,
                            event,
                        )
                        finished = finished or did_finish
                        if call.name == "observe":
                            requires_observation = False
                        elif did_change:
                            requires_observation = True
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "name": call.name,
                            "content": result if isinstance(result, list) else json.dumps(result, ensure_ascii=False),
                        }
                    )
                if finished:
                    return
            await event("task.failed", {"error": f"超过最大步骤数 {self.config.max_steps}，任务已停止"})
        except InterruptedError as exc:
            if not cancel.is_set():
                await event("task.stopped", {"reason": str(exc)})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not cancel.is_set():
                await event("task.failed", {"error": str(exc)[:20000]})
        finally:
            if control_overlay_active and self.windows:
                self.windows.set_control_active(False)
            if llm is not None:
                await llm.close()

    async def _run_safe_window_capture(self, instruction: str, target_kind: str, cancel: asyncio.Event, event) -> bool:
        intent = safe_window_capture_intent(instruction, target_kind, self.config.allowed_apps)
        if not intent or not self.windows:
            return False
        app_name, launch = intent
        self._check(cancel)
        if launch:
            await event("action.started", {"action": "launch_app", "summary": f"启动允许列表应用：{app_name}"})
            try:
                result = await asyncio.to_thread(self.windows.launch_app, app_name)
            except Exception as exc:
                await event("action.failed", {"action": "launch_app", "error": str(exc)[:2000]})
                raise
            await event("action.completed", {"action": "launch_app", "result": result})

        window = await self._wait_for_app_window(app_name, cancel, timeout=6.0 if launch else 0.5)
        if not window:
            raise RuntimeError(f"未找到可见的 {app_name} 窗口")
        observation = await self._observe("windows", "desktop", {"windowId": int(window["windowId"])})
        summary = observation.summary()
        summary.update({"message": "最终截图已回传", "purpose": "result", "app": app_name})
        await event("observation", summary, observation.frame_base64())
        action = "打开并截取" if launch else "截取"
        await event("task.completed", {"output": f"已{action} {app_name} 当前窗口，截图已回传。"})
        return True

    async def _wait_for_app_window(
        self,
        app_name: str,
        cancel: asyncio.Event,
        *,
        timeout: float,
    ) -> dict[str, Any] | None:
        if not self.windows:
            return None
        command = str(self.config.allowed_apps.get(app_name) or "")
        executable = Path(command).name.casefold()
        title_tokens = {
            "浏览器": ("edge", "chrome", "firefox"),
            "Microsoft Edge": ("edge",),
            "Google Chrome": ("chrome",),
            "Mozilla Firefox": ("firefox",),
        }.get(app_name, (_compact_instruction(app_name),))
        deadline = asyncio.get_running_loop().time() + max(0.0, timeout)
        while True:
            self._check(cancel)
            windows = await asyncio.to_thread(self.windows.list_windows)
            candidates = [
                item
                for item in windows
                if (executable and str(item.get("executable") or "").casefold() == executable)
                or any(token and token in str(item.get("title") or "").casefold() for token in title_tokens)
            ]
            if candidates:
                return max(
                    candidates,
                    key=lambda item: int(item.get("width") or 0) * int(item.get("height") or 0),
                )
            if asyncio.get_running_loop().time() >= deadline:
                return None
            await asyncio.sleep(0.25)

    def _check(self, cancel: asyncio.Event) -> None:
        if cancel.is_set():
            raise InterruptedError("用户已停止任务")
        self.emergency.check()
        if self.config.pause_on_user_input and self.activity and self.activity.recent():
            raise InterruptedError("检测到本机用户正在使用键盘或鼠标，远程动作已停止")

    async def _execute(
        self,
        call: ToolCall,
        instruction: str,
        target_kind: str,
        target_id: str,
        task_id: str,
        lease_id: str,
        transport: TaskTransport,
        cancel: asyncio.Event,
        event,
    ) -> tuple[Any, bool, bool]:
        self._check(cancel)
        name, arguments = call.name, dict(call.arguments)
        if name == "finish":
            output = str(arguments.get("output") or "任务已完成")[:100000]
            await self._send_final_observation(target_kind, target_id, event)
            await event("task.completed", {"output": output})
            return {"success": True}, False, True
        if name == "list_windows":
            if target_kind != "windows" or not self.windows:
                return {"error": "Windows 目标不可用"}, False, False
            return {"windows": await asyncio.to_thread(self.windows.list_windows)}, False, False
        if name == "observe":
            observation = await self._observe(target_kind, target_id, arguments)
            await event("observation", observation.summary(), observation.frame_base64())
            return observation.tool_content(), False, False
        if name not in _ACTION_TOOLS:
            return {"error": "不支持的工具"}, False, False

        risk = assess_action(instruction, name, arguments)
        if risk.requires_remote_approval:
            await event(
                "approval.required",
                {"summary": risk.summary, "action": name, "actionHash": risk.action_hash},
            )
            approved = await transport.wait_for_approval(task_id, lease_id, risk.action_hash, cancel)
            if not approved:
                return {"error": "用户拒绝了此高风险操作"}, False, True
        self._check(cancel)
        await event("action.started", {"action": name, "summary": risk.summary})
        try:
            result = await asyncio.to_thread(self._perform_action, target_kind, name, arguments)
        except Exception as exc:
            await event("action.failed", {"action": name, "error": str(exc)[:2000]})
            return {"error": str(exc)[:2000]}, False, False
        await event("action.completed", {"action": name, "result": result})
        return result, True, False

    async def _observe(self, target_kind: str, target_id: str, arguments: dict[str, Any]) -> Observation:
        if target_kind == "adb":
            if not self.adb:
                raise RuntimeError("ADB 目标不可用")
            return await asyncio.to_thread(self.adb.observe, target_id)
        if not self.windows:
            raise RuntimeError("Windows 目标不可用")
        raw_window = arguments.get("windowId")
        window_id = int(raw_window) if raw_window is not None else None
        return await asyncio.to_thread(self.windows.observe, window_id)

    def _perform_action(self, target_kind: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if target_kind == "adb":
            if not self.adb:
                raise RuntimeError("ADB 目标不可用")
            if name == "adb_tap":
                return self.adb.tap(arguments["observationId"], int(arguments["x"]), int(arguments["y"]))
            if name == "adb_swipe":
                return self.adb.swipe(
                    arguments["observationId"],
                    int(arguments["x1"]),
                    int(arguments["y1"]),
                    int(arguments["x2"]),
                    int(arguments["y2"]),
                    int(arguments.get("durationMs") or 350),
                )
            if name == "adb_type_text":
                return self.adb.type_ascii(arguments["observationId"], str(arguments.get("text") or ""))
            if name == "adb_press_key":
                return self.adb.press_key(arguments["observationId"], str(arguments.get("key") or ""))
            raise ValueError("当前 ADB 目标不支持该工具")
        if not self.windows:
            raise RuntimeError("Windows 目标不可用")
        if name == "click":
            return self.windows.click(
                arguments["observationId"], int(arguments["x"]), int(arguments["y"]), int(arguments.get("clicks") or 1)
            )
        if name == "type_text":
            return self.windows.type_text(arguments["observationId"], str(arguments.get("text") or ""))
        if name == "press_key":
            return self.windows.press_key(arguments["observationId"], str(arguments.get("key") or ""))
        if name == "hotkey":
            return self.windows.hotkey(arguments["observationId"], list(arguments.get("keys") or []))
        if name == "scroll":
            return self.windows.scroll(
                arguments["observationId"],
                int(arguments["x"]),
                int(arguments["y"]),
                int(arguments["amount"]),
            )
        if name == "drag":
            return self.windows.drag(
                arguments["observationId"],
                int(arguments["x1"]),
                int(arguments["y1"]),
                int(arguments["x2"]),
                int(arguments["y2"]),
            )
        if name == "launch_app":
            return self.windows.launch_app(str(arguments.get("name") or ""))
        raise ValueError("当前 Windows 目标不支持该工具")

    async def _send_final_observation(self, target_kind: str, target_id: str, event) -> None:
        arguments: dict[str, Any] = {}
        if target_kind == "windows" and self.windows:
            last_window_id = self.windows.last_observation_window_id()
            if last_window_id:
                arguments["windowId"] = last_window_id
        try:
            observation = await self._observe(target_kind, target_id, arguments)
        except Exception as exc:
            await event("reasoning", {"message": f"最终截图获取失败：{str(exc)[:500]}"})
            return
        summary = observation.summary()
        summary.update({"message": "最终截图已回传", "purpose": "result"})
        await event("observation", summary, observation.frame_base64())
