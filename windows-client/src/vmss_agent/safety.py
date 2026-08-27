from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from typing import Any, Callable


_RISK_KEYWORDS = {
    "删除",
    "清空",
    "卸载",
    "付款",
    "支付",
    "购买",
    "下单",
    "转账",
    "发送",
    "发布",
    "提交",
    "确认订单",
    "密码",
    "验证码",
    "登录",
    "注册",
    "授权",
    "隐私",
    "delete",
    "remove",
    "uninstall",
    "purchase",
    "buy",
    "pay",
    "transfer",
    "send",
    "post",
    "submit",
    "password",
    "verification code",
    "login",
    "sign in",
}


def canonical_action(action: str, arguments: dict[str, Any]) -> str:
    return json.dumps(
        {"action": str(action), "arguments": arguments},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def action_hash(action: str, arguments: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_action(action, arguments).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RiskDecision:
    requires_remote_approval: bool
    summary: str
    action_hash: str


def assess_action(instruction: str, action: str, arguments: dict[str, Any]) -> RiskDecision:
    value = f"{instruction}\n{action}\n{json.dumps(arguments, ensure_ascii=False)}".lower()
    risky = any(keyword in value for keyword in _RISK_KEYWORDS)
    if action in {"type_text", "adb_type_text"} and len(str(arguments.get("text") or "")) > 200:
        risky = True
    description = {
        "click": "点击 Windows 界面",
        "type_text": "向 Windows 输入文本",
        "press_key": "发送 Windows 按键",
        "hotkey": "发送 Windows 组合键",
        "scroll": "滚动 Windows 界面",
        "drag": "拖动 Windows 界面",
        "launch_app": "启动白名单应用",
        "adb_tap": "点击 Android 界面",
        "adb_swipe": "滑动 Android 界面",
        "adb_type_text": "向 Android 输入 ASCII 文本",
        "adb_press_key": "发送 Android 按键",
    }.get(action, action)
    return RiskDecision(risky, description, action_hash(action, arguments))


class EmergencyStop:
    def __init__(self):
        self._paused = threading.Event()
        self._stopped = threading.Event()

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    @property
    def stopped(self) -> bool:
        return self._stopped.is_set()

    def set_paused(self, value: bool) -> None:
        if value:
            self._paused.set()
        else:
            self._paused.clear()

    def stop(self) -> None:
        self._stopped.set()
        self._paused.set()

    def prepare_task(self) -> None:
        if self.paused:
            raise InterruptedError("电脑客户端已暂停")
        self._stopped.clear()

    def check(self) -> None:
        if self.stopped:
            raise InterruptedError("本机急停已触发")
        if self.paused:
            raise InterruptedError("电脑客户端已暂停")


LocalConfirmation = Callable[[str, str, str], bool]
