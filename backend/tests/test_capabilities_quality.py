from __future__ import annotations

import json
import zipfile
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.capabilities import CapabilityManager
from app.database import Database
from app.config import get_settings
from app.hermes_client import HermesClient
from app import hermes_client as hermes_client_module
from app.quality_reviewer import QualityReviewer, required_artifact_issues
from app.task_dispatcher import (
    TaskDispatcher,
    browser_cdp_evidence,
    browser_cdp_evidence_issue,
    checkpoint_http_urls,
    quality_revision_requirements,
    research_office_source_gap,
    submission_only_quality_issues,
    task_workspace_checkpoint,
    transient_run_error,
)


class CapabilityRuntime:
    def __init__(self, root: Path):
        self.root = root
        self.settings = SimpleNamespace(data_dir=root)

    def _ensure_user_dirs_sync(self, user_id: str) -> dict[str, Path]:
        base = self.root / "users" / user_id
        result = {
            "container_root": base,
            "container_hermes": base / "hermes",
            "container_skills": base / "hermes" / "skills",
            "container_workspace": base / "WORKSPACE",
        }
        for path in result.values():
            path.mkdir(parents=True, exist_ok=True)
        return result


class DispatcherRuntime(CapabilityRuntime):
    def __init__(self, root: Path):
        super().__init__(root)
        self.settings.expert_quality_threshold = 82
        self.settings.expert_max_revisions = 2

    def user_paths(self, user_id: str) -> dict[str, Path]:
        return self._ensure_user_dirs_sync(user_id)

    async def ensure_worker(self, _user_id: str, _busy: set[str]) -> str:
        return "worker-test"

    def mark_used(self, _user_id: str) -> None:
        return None


class RevisionHermes:
    def __init__(self):
        self.starts = 0
        self.requests: list[dict] = []

    async def start_run(self, **kwargs):
        self.starts += 1
        self.requests.append(kwargs)
        return {"run_id": f"revision-{self.starts}"}

    async def events(self, _worker: str, _user_id: str, _run_id: str):
        yield {"event": "run.completed", "output": "Revised and verified."}


class EmptyRecoveryHermes(RevisionHermes):
    def __init__(self):
        super().__init__()

    async def start_run(self, **kwargs):
        self.starts += 1
        self.requests.append(kwargs)
        return {"run_id": f"empty-recovery-{self.starts}"}

    async def events(self, _worker: str, _user_id: str, _run_id: str):
        yield {"event": "run.completed", "output": "Recovered and verified."}


class TransientRecoveryHermes(RevisionHermes):
    async def events(self, _worker: str, _user_id: str, run_id: str):
        if run_id == "initial-failed-run":
            yield {
                "event": "run.failed",
                "error": 'HTTP 502: {"detail":"模型服务暂时不可用（HTTP 503）"}',
            }
            return
        yield {"event": "run.completed", "output": "Recovered and verified."}


class PassingReviewer:
    async def review(self, **_kwargs):
        return {
            "score": 93,
            "passed": True,
            "threshold": 82,
            "issues": [],
            "pageReviews": [],
        }


