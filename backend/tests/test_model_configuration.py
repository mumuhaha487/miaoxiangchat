from __future__ import annotations

import json

import httpx
import pytest

from app import model_gateway as model_gateway_module
from app.config import get_settings
from app.coordinator import Coordinator, required_artifact_contract
from app.database import Database
from app.model_config import MODEL_CONFIGURATION_KEY, ModelConfigStore, ModelEndpoint
from app.llm_retry import LLMResponseFatal
from app.model_gateway import ModelGateway, _red_png_data_url


def endpoint_payload(*, supports_vision: bool, prefix: str) -> dict[str, object]:
    return {
        "base_url": f"https://{prefix}.example.test/v1",
        "api_key": f"{prefix}-secret-key",
        "model": f"{prefix}-model-with-a-long-name",
        "supports_vision": supports_vision,
        "reasoning_enabled": True,
        "reasoning_effort": "max",
        "vision_base_url": "" if supports_vision else f"https://{prefix}-vision.example.test/v1",
        "vision_api_key": "" if supports_vision else f"{prefix}-vision-secret",
        "vision_model": "" if supports_vision else f"{prefix}-vision-model",
    }


def test_model_configuration_is_split_and_secrets_are_not_exposed(tmp_path):
    database = Database(tmp_path / "app.db")
    store = ModelConfigStore(database, get_settings())
    configuration = store.update({
        "split_enabled": True,
        "chat": endpoint_payload(supports_vision=True, prefix="chat"),
        "coordinator": endpoint_payload(supports_vision=True, prefix="coordinator"),
        "executor": endpoint_payload(supports_vision=False, prefix="executor"),
    })

    assert configuration.split_enabled is True
    assert store.endpoint("chat").model.startswith("chat-")
    assert store.endpoint("coordinator").model.startswith("coordinator-")
    assert store.endpoint("executor").vision_model == "executor-vision-model"
    raw = database.get_app_setting(MODEL_CONFIGURATION_KEY) or ""
    assert "coordinator-secret-key" not in raw
    assert "chat-secret-key" not in raw
    assert "executor-vision-secret" not in raw
    public = store.public()
    assert public["chat"]["apiKeyConfigured"] is True
    assert public["coordinator"]["apiKeyConfigured"] is True
    assert public["executor"]["visionApiKeyConfigured"] is True
    assert public["executor"]["reasoningEnabled"] is True
    assert public["executor"]["reasoningEffort"] == "max"
    assert "apiKey" not in public["executor"]


def test_unified_mode_uses_executor_configuration_for_both_roles(tmp_path):
    database = Database(tmp_path / "app.db")
    store = ModelConfigStore(database, get_settings())
    store.update({
        "split_enabled": False,
        "chat": endpoint_payload(supports_vision=True, prefix="chat"),
        "coordinator": endpoint_payload(supports_vision=True, prefix="coordinator"),
        "executor": endpoint_payload(supports_vision=True, prefix="unified"),
    })

    assert store.endpoint("coordinator") == store.endpoint("executor")
    assert store.endpoint("coordinator").model == "unified-model-with-a-long-name"
    assert store.endpoint("chat").model == "chat-model-with-a-long-name"
    assert store.endpoint("chat") != store.endpoint("executor")


def test_legacy_configuration_adds_chat_from_current_default_without_changing_agent_models(tmp_path):
    database = Database(tmp_path / "app.db")
    store = ModelConfigStore(database, get_settings())
    store.update({
        "split_enabled": True,
        "coordinator": endpoint_payload(supports_vision=True, prefix="coordinator"),
        "executor": endpoint_payload(supports_vision=True, prefix="executor"),
    })
    raw = json.loads(database.get_app_setting(MODEL_CONFIGURATION_KEY) or "{}")
    raw.pop("chat", None)
    database.set_app_setting(MODEL_CONFIGURATION_KEY, json.dumps(raw))

    assert store.endpoint("chat").model == get_settings().llm_model
    assert store.endpoint("coordinator").model == "coordinator-model-with-a-long-name"
    assert store.endpoint("executor").model == "executor-model-with-a-long-name"


