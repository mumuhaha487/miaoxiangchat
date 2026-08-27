from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import mimetypes
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter

from .database import Database, now_ms
from .context_manager import context_messages, fallback_summary, plan_compression
from .hermes_client import HermesClient, HermesError
from .quality_reviewer import QualityReviewer, required_artifact_issues
from .runtime_manager import RuntimeManager, WorkerPoolBusy


TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
ACTIVE_STATUSES = {"starting", "running", "waiting_approval", "stopping"}
ARTIFACT_MARKER = re.compile(r"\[\[(?:artifact|file):(/workspace/[^\]\r\n]+)\]\]")
LOCAL_IMAGE_MARKDOWN = re.compile(
    r"!\[[^\]\r\n]*\]\(\s*<?(?:(?:file://)|(?:sandbox:))?(/workspace/[^)\r\n>]+)>?\s*\)",
    re.IGNORECASE,
)
BARE_LOCAL_IMAGE_PATH = re.compile(
    r"(?<![:\w])`?(/workspace/[^\r\n`<>\[\]()\"']+?\.(?:avif|bmp|gif|jpe?g|png|webp))`?"
    r"(?=$|[\s,，。；;!?！？])",
    re.IGNORECASE,
)
MAX_TASK_ARTIFACTS = 150
MAX_TASK_ARTIFACT_BYTES = 100 * 1024 * 1024
RECOVERABLE_OFFICE_SUFFIXES = {".doc", ".docx", ".pdf", ".ppt", ".pptx", ".xls", ".xlsx"}
RECOVERABLE_PREVIEW_SUFFIXES = {".avif", ".jpg", ".jpeg", ".png", ".webp"}
EMPTY_RUN_MARKERS = (
    "(empty)",
    "no reply: the model returned empty content",
    "模型没有返回文本内容",
)
BROWSER_HTTP_URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
TASK_CHECKPOINT_SUFFIXES = {
    ".csv", ".docx", ".js", ".json", ".md", ".mjs", ".pdf", ".png", ".pptx", ".py", ".ts",
    ".txt", ".xlsx",
}
MAX_TRANSIENT_RUN_RECOVERIES = 3


def transient_run_error(error: str) -> bool:
    normalized = str(error or "").casefold()
    fatal_markers = (
        "模型身份不一致",
        "未报告实际模型",
        "model identity",
        "model substitution",
        "invalid api key",
        "unauthorized",
        "forbidden",
    )
    if any(marker in normalized for marker in fatal_markers):
        return False
    transient_markers = (
        "http 429",
        "http 500",
        "http 502",
        "http 503",
        "http 504",
        "上游模型连接失败",
        "模型服务暂时不可用",
        "temporarily unavailable",
        "service unavailable",
        "connection reset",
        "connection refused",
        "connection timeout",
        "read timeout",
        "timed out",
    )
    return any(marker in normalized for marker in transient_markers)


def quality_revision_requirements(report: dict[str, Any] | str | None) -> str:
    if isinstance(report, str):
        try:
            parsed = json.loads(report or "{}")
        except json.JSONDecodeError:
            parsed = {}
    else:
        parsed = report if isinstance(report, dict) else {}
    raw_issues = parsed.get("issues") if isinstance(parsed.get("issues"), list) else []
    issues: list[str] = []
    for value in raw_issues:
        issue = str(value or "").strip()
        if issue and issue not in issues:
            issues.append(issue)
    if not issues:
        issues.append("重新打开实际交付物，修复上一轮未通过的问题，并完成结构与逐页验证。")
    lines = ["REQUIRED FIXES FOR THE REJECTED DELIVERABLE:"]
    lines.extend(f"{index}. {issue}" for index, issue in enumerate(issues, start=1))
    if any("来源" in issue or "URL" in issue or "证据页" in issue for issue in issues):
        lines.extend([
            "SOURCE FIX PROCEDURE:",
            "- Open the exact article, announcement, release-note, paper, or dataset pages that support the claims.",
            "- Replace generic home, news, blog, category, profile, and search-result links in the source ledger and generator.",
            "- Copy the complete final URL and the publication date shown on each source page into the deliverable.",
            "- Regenerate the Office file and confirm it contains at least two different claim-level source URLs.",
        ])
    if any("日期" in issue or "时间窗口" in issue or "最近一周" in issue for issue in issues):
        lines.extend([
            "DATE-WINDOW FIX PROCEDURE:",
            "- Keep only updates whose source-page publication/effective date falls inside the requested date window.",
            "- Remove out-of-window items from the current-update list or label them only as historical background.",
            "- Copy dates from the exact supporting page; never infer a publication date from a search snippet or report period.",
        ])
    return "\n".join(lines)