class SequenceReviewer:
    def __init__(self):
        self.calls = 0

    async def review(self, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            return {
                "score": 61,
                "passed": False,
                "threshold": 82,
                "issues": ["Page 1 hierarchy is weak"],
                "pageReviews": [{"page": 1, "score": 61, "issues": ["Weak hierarchy"], "redo": True}],
            }
        return {
            "score": 91,
            "passed": True,
            "threshold": 82,
            "issues": [],
            "pageReviews": [{"page": 1, "score": 91, "issues": [], "redo": False}],
        }


class AlwaysRedoReviewer:
    def __init__(self, scores: list[int]):
        self.scores = scores
        self.calls = 0

    async def review(self, **_kwargs):
        score = self.scores[self.calls]
        self.calls += 1
        return {
            "score": score,
            "passed": False,
            "threshold": 90,
            "summary": f"第 {self.calls} 次仍需重做",
            "issues": [f"第 {self.calls} 次存在问题"],
            "pageReviews": [],
        }


class ArtifactRevisionHermes(RevisionHermes):
    def __init__(self, workspace: Path):
        super().__init__()
        self.workspace = workspace

    async def events(self, _worker: str, _user_id: str, run_id: str):
        revision = int(run_id.rsplit("-", 1)[-1]) + 1
        (self.workspace / "result.txt").write_text(f"version-{revision}", encoding="utf-8")
        yield {
            "event": "run.completed",
            "output": f"第 {revision} 个版本\n[[artifact:/workspace/result.txt]]",
        }


class UnchangedOfficeHermes(RevisionHermes):
    def __init__(self, workspace: Path, filename: str = "report.pptx"):
        super().__init__()
        self.workspace = workspace
        self.filename = filename

    async def events(self, _worker: str, _user_id: str, _run_id: str):
        if self.starts == 2:
            (self.workspace / self.filename).write_bytes(b"corrected-office-version")
        yield {
            "event": "run.completed",
            "output": f"已完成定点修复\n[[artifact:/workspace/{self.filename}]]",
        }


class SourceEmbeddingHermes(RevisionHermes):
    def __init__(self, workspace: Path):
        super().__init__()
        self.workspace = workspace

    async def events(self, _worker: str, _user_id: str, _run_id: str):
        relationships = """<?xml version="1.0" encoding="UTF-8"?>
        <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
          <Relationship Id="rId1" Type="hyperlink" Target="https://openai.com/index/specific-release/" TargetMode="External"/>
          <Relationship Id="rId2" Type="hyperlink" Target="https://www.anthropic.com/news/specific-release" TargetMode="External"/>
        </Relationships>"""
        with zipfile.ZipFile(self.workspace / "report.pptx", "w") as archive:
            archive.writestr("ppt/slides/slide1.xml", "<p:sld xmlns:p='urn:p'><p:cSld/></p:sld>")
            archive.writestr("ppt/slides/_rels/slide1.xml.rels", relationships)
        yield {
            "event": "run.completed",
            "output": "来源附录已嵌入并验证\n[[artifact:/workspace/report.pptx]]",
        }


def create_user(database: Database, email: str) -> dict:
    return database.create_user(
        email=email,
        display_name=email.split("@", 1)[0],
        password_hash="hash",
        access_tier="vip",
    )


def test_browser_cdp_evidence_persists_across_quality_attempts(tmp_path: Path):
    database = Database(tmp_path / "app.db")
    user = create_user(database, "cdp-evidence@example.com")
    conversation = database.create_conversation(user["id"], "CDP", "agent", agent_profile="expert")
    task = database.create_task(
        user_id=user["id"], conversation_id=conversation["id"], prompt="研究当前资料", attachment_ids=[]
    )
    database.add_task_event(task["id"], "tool.started", {"tool": "browser_exec", "preview": "old Google"})
    database.add_task_event(task["id"], "tool.completed", {"tool": "browser_exec", "error": False})
    database.add_task_event(task["id"], "quality.started", {"attempt": 1})
    database.add_task_event(task["id"], "tool.started", {"tool": "web_search", "preview": "does not count"})
    database.add_task_event(task["id"], "tool.started", {"tool": "browser_exec", "preview": "Google"})
    database.add_task_event(task["id"], "tool.completed", {"tool": "browser_exec", "error": False})
    database.add_task_event(task["id"], "tool.started", {
        "tool": "browser_exec", "preview": "Open https://openai.com/index/specific-release/",
    })
    database.add_task_event(task["id"], "tool.completed", {"tool": "browser_exec", "error": False})
    database.add_task_event(task["id"], "tool.started", {
        "tool": "browser_exec", "preview": "Open https://www.anthropic.com/news/specific-release",
    })
    database.add_task_event(task["id"], "tool.completed", {"tool": "browser_exec", "error": False})
    database.add_task_event(task["id"], "quality.started", {"attempt": 2})

    evidence = browser_cdp_evidence(database, task["id"])
    assert [item["preview"] for item in evidence] == [
        "old Google",
        "Google",
        "Open https://openai.com/index/specific-release/",
        "Open https://www.anthropic.com/news/specific-release",
    ]
    assert browser_cdp_evidence_issue(evidence) == ""
    assert "非 Google 来源页至少 2 个" in browser_cdp_evidence_issue([
        {"tool": "browser_exec", "preview": "Google"},
        {"tool": "browser_exec", "preview": "Open https://example.com/article/one"},
        {"tool": "browser_exec", "preview": "Open https://example.com/article/one"},
    ])
    assert "非 Google 来源页至少 2 个" in browser_cdp_evidence_issue([
        {"tool": "browser_exec", "preview": "Google"},
        {"tool": "browser_exec", "preview": "Testing browser CDP connection"},
        {"tool": "browser_exec", "preview": "Checking CDP connection status"},
    ])


def test_browser_cdp_evidence_ignores_failed_completion(tmp_path: Path):
    database = Database(tmp_path / "app.db")
    user = create_user(database, "cdp-failed@example.com")
    conversation = database.create_conversation(user["id"], "CDP", "agent", agent_profile="expert")
    task = database.create_task(
        user_id=user["id"], conversation_id=conversation["id"], prompt="研究当前资料", attachment_ids=[]
    )
    database.add_task_event(task["id"], "tool.started", {
        "tool": "browser_exec", "run_id": "run-1", "preview": "Google",
    })
    database.add_task_event(task["id"], "tool.completed", {
        "tool": "browser_exec", "run_id": "run-1", "error": True,
    })
    assert browser_cdp_evidence(database, task["id"]) == []


def test_submission_only_quality_issues_do_not_require_office_byte_change():
    assert submission_only_quality_issues({
        "issues": [
            "质检清单没有提供逐页预览图。",
            "以下必要检查没有完成证明：all_pages_rendered",
        ],
    })
    assert not submission_only_quality_issues({
        "issues": ["第 2 页表格发生裁切。", "质检清单没有提供逐页预览图。"],
    })


def test_transient_run_error_retries_availability_failures_but_not_model_identity_or_auth():
    assert transient_run_error("HTTP 502: 模型服务暂时不可用，upstream HTTP 503")
    assert transient_run_error("connection reset by peer")
    assert not transient_run_error("HTTP 502: 模型身份不一致，拒绝模型替换")
    assert not transient_run_error("HTTP 401 unauthorized")


def test_quality_revision_requirements_hides_reviewer_internals_and_adds_source_fix_steps():
    feedback = quality_revision_requirements({
        "score": 59,
        "specificSourceUrlCount": 1,
        "issues": ["当前只有 1 个具体来源 URL，需要至少 2 个证据页。"],
        "pageReviews": [{"page": 3, "score": 98}],
    })
    assert "当前只有 1 个具体来源 URL" in feedback
    assert "SOURCE FIX PROCEDURE" in feedback
    assert "specificSourceUrlCount" not in feedback
    assert '"score"' not in feedback
    assert "pageReviews" not in feedback


def test_task_checkpoint_is_a_recent_file_allowlist_and_collects_exact_urls(tmp_path: Path):
    recent = tmp_path / "research-sources.md"
    recent.write_text(
        "https://openai.com/index/specific-release/\nhttps://www.anthropic.com/news/specific-release",
        encoding="utf-8",
    )
    stale = tmp_path / "old-generator.py"
    stale.write_text("https://example.com/old/source", encoding="utf-8")
    stale_time = recent.stat().st_mtime - 30
    stale.touch()
    import os
    os.utime(stale, (stale_time, stale_time))
    task = {"started_at": int(recent.stat().st_mtime * 1000), "created_at": 0}

    files, checkpoint = task_workspace_checkpoint(tmp_path, task)
    assert files == [recent]
    assert "research-sources.md" in checkpoint
    assert "old-generator.py" not in checkpoint
    assert checkpoint_http_urls(files) == [
        "https://openai.com/index/specific-release/",
        "https://www.anthropic.com/news/specific-release",
    ]


@pytest.mark.asyncio
async def test_hermes_prompts_separate_research_revision_and_targeted_recoveries(monkeypatch):
    payloads: list[dict] = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"run_id": f"run-{len(payloads)}"}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url, **kwargs):
            payloads.append(kwargs["json"])
            return Response()

    monkeypatch.setattr(hermes_client_module.httpx, "AsyncClient", lambda **_kwargs: Client())
    client = HermesClient(get_settings())
    common = {
        "worker_name": "worker-test",
        "user_id": "user-test",
        "user_input": "生成研究简报",
        "conversation_history": [],
    }
    await client.start_run(session_id="initial", agent_profile="expert", **common)
    await client.start_run(
        session_id="revision",
        revision_feedback=(
            "REQUIRED FIXES FOR THE REJECTED DELIVERABLE:\n1. Replace the generic source URL.\n"
            "CURRENT TASK WORKSPACE CHECKPOINT (strict file allowlist):\n- /workspace/report.docx"
        ),
        attempt_number=2,
        **common,
    )
    await client.start_run(
        session_id="cdp",
        revision_feedback=(
            "CDP EVIDENCE ONLY RECOVERY. Use browser_exec only.\n"
            "EXACT URL CANDIDATES:\n- https://openai.com/index/specific-release/"
        ),
        **common,
    )
    await client.start_run(
        session_id="source-embedding",
        revision_feedback=(
            "SOURCE EMBEDDING RECOVERY. Add a Sources slide to the actual Office file.\n"
            "EXACT CLAIM-LEVEL URL CANDIDATES FROM THIS TASK:\n"
            "- https://openai.com/index/specific-release/"
        ),
        **common,
    )
    await client.start_run(
        session_id="transient",
        recovery_feedback=(
            "TRANSIENT UPSTREAM RECOVERY. Resume from the current checkpoint.\n"
            "CURRENT TASK WORKSPACE CHECKPOINT (strict file allowlist):\n- /workspace/report.docx"
        ),
        **common,
    )

    initial = payloads[0]["instructions"]
    revision = payloads[1]["instructions"]
    cdp = payloads[2]["instructions"]
    source_embedding = payloads[3]["instructions"]
    transient = payloads[4]["instructions"]
    assert "/workspace/research-sources.md" in initial
    assert "at least two different specific source pages" in initial
    assert "strict current-task allowlist" in revision
    assert "INDEPENDENT REVIEW JSON" not in revision
    assert "locate/read validator implementation source" in revision
    assert "CDP EVIDENCE ONLY RECOVERY" in cdp
    assert "THIS IS QUALITY REVISION ATTEMPT" not in cdp
    assert "SOURCE EMBEDDING RECOVERY" in source_embedding
    assert "THIS IS QUALITY REVISION ATTEMPT" not in source_embedding
    assert "TRANSIENT UPSTREAM RECOVERY" in transient
    assert "EMPTY-RESPONSE RECOVERY CONTINUATION" not in transient