def test_text_only_model_requires_complete_visual_fallback(tmp_path):
    database = Database(tmp_path / "app.db")
    store = ModelConfigStore(database, get_settings())
    invalid = endpoint_payload(supports_vision=False, prefix="executor")
    invalid["vision_api_key"] = ""
    with pytest.raises(ValueError, match="必须同时填写"):
        store.update({
            "split_enabled": False,
            "coordinator": endpoint_payload(supports_vision=True, prefix="coordinator"),
            "executor": invalid,
        })


@pytest.mark.asyncio
async def test_text_model_receives_visual_description_instead_of_image(tmp_path, monkeypatch):
    store = ModelConfigStore(Database(tmp_path / "app.db"), get_settings())
    gateway = ModelGateway(store, get_settings())
    endpoint = ModelEndpoint(
        base_url="https://text.example.test/v1",
        api_key="text-secret",
        model="text-model",
        supports_vision=False,
        vision_base_url="https://vision.example.test/v1",
        vision_api_key="vision-secret",
        vision_model="vision-model",
    )
    seen: list[tuple[str, str]] = []

    async def describe(vision_endpoint, image_url):
        seen.append((vision_endpoint.model, image_url))
        return "一张纯红色图片"

    monkeypatch.setattr(gateway, "_describe_image", describe)
    prepared = await gateway.prepare_messages(endpoint, [{
        "role": "user",
        "content": [
            {"type": "text", "text": "请查看"},
            {"type": "image_url", "image_url": {"url": _red_png_data_url()}},
        ],
    }])

    assert seen[0][0] == "vision-model"
    assert seen[0][1].startswith("data:image/png;base64,")
    assert all(block.get("type") != "image_url" for block in prepared[0]["content"])
    assert "纯红色" in prepared[0]["content"][1]["text"]


@pytest.mark.asyncio
async def test_reasoning_configuration_is_forced_into_upstream_payload(tmp_path, monkeypatch):
    store = ModelConfigStore(Database(tmp_path / "app.db"), get_settings())
    executor = endpoint_payload(supports_vision=True, prefix="executor")
    executor["reasoning_effort"] = "xhigh"
    store.update({
        "split_enabled": False,
        "chat": endpoint_payload(supports_vision=True, prefix="chat"),
        "coordinator": endpoint_payload(supports_vision=True, prefix="coordinator"),
        "executor": executor,
    })
    gateway = ModelGateway(store, get_settings())
    endpoint, _model, payload = await gateway.prepare_payload("executor", {
        "messages": [{"role": "user", "content": "test"}],
        "reasoning_effort": "low",
    })
    assert endpoint.reasoning_effort == "xhigh"
    assert payload["reasoning_effort"] == "xhigh"

    executor["reasoning_enabled"] = False
    store.update({"split_enabled": False, "executor": executor})
    _endpoint, _model, payload = await gateway.prepare_payload("executor", {
        "messages": [{"role": "user", "content": "test"}],
    })
    assert payload["reasoning_effort"] == "none"


@pytest.mark.asyncio
async def test_text_only_connection_test_passes_visual_description_back_to_text_model(tmp_path, monkeypatch):
    database = Database(tmp_path / "app.db")
    store = ModelConfigStore(database, get_settings())
    store.update({
        "split_enabled": False,
        "coordinator": endpoint_payload(supports_vision=True, prefix="unused"),
        "executor": endpoint_payload(supports_vision=False, prefix="executor"),
    })
    gateway = ModelGateway(store, get_settings())
    messages_seen: list[list[dict[str, object]]] = []

    async def complete(_role, messages, *, max_tokens=2048):
        messages_seen.append(messages)
        return "hi" if len(messages_seen) == 1 else "已理解红色图片", {}, "executor-model"

    async def describe(_endpoint, image_url):
        assert image_url.startswith("data:image/png;base64,")
        return "测试图为纯红色"

    monkeypatch.setattr(gateway, "complete", complete)
    monkeypatch.setattr(gateway, "_describe_image", describe)
    result = await gateway.test_connection("executor")

    assert [stage["name"] for stage in result["stages"]] == ["文本模型", "视觉模型", "视觉描述转交", "并发容错"]
    assert result["stages"][-1]["successful"] == 4
    assert "纯红色" in str(messages_seen[1])


