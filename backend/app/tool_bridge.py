from __future__ import annotations

import ast
import json
import re
import secrets
import time
from typing import Any, Iterator


TOOL_CALL_PATTERN = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL | re.IGNORECASE)
TOOL_CALL_TAG_PATTERN = re.compile(r"</?tool_call\b", re.IGNORECASE)
CODEX_TO_PATTERN = re.compile(r"(?:assistant\s+)?to=(?P<name>[\w.:-]+)\s+code\s*:\s*", re.IGNORECASE)
CODEX_EXEC_PATTERN = re.compile(r"(?:const\s+\w+\s*=\s*)?await\s+tools\.exec_command\s*\(", re.IGNORECASE)
CODEX_DIRECTIVE_PATTERN = re.compile(r"(?:\bto=[\w.:-]+\s+code\s*:|\btools\.exec_command\s*\()", re.IGNORECASE)
BRIDGE_MAX_TOKENS = 8192


def _tool_protocol(tools: list[dict[str, Any]]) -> str:
    definitions = []
    for item in tools:
        function = item.get("function") if isinstance(item, dict) else None
        if not isinstance(function, dict) or not function.get("name"):
            continue
        definitions.append(
            {
                "name": str(function["name"]),
                "description": str(function.get("description") or ""),
                "parameters": function.get("parameters") or {"type": "object", "properties": {}},
            }
        )
    return (
        "\n\nTOOL CALLING PROTOCOL\n"
        "You can use the tools listed below. To call a tool, output only one or more exact XML blocks in this form:\n"
        '<tool_call>{"name":"tool_name","arguments":{"key":"value"}}</tool_call>\n'
        "This runtime is Hermes, not Codex. Never emit `to=terminal`, `to=functions.*`, `tools.exec_command(...)`, "
        "channel labels, JavaScript wrappers, or simulated tool transcripts. Do not wrap tool calls in Markdown. "
        "Do not claim an action succeeded before receiving its tool response. "
        "Tool results arrive inside <tool_response> blocks. After a tool result, continue until the user task is complete.\n"
        f"<tools>{json.dumps(definitions, ensure_ascii=False, separators=(',', ':'))}</tools>"
    )


def _append_text(content: Any, text: str) -> Any:
    if isinstance(content, list):
        return [*content, {"type": "text", "text": text}]
    return f"{str(content or '')}{text}"


def _prepend_text(content: Any, text: str) -> Any:
    if isinstance(content, list):
        return [{"type": "text", "text": text}, *content]
    return f"{text}{str(content or '')}"


def _tool_result_content(message: dict[str, Any]) -> Any:
    name = str(message.get("name") or "tool")
    call_id = str(message.get("tool_call_id") or "")
    opening = f'<tool_response name="{name}" tool_call_id="{call_id}">\n'
    closing = "\n</tool_response>"
    content = message.get("content")
    if isinstance(content, list):
        return [{"type": "text", "text": opening}, *content, {"type": "text", "text": closing}]
    return f"{opening}{str(content or '')}{closing}"