def test_quality_reviewer_extracts_office_hyperlink_targets(tmp_path: Path):
    docx = tmp_path / "report.docx"
    relationships = """<?xml version="1.0" encoding="UTF-8"?>
    <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
      <Relationship Id="rId1" Type="hyperlink" Target="https://openai.com/news/example" TargetMode="External"/>
      <Relationship Id="rId2" Type="hyperlink" Target="https://www.anthropic.com/news/example" TargetMode="External"/>
    </Relationships>"""
    with zipfile.ZipFile(docx, "w") as archive:
        archive.writestr("word/_rels/document.xml.rels", relationships)
        archive.writestr("word/document.xml", "<w:document xmlns:w='urn:test'><w:t>研究报告</w:t></w:document>")
    urls = QualityReviewer._office_urls(tmp_path, [{
        "filename": "report.docx",
        "relative_path": "report.docx",
    }])
    assert urls == [
        "https://openai.com/news/example",
        "https://www.anthropic.com/news/example",
    ]
    assert QualityReviewer._specific_source_urls(urls) == urls
    assert research_office_source_gap(
        tmp_path,
        [{"filename": "report.docx", "relative_path": "report.docx"}],
        ["tool:browser-CDP"],
    ) == (urls, urls)


def test_research_office_source_gap_detects_missing_embedded_urls(tmp_path: Path):
    pptx = tmp_path / "report.pptx"
    with zipfile.ZipFile(pptx, "w") as archive:
        archive.writestr(
            "ppt/slides/slide1.xml",
            "<p:sld xmlns:p='urn:p' xmlns:a='urn:a'><a:t>Research result without URLs</a:t></p:sld>",
        )
    assert research_office_source_gap(
        tmp_path,
        [{"filename": "report.pptx", "relative_path": "report.pptx"}],
        ["tool:browser-CDP"],
    ) == ([], [])