@pytest.mark.asyncio
async def test_connection_test_filters_partial_concurrent_probe_failures(tmp_path, monkeypatch):
    database = Database(tmp_path / "app.db")
    store = ModelConfigStore(database, get_settings())
    store.update({"split_enabled": False, "chat": endpoint_payload(supports_vision=True, prefix="chat")})
    gateway = ModelGateway(store, get_settings())

    async def complete(_role, messages, *, max_tokens=2048):
        text = str(messages[0]["content"])
        if "探针 2" in text:
            raise RuntimeError("simulated transient upstream failure")
        return "OK", {"completion_tokens": min(max_tokens, 2)}, "chat-model"

    monkeypatch.setattr(gateway, "complete", complete)
    result = await gateway.test_connection("chat")

    resilience = result["stages"][-1]
    assert resilience["name"] == "并发容错"
    assert resilience["successful"] == 3
    assert resilience["filteredErrors"] == 1


@pytest.mark.asyncio
async def test_model_gateway_parses_real_upstream_sse_chunks(tmp_path, monkeypatch):
    database = Database(tmp_path / "app.db")
    store = ModelConfigStore(database, get_settings())
    store.update({"split_enabled": False, "chat": endpoint_payload(supports_vision=True, prefix="chat")})
    gateway = ModelGateway(store, get_settings())
    request_payloads: list[dict[str, object]] = []

    async def fake_post(_client, url, *, payload, **_kwargs):
        request_payloads.append(payload)
        body = "\n\n".join([
            'data: {"model":"chat-model-with-a-long-name","choices":[{"delta":{"content":"流"},"finish_reason":null}]}',
            'data: {"model":"chat-model-with-a-long-name","choices":[{"delta":{"content":"式"},"finish_reason":null}]}',
            'data: {"model":"chat-model-with-a-long-name","choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"completion_tokens":2}}',
            "data: [DONE]",
            "",
        ])
        return httpx.Response(200, text=body, request=httpx.Request("POST", url))

    monkeypatch.setattr(model_gateway_module, "post_llm_with_retry", fake_post)
    chunks = [chunk async for chunk in gateway.stream("chat", [{"role": "user", "content": "test"}])]

    assert request_payloads[0]["stream"] is True
    assert [chunk["content"] for chunk in chunks if chunk["type"] == "delta"] == ["流", "式"]
    assert chunks[-1]["type"] == "done"
    assert chunks[-1]["usage"]["completion_tokens"] == 2
    assert chunks[-1]["model"] == "chat-model-with-a-long-name"


@pytest.mark.asyncio
@pytest.mark.parametrize("reported_model", ["gpt-5.6-luna", ""])
async def test_model_gateway_rejects_substituted_or_unreported_model(tmp_path, monkeypatch, reported_model):
    database = Database(tmp_path / "app.db")
    store = ModelConfigStore(database, get_settings())
    store.update({"split_enabled": False, "chat": endpoint_payload(supports_vision=True, prefix="chat")})
    gateway = ModelGateway(store, get_settings())
    calls = 0

    async def fake_post(_client, url, *, payload, validate_response, **_kwargs):
        nonlocal calls
        calls += 1
        assert payload["model"] == "chat-model-with-a-long-name"
        response = httpx.Response(200, json={
            "model": reported_model,
            "choices": [{"message": {"content": "wrong route"}}],
        }, request=httpx.Request("POST", url))
        validate_response(response)
        return response

    monkeypatch.setattr(model_gateway_module, "post_llm_with_retry", fake_post)
    with pytest.raises(LLMResponseFatal, match="上游"):
        await gateway.complete("chat", [{"role": "user", "content": "test"}])
    assert calls == 1