def prepare_upstream_payload(payload: dict[str, Any], model: str) -> tuple[dict[str, Any], set[str]]:
    tools = payload.get("tools") if isinstance(payload.get("tools"), list) else []
    allowed_names = {
        str(item.get("function", {}).get("name"))
        for item in tools
        if isinstance(item, dict) and isinstance(item.get("function"), dict) and item["function"].get("name")
    }
    protocol = _tool_protocol(tools)
    messages: list[dict[str, Any]] = []
    system_parts: list[str] = []
    for original in payload.get("messages") or []:
        if not isinstance(original, dict):
            continue
        role = str(original.get("role") or "user")
        message = dict(original)
        if role == "system":
            content = message.get("content")
            if isinstance(content, str) and content:
                system_parts.append(content)
            elif isinstance(content, list):
                system_parts.extend(
                    str(block.get("text") or "")
                    for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                )
            continue
        if role == "assistant" and isinstance(message.get("tool_calls"), list):
            blocks = []
            for call in message.get("tool_calls") or []:
                function = call.get("function") if isinstance(call, dict) else None
                if not isinstance(function, dict) or not function.get("name"):
                    continue
                arguments = function.get("arguments") or "{}"
                try:
                    arguments = json.loads(arguments) if isinstance(arguments, str) else arguments
                except json.JSONDecodeError:
                    arguments = {"raw": arguments}
                block = {"name": str(function["name"]), "arguments": arguments}
                blocks.append(f"\n<tool_call>{json.dumps(block, ensure_ascii=False)}</tool_call>")
            message["content"] = _append_text(message.get("content"), "".join(blocks))
            message.pop("tool_calls", None)
        elif role == "tool":
            message = {"role": "user", "content": _tool_result_content(message)}
        messages.append(message)
    instruction_text = (
        "<system_instructions>\n"
        + "\n\n".join(part for part in system_parts if part)
        + protocol
        + "\n</system_instructions>\n\n<user_message>\n"
    )
    if messages and messages[0].get("role") == "user":
        messages[0]["content"] = _prepend_text(messages[0].get("content"), instruction_text)
        messages[0]["content"] = _append_text(messages[0]["content"], "\n</user_message>")
    else:
        messages.insert(0, {"role": "user", "content": instruction_text + "</user_message>"})

    upstream = dict(payload)
    upstream["model"] = model
    upstream["messages"] = messages
    upstream["stream"] = False
    configured_max = upstream.get("max_tokens")
    if isinstance(configured_max, int) and not isinstance(configured_max, bool):
        upstream["max_tokens"] = max(1, min(configured_max, BRIDGE_MAX_TOKENS))
    else:
        upstream["max_tokens"] = BRIDGE_MAX_TOKENS
    for key in ("tools", "tool_choice", "parallel_tool_calls", "stream_options"):
        upstream.pop(key, None)
    return upstream, allowed_names


def _parse_call(raw: str, allowed_names: set[str]) -> dict[str, Any] | None:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        try:
            value = ast.literal_eval(raw)
        except (SyntaxError, ValueError):
            return None
    if not isinstance(value, dict):
        return None
    function = value.get("function") if isinstance(value.get("function"), dict) else value
    name = str(function.get("name") or "")
    if not name or name not in allowed_names:
        return None
    arguments = function.get("arguments") or {}
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            return None
    if not isinstance(arguments, dict):
        return None
    return {
        "id": f"call_{secrets.token_hex(12)}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))},
    }


def _json_object_at(text: str, start: int) -> tuple[dict[str, Any], int] | None:
    decoder = json.JSONDecoder()
    try:
        value, consumed = decoder.raw_decode(text[start:].lstrip())
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        return None
    leading = len(text[start:]) - len(text[start:].lstrip())
    return value, start + leading + consumed


def _external_tool_call(name: str, arguments: dict[str, Any], allowed_names: set[str]) -> dict[str, Any] | None:
    normalized_name = name.rsplit(".", 1)[-1]
    if normalized_name == "exec_command":
        normalized_name = "terminal"
        raw_arguments = arguments
        command = raw_arguments.get("cmd", raw_arguments.get("command"))
        arguments = {"command": str(command or "")}
        if isinstance(raw_arguments.get("timeout"), int):
            arguments["timeout"] = raw_arguments["timeout"]
        if isinstance(raw_arguments.get("workdir"), str) and raw_arguments["workdir"].strip():
            arguments["workdir"] = raw_arguments["workdir"].strip()
    if normalized_name not in allowed_names:
        return None
    return _parse_call(
        json.dumps({"name": normalized_name, "arguments": arguments}, ensure_ascii=False),
        allowed_names,
    )