def test_quality_reviewer_rejects_generic_source_landing_pages():
    urls = [
        "https://openai.com/index/",
        "https://www.anthropic.com/news/",
        "https://blog.google/technology/ai/",
        "https://huggingface.co/Qwen",
        "https://openai.com/index/how-the-world-is-putting-chatgpt-to-work/",
        "https://www.reuters.com/world/france-use-ai-tools-test-cybersecurity-2026-08-18/",
    ]
    assert QualityReviewer._specific_source_urls(urls) == [
        "https://openai.com/index/how-the-world-is-putting-chatgpt-to-work/",
        "https://www.reuters.com/world/france-use-ai-tools-test-cybersecurity-2026-08-18/",
    ]


def test_quality_reviewer_rejects_out_of_window_publication_dates_for_recent_week_request():
    issue = QualityReviewer._recent_week_issue(
        "请查找最近一周生成式 AI 产品更新",
        "发布日期：2026 年 8 月 21 日\n发布日期：2026 年 8 月 16 日正式生效",
        date(2026, 8, 27),
    )
    assert "2026-08-20 至 2026-08-27" in issue
    assert "2026-08-16" in issue
    assert QualityReviewer._recent_week_issue(
        "请查找最近一周生成式 AI 产品更新",
        "发布日期：2026-08-20\n发布日期：2026-08-27",
        date(2026, 8, 27),
    ) == ""
    assert QualityReviewer._recent_week_issue(
        "整理生成式 AI 产品历史",
        "发布日期：2025-01-01",
        date(2026, 8, 27),
    ) == ""


def test_required_artifact_contract_rejects_two_docx_files_when_pptx_is_missing():
    plan = {
        "requiredArtifacts": [
            {"extension": ".pptx", "minimumCount": 1},
            {"extension": ".docx", "minimumCount": 1},
        ]
    }
    artifacts = [
        {"filename": "讲座材料.docx"},
        {"filename": "主讲稿.docx"},
    ]
    issues = required_artifact_issues(
        plan,
        artifacts,
        {"deliverables": ["/workspace/讲座材料.docx", "/workspace/主讲稿.docx"]},
        require_submission_listing=True,
    )

    assert len(issues) == 1
    assert ".pptx" in issues[0]
    assert "不能替代" in issues[0]


@pytest.mark.asyncio
async def test_quality_prompt_repairs_non_json_response_before_rework():
    class Gateway:
        def __init__(self):
            self.calls = 0

        async def complete(self, role, messages, *, max_tokens):
            self.calls += 1
            if self.calls == 1:
                return "验收不通过，请重做。", {}, "reviewer-model"
            return json.dumps({
                "score": 72,
                "summary": "文件存在，但缺少逐页来源标注。",
                "issues": ["第 2 页的热点数据没有标注采集日期和来源链接。"],
                "redo": True,
            }, ensure_ascii=False), {}, "reviewer-model"

    gateway = Gateway()
    reviewer = QualityReviewer(get_settings(), gateway)
    result = await reviewer._completion([{"type": "text", "text": "只返回 JSON"}])
    assert gateway.calls == 2
    assert result["score"] == 72
    assert "第 2 页" in result["issues"][0]