@pytest.mark.asyncio
async def test_coordinator_returns_verifiable_execution_plan():
    class Gateway:
        async def complete(self, role, messages, *, max_tokens):
            assert role == "coordinator"
            assert max_tokens == 2400
            assert "验收条件" in json.dumps(messages, ensure_ascii=False)
            return json.dumps({
                "objective": "完成页面修复",
                "steps": ["读取前端代码", "修改并构建"],
                "requirements": ["沿用现有 CSS"],
                "acceptanceCriteria": ["移动端无横向溢出", "构建通过"],
                "risks": ["长模型名称"],
            }, ensure_ascii=False), {}, "coordinator-model"

    plan = await Coordinator(Gateway()).plan(
        request="修复移动端页面",
        conversation_history=[],
        capability_context="browser",
    )
    assert plan["model"] == "coordinator-model"
    assert plan["acceptanceCriteria"] == ["移动端无横向溢出", "构建通过"]
    assert "COORDINATOR PLAN" in Coordinator.execution_brief(plan)


@pytest.mark.asyncio
async def test_coordinator_repairs_invalid_json_and_selects_real_ppt_workflow():
    class Gateway:
        def __init__(self):
            self.calls = 0

        async def complete(self, role, messages, *, max_tokens):
            self.calls += 1
            if self.calls == 1:
                return "我先制定计划，然后调用工具。", {}, "coordinator-model"
            return json.dumps({
                "objective": "调研 B 站热点并制作 PPT",
                "steps": ["核验近期榜单", "生成并逐页渲染 PPTX"],
                "requirements": ["记录来源"],
                "acceptanceCriteria": ["交付文件可打开"],
                "capabilityRoute": ["skill:codex-ppt"],
                "deliverables": ["热点报告"],
                "evidencePlan": ["检查文件"],
                "failureRecovery": ["失败后再试"],
                "risks": ["榜单会变化"],
            }, ensure_ascii=False), {}, "coordinator-model"

    gateway = Gateway()
    plan = await Coordinator(gateway).plan(
        request="帮我看看 B 站最近有哪些热门热点，并且帮我总结为一个 PPT",
        conversation_history=[],
        capability_context="skill:office-research-qa\nskill:pptx\nskill:codex-ppt",
    )
    assert gateway.calls == 2
    assert plan["capabilityRoute"] == ["tool:browser-CDP", "skill:office-research-qa", "skill:pptx"]
    assert any("不重复创建环境" in item for item in plan["requirements"])
    assert any("quality-submission.json" in item for item in plan["acceptanceCriteria"])


@pytest.mark.asyncio
async def test_coordinator_routes_current_research_docx_through_visible_cdp_and_office_qa():
    class Gateway:
        async def complete(self, role, messages, *, max_tokens):
            return json.dumps({
                "objective": "生成当前 AI 产品更新研究简报",
                "steps": ["检索资料", "制作 DOCX"],
                "requirements": ["引用来源"],
                "acceptanceCriteria": ["文档可打开"],
                "capabilityRoute": ["skill:deep-research"],
                "deliverables": ["研究简报"],
                "evidencePlan": ["检查引用"],
                "failureRecovery": ["更换来源"],
                "risks": ["资料随时变化"],
            }, ensure_ascii=False), {}, "coordinator-model"

    plan = await Coordinator(Gateway()).plan(
        request="请查找最近一周生成式 AI 产品更新，并生成一份可下载的中文 DOCX 研究简报",
        conversation_history=[],
        capability_context="tool:browser-CDP\nskill:office-research-qa\nskill:docx",
    )

    assert plan["capabilityRoute"] == ["tool:browser-CDP", "skill:office-research-qa", "skill:docx"]
    assert any("至少两次打开两个不同" in item for item in plan["requirements"])
    assert any("可解析的 .docx" in item for item in plan["acceptanceCriteria"])
    assert any("browser_exec" in item for item in plan["evidencePlan"])