def _codex_compat_calls(text: str, allowed_names: set[str]) -> tuple[list[dict[str, Any]], list[tuple[int, int]]]:
    calls: list[dict[str, Any]] = []
    spans: list[tuple[int, int]] = []
    for match in CODEX_TO_PATTERN.finditer(text):
        parsed = _json_object_at(text, match.end())
        if not parsed:
            continue
        arguments, end = parsed
        call = _external_tool_call(match.group("name"), arguments, allowed_names)
        if call:
            calls.append(call)
            spans.append((match.start(), end))
    for match in CODEX_EXEC_PATTERN.finditer(text):
        parsed = _json_object_at(text, match.end())
        if not parsed:
            continue
        arguments, end = parsed
        call = _external_tool_call("exec_command", arguments, allowed_names)
        if call:
            calls.append(call)
            closing = text.find(";", end)
            spans.append((match.start(), closing + 1 if closing >= 0 else end))
    return calls, spans


def normalize_completion(completion: dict[str, Any], model: str, allowed_names: set[str]) -> dict[str, Any]:
    choices = completion.get("choices") if isinstance(completion.get("choices"), list) else []
    first = choices[0] if choices and isinstance(choices[0], dict) else {}
    message = first.get("message") if isinstance(first.get("message"), dict) else {}
    content = message.get("content")
    text = content if isinstance(content, str) else "".join(
        str(item.get("text") or "")
        for item in content if isinstance(item, dict) and item.get("type") in {None, "text"}
    ) if isinstance(content, list) else ""
    matches = list(TOOL_CALL_PATTERN.finditer(text))
    calls = []
    for native_call in message.get("tool_calls") or []:
        function = native_call.get("function") if isinstance(native_call, dict) else None
        if not isinstance(function, dict):
            raise ValueError("模型返回了无效或未授权的工具调用")
        parsed = _parse_call(json.dumps(function, ensure_ascii=False), allowed_names)
        if not parsed:
            raise ValueError("模型返回了无效或未授权的工具调用")
        calls.append(parsed)
    for match in matches:
        parsed = _parse_call(match.group(1), allowed_names)
        if not parsed:
            raise ValueError("模型返回了无效或未授权的工具调用")
        calls.append(parsed)
    visible = TOOL_CALL_PATTERN.sub("", text).strip()
    compatibility_calls, compatibility_spans = _codex_compat_calls(visible, allowed_names)
    calls.extend(compatibility_calls)
    for start, end in reversed(compatibility_spans):
        visible = visible[:start] + visible[end:]
    visible = re.sub(r"\s*text\(\w+\.output\);?\s*", "", visible).strip()
    if TOOL_CALL_TAG_PATTERN.search(visible):
        raise ValueError("模型返回的工具调用未完整结束")
    if CODEX_DIRECTIVE_PATTERN.search(visible):
        raise ValueError("模型返回了无法安全解析的外部工具调用格式")
    normalized_message: dict[str, Any] = {"role": "assistant", "content": visible or None}
    if calls:
        normalized_message["tool_calls"] = calls
    result = {
        "id": str(completion.get("id") or f"chatcmpl_{secrets.token_hex(12)}"),
        "object": "chat.completion",
        "created": int(completion.get("created") or time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": normalized_message,
                "finish_reason": "tool_calls" if calls else str(first.get("finish_reason") or "stop"),
            }
        ],
    }
    if isinstance(completion.get("usage"), dict):
        result["usage"] = completion["usage"]
    return result


def completion_sse(completion: dict[str, Any]) -> Iterator[bytes]:
    choice = completion["choices"][0]
    message = choice["message"]
    common = {
        "id": completion["id"],
        "object": "chat.completion.chunk",
        "created": completion["created"],
        "model": completion["model"],
    }

    def event(delta: dict[str, Any], finish_reason: str | None = None) -> bytes:
        payload = {
            **common,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }
        return f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n".encode("utf-8")

    yield event({"role": "assistant", "content": ""})
    if message.get("content"):
        yield event({"content": message["content"]})
    if message.get("tool_calls"):
        for index, call in enumerate(message["tool_calls"]):
            yield event({"tool_calls": [{"index": index, **call}]})
    yield event({}, str(choice.get("finish_reason") or "stop"))
    if completion.get("usage"):
        usage_payload = {**common, "choices": [], "usage": completion["usage"]}
        yield f"data: {json.dumps(usage_payload, separators=(',', ':'))}\n\n".encode("utf-8")
    yield b"data: [DONE]\n\n"