def test_agent_profile_is_snapshotted_to_task(tmp_path: Path):
    database = Database(tmp_path / "app.db")
    user = create_user(database, "profile@example.com")
    conversation = database.create_conversation(
        user["id"], "Expert", "agent", agent_profile="expert"
    )
    expert = database.create_task(
        user_id=user["id"], conversation_id=conversation["id"], prompt="one", attachment_ids=[]
    )
    database.execute(
        "UPDATE conversations SET agent_profile = 'fast' WHERE id = ?",
        (conversation["id"],),
    )
    fast = database.create_task(
        user_id=user["id"], conversation_id=conversation["id"], prompt="two", attachment_ids=[]
    )
    assert expert["agent_profile"] == "expert"
    assert fast["agent_profile"] == "fast"


def test_shared_workflow_and_skill_are_persisted_and_indexed(tmp_path: Path):
    database = Database(tmp_path / "app.db")
    runtime = CapabilityRuntime(tmp_path)
    manager = CapabilityManager(database, runtime)
    owner = create_user(database, "owner@example.com")
    recipient = create_user(database, "recipient@example.com")

    workflow = manager.create_workflow_file(
        owner["id"],
        name="研究简报",
        description="检索一手资料并形成可验证研究简报",
        instructions="先拆解问题，再搜索一手资料，记录来源，最后核验每一项结论并输出简报。",
        triggers=["研究简报", "一手资料"],
        category_id=None,
        source_conversation_id=None,
    )
    workflow_code, _share = manager.create_share(owner["id"], "workflow", workflow["id"])
    imported_workflow = manager.import_share(recipient["id"], workflow_code)
    imported_path = runtime._ensure_user_dirs_sync(recipient["id"])["container_hermes"] / imported_workflow["item"]["relative_path"]
    assert imported_path.is_file()
    assert "先拆解问题" in manager.capability_context(recipient["id"], "请做一份研究简报")

    owner_skills = runtime._ensure_user_dirs_sync(owner["id"])["container_skills"]
    skill_root = owner_skills / "proof-skill"
    skill_root.mkdir()
    (skill_root / "SKILL.md").write_text(
        "---\nname: proof-skill\ndescription: Validate a real shared skill installation with durable files.\n---\n\n# Proof\n",
        encoding="utf-8",
    )
    owner_records = manager.sync_skill_records(owner["id"])
    skill = next(item for item in owner_records if item["name"] == "proof-skill")
    skill_code, _share = manager.create_share(owner["id"], "skill", skill["id"])
    imported_skill = manager.import_share(recipient["id"], skill_code)
    imported_skill_path = runtime._ensure_user_dirs_sync(recipient["id"])["container_skills"] / imported_skill["item"]["relative_path"] / "SKILL.md"
    assert imported_skill_path.is_file()
    assert imported_skill["item"]["status"] == "validated"
    assert "proof-skill" in manager.capability_context(recipient["id"], "use proof-skill")


@pytest.mark.asyncio
async def test_failed_quality_review_starts_revision_and_passes(tmp_path: Path):
    database = Database(tmp_path / "app.db")
    runtime = DispatcherRuntime(tmp_path)
    hermes = RevisionHermes()
    reviewer = SequenceReviewer()
    dispatcher = TaskDispatcher(database, runtime, hermes, quality_reviewer=reviewer)
    user = create_user(database, "quality@example.com")
    conversation = database.create_conversation(
        user["id"], "Quality", "agent", agent_profile="expert"
    )
    task = database.create_task(
        user_id=user["id"], conversation_id=conversation["id"], prompt="Create a polished result", attachment_ids=[]
    )
    database.update_task(task["id"], status="running", worker_name="worker-test")

    await dispatcher._handle_completed_task(task["id"], "Initial result")

    completed = database.get_task(task["id"])
    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["quality_status"] == "passed"
    assert completed["quality_score"] == 91
    assert completed["quality_attempt"] == 2
    assert reviewer.calls == 2
    assert hermes.starts == 1
    assert hermes.requests[0]["agent_profile"] == "fast"
    assert hermes.requests[0]["conversation_history"] == []
    assert "CURRENT TASK WORKSPACE CHECKPOINT" in hermes.requests[0]["revision_feedback"]
    assert "Page 1 hierarchy is weak" in hermes.requests[0]["revision_feedback"]
    assert '"score"' not in hermes.requests[0]["revision_feedback"]
    assert "pageReviews" not in hermes.requests[0]["revision_feedback"]
    report = json.loads(completed["quality_report_json"])
    assert report["passed"] is True