@pytest.mark.parametrize(("prompt_text", "extensions"), [
    (
        "小学语文老师，在学校的校本培训中做讲座，30-60分钟，关于作业设计，既要新颖有趣，又要实用，"
        "主要讲双减背景下作业的意义，日常分层作业的实施，参加设计作业比赛的具体策略，情境设计，"
        "帮我做一份 PPT 和一份 doc 文档，doc 文档的内容为主字稿",
        [".pptx", ".docx"],
    ),
    (
        "帮我做一个 PPT：小学语文老师做校本培训讲座。除了做 PPT 之外，还要写一个 doc 文档，一起发给我",
        [".pptx", ".docx"],
    ),
    ("帮我看看 B 站最近有哪些热门热点，并且帮我总结为一个 PPT", [".pptx"]),
    ("请查找最近一周生成式 AI 产品更新，并生成一份可下载的中文 DOCX 研究简报。", [".docx"]),
    ("帮我写一个关于最近 AI 热点的 doc 文档，并且将其做成视频 PPT 文件发给我。", [".pptx", ".docx"]),
    ("帮我查找一下最近《明日方舟》有哪些热门角色出现，并且将其归纳为 doc 文档发给我。", [".docx"]),
    ("帮我看看最近澳游有哪些热点，并且将其总结归纳为 doc 文档以及 PPT 发给我。", [".pptx", ".docx"]),
    ("制作 Excel 工作簿、PDF 摘要和 PPT 演示文稿", [".pptx", ".pdf", ".xlsx"]),
    ("请交付PPT文件和doc文档，不要PDF", [".pptx", ".docx"]),
])
def test_required_artifact_contract_uses_real_requests_without_type_substitution(prompt_text, extensions):
    assert [item["extension"] for item in required_artifact_contract(prompt_text)] == extensions


@pytest.mark.asyncio
async def test_coordinator_keeps_every_requested_office_type_in_machine_contract():
    class Gateway:
        async def complete(self, role, messages, *, max_tokens):
            return json.dumps({
                "objective": "完成校本培训材料",
                "steps": ["编写讲座内容", "制作材料"],
                "requirements": ["内容实用"],
                "acceptanceCriteria": ["文件可打开"],
                "capabilityRoute": ["skill:docx"],
                "deliverables": ["讲座材料"],
                "evidencePlan": ["检查文件"],
                "failureRecovery": ["修复失败文件"],
                "risks": [],
            }, ensure_ascii=False), {}, "coordinator-model"

    plan = await Coordinator(Gateway()).plan(
        request="帮我做一份 PPT 和一份 doc 文档，doc 文档的内容为主讲稿",
        conversation_history=[],
        capability_context="skill:office-research-qa\nskill:pptx\nskill:docx",
    )

    assert [item["extension"] for item in plan["requiredArtifacts"]] == [".pptx", ".docx"]
    assert plan["capabilityRoute"] == ["skill:office-research-qa", "skill:pptx", "skill:docx"]
    assert any(".pptx" in item for item in plan["acceptanceCriteria"])
    assert any(".docx" in item for item in plan["acceptanceCriteria"])


@pytest.mark.asyncio
async def test_memory_gate_lets_llm_choose_none_or_a_small_relevant_subset():
    class Gateway:
        def __init__(self):
            self.calls = 0

        async def complete(self, role, messages, *, max_tokens):
            self.calls += 1
            assert max_tokens == 900
            if self.calls == 1:
                return '{"useMemory":false,"selectedIds":[1],"reason":"新任务无关"}', {}, "gate-model"
            return '{"useMemory":true,"selectedIds":[2,999,2],"reason":"沿用明确偏好"}', {}, "gate-model"

    coordinator = Coordinator(Gateway())
    candidates = [
        {"id": 1, "user": "旧的 B 站研究", "result": "旧热点"},
        {"id": 2, "user": "我的文档都使用中文", "result": "已记录"},
    ]
    none = await coordinator.select_memories(request="设计小学语文作业", candidates=candidates)
    selected = await coordinator.select_memories(request="继续沿用我的文档语言偏好", candidates=candidates)

    assert none["selectedIds"] == []
    assert selected["selectedIds"] == [2]
