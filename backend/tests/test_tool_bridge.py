from __future__ import annotations

import json

import pytest

from app.tool_bridge import completion_sse, normalize_completion, prepare_upstream_payload


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "terminal",
            "description": "Run a command",
            "parameters": {"type": "object", "properties": {"command": {"type": "string"}}},
        },
    }
]


def test_prepare_upstream_payload_embeds_tools_and_translates_history():
    payload, names = prepare_upstream_payload(
        {
            "model": "ignored",
            "stream": True,
            "tools": TOOLS,
            "tool_choice": "auto",
            "messages": [
                {"role": "system", "content": "Be useful."},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_old",
                            "type": "function",
                            "function": {"name": "terminal", "arguments": '{"command":"pwd"}'},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call_old", "name": "terminal", "content": "/workspace"},
            ],
        },
        "fixed-model",
    )
    assert names == {"terminal"}
    assert payload["model"] == "fixed-model"
    assert payload["stream"] is False
    assert payload["max_tokens"] == 8192
    assert "tools" not in payload and "tool_choice" not in payload
    assert payload["messages"][0]["role"] == "user"
    assert all(message["role"] != "system" for message in payload["messages"])
    assert "TOOL CALLING PROTOCOL" in payload["messages"][0]["content"]
    assert "<tool_call>" in payload["messages"][1]["content"]
    assert payload["messages"][2]["role"] == "user"
    assert "<tool_response" in payload["messages"][2]["content"]


def test_normalize_completion_converts_allowed_tag_to_tool_call():
    completion = normalize_completion(
        {
            "id": "upstream-id",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": 'Working\n<tool_call>{"name":"terminal","arguments":{"command":"pwd"}}</tool_call>',
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        },
        "fixed-model",
        {"terminal"},
    )
    choice = completion["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    assert choice["message"]["content"] == "Working"
    call = choice["message"]["tool_calls"][0]
    assert call["function"]["name"] == "terminal"
    assert json.loads(call["function"]["arguments"]) == {"command": "pwd"}


def test_normalize_completion_preserves_valid_native_tool_call():
    completion = normalize_completion(
        {"choices": [{"message": {"content": None, "tool_calls": [{
            "id": "upstream-call",
            "type": "function",
            "function": {"name": "terminal", "arguments": '{"command":"pwd"}'},
        }]}}]},
        "fixed-model",
        {"terminal"},
    )
    assert completion["choices"][0]["finish_reason"] == "tool_calls"
    assert json.loads(completion["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]) == {"command": "pwd"}


def test_unknown_or_malformed_tool_calls_are_not_executable():
    with pytest.raises(ValueError, match="无效或未授权"):
        normalize_completion(
            {
                "choices": [
                    {
                        "message": {
                            "content": '<tool_call>{"name":"host_shell","arguments":{}}</tool_call>'
                        },
                        "finish_reason": "stop",
                    }
                ]
            },
            "fixed-model",
            {"terminal"},
        )


def test_incomplete_tool_call_is_rejected_instead_of_shown_as_text():
    with pytest.raises(ValueError, match="未完整结束"):
        normalize_completion(
            {
                "choices": [
                    {
                        "message": {
                            "content": '<tool_call>{"name":"write_file","arguments":{"content":"import os'
                        },
                        "finish_reason": "length",
                    }
                ]
            },
            "fixed-model",
            {"write_file"},
        )


@pytest.mark.parametrize("content", [
    'to=terminal code:\n{"command":"pwd","timeout":30}',
    'const r = await tools.exec_command({"cmd":"pwd","workdir":"/workspace"}); text(r.output);',
])
def test_codex_style_terminal_calls_are_executed_instead_of_exposed(content):
    completion = normalize_completion(
        {"choices": [{"message": {"content": content}, "finish_reason": "stop"}]},
        "fixed-model",
        {"terminal"},
    )
    choice = completion["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    assert choice["message"]["content"] is None
    assert choice["message"]["tool_calls"][0]["function"]["name"] == "terminal"
    assert json.loads(choice["message"]["tool_calls"][0]["function"]["arguments"])["command"] == "pwd"


def test_unparseable_codex_style_tool_call_is_rejected():
    with pytest.raises(ValueError, match="无法安全解析"):
        normalize_completion(
            {"choices": [{"message": {"content": "to=terminal code: not-json"}}]},
            "fixed-model",
            {"terminal"},
        )


def test_completion_sse_contains_tool_delta_and_done_marker():
    completion = normalize_completion(
        {
            "choices": [
                {
                    "message": {
                        "content": '<tool_call>{"name":"terminal","arguments":{"command":"pwd"}}</tool_call>'
                    }
                }
            ]
        },
        "fixed-model",
        {"terminal"},
    )
    stream = b"".join(completion_sse(completion))
    assert b'"tool_calls"' in stream
    assert b'"finish_reason":"tool_calls"' in stream
    assert stream.endswith(b"data: [DONE]\n\n")