@pytest.mark.asyncio
async def test_empty_model_reply_continues_same_attempt_from_workspace_checkpoint(tmp_path: Path):
    database = Database(tmp_path / "app.db")
    runtime = DispatcherRuntime(tmp_path)
    hermes = EmptyRecoveryHermes()
    dispatcher = TaskDispatcher(database, runtime, hermes, quality_reviewer=PassingReviewer())
    user = create_user(database, "empty-recovery@example.com")
    conversation = database.create_conversation(user["id"], "Recovery", "agent", agent_profile="expert")
    workspace = runtime.user_paths(user["id"])["container_workspace"]
    (workspace / "research.json").write_text('{"ready": true}', encoding="utf-8")
    task = database.create_task(
        user_id=user["id"], conversation_id=conversation["id"], prompt="生成 PPT", attachment_ids=[]
    )
    database.update_task(task["id"], status="running", worker_name="worker-test")

    await dispatcher._handle_completed_task(task["id"], "(empty)")

    completed = database.get_task(task["id"])
    assert completed and completed["status"] == "completed"
    assert completed["quality_status"] == "passed"
    assert completed["quality_attempt"] == 1
    assert hermes.starts == 1
    assert hermes.requests[0]["agent_profile"] == "fast"
    assert hermes.requests[0]["conversation_history"] == []
    assert "research.json" in hermes.requests[0]["recovery_feedback"]
    assert "strict file allowlist" in hermes.requests[0]["recovery_feedback"]
    assert "Do not list, search, read, execute, or reuse any unlisted" in hermes.requests[0]["recovery_feedback"]
    assert database.all(
        "SELECT id FROM task_events WHERE task_id = ? AND event_type = 'run.empty_recovery_started'",
        (task["id"],),
    )


@pytest.mark.asyncio
async def test_transient_upstream_failure_resumes_same_task_from_checkpoint(tmp_path: Path):
    database = Database(tmp_path / "app.db")
    runtime = DispatcherRuntime(tmp_path)
    hermes = TransientRecoveryHermes()
    dispatcher = TaskDispatcher(database, runtime, hermes, quality_reviewer=PassingReviewer())
    user = create_user(database, "transient-recovery@example.com")
    conversation = database.create_conversation(user["id"], "Recovery", "agent", agent_profile="expert")
    task = database.create_task(
        user_id=user["id"], conversation_id=conversation["id"], prompt="生成研究 DOCX", attachment_ids=[]
    )
    database.update_task(task["id"], status="running", worker_name="worker-test")
    workspace = runtime.user_paths(user["id"])["container_workspace"]
    (workspace / "research-sources.md").write_text("source ledger", encoding="utf-8")

    await dispatcher._monitor_events(task["id"], user["id"], "worker-test", "initial-failed-run")

    completed = database.get_task(task["id"])
    assert completed and completed["status"] == "completed"
    assert completed["quality_status"] == "passed"
    assert hermes.starts == 1
    assert "TRANSIENT UPSTREAM RECOVERY" in hermes.requests[0]["recovery_feedback"]
    assert "research-sources.md" in hermes.requests[0]["recovery_feedback"]
    assert database.all(
        "SELECT id FROM task_events WHERE task_id = ? AND event_type = 'run.transient_recovery_started'",
        (task["id"],),
    )