def task_workspace_checkpoint(workspace: Path, task: dict[str, Any]) -> tuple[list[Path], str]:
    files = sorted(
        (
            path for path in workspace.rglob("*")
            if path.is_file()
            and path.suffix.casefold() in TASK_CHECKPOINT_SUFFIXES
            and not any(part.startswith(".") for part in path.relative_to(workspace).parts)
            and path.stat().st_mtime * 1000 >= int(
                task.get("started_at") or task.get("created_at") or 0
            ) - 2_000
            and path.stat().st_size <= MAX_TASK_ARTIFACT_BYTES
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:40]
    checkpoint = "\n".join(
        f"- /workspace/{path.relative_to(workspace).as_posix()} ({path.stat().st_size} bytes)"
        for path in files
    )
    return files, checkpoint


def checkpoint_http_urls(files: list[Path]) -> list[str]:
    urls: list[str] = []
    for path in files:
        if path.suffix.casefold() not in {".json", ".md", ".py", ".txt"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for value in BROWSER_HTTP_URL.findall(text):
            normalized = value.rstrip(".,;:!?)]}，。；：！？）】")
            if normalized and normalized not in urls:
                urls.append(normalized)
    return urls[:20]


def research_office_source_gap(
    workspace: Path,
    artifacts: list[dict[str, Any]],
    capability_route: Any,
) -> tuple[list[str], list[str]]:
    if not isinstance(capability_route, list) or "tool:browser-CDP" not in capability_route:
        return [], []
    if not any(
        Path(str(item.get("filename") or "")).suffix.casefold() in {".docx", ".pptx", ".xlsx"}
        for item in artifacts
    ):
        return [], []
    urls = QualityReviewer._office_urls(workspace, artifacts)
    specific = QualityReviewer._specific_source_urls(urls)
    return urls, specific


def browser_cdp_evidence(database: Database, task_id: str) -> list[dict[str, str]]:
    rows = database.all(
        "SELECT event_type, payload_json FROM task_events WHERE task_id = ? "
        "AND event_type IN ('tool.started', 'tool.completed') ORDER BY id ASC",
        (task_id,),
    )
    evidence: list[dict[str, str]] = []
    pending: dict[str, list[str]] = {}
    for row in rows:
        try:
            payload = json.loads(str(row.get("payload_json") or "{}"))
        except json.JSONDecodeError:
            continue
        if str(payload.get("tool") or "").casefold() != "browser_exec":
            continue
        run_id = str(payload.get("run_id") or "unknown")
        if str(row.get("event_type") or "") == "tool.started":
            pending.setdefault(run_id, []).append(str(payload.get("preview") or "")[:500])
            continue
        previews = pending.get(run_id) or []
        preview = previews.pop(0) if previews else ""
        if previews:
            pending[run_id] = previews
        else:
            pending.pop(run_id, None)
        if not bool(payload.get("error")):
            evidence.append({"tool": "browser_exec", "preview": preview})
    return evidence


def browser_cdp_evidence_issue(evidence: list[dict[str, str]]) -> str:
    previews = [str(item.get("preview") or "").strip() for item in evidence]
    google_seen = any("google" in preview.casefold() for preview in previews)
    diagnostic_markers = (
        "about:blank", "connection", "fresh session", "fresh tab", "responsiveness",
        "status", "testing", "checking browser", "checking cdp",
    )
    source_pages: set[str] = set()
    for preview in previews:
        normalized = preview.casefold()
        if not preview or "google" in normalized or any(marker in normalized for marker in diagnostic_markers):
            continue
        source_pages.update(
            value.rstrip(".,;:!?)]}，。；：！？）】").casefold()
            for value in BROWSER_HTTP_URL.findall(preview)
        )
    failures: list[str] = []
    if len(evidence) < 3:
        failures.append(f"browser_exec 总数至少 3 次，实际 {len(evidence)} 次")
    if not google_seen:
        failures.append("没有 browser_exec 打开 Google 的证据")
    if len(source_pages) < 2:
        failures.append(
            f"带完整 URL 的不同非 Google 来源页至少 2 个，实际 {len(source_pages)} 个"
        )
    return "；".join(failures)


def canonical_office_output(output: str, artifacts: list[dict[str, Any]]) -> str:
    if any(
        Path(str(item.get("filename") or "")).suffix.casefold() in RECOVERABLE_OFFICE_SUFFIXES
        for item in artifacts
    ):
        return "文件已生成并完成验证，请在下方下载。"
    return output


def submission_only_quality_issues(report: dict[str, Any]) -> bool:
    issues = report.get("issues") if isinstance(report.get("issues"), list) else []
    normalized = [str(value or "").casefold() for value in issues if str(value or "").strip()]
    metadata_markers = (
        "quality-submission",
        "质检清单",
        "逐页预览",
        "预览编号",
        "必要检查",
    )
    return bool(normalized) and all(any(marker in issue for marker in metadata_markers) for issue in normalized)


def _verified_deliverables(workspace: Path, artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    root = workspace.resolve()
    submission_paths = [
        root / str(item.get("relative_path") or "")
        for item in artifacts
        if str(item.get("filename") or "").casefold() == "quality-submission.json"
    ]
    for submission_path in submission_paths:
        try:
            submission = json.loads(submission_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        requested = submission.get("deliverables") if isinstance(submission, dict) else None
        if not isinstance(requested, list):
            continue
        deliverables: list[dict[str, Any]] = []
        seen: set[str] = set()
        for value in requested:
            raw = str(value or "").strip()
            if not raw.startswith("/workspace/"):
                continue
            candidate = (root / raw.removeprefix("/workspace/")).resolve()
            if root not in candidate.parents or not candidate.is_file():
                continue
            relative = candidate.relative_to(root).as_posix()
            size = candidate.stat().st_size
            if relative in seen or size <= 0 or size > MAX_TASK_ARTIFACT_BYTES:
                continue
            seen.add(relative)
            deliverables.append({
                "relative_path": relative,
                "filename": candidate.name,
                "mime_type": mimetypes.guess_type(candidate.name)[0] or "application/octet-stream",
                "size_bytes": size,
            })
        if deliverables:
            return deliverables
    return [
        item for item in artifacts
        if str(item.get("filename") or "").casefold() != "quality-submission.json"
    ]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def unchanged_office_artifacts(
    workspace: Path,
    current_artifacts: list[dict[str, Any]],
    previous_artifacts: list[dict[str, Any]],
) -> list[dict[str, str]]:
    root = workspace.resolve()
    previous_by_name: dict[str, Path] = {}
    for item in previous_artifacts:
        filename = str(item.get("filename") or "")
        if Path(filename).suffix.casefold() not in RECOVERABLE_OFFICE_SUFFIXES:
            continue
        candidate = (root / str(item.get("relative_path") or "")).resolve()
        if root in candidate.parents and candidate.is_file():
            previous_by_name[filename.casefold()] = candidate
    unchanged: list[dict[str, str]] = []
    for item in current_artifacts:
        filename = str(item.get("filename") or "")
        if Path(filename).suffix.casefold() not in RECOVERABLE_OFFICE_SUFFIXES:
            continue
        current = (root / str(item.get("relative_path") or "")).resolve()
        previous = previous_by_name.get(filename.casefold())
        if root not in current.parents or not current.is_file() or not previous:
            continue
        current_hash = _sha256(current)
        if current_hash == _sha256(previous):
            unchanged.append({
                "filename": filename,
                "path": f"/workspace/{current.relative_to(root).as_posix()}",
                "sha256": current_hash,
            })
    return unchanged


def snapshot_quality_artifacts(
    workspace: Path,
    task_id: str,
    attempt: int,
    artifacts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    root = workspace.resolve()
    target_root = root / ".quality-history" / task_id / f"attempt-{attempt}"
    target_root.mkdir(parents=True, exist_ok=True)
    recorded: list[dict[str, Any]] = []
    used_names: set[str] = set()
    for artifact in artifacts:
        source = (root / str(artifact.get("relative_path") or "")).resolve()
        if root not in source.parents or not source.is_file():
            continue
        filename = source.name
        stem, suffix = source.stem, source.suffix
        candidate_name = filename
        index = 2
        while candidate_name.casefold() in used_names:
            candidate_name = f"{stem}-{index}{suffix}"
            index += 1
        used_names.add(candidate_name.casefold())
        target = target_root / candidate_name
        shutil.copy2(source, target)
        size = target.stat().st_size
        if size <= 0:
            target.unlink(missing_ok=True)
            continue
        recorded.append({
            "relative_path": target.relative_to(root).as_posix(),
            "filename": target.name,
            "mime_type": str(artifact.get("mime_type") or mimetypes.guess_type(target.name)[0] or "application/octet-stream"),
            "size_bytes": size,
        })
    return recorded


def collect_task_artifacts(
    workspace: Path,
    output: str,
    *,
    modified_after_ms: int = 0,
) -> tuple[str, list[dict[str, Any]], list[str]]:
    root = workspace.resolve()
    artifacts: list[dict[str, Any]] = []
    rejected: list[str] = []
    seen: set[str] = set()
    references = [match.group(1) for match in ARTIFACT_MARKER.finditer(output)]
    references.extend(match.group(1) for match in LOCAL_IMAGE_MARKDOWN.finditer(output))
    references.extend(match.group(1) for match in BARE_LOCAL_IMAGE_PATH.finditer(output))
    def recent_enough(path: Path) -> bool:
        return not modified_after_ms or path.stat().st_mtime * 1000 >= modified_after_ms - 2_000

    def add_reference(reference: str, *, report_missing: bool = True) -> None:
        raw = reference.strip()
        relative = raw.removeprefix("/workspace/")
        candidate = (root / relative).resolve()
        if relative in seen:
            return
        seen.add(relative)
        if len(artifacts) >= MAX_TASK_ARTIFACTS:
            if report_missing:
                rejected.append(raw)
            return
        if candidate == root or root not in candidate.parents or not candidate.is_file():
            if report_missing:
                rejected.append(raw)
            return
        size = candidate.stat().st_size
        if size <= 0 or size > MAX_TASK_ARTIFACT_BYTES or not recent_enough(candidate):
            if report_missing:
                rejected.append(raw)
            return
        artifacts.append({
            "relative_path": candidate.relative_to(root).as_posix(),
            "filename": candidate.name,
            "mime_type": mimetypes.guess_type(candidate.name)[0] or "application/octet-stream",
            "size_bytes": size,
        })

    for reference in references:
        add_reference(reference)

    submission = root / "quality-submission.json"
    if submission.is_file() and recent_enough(submission):
        add_reference("/workspace/quality-submission.json", report_missing=False)
        try:
            submission_data = json.loads(submission.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            submission_data = {}
        if isinstance(submission_data, dict):
            for value in submission_data.get("deliverables") or []:
                add_reference(str(value or ""), report_missing=False)
            for item in submission_data.get("previews") or []:
                if isinstance(item, dict):
                    add_reference(str(item.get("path") or ""), report_missing=False)

    if not any(Path(str(item.get("filename") or "")).suffix.casefold() in RECOVERABLE_OFFICE_SUFFIXES for item in artifacts):
        office_candidates = sorted(
            (
                path for path in root.rglob("*")
                if path.is_file()
                and Path(path.name).suffix.casefold() in RECOVERABLE_OFFICE_SUFFIXES
                and ".quality-history" not in path.parts
                and recent_enough(path)
            ),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for candidate in office_candidates[:20]:
            add_reference("/workspace/" + candidate.relative_to(root).as_posix(), report_missing=False)
        if office_candidates:
            preview_candidates = sorted(
                (
                    path for path in root.rglob("*")
                    if path.is_file()
                    and Path(path.name).suffix.casefold() in RECOVERABLE_PREVIEW_SUFFIXES
                    and ".quality-history" not in path.parts
                    and recent_enough(path)
                    and any(marker in path.as_posix().casefold() for marker in ("preview", "render", "slide"))
                ),
                key=lambda path: path.as_posix(),
            )
            for candidate in preview_candidates[:100]:
                add_reference("/workspace/" + candidate.relative_to(root).as_posix(), report_missing=False)
    cleaned = ARTIFACT_MARKER.sub("", output)
    cleaned = LOCAL_IMAGE_MARKDOWN.sub("", cleaned)
    cleaned = BARE_LOCAL_IMAGE_PATH.sub("", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    if artifacts and not cleaned:
        cleaned = "内容已生成并完成验证，请在下方查看。"
    return cleaned, artifacts, rejected


def _empty_run_output(output: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(output or "")).strip().casefold()
    return not normalized or any(marker in normalized for marker in EMPTY_RUN_MARKERS)


def next_schedule_ms(expression: str, timezone: str, base_ms: int | None = None) -> int:
    try:
        tz = ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("无效时区") from exc
    base = datetime.fromtimestamp((base_ms or now_ms()) / 1000, tz=tz)
    if not croniter.is_valid(expression):
        raise ValueError("无效 Cron 表达式")
    return int(croniter(expression, base).get_next(datetime).timestamp() * 1000)


class TaskDispatcher:
    def __init__(
        self,
        database: Database,
        runtimes: RuntimeManager,
        hermes: HermesClient,
        *,
        capability_manager: Any | None = None,
        coordinator: Any | None = None,
        quality_reviewer: Any | None = None,
    ):
        self.database = database
        self.runtimes = runtimes
        self.hermes = hermes
        self.capability_manager = capability_manager
        self.coordinator = coordinator
        self.quality_reviewer = quality_reviewer
        self._loop_task: asyncio.Task[Any] | None = None
        self._active: dict[str, asyncio.Task[Any]] = {}
        self._closing = False

    async def start(self) -> None:
        self._closing = False
        await self._recover_tasks()
        self._loop_task = asyncio.create_task(self._loop(), name="mumu-task-dispatcher")

    async def close(self) -> None:
        self._closing = True
        if self._loop_task:
            self._loop_task.cancel()
        tasks = [task for task in [self._loop_task, *self._active.values()] if task]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def wake(self) -> None:
        # The dispatcher polls every second; this method is an explicit API hook.
        return None

    def busy_user_ids(self) -> set[str]:
        if not self._active:
            return set()
        placeholders = ",".join("?" for _ in self._active)
        rows = self.database.all(
            f"SELECT DISTINCT user_id FROM tasks WHERE id IN ({placeholders})",
            tuple(self._active),
        )
        return {str(row["user_id"]) for row in rows}

    async def _recover_tasks(self) -> None:
        rows = self.database.all(
            "SELECT * FROM tasks WHERE status IN ('starting','running','waiting_approval','stopping') ORDER BY created_at"
        )
        for row in rows:
            task_id = str(row["id"])
            if row.get("hermes_run_id") and row.get("worker_name"):
                monitor = asyncio.create_task(self._resume_task(row), name=f"resume-{task_id}")
                self._track(task_id, monitor)
            else:
                self.database.update_task(task_id, status="queued", started_at=None)
                self.database.add_task_event(task_id, "task.recovered", {"message": "服务重启后重新排队"})

    def _track(self, task_id: str, task: asyncio.Task[Any]) -> None:
        self._active[task_id] = task

        def done(_task: asyncio.Task[Any]) -> None:
            self._active.pop(task_id, None)

        task.add_done_callback(done)

    async def _loop(self) -> None:
        cleanup_at = 0.0
        while True:
            try:
                await self._dispatch_queued()
                if time.monotonic() >= cleanup_at:
                    users = self.database.all("SELECT id FROM users WHERE status = 'active'")
                    await self.runtimes.cleanup_idle(
                        [str(row["id"]) for row in users],
                        self.busy_user_ids(),
                    )
                    cleanup_at = time.monotonic() + 60
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[dispatcher] loop error: {exc}", flush=True)
            await asyncio.sleep(1)

    async def _dispatch_schedules(self) -> None:
        due = self.database.all(
            "SELECT * FROM schedules WHERE status = 'active' AND next_run_at IS NOT NULL AND next_run_at <= ? ORDER BY next_run_at LIMIT 20",
            (now_ms(),),
        )
        for schedule in due:
            user = self.database.get_user_by_id(str(schedule["user_id"]))
            if not user or user.get("status") != "active":
                self.database.execute(
                    "UPDATE schedules SET status = 'paused', updated_at = ? WHERE id = ?",
                    (now_ms(), schedule["id"]),
                )
                continue
            self.database.create_task(
                user_id=str(schedule["user_id"]),
                conversation_id=str(schedule["conversation_id"]),
                prompt=str(schedule["prompt"]),
                attachment_ids=[],
                source="schedule",
                schedule_id=str(schedule["id"]),
            )
            next_run = next_schedule_ms(
                str(schedule["cron_expr"]),
                str(schedule["timezone"]),
                int(schedule["next_run_at"]),
            )
            self.database.execute(
                "UPDATE schedules SET last_run_at = ?, next_run_at = ?, updated_at = ? WHERE id = ?",
                (now_ms(), next_run, now_ms(), schedule["id"]),
            )

    async def _dispatch_queued(self) -> None:
        worker_limit = self.runtimes.current_worker_limit()
        if len(self._active) >= worker_limit:
            return
        active_users = self.busy_user_ids()
        rows = self.database.all("SELECT * FROM tasks WHERE status = 'queued' ORDER BY created_at LIMIT 20")
        for row in rows:
            if len(self._active) >= worker_limit:
                break
            user_id = str(row["user_id"])
            if user_id in active_users:
                continue
            claimed = self.database.claim_task(str(row["id"]))
            if not claimed:
                continue
            task_id = str(claimed["id"])
            runner = asyncio.create_task(self._execute_task(claimed), name=f"task-{task_id}")
            self._track(task_id, runner)
            active_users.add(user_id)

    async def _execute_task(self, task: dict[str, Any]) -> None:
        task_id = str(task["id"])
        user_id = str(task["user_id"])
        try:
            user_input = await asyncio.to_thread(self._build_user_input, task)
            history = await self._conversation_history_with_memory(task)
            capability_context = ""
            if self.capability_manager:
                capability_context = await asyncio.to_thread(
                    self.capability_manager.capability_context,
                    user_id,
                    str(task["prompt"]),
                )
            coordination_plan: dict[str, Any] = {}
            if self.coordinator:
                self.database.update_task(task_id, coordination_status="planning")
                self.database.add_task_event(task_id, "coordination.started", {
                    "message": "统筹模型正在制定执行计划与验收条件",
                })
                coordination_plan = await self.coordinator.plan(
                    request=user_input,
                    conversation_history=history,
                    capability_context=capability_context,
                )
                self.database.update_task(
                    task_id,
                    coordination_status="planned",
                    coordination_plan_json=json.dumps(coordination_plan, ensure_ascii=False),
                )
                self.database.add_task_event(task_id, "coordination.planned", {
                    "objective": coordination_plan.get("objective"),
                    "steps": coordination_plan.get("steps") or [],
                    "acceptanceCriteria": coordination_plan.get("acceptanceCriteria") or [],
                    "model": coordination_plan.get("model"),
                })
            worker = await self.runtimes.ensure_worker(user_id, self.busy_user_ids())
            self.database.add_task_event(task_id, "task.started", {"worker": worker})
            run = await self.hermes.start_run(
                worker_name=worker,
                user_id=user_id,
                session_id=f"{task['conversation_id']}:{task_id}:attempt-1",
                user_input=user_input,
                conversation_history=history,
                agent_profile=str(task.get("agent_profile") or "expert"),
                capability_context=capability_context,
                coordination_plan=(
                    self.coordinator.execution_brief(coordination_plan)
                    if self.coordinator and coordination_plan else ""
                ),
                attempt_number=1,
            )
            run_id = str(run["run_id"])
            self.database.update_task(task_id, status="running", hermes_run_id=run_id, worker_name=worker)
            await self._monitor_events(task_id, user_id, worker, run_id)
        except WorkerPoolBusy:
            self.database.update_task(task_id, status="queued", started_at=None)
            self.database.add_task_event(task_id, "task.queued", {"message": "Worker 池忙，等待空闲资源"})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            current = self.database.get_task(task_id)
            if current and str(current.get("coordination_status") or "") == "planning":
                self.database.update_task(task_id, coordination_status="failed")
                self.database.add_task_event(task_id, "coordination.failed", {"error": str(exc)[:1000]})
            self.database.add_task_event(task_id, "run.failed", {"error": str(exc)[:1000]})
            current = self.database.get_task(task_id)
            if current and str(current.get("quality_status") or "") in {"revision_required", "revising"}:
                self.database.add_task_event(task_id, "quality.revision_failed", {"error": str(exc)[:1000]})
                if self._finish_best_quality_attempt(task_id, reason="本次重做无法启动，已选择现有最高分版本交付"):
                    return
            self.database.finish_task(task_id, status="failed", error=str(exc)[:2000])
        finally:
            self.runtimes.mark_used(user_id)

    def _finish_completed_task(self, task_id: str, output: str) -> None:
        task = self.database.get_task(task_id)
        if not task:
            return
        workspace = self.runtimes.user_paths(str(task["user_id"]))["container_workspace"]
        cleaned, artifacts, rejected = collect_task_artifacts(
            workspace,
            output,
            modified_after_ms=int(task.get("started_at") or task.get("created_at") or 0),
        )
        cleaned = canonical_office_output(cleaned, artifacts)
        recorded = self.database.replace_task_artifacts(task_id, artifacts)
        if recorded:
            self.database.add_task_event(task_id, "artifacts.recorded", {
                "count": len(recorded),
                "files": [item["filename"] for item in recorded],
            })
        if rejected:
            self.database.add_task_event(task_id, "artifacts.rejected", {"count": len(rejected)})
        self.database.finish_task(task_id, status="completed", output=cleaned)

    def _finish_best_quality_attempt(self, task_id: str, *, reason: str = "") -> bool:
        best = self.database.best_quality_attempt(task_id)
        if not best:
            return False
        try:
            report = json.loads(str(best.get("report_json") or "{}"))
        except json.JSONDecodeError:
            report = {"summary": "质检记录读取失败", "issues": []}
        selected_attempt = int(best.get("attempt") or 1)
        try:
            best_artifacts = json.loads(str(best.get("artifacts_json") or "[]"))
        except json.JSONDecodeError:
            best_artifacts = []
        if not isinstance(best_artifacts, list):
            best_artifacts = []
        task = self.database.get_task(task_id) or {}
        try:
            plan = json.loads(str(task.get("coordination_plan_json") or "{}"))
        except json.JSONDecodeError:
            plan = {}
        contract_issues = required_artifact_issues(
            plan if isinstance(plan, dict) else {},
            [item for item in best_artifacts if isinstance(item, dict)],
        )
        if contract_issues:
            report["passed"] = False
            report["issues"] = list(dict.fromkeys([
                *(report.get("issues") if isinstance(report.get("issues"), list) else []),
                *contract_issues,
            ]))
            self.database.update_task(
                task_id,
                quality_status="exhausted",
                quality_score=min(59, max(1, int(best.get("score") or 1))),
                quality_selected_attempt=selected_attempt,
                quality_report_json=json.dumps(report, ensure_ascii=False),
            )
            self.database.add_task_event(task_id, "quality.contract_failed", {
                "attempt": selected_attempt,
                "issues": contract_issues,
                "message": "自动重做已达上限，但必需文件类型仍未交付，任务不会伪装成已完成",
            })
            self.database.finish_task(
                task_id,
                status="failed",
                output=str(best.get("output") or ""),
                error="；".join(contract_issues)[:2000],
            )
            return True
        self.database.replace_task_artifacts(
            task_id,
            [item for item in best_artifacts if isinstance(item, dict)],
        )
        self.database.update_task(
            task_id,
            quality_status="exhausted",
            quality_score=max(1, min(100, int(best.get("score") or 1))),
            quality_selected_attempt=selected_attempt,
            quality_report_json=json.dumps(report, ensure_ascii=False),
        )
        self.database.add_task_event(task_id, "quality.exhausted", {
            "attempt": selected_attempt,
            "score": int(best.get("score") or 1),
            "message": reason or "已达到三次自动验收上限，已交付最高分版本",
        })
        self.database.finish_task(
            task_id,
            status="completed",
            output=str(best.get("output") or ""),
        )
        return True

    async def _handle_completed_task(self, task_id: str, output: str) -> None:
        task = self.database.get_task(task_id)
        if not task:
            return
        if not self.quality_reviewer:
            self._finish_completed_task(task_id, output)
            return
        workspace = self.runtimes.user_paths(str(task["user_id"]))["container_workspace"]
        cleaned, artifacts, rejected = collect_task_artifacts(
            workspace,
            output,
            modified_after_ms=int(task.get("started_at") or task.get("created_at") or 0),
        )
        cleaned = canonical_office_output(cleaned, artifacts)
        if rejected:
            self.database.add_task_event(task_id, "artifacts.rejected", {"count": len(rejected)})
        attempt = int(task.get("quality_attempt") or 0) + 1
        if _empty_run_output(output) and not any(
            Path(str(item.get("filename") or "")).suffix.casefold() in RECOVERABLE_OFFICE_SUFFIXES
            for item in artifacts
        ):
            recovery_rows = self.database.all(
                "SELECT payload_json FROM task_events WHERE task_id = ? AND event_type = 'run.empty_recovery_started'",
                (task_id,),
            )
            recovered_attempts: set[int] = set()
            for row in recovery_rows:
                try:
                    recovered_attempts.add(int(json.loads(str(row.get("payload_json") or "{}")).get("attempt") or 0))
                except (ValueError, TypeError, json.JSONDecodeError):
                    continue
            if attempt not in recovered_attempts:
                await self._start_empty_run_recovery(task, attempt, workspace)
                return
        try:
            plan = json.loads(str(task.get("coordination_plan_json") or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            plan = {}
        capability_route = plan.get("capabilityRoute") if isinstance(plan, dict) else []
        cdp_issue = ""
        if isinstance(capability_route, list) and "tool:browser-CDP" in capability_route:
            cdp_issue = browser_cdp_evidence_issue(browser_cdp_evidence(self.database, task_id))
            if cdp_issue:
                recovery_rows = self.database.all(
                    "SELECT payload_json FROM task_events WHERE task_id = ? "
                    "AND event_type = 'run.cdp_evidence_recovery_started'",
                    (task_id,),
                )
                recovery_count = 0
                for row in recovery_rows:
                    try:
                        payload = json.loads(str(row.get("payload_json") or "{}"))
                        if int(payload.get("attempt") or 0) == attempt:
                            recovery_count += 1
                    except (ValueError, TypeError, json.JSONDecodeError):
                        continue
                if recovery_count < 2:
                    await self._start_cdp_evidence_recovery(
                        task,
                        attempt,
                        workspace,
                        cdp_issue,
                        recovery_count + 1,
                    )
                    return
        office_urls, specific_source_urls = research_office_source_gap(
            workspace, artifacts, capability_route
        )
        if isinstance(capability_route, list) and "tool:browser-CDP" in capability_route and not cdp_issue:
            source_recovery_rows = self.database.all(
                "SELECT payload_json FROM task_events WHERE task_id = ? "
                "AND event_type = 'run.source_embedding_recovery_started'",
                (task_id,),
            )
            recovered_source_attempts: set[int] = set()
            for row in source_recovery_rows:
                try:
                    recovered_source_attempts.add(
                        int(json.loads(str(row.get("payload_json") or "{}")).get("attempt") or 0)
                    )
                except (ValueError, TypeError, json.JSONDecodeError):
                    continue
            if (
                any(
                    Path(str(item.get("filename") or "")).suffix.casefold() in {".docx", ".pptx", ".xlsx"}
                    for item in artifacts
                )
                and (len(office_urls) < 2 or len(specific_source_urls) < 2)
                and attempt not in recovered_source_attempts
            ):
                await self._start_source_embedding_recovery(
                    task,
                    attempt,
                    workspace,
                    office_urls,
                    specific_source_urls,
                )
                return
        if attempt > 1:
            previous_attempts = self.database.list_quality_attempts(task_id)
            previous_row = next(
                (row for row in previous_attempts if int(row.get("attempt") or 0) == attempt - 1),
                None,
            )
            try:
                previous_artifacts = json.loads(str(previous_row.get("artifacts_json") or "[]")) if previous_row else []
            except json.JSONDecodeError:
                previous_artifacts = []
            try:
                previous_report = json.loads(str(previous_row.get("report_json") or "{}")) if previous_row else {}
            except json.JSONDecodeError:
                previous_report = {}
            unchanged = []
            if not submission_only_quality_issues(previous_report if isinstance(previous_report, dict) else {}):
                unchanged = await asyncio.to_thread(
                    unchanged_office_artifacts,
                    workspace,
                    artifacts,
                    previous_artifacts if isinstance(previous_artifacts, list) else [],
                )
            unchanged_rows = self.database.all(
                "SELECT payload_json FROM task_events WHERE task_id = ? "
                "AND event_type = 'run.unchanged_artifact_recovery_started'",
                (task_id,),
            )
            recovered_attempts: set[int] = set()
            for row in unchanged_rows:
                try:
                    recovered_attempts.add(int(json.loads(str(row.get("payload_json") or "{}")).get("attempt") or 0))
                except (ValueError, TypeError, json.JSONDecodeError):
                    continue
            if unchanged and attempt not in recovered_attempts:
                await self._start_unchanged_artifact_recovery(task, attempt, workspace, unchanged)
                return
        self.database.update_task(task_id, quality_status="reviewing", quality_attempt=attempt, output=cleaned)
        self.database.add_task_event(task_id, "quality.started", {
            "attempt": attempt,
            "message": "正在使用空白上下文逐项质检",
        })
        try:
            if cdp_issue:
                raise ValueError(
                    f"当前任务的 browser_exec/CDP 证据不足：{cdp_issue}。"
                    "必须分别用 browser_exec 打开 Google 和两个不同来源页；"
                    "web_search、web_extract、API 与终端请求不计入。"
                )
            report = await self.quality_reviewer.review(
                request=str(task["prompt"]),
                output=cleaned,
                workspace=workspace,
                artifacts=artifacts,
                plan=plan,
            )
        except Exception as exc:
            report = {
                "score": 35,
                "passed": False,
                "threshold": int(getattr(self.runtimes.settings, "expert_quality_threshold", 82)),
                "summary": "独立质检暂时失败，需要重做并再次检查",
                "issues": [f"质检服务异常：{str(exc)[:900]}"],
                "pageReviews": [],
            }
        score = max(1, min(100, int(report.get("score") or 35)))
        passed = bool(report.get("passed"))
        status = "passed" if passed else "revision_required"
        visible_artifacts = _verified_deliverables(workspace, artifacts)
        snapshot_artifacts = await asyncio.to_thread(
            snapshot_quality_artifacts, workspace, task_id, attempt, visible_artifacts
        )
        recorded = self.database.replace_task_artifacts(task_id, snapshot_artifacts)
        self.database.record_quality_attempt(
            task_id,
            attempt=attempt,
            score=score,
            passed=passed,
            output=cleaned,
            report=report,
            artifacts=snapshot_artifacts,
        )
        if recorded:
            self.database.add_task_event(task_id, "artifacts.recorded", {
                "count": len(recorded),
                "files": [item["filename"] for item in recorded],
                "attempt": attempt,
            })
        self.database.update_task(
            task_id,
            quality_status=status,
            quality_score=score,
            quality_report_json=json.dumps(report, ensure_ascii=False),
        )
        self.database.add_task_event(task_id, "quality.reviewed", {
            "attempt": attempt,
            "score": score,
            "passed": passed,
            "threshold": report.get("threshold"),
            "issues": report.get("issues") or [],
            "pageReviews": report.get("pageReviews") or [],
        })
        if passed:
            self.database.update_task(task_id, quality_selected_attempt=attempt)
            self.database.finish_task(task_id, status="completed", output=cleaned)
            return
        max_attempts = min(3, max(1, int(getattr(self.runtimes.settings, "expert_max_revisions", 2)) + 1))
        if attempt >= max_attempts:
            self._finish_best_quality_attempt(task_id)
            return
        user_id = str(task["user_id"])
        worker = str(task.get("worker_name") or "")
        if not worker:
            worker = await self.runtimes.ensure_worker(user_id, self.busy_user_ids())
        _checkpoint_files, checkpoint = task_workspace_checkpoint(workspace, task)
        run = await self.hermes.start_run(
            worker_name=worker,
            user_id=user_id,
            session_id=f"{task['conversation_id']}:{task_id}:attempt-{attempt + 1}",
            user_input=await asyncio.to_thread(self._build_user_input, task),
            conversation_history=[],
            agent_profile="fast",
            capability_context="",
            coordination_plan=(
                self.coordinator.execution_brief(
                    json.loads(str(task.get("coordination_plan_json") or "{}"))
                )
                if self.coordinator else ""
            ),
            revision_feedback=(
                quality_revision_requirements(report)[:24_000]
                + "\n\nCURRENT TASK WORKSPACE CHECKPOINT (strict file allowlist; only these files were updated "
                "since this task started):\n"
                + checkpoint[:10_000]
                + "\nDo not list, search, read, execute, or reuse any unlisted /workspace file. Older generators, "
                "deliverables, validators, and scratch files belong to prior tasks. Do not locate or read validator source code."
            ),
            attempt_number=attempt + 1,
            previous_output=cleaned,
        )
        run_id = str(run["run_id"])
        self.database.update_task(
            task_id,
            status="running",
            hermes_run_id=run_id,
            worker_name=worker,
            quality_status="revising",
        )
        self.database.add_task_event(task_id, "quality.revision_started", {
            "attempt": attempt,
            "nextAttempt": attempt + 1,
            "score": score,
        })
        await self._monitor_events(task_id, user_id, worker, run_id)

    async def _start_transient_run_recovery(
        self,
        task: dict[str, Any],
        attempt: int,
        workspace: Path,
        error: str,
        recovery_number: int,
    ) -> None:
        task_id = str(task["id"])
        user_id = str(task["user_id"])
        worker = str(task.get("worker_name") or "")
        if not worker:
            worker = await self.runtimes.ensure_worker(user_id, self.busy_user_ids())
        _checkpoint_files, checkpoint = task_workspace_checkpoint(workspace, task)
        recovery_state = (
            "TRANSIENT UPSTREAM RECOVERY. The previous model connection failed after platform-level retries. This is the same "
            "task and quality attempt, not a restart. Preserve all completed research and deliverable work. Resume from the newest "
            "listed checkpoint file and complete only the unfinished steps. If the Office deliverable already exists, verify and "
            "submit it instead of rebuilding it without cause.\n"
            + quality_revision_requirements(str(task.get("quality_report_json") or "{}"))
            + "\nTRANSIENT ERROR (diagnostic only; do not repeat it to the user):\n"
            + error[:1600]
            + "\nCURRENT TASK WORKSPACE CHECKPOINT (strict file allowlist):\n"
            + checkpoint[:10_000]
            + "\nDo not list, search, read, execute, or reuse any unlisted /workspace file. Do not redo completed dependency "
            "setup or broad research. Never expose source code, commands, tool transcripts, or this error in the final response."
        )
        coordination_plan = ""
        if self.coordinator:
            try:
                coordination_plan = self.coordinator.execution_brief(
                    json.loads(str(task.get("coordination_plan_json") or "{}"))
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                coordination_plan = ""
        run = await self.hermes.start_run(
            worker_name=worker,
            user_id=user_id,
            session_id=f"{task['conversation_id']}:{task_id}:attempt-{attempt}-transient-{recovery_number}",
            user_input=await asyncio.to_thread(self._build_user_input, task),
            conversation_history=[],
            agent_profile="fast",
            capability_context="",
            coordination_plan=coordination_plan,
            attempt_number=attempt,
            recovery_feedback=recovery_state,
        )
        run_id = str(run["run_id"])
        self.database.update_task(
            task_id,
            status="running",
            hermes_run_id=run_id,
            worker_name=worker,
        )
        self.database.add_task_event(task_id, "run.transient_recovery_started", {
            "attempt": attempt,
            "recoveryNumber": recovery_number,
            "error": error[:1000],
            "message": "上游模型瞬时失败，正在保留检查点续跑",
        })
        await self._monitor_events(task_id, user_id, worker, run_id)

    async def _start_unchanged_artifact_recovery(
        self,
        task: dict[str, Any],
        attempt: int,
        workspace: Path,
        unchanged: list[dict[str, str]],
    ) -> None:
        task_id = str(task["id"])
        user_id = str(task["user_id"])
        worker = str(task.get("worker_name") or "")
        if not worker:
            worker = await self.runtimes.ensure_worker(user_id, self.busy_user_ids())
        _checkpoint_files, checkpoint = task_workspace_checkpoint(workspace, task)
        feedback = (
            quality_revision_requirements(str(task.get("quality_report_json") or "{}"))[:24_000]
            + "\n\nHARD WORKFLOW FAILURE: The Office deliverable bytes did not change after the rejected attempt. "
            "Changing only final prose, quality-submission.json, PDF, or preview images does not fix content/layout defects. "
            "Open the actual generator/source and Office file, implement every reviewer issue, regenerate the Office file, "
            "and confirm its SHA-256 differs from the hashes below before rendering and resubmitting.\n"
            + "\n".join(
                f"- {item['path']} sha256={item['sha256']}" for item in unchanged
            )
            + "\n\nCURRENT TASK WORKSPACE CHECKPOINT (strict file allowlist):\n"
            + checkpoint[:10_000]
            + "\nDo not list, search, read, execute, or reuse any unlisted /workspace file. Do not locate or read "
            "validator source code."
        )
        coordination_plan = ""
        if self.coordinator:
            try:
                coordination_plan = self.coordinator.execution_brief(
                    json.loads(str(task.get("coordination_plan_json") or "{}"))
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                coordination_plan = ""
        run = await self.hermes.start_run(
            worker_name=worker,
            user_id=user_id,
            session_id=f"{task['conversation_id']}:{task_id}:attempt-{attempt}-artifact-recovery",
            user_input=await asyncio.to_thread(self._build_user_input, task),
            conversation_history=[],
            agent_profile="fast",
            capability_context="",
            coordination_plan=coordination_plan,
            revision_feedback=feedback,
            attempt_number=attempt,
        )
        run_id = str(run["run_id"])
        self.database.update_task(
            task_id,
            status="running",
            hermes_run_id=run_id,
            worker_name=worker,
            quality_status="recovering",
        )
        self.database.add_task_event(task_id, "run.unchanged_artifact_recovery_started", {
            "attempt": attempt,
            "files": [item["filename"] for item in unchanged],
            "message": "质量重做未改变 Office 文件，正在同轮定点修复",
        })
        await self._monitor_events(task_id, user_id, worker, run_id)

    async def _start_source_embedding_recovery(
        self,
        task: dict[str, Any],
        attempt: int,
        workspace: Path,
        office_urls: list[str],
        specific_source_urls: list[str],
    ) -> None:
        task_id = str(task["id"])
        user_id = str(task["user_id"])
        worker = str(task.get("worker_name") or "")
        if not worker:
            worker = await self.runtimes.ensure_worker(user_id, self.busy_user_ids())
        checkpoint_files, checkpoint = task_workspace_checkpoint(workspace, task)
        candidates = checkpoint_http_urls(checkpoint_files)
        for item in browser_cdp_evidence(self.database, task_id):
            for value in BROWSER_HTTP_URL.findall(str(item.get("preview") or "")):
                normalized = value.rstrip(".,;:!?)]}，。；：！？）】")
                if normalized and normalized not in candidates:
                    candidates.append(normalized)
        feedback = (
            "SOURCE EMBEDDING RECOVERY. A deterministic preflight inspected the actual DOCX/PPTX/XLSX package before "
            "quality scoring. The final Office file does not contain at least two distinct claim-level http(s) source URLs. "
            f"It currently contains {len(office_urls)} total URL(s) and {len(specific_source_urls)} specific source URL(s). "
            "This is a same-attempt blocking repair, not a new task and not optional advice.\n\n"
            "REQUIRED FILE REPAIR:\n"
            "1. Open the listed source ledger and the listed generator/spec/source file used for the actual Office deliverable.\n"
            "2. Map material claims to the exact candidate URLs below. For a presentation, add a concise final Sources/References "
            "slide with readable claim labels and real clickable hyperlinks. For a document or spreadsheet, add an equivalent "
            "sources section/sheet. A separate Markdown ledger or publisher name does not satisfy this gate.\n"
            "3. Regenerate or directly edit the actual Office file. Do not change only prose, the ledger, PDF, previews, or "
            "quality-submission.json. The Office bytes must change.\n"
            "4. Inspect the final Office ZIP package XML and .rels members and confirm at least two different exact http(s) URLs "
            "are present, then render every page/slide again and update quality-submission.json.\n"
            "5. Finish only after the Office package itself passes that URL count. Do not expose source code or commands in the "
            "user-facing response.\n\n"
            "EXACT CLAIM-LEVEL URL CANDIDATES FROM THIS TASK:\n"
            + (
                "\n".join(f"- {value}" for value in candidates[:20])
                if candidates
                else "- None recorded; use browser_exec to open exact claim pages first."
            )
            + "\n\nCURRENT TASK WORKSPACE CHECKPOINT (strict file allowlist):\n"
            + checkpoint[:10_000]
            + "\nDo not list, search, read, execute, or reuse any unlisted /workspace file. Do not locate or read "
            "validator source code."
        )
        coordination_plan = ""
        if self.coordinator:
            try:
                coordination_plan = self.coordinator.execution_brief(
                    json.loads(str(task.get("coordination_plan_json") or "{}"))
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                coordination_plan = ""
        run = await self.hermes.start_run(
            worker_name=worker,
            user_id=user_id,
            session_id=f"{task['conversation_id']}:{task_id}:attempt-{attempt}-source-embedding",
            user_input=await asyncio.to_thread(self._build_user_input, task),
            conversation_history=[],
            agent_profile="fast",
            capability_context="",
            coordination_plan=coordination_plan,
            revision_feedback=feedback,
            attempt_number=attempt,
        )
        run_id = str(run["run_id"])
        self.database.update_task(
            task_id,
            status="running",
            hermes_run_id=run_id,
            worker_name=worker,
            quality_status="recovering",
        )
        self.database.add_task_event(task_id, "run.source_embedding_recovery_started", {
            "attempt": attempt,
            "currentUrlCount": len(office_urls),
            "currentSpecificUrlCount": len(specific_source_urls),
            "candidateUrlCount": len(candidates),
            "message": "Office 文件缺少可复核来源，正在同轮嵌入来源附录",
        })
        await self._monitor_events(task_id, user_id, worker, run_id)

    async def _start_empty_run_recovery(self, task: dict[str, Any], attempt: int, workspace: Path) -> None:
        task_id = str(task["id"])
        user_id = str(task["user_id"])
        worker = str(task.get("worker_name") or "")
        if not worker:
            worker = await self.runtimes.ensure_worker(user_id, self.busy_user_ids())
        existing_files, checkpoint = task_workspace_checkpoint(workspace, task)
        state_lines = [
            "Original request: " + str(task.get("prompt") or "")[:4000],
            quality_revision_requirements(str(task.get("quality_report_json") or "{}")),
            "CURRENT TASK WORKSPACE CHECKPOINT (strict file allowlist):",
            checkpoint,
            "Use only these files as the checkpoint. Do not list, search, read, execute, or reuse any unlisted "
            "/workspace file, and do not locate or read validator source code. Continue the required fix instead of "
            "redoing completed dependency setup or unrelated research.",
        ]
        coordination_plan = ""
        if self.coordinator:
            try:
                coordination_plan = self.coordinator.execution_brief(
                    json.loads(str(task.get("coordination_plan_json") or "{}"))
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                coordination_plan = ""
        run = await self.hermes.start_run(
            worker_name=worker,
            user_id=user_id,
            session_id=f"{task['conversation_id']}:{task_id}:attempt-{attempt}-empty-recovery",
            user_input=await asyncio.to_thread(self._build_user_input, task),
            conversation_history=[],
            agent_profile="fast",
            capability_context="",
            coordination_plan=coordination_plan,
            attempt_number=attempt,
            recovery_feedback="\n".join(state_lines),
        )
        run_id = str(run["run_id"])
        self.database.update_task(
            task_id,
            status="running",
            hermes_run_id=run_id,
            worker_name=worker,
            quality_status="recovering",
        )
        self.database.add_task_event(task_id, "run.empty_recovery_started", {
            "attempt": attempt,
            "files": [path.name for path in existing_files[:20]],
            "message": "模型空回复，正在从现有工具结果断点续作",
        })
        await self._monitor_events(task_id, user_id, worker, run_id)

    async def _start_cdp_evidence_recovery(
        self,
        task: dict[str, Any],
        attempt: int,
        workspace: Path,
        issue: str,
        recovery_number: int,
    ) -> None:
        task_id = str(task["id"])
        user_id = str(task["user_id"])
        worker = str(task.get("worker_name") or "")
        if not worker:
            worker = await self.runtimes.ensure_worker(user_id, self.busy_user_ids())
        checkpoint_files, checkpoint = task_workspace_checkpoint(workspace, task)
        exact_url_candidates = [
            value for value in checkpoint_http_urls(checkpoint_files)
            if "google." not in value.casefold()
        ][:8]
        candidate_text = "\n".join(f"- {value}" for value in exact_url_candidates)
        run = await self.hermes.start_run(
            worker_name=worker,
            user_id=user_id,
            session_id=f"{task['conversation_id']}:{task_id}:attempt-{attempt}-cdp-{recovery_number}",
            user_input=await asyncio.to_thread(self._build_user_input, task),
            conversation_history=[],
            agent_profile="fast",
            capability_context="",
            coordination_plan="",
            revision_feedback=(
                "CDP EVIDENCE ONLY RECOVERY. Preserve every existing deliverable and its bytes. "
                "Do not repeat document research, drafting, rendering, validation, or dependency setup. Fix only this issue: "
                + issue[:3000]
                + "\nYour first actions must be native browser_exec calls: open https://www.google.com and then open two "
                "different non-Google source URLs. Write each browser task description exactly as `Open <complete URL>`. "
                "Do not call terminal, search_files, read_file, write_file, skill_view, or any other tool. Do not inspect or "
                "manage browser processes. After the three successful visits, emit the existing artifact marker(s).\n"
                + ("EXACT URL CANDIDATES (copy two verbatim):\n" + candidate_text + "\n" if candidate_text else "")
                + "CURRENT TASK FILES (do not open or modify them):\n"
                + checkpoint[:10_000]
            ),
            attempt_number=attempt,
            previous_output=str(task.get("output") or ""),
        )
        run_id = str(run["run_id"])
        self.database.update_task(
            task_id,
            status="running",
            hermes_run_id=run_id,
            worker_name=worker,
            quality_status="recovering",
        )
        self.database.add_task_event(task_id, "run.cdp_evidence_recovery_started", {
            "attempt": attempt,
            "recoveryNumber": recovery_number,
            "issue": issue[:900],
            "message": "CDP 取证不足，正在同轮补充可审计来源访问",
        })
        await self._monitor_events(task_id, user_id, worker, run_id)

    def _conversation_history(self, task: dict[str, Any]) -> list[dict[str, Any]]:
        conversation = self.database.get_conversation(str(task["conversation_id"]), str(task["user_id"])) or {}
        summary = str(conversation.get("summary") or "")
        summarized_through = int(conversation.get("summary_through_message_id") or 0)
        rows = self.database.all(
            "SELECT id, role, content FROM messages WHERE conversation_id = ? AND id > ? ORDER BY id ASC",
            (task["conversation_id"], summarized_through),
        )
        if rows and rows[-1]["role"] == "user" and rows[-1]["content"] == task["prompt"]:
            rows.pop()
        context_length = int(getattr(self.runtimes.settings, "llm_context_length", 128_000) or 128_000)
        plan = plan_compression(rows, context_length, summary)
        if plan.compressed and plan.older:
            summary = fallback_summary(summary, plan.older)
        return context_messages(summary, plan.recent)

    async def _conversation_history_with_memory(self, task: dict[str, Any]) -> list[dict[str, Any]]:
        history = self._conversation_history(task)
        if not self.coordinator or not hasattr(self.coordinator, "select_memories"):
            return history
        conversation_id = str(task["conversation_id"])
        memories = [
            item for item in self.database.user_memories(str(task["user_id"]), limit=80)
            if not (
                str(item.get("conversation_id") or "") == conversation_id
                and str(item.get("source") or "") == "chat"
            )
        ]
        candidates = [
            {
                "id": int(item["id"]),
                "conversation": str(item.get("conversation_title") or "")[:120],
                "source": str(item.get("source") or ""),
                "user": str(item.get("user_content") or "")[:1200],
                "result": str(item.get("assistant_content") or "")[:1800],
            }
            for item in memories
        ]
        try:
            decision = await self.coordinator.select_memories(
                request=str(task.get("prompt") or ""), candidates=candidates
            )
        except Exception as exc:
            self.database.add_task_event(str(task["id"]), "memory.selection_failed", {
                "error": str(exc)[:500],
                "message": "跨会话记忆筛选失败，本任务按独立上下文执行",
            })
            return history
        selected_ids = {
            int(value) for value in decision.get("selectedIds") or []
            if str(value).isdigit()
        }
        selected = [item for item in memories if int(item["id"]) in selected_ids]
        self.database.add_task_event(str(task["id"]), "memory.selected", {
            "candidateCount": len(candidates),
            "selectedCount": len(selected),
            "selectedIds": [int(item["id"]) for item in selected],
            "reason": str(decision.get("reason") or "")[:500],
            "model": str(decision.get("model") or ""),
        })
        if not selected:
            return history
        lines = [
            "SELECTED GLOBAL MEMORY (chosen by the coordinator LLM for this task only):",
            "Use it only as optional background. The current request and current-conversation messages are authoritative. ",
            "Never continue an old task, reuse old subject matter, or copy an old deliverable unless the current request explicitly asks.",
        ]
        for item in selected:
            lines.extend([
                f"[memory {int(item['id'])}; conversation={str(item.get('conversation_title') or '')[:120]}]",
                "Previous user request: " + str(item.get("user_content") or "")[:3000],
                "Previous result: " + str(item.get("assistant_content") or "")[:5000],
            ])
        memory_text = "\n".join(lines)[:24_000]
        return [{"role": "system", "content": memory_text}, *history]

    def _build_user_input(self, task: dict[str, Any]) -> str | list[dict[str, Any]]:
        prompt = str(task["prompt"])
        workspace = self.runtimes.user_paths(str(task["user_id"]))["container_workspace"].resolve()
        referenced: list[str] = []
        for raw_path in re.findall(r"@<([^>]+)>", prompt)[:32]:
            candidate = (workspace / raw_path.lstrip("/\\")).resolve()
            if candidate != workspace and workspace not in candidate.parents:
                continue
            if candidate.exists():
                referenced.append(f"/workspace/{candidate.relative_to(workspace).as_posix()}")
        if referenced:
            prompt += "\n\n工作区引用（可在当前环境中直接读写）：\n" + "\n".join(dict.fromkeys(referenced))
        attachment_ids = json.loads(task.get("attachment_ids") or "[]")
        if not attachment_ids:
            return prompt
        blocks: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        file_notes: list[str] = []
        for attachment_id in attachment_ids:
            row = self.database.one(
                "SELECT * FROM attachments WHERE id = ? AND user_id = ?",
                (attachment_id, task["user_id"]),
            )
            if not row:
                continue
            path = self.runtimes.user_paths(str(task["user_id"]))["container_attachments"] / str(row["relative_path"])
            container_path = f"/attachments/{row['relative_path']}"
            mime = str(row.get("mime_type") or mimetypes.guess_type(str(row["filename"]))[0] or "application/octet-stream")
            file_notes.append(f"{row['filename']}: {container_path}")
            if mime.startswith("image/") and path.is_file():
                encoded = base64.b64encode(path.read_bytes()).decode("ascii")
                blocks.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}})
        if file_notes:
            blocks[0]["text"] += "\n\n附件已只读挂载：\n" + "\n".join(file_notes)
        return [{"role": "user", "content": blocks}]

    async def _resume_task(self, task: dict[str, Any]) -> None:
        task_id = str(task["id"])
        try:
            await self._monitor_poll(
                task_id,
                str(task["user_id"]),
                str(task["worker_name"]),
                str(task["hermes_run_id"]),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            self.database.update_task(
                task_id,
                status="queued",
                hermes_run_id=None,
                worker_name=None,
                started_at=None,
            )
            self.database.add_task_event(task_id, "task.recovered", {"message": "原运行不可恢复，任务重新排队"})

    async def _monitor_events(self, task_id: str, user_id: str, worker: str, run_id: str) -> None:
        delta_parts: list[str] = []
        last_flush = time.monotonic()
        try:
            async for event in self.hermes.events(worker, user_id, run_id):
                event_type = str(event.get("event") or "message")
                if event_type == "message.delta":
                    delta_parts.append(str(event.get("delta") or ""))
                    if sum(map(len, delta_parts)) >= 300 or time.monotonic() - last_flush >= 0.75:
                        self.database.add_task_event(task_id, event_type, {"delta": "".join(delta_parts)})
                        delta_parts = []
                        last_flush = time.monotonic()
                else:
                    if delta_parts:
                        self.database.add_task_event(task_id, "message.delta", {"delta": "".join(delta_parts)})
                        delta_parts = []
                    self.database.add_task_event(task_id, event_type, event)
                    if event_type == "approval.request":
                        try:
                            await self.hermes.approve(worker, user_id, run_id, "always")
                            self.database.update_task(task_id, status="running")
                            self.database.add_task_event(task_id, "approval.auto_approved", {
                                "choice": "always",
                                "message": "Docker 内 Hermes 指令已自动批准",
                            })
                        except HermesError:
                            self.database.update_task(task_id, status="waiting_approval")
                    elif event_type == "approval.responded":
                        self.database.update_task(task_id, status="running")
                    elif event_type == "run.completed":
                        await self._handle_completed_task(task_id, str(event.get("output") or ""))
                        return
                    elif event_type == "run.failed":
                        task = self.database.get_task(task_id)
                        error = str(event.get("error") or "Hermes 任务失败")
                        if task and await self._recover_transient_run_failure(task, error):
                            return
                        if task and str(task.get("quality_status") or "") == "revising":
                            self.database.add_task_event(task_id, "quality.revision_failed", {"error": error[:1000]})
                            if self._finish_best_quality_attempt(task_id, reason="本次重做失败，已选择现有最高分版本交付"):
                                return
                        self.database.finish_task(task_id, status="failed", error=error)
                        return
                    elif event_type == "run.cancelled":
                        self.database.finish_task(task_id, status="cancelled")
                        return
            if delta_parts:
                self.database.add_task_event(task_id, "message.delta", {"delta": "".join(delta_parts)})
        except HermesError:
            pass
        await self._monitor_poll(task_id, user_id, worker, run_id)

    async def _monitor_poll(self, task_id: str, user_id: str, worker: str, run_id: str) -> None:
        while True:
            status = await self.hermes.run_status(worker, user_id, run_id)
            state = str(status.get("status") or "running")
            if state == "waiting_for_approval":
                try:
                    await self.hermes.approve(worker, user_id, run_id, "always")
                    self.database.update_task(task_id, status="running")
                    self.database.add_task_event(task_id, "approval.auto_approved", {
                        "choice": "always",
                        "message": "Docker 内 Hermes 指令已自动批准",
                    })
                    await asyncio.sleep(0)
                    continue
                except HermesError:
                    pass
            mapped = "waiting_approval" if state == "waiting_for_approval" else state
            if mapped in {"running", "waiting_approval", "stopping"}:
                self.database.update_task(task_id, status=mapped)
            if state == "completed":
                self.database.add_task_event(task_id, "run.completed", status)
                await self._handle_completed_task(task_id, str(status.get("output") or ""))
                return
            if state in {"failed", "cancelled"}:
                self.database.add_task_event(task_id, f"run.{state}", status)
                task = self.database.get_task(task_id)
                error = str(status.get("error") or "Hermes 任务失败")
                if state == "failed" and task and await self._recover_transient_run_failure(task, error):
                    return
                if state == "failed" and task and str(task.get("quality_status") or "") == "revising":
                    self.database.add_task_event(task_id, "quality.revision_failed", {"error": error[:1000]})
                    if self._finish_best_quality_attempt(task_id, reason="本次重做失败，已选择现有最高分版本交付"):
                        return
                self.database.finish_task(task_id, status=state, error=str(status.get("error") or ""))
                return
            await asyncio.sleep(2)

    async def _recover_transient_run_failure(self, task: dict[str, Any], error: str) -> bool:
        if not transient_run_error(error):
            return False
        task_id = str(task["id"])
        recovery_rows = self.database.all(
            "SELECT id FROM task_events WHERE task_id = ? AND event_type = 'run.transient_recovery_started'",
            (task_id,),
        )
        if len(recovery_rows) >= MAX_TRANSIENT_RUN_RECOVERIES:
            self.database.add_task_event(task_id, "run.transient_recovery_exhausted", {
                "retries": len(recovery_rows),
                "error": error[:1000],
                "message": "瞬时上游故障续跑次数已用尽",
            })
            return False
        quality_status = str(task.get("quality_status") or "")
        quality_attempt = int(task.get("quality_attempt") or 0)
        attempt = max(1, quality_attempt + (1 if quality_status in {"revising", "recovering"} else 0))
        workspace = self.runtimes.user_paths(str(task["user_id"]))["container_workspace"]
        await self._start_transient_run_recovery(
            task,
            attempt,
            workspace,
            error,
            len(recovery_rows) + 1,
        )
        return True