@pytest.mark.asyncio
async def test_missing_office_urls_trigger_same_attempt_source_embedding_recovery(tmp_path: Path):
    database = Database(tmp_path / "app.db")
    runtime = DispatcherRuntime(tmp_path)
    user = create_user(database, "source-embedding@example.com")
    conversation = database.create_conversation(user["id"], "Research", "agent", agent_profile="expert")
    workspace = runtime.user_paths(user["id"])["container_workspace"]
    with zipfile.ZipFile(workspace / "report.pptx", "w") as archive:
        archive.writestr("ppt/slides/slide1.xml", "<p:sld xmlns:p='urn:p'><p:cSld/></p:sld>")
    (workspace / "research-sources.md").write_text(
        "https://openai.com/index/specific-release/\n"
        "https://www.anthropic.com/news/specific-release",
        encoding="utf-8",
    )
    (workspace / "generate.py").write_text("# presentation generator", encoding="utf-8")
    hermes = SourceEmbeddingHermes(workspace)
    dispatcher = TaskDispatcher(database, runtime, hermes, quality_reviewer=PassingReviewer())
    task = database.create_task(
        user_id=user["id"], conversation_id=conversation["id"], prompt="调研热点并生成 PPT", attachment_ids=[]
    )
    database.update_task(
        task["id"],
        status="running",
        worker_name="worker-test",
        coordination_plan_json=json.dumps({"capabilityRoute": ["tool:browser-CDP", "skill:pptx"]}),
    )
    for run_id, preview in [
        ("google", "Open https://www.google.com"),
        ("source-1", "Open https://openai.com/index/specific-release/"),
        ("source-2", "Open https://www.anthropic.com/news/specific-release"),
    ]:
        database.add_task_event(task["id"], "tool.started", {
            "tool": "browser_exec", "run_id": run_id, "preview": preview,
        })
        database.add_task_event(task["id"], "tool.completed", {
            "tool": "browser_exec", "run_id": run_id, "error": False,
        })

    await dispatcher._handle_completed_task(
        task["id"], "初稿\n[[artifact:/workspace/report.pptx]]"
    )

    completed = database.get_task(task["id"])
    assert completed and completed["quality_status"] == "passed"
    assert completed["quality_attempt"] == 1
    assert hermes.starts == 1
    feedback = hermes.requests[0]["revision_feedback"]
    assert feedback.startswith("SOURCE EMBEDDING RECOVERY.")
    assert "generate.py" in feedback
    assert "https://openai.com/index/specific-release/" in feedback
    assert "https://www.anthropic.com/news/specific-release" in feedback
    assert database.all(
        "SELECT id FROM task_events WHERE task_id = ? "
        "AND event_type = 'run.source_embedding_recovery_started'",
        (task["id"],),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("filename", ["report.pptx", "report.docx", "report.xlsx", "report.pdf"])
async def test_unchanged_office_revision_gets_same_attempt_targeted_recovery(tmp_path: Path, filename: str):
    database = Database(tmp_path / "app.db")
    runtime = DispatcherRuntime(tmp_path)
    user = create_user(database, "unchanged@example.com")
    conversation = database.create_conversation(user["id"], "Quality", "agent", agent_profile="expert")
    workspace = runtime.user_paths(user["id"])["container_workspace"]
    (workspace / filename).write_bytes(b"rejected-office-version")
    hermes = UnchangedOfficeHermes(workspace, filename)
    reviewer = SequenceReviewer()
    dispatcher = TaskDispatcher(database, runtime, hermes, quality_reviewer=reviewer)
    task = database.create_task(
        user_id=user["id"], conversation_id=conversation["id"], prompt="生成 PPT", attachment_ids=[]
    )
    database.update_task(task["id"], status="running", worker_name="worker-test")

    await dispatcher._handle_completed_task(
        task["id"], f"初稿\n[[artifact:/workspace/{filename}]]"
    )

    completed = database.get_task(task["id"])
    assert completed and completed["quality_status"] == "passed"
    assert completed["quality_attempt"] == 2
    assert reviewer.calls == 2
    assert hermes.starts == 2
    assert hermes.requests[1]["agent_profile"] == "fast"
    assert hermes.requests[1]["conversation_history"] == []
    assert "Office deliverable bytes did not change" in hermes.requests[1]["revision_feedback"]
    assert "CURRENT TASK WORKSPACE CHECKPOINT (strict file allowlist)" in hermes.requests[1]["revision_feedback"]
    assert database.all(
        "SELECT id FROM task_events WHERE task_id = ? "
        "AND event_type = 'run.unchanged_artifact_recovery_started'",
        (task["id"],),
    )


@pytest.mark.asyncio
async def test_quality_prompt_uses_server_time_as_authoritative_context(tmp_path: Path):
    class Gateway:
        def __init__(self):
            self.messages = []

        async def complete(self, role, messages, *, max_tokens):
            self.messages.append(messages)
            return json.dumps({
                "score": 95,
                "summary": "结果完整且时间准确。",
                "issues": [],
                "redo": False,
            }, ensure_ascii=False), {}, "reviewer-model"

    gateway = Gateway()
    reviewer = QualityReviewer(get_settings(), gateway)
    result = await reviewer.review(
        request="总结今天的热点",
        output="完成",
        workspace=tmp_path,
        artifacts=[],
        plan={},
    )

    assert result["passed"] is True
    prompt = gateway.messages[0][0]["content"][0]["text"]
    assert "验收运行时的权威当前时间" in prompt
    assert "不得根据模型训练时间" in prompt


@pytest.mark.asyncio
async def test_quality_exhaustion_delivers_highest_scoring_snapshot(tmp_path: Path):
    database = Database(tmp_path / "app.db")
    runtime = DispatcherRuntime(tmp_path)
    runtime.settings.expert_max_revisions = 2
    user = create_user(database, "highest@example.com")
    conversation = database.create_conversation(user["id"], "Quality", "agent", agent_profile="expert")
    workspace = runtime.user_paths(user["id"])["container_workspace"]
    (workspace / "result.txt").write_text("version-1", encoding="utf-8")
    hermes = ArtifactRevisionHermes(workspace)
    reviewer = AlwaysRedoReviewer([64, 88, 73])
    dispatcher = TaskDispatcher(database, runtime, hermes, quality_reviewer=reviewer)
    task = database.create_task(
        user_id=user["id"], conversation_id=conversation["id"], prompt="生成可下载文件", attachment_ids=[]
    )
    database.update_task(task["id"], status="running", worker_name="worker-test")

    await dispatcher._handle_completed_task(
        task["id"], "第 1 个版本\n[[artifact:/workspace/result.txt]]"
    )

    completed = database.get_task(task["id"])
    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["quality_status"] == "exhausted"
    assert completed["quality_score"] == 88
    assert completed["quality_selected_attempt"] == 2
    assert completed["output"] == "第 2 个版本"
    assert completed["error"] == ""
    attempts = database.list_quality_attempts(task["id"])
    assert [row["score"] for row in attempts] == [64, 88, 73]
    assert len(attempts) == 3
    assert all(json.loads(row["artifacts_json"]) for row in attempts)
    selected_artifacts = database.list_task_artifacts(task["id"])
    assert len(selected_artifacts) == 1
    assert "attempt-2" in selected_artifacts[0]["relative_path"]
    for index in range(1, 4):
        assert (workspace / ".quality-history" / task["id"] / f"attempt-{index}" / "result.txt").is_file()


@pytest.mark.asyncio
async def test_quality_reviewer_hard_rejects_missing_planned_pptx_without_calling_llm(tmp_path: Path):
    class Gateway:
        async def complete(self, *_args, **_kwargs):
            raise AssertionError("deterministic contract failure must not depend on an LLM score")

    reviewer = QualityReviewer(get_settings(), Gateway())
    result = await reviewer.review(
        request="制作一份 PPT 和一份 doc 文档",
        output="文件已完成",
        workspace=tmp_path,
        artifacts=[{"filename": "讲稿.docx", "relative_path": "讲稿.docx"}],
        plan={
            "requiredArtifacts": [
                {"extension": ".pptx", "minimumCount": 1},
                {"extension": ".docx", "minimumCount": 1},
            ]
        },
    )

    assert result["passed"] is False
    assert result["score"] <= 59
    assert any(".pptx" in issue and "不能替代" in issue for issue in result["issues"])


def test_quality_exhaustion_fails_instead_of_delivering_wrong_file_types(tmp_path: Path):
    database = Database(tmp_path / "app.db")
    runtime = DispatcherRuntime(tmp_path)
    dispatcher = TaskDispatcher(database, runtime, RevisionHermes(), quality_reviewer=PassingReviewer())
    user = create_user(database, "contract-failure@example.com")
    conversation = database.create_conversation(user["id"], "Contract", "agent", agent_profile="expert")
    task = database.create_task(
        user_id=user["id"], conversation_id=conversation["id"], prompt="制作 PPT 和 doc", attachment_ids=[]
    )
    database.update_task(task["id"], coordination_plan_json=json.dumps({
        "requiredArtifacts": [
            {"extension": ".pptx", "minimumCount": 1},
            {"extension": ".docx", "minimumCount": 1},
        ]
    }))
    database.record_quality_attempt(
        task["id"],
        attempt=3,
        score=88,
        passed=False,
        output="只有两个文档",
        report={"score": 88, "passed": False, "issues": []},
        artifacts=[{"filename": "甲.docx"}, {"filename": "乙.docx"}],
    )

    assert dispatcher._finish_best_quality_attempt(task["id"]) is True
    completed = database.get_task(task["id"])
    assert completed and completed["status"] == "failed"
    assert ".pptx" in completed["error"]
    assert database.all(
        "SELECT id FROM task_events WHERE task_id = ? AND event_type = 'quality.contract_failed'",
        (task["id"],),
    )


@pytest.mark.asyncio
async def test_dispatcher_injects_only_llm_selected_cross_conversation_memory(tmp_path: Path):
    class MemoryCoordinator:
        async def select_memories(self, *, request, candidates):
            assert request == "继续沿用我的中文文档偏好"
            selected = next(item["id"] for item in candidates if "中文" in item["user"])
            return {"selectedIds": [selected], "reason": "明确延续语言偏好", "model": "gate-model"}

    database = Database(tmp_path / "app.db")
    runtime = DispatcherRuntime(tmp_path)
    dispatcher = TaskDispatcher(database, runtime, RevisionHermes(), coordinator=MemoryCoordinator())
    user = create_user(database, "memory-gate@example.com")
    old = database.create_conversation(user["id"], "旧任务", "agent")
    current = database.create_conversation(user["id"], "新任务", "agent")
    database.add_conversation_memory(
        conversation_id=old["id"], user_id=user["id"], source="chat", source_id="old-1",
        user_content="B 站热点研究", assistant_content="一份与当前无关的旧热点",
    )
    database.add_conversation_memory(
        conversation_id=old["id"], user_id=user["id"], source="chat", source_id="old-2",
        user_content="以后文档全部使用中文", assistant_content="已记录语言偏好",
    )
    task = database.create_task(
        user_id=user["id"], conversation_id=current["id"], prompt="继续沿用我的中文文档偏好", attachment_ids=[]
    )

    history = await dispatcher._conversation_history_with_memory(task)

    assert history[0]["role"] == "system"
    assert "已记录语言偏好" in history[0]["content"]
    assert "旧热点" not in history[0]["content"]
    event = database.one(
        "SELECT payload_json FROM task_events WHERE task_id = ? AND event_type = 'memory.selected'",
        (task["id"],),
    )
    assert event and json.loads(event["payload_json"])["selectedCount"] == 1
