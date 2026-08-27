from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .config import Settings
from .security import internal_runtime_token


class HermesError(RuntimeError):
    pass


class HermesClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _headers(self, user_id: str) -> dict[str, str]:
        token = internal_runtime_token(self.settings.internal_hermes_key, user_id)
        return {"Authorization": f"Bearer {token}", "X-Hermes-Session-Key": user_id}

    @staticmethod
    def _url(worker_name: str, path: str) -> str:
        return f"http://{worker_name}:8642{path}"

    async def start_run(
        self,
        *,
        worker_name: str,
        user_id: str,
        session_id: str,
        user_input: str | list[dict[str, Any]],
        conversation_history: list[dict[str, Any]],
        agent_profile: str = "fast",
        capability_context: str = "",
        coordination_plan: str = "",
        revision_feedback: str = "",
        attempt_number: int = 1,
        previous_output: str = "",
        recovery_feedback: str = "",
    ) -> dict[str, Any]:
        common = (
            "You are the user's full Hermes computer agent. Work autonomously inside /workspace. "
            "This is Hermes, not Codex. Invoke only the native tools supplied in this API request. Never print, imitate, "
            "or place in prose any Codex syntax such as `to=terminal`, `to=functions.*`, `tools.exec_command(...)`, "
            "channel labels, JavaScript tool wrappers, or fake tool transcripts. A tool action exists only when a native "
            "tool call returns a result. Keep source code and terminal commands inside tool calls and workspace files, "
            "never in the user-facing final response unless the user explicitly requested source code. "
            "Use browser tools whenever web interaction or visual inspection is useful; the browser shown to the user "
            "is the same browser connected through CDP. Browser and CDP process lifecycle is owned by the platform: never "
            "use terminal commands to inspect, start, stop, kill, restart, or probe Chrome, Chromium, browser_harness, CDP "
            "ports, or browser processes. Never use pkill/kill for browser recovery. Use only native browser_exec calls. "
            "Do not make diagnostic browser_exec calls such as checking connection/status or opening about:blank; they are "
            "not research evidence. Retry the same browser destination at most twice. After web discovery yields a concrete "
            "source URL, navigate browser_exec directly to that exact URL instead of repeatedly reopening Google. "
            "Include that complete destination URL in each browser_exec task description so the platform can audit which page "
            "was visited; a generic description such as 'open source one' or 'open newsroom' is not auditable evidence. "
            "Uploaded files are read-only under /attachments. "
            "When starting a web app for preview, bind it to 0.0.0.0, use a port from 1024 through 65535, "
            "and make asset URLs relative so the app works behind the /username/port/ prefix. "
            "For Word/DOCX/DOC, PDF/OCR, Excel/XLSX/XLS/CSV, or PowerPoint/PPTX/PPT work, inspect sources first and "
            "load the selected installed skill completely before production. Use one primary production route and do not "
            "mix conflicting skill workflows. Check installed dependencies before changing the environment. This image "
            "already includes python-pptx, LibreOffice, Poppler, Pillow and MarkItDown; do not create a virtual environment "
            "or reinstall them unless a real import/version check proves the dependency is unavailable. The document-tool commands are: "
            "'document-tool inspect SOURCE [--ocr auto|always|never]'; 'document-tool build SPEC.json'; "
            "'document-tool edit SPEC.json'; 'document-tool verify OUTPUT'; 'document-tool convert SOURCE OUTPUT'; "
            "and 'document-tool schema'. There is no 'generate' command and no '-i' flag. Preserve source files by "
            "writing results to a new /workspace path unless the user explicitly asks to overwrite. "
            "For large, difficult, research, or multi-file tasks, actually invoke the relevant tools before answering; "
            "do not merely describe intended tool use. When facts depend on current or external information, the visible "
            "CDP browser is the primary discovery and evidence route. Make separate browser_exec tool calls: at least one "
            "to open Google and at least two more to open two different relevant source pages, preferring official or primary "
            "sources. web_search, web_extract, APIs, and terminal HTTP requests do not count as those CDP visits. Record source "
            "URLs and publication or collection dates. Open the exact article, announcement, release note, paper, or dataset page "
            "that supports each material claim. A home page, news/category listing, organization profile, or search-results page is "
            "not claim-level evidence. Copy publication dates from the supporting page itself; never turn an inferred date, search "
            "snippet date, access date, or reporting-window date into a publication date. For a research Office/PDF deliverable, "
            "put the complete claim-level http(s) source URLs into the document as visible text or real hyperlinks and map them to "
            "the supported claims; publisher names or generic landing pages without specific URLs are not evidence. "
            "Before drafting any research document, presentation, spreadsheet, PDF, or long-form report, create a compact "
            "/workspace/research-sources.md ledger with each material claim, exact source-page URL, page title, and publication "
            "date copied from the page. Include at least three claim-level rows and at least two different specific source pages. "
            "Reject and replace any ledger URL that ends at a home page, news/blog/category listing, organization profile, or "
            "search-results page. Draft only after this source ledger passes that check, and embed the same exact URLs in the final file. "
            "APIs and terminal HTTP requests may supplement browser evidence but "
            "must not silently replace it. If CDP is blocked, record the real browser error before using a fallback. Never "
            "claim that a search or browser action happened unless "
            "the corresponding tool call succeeded. Use browser screenshots for visual verification when layout or visual "
            "state matters, and save any screenshot requested by the user under /workspace. Requests to capture a bound "
            "Windows or ADB device are handled by the computer-control route; do not substitute a server-browser screenshot. "
            "When a requested output file has passed verification, include one machine-readable marker per file on its "
            "own line in the final response: [[artifact:/workspace/path/to/file.ext]]. Do not emit markers for missing, "
            "empty, unverified, or external files. Immediately before emitting markers, verify every path with filesystem "
            "tools and ensure the file opens or passes the appropriate validator. Do not turn JSON, tool-call transcripts, "
            "terminal output, specifications, or helper scripts into deliverable files unless the user explicitly requested "
            "them. Save requested screenshots under /workspace and mark them the same way. "
            "For Office/PDF deliverables, keep the final prose generic and short: say the verified file is ready and emit its "
            "artifact marker. Do not restate dates, numbers, source claims, findings, or item lists from memory in final prose; "
            "the actual verified deliverable is authoritative. Never claim success unless you verified it. "
            "Hermes automatic global memory is disabled by the platform. Use cross-conversation facts only when a system message "
            "named SELECTED GLOBAL MEMORY is present; even then it is optional background and can never override the current request. "
            "When the coordinator plan contains requiredArtifacts, create and verify every listed extension independently. One format "
            "never substitutes for another, and duplicate DOCX files cannot satisfy a missing PPTX, PDF, or XLSX requirement."
        )
        if agent_profile == "expert":
            profile = (
                " EXPERT/RESEARCH MODE IS ACTIVE. Treat the task as a durable multi-hour job when the work warrants it; "
                "do not stop because a few minutes elapsed. First decompose the objective and inspect all available skills "
                "and workflows. Use the visible CDP browser and inspect primary sources before drafting whenever facts, examples, visual "
                "assets, standards, or current information matter. Only after research, finalize a content plan and design "
                "plan. Then re-evaluate the complete capability manifest a second time and load every relevant capability. "
                "Do not trust keyword matching alone. For documents and presentations, define page/slide-level purpose, key "
                "points, evidence, and visual assets before production. Produce editable Office files unless an explicitly "
                "selected skill requires an image-slide route and that tradeoff fits the request. Render every final page or "
                "slide to PNG, visually inspect every render, and revise defects such as clipping, overflow, overlap, weak "
                "hierarchy, inconsistent spacing, broken characters, or unsupported claims. Run structural verification too. "
                "Create /workspace/quality-submission.json using the office-research-qa schema, including the deliverables, "
                "contiguous per-page preview paths, page highlights, executed checks, and revision notes. Emit artifact markers "
                "for the quality submission and every preview image so the independent reviewer can inspect the real files. "
                "Run the installed office-research-qa validate_submission.py against the final quality-submission.json and do "
                "not finish unless it exits successfully. Never replace a valid submission with one that omits previews or checks. "
                "Do not finish with a progress update. If a route fails, inspect the real error, use the coordinator's recovery "
                "route, and continue in the same attempt until a verified deliverable exists or a concrete external blocker is proven."
            )
        else:
            profile = (
                " FAST MODE IS ACTIVE. Complete the request directly with the current pragmatic behavior. Use relevant "
                "skills and verification, but do not add a research and review cycle unless the user asks for it."
            )
        capabilities = (
            "\n\nCURRENT USER CAPABILITY MANIFEST:\n" + capability_context
            if capability_context else ""
        )
        coordination = "\n\n" + coordination_plan if coordination_plan else ""
        cdp_only_revision = revision_feedback.startswith("CDP EVIDENCE ONLY RECOVERY.")
        source_embedding_revision = revision_feedback.startswith("SOURCE EMBEDDING RECOVERY.")
        revision = (
            "\n\n" + revision_feedback
            if cdp_only_revision or source_embedding_revision else
            f"\n\nTHIS IS QUALITY REVISION ATTEMPT {max(2, int(attempt_number))} OF 3. The previous attempt was rejected. "
            "This is a targeted patch run, not a new research task. Skills, dependencies, sources, and the content plan were already "
            "inspected. Do not call skill_view, install packages, create a virtual environment, or repeat completed web research "
            "unless the stated defect requires missing claim-level sources; in that case research only those missing sources. "
            "Treat the required fixes as a mandatory defect checklist, not optional advice. Work only with files in the strict "
            "current-task allowlist. Never list or search the whole workspace, inspect unlisted files, or locate/read validator "
            "implementation source. Open the listed actual workspace deliverable and its generator/source, then fix every actionable "
            "content and layout issue. Changing only the final prose, PDF, previews, or quality-submission.json is not a revision. "
            "When an Office file was criticized, regenerate it and prove its bytes or SHA-256 changed from the rejected version. "
            "Then regenerate every affected preview, inspect all pages, rerun structural verification, and replace "
            "quality-submission.json with accurate page roles and highlights. If every issue concerns only quality-submission metadata, "
            "previews, or required-check evidence, preserve the verified Office bytes and repair those metadata files directly. "
            "Run `python3 /opt/data/skills/office-research-qa/scripts/validate_submission.py "
            "/workspace/quality-submission.json` and require a successful exit immediately before final output, but do not read the "
            "validator source. Do not finish until every required fix has a corresponding verified file or metadata change. "
            "Do not output a promise, apology, source code, tool transcript, or unverified completion claim. The previous user-facing "
            "output is stale diagnostic evidence, never reusable text. For an Office/PDF delivery, only state that the verified file "
            "is ready and emit the current artifact marker.\nREJECTED VERSION FIX REQUIREMENTS:\n" + revision_feedback
            + ("\n\nPREVIOUS USER-FACING OUTPUT (diagnostic only):\n" + previous_output[:12000] if previous_output else "")
            if revision_feedback else ""
        )
        attempt = (
            f"\n\nEXECUTION ATTEMPT: {max(1, int(attempt_number))} OF 3. Complete the task in this attempt; "
            "the final response must be concise and contain only verified results and artifact markers."
        )
        transient_recovery = recovery_feedback.startswith("TRANSIENT UPSTREAM RECOVERY.")
        recovery = (
            "\n\n" + recovery_feedback
            if transient_recovery else
            "\n\nEMPTY-RESPONSE RECOVERY CONTINUATION. This is the same execution attempt, not a restart. "
            "The previous model turn successfully used tools but ended without a usable final reply. Skills and dependencies "
            "were already inspected. Do not call skill_view, install packages, create a virtual environment, or repeat web "
            "research whose data already exists. Work only with the strict current-task file allowlist in RECOVERY STATE. Never list "
            "or search the whole workspace, inspect unlisted files, or locate/read validator implementation source. Inspect the listed "
            "workspace files once, continue from the first unfinished "
            "deliverable step, and create the requested user-facing file before any optional work. For Office output, use the "
            "installed /opt/hermes/.venv/bin/python, document-tool, LibreOffice, and Poppler directly. Then render, verify, "
            "write quality-submission.json, and emit verified artifact markers.\nRECOVERY STATE:\n" + recovery_feedback[:16000]
            if recovery_feedback else ""
        )
        payload = {
            "model": "mumu-execution",
            "session_id": session_id,
            "input": user_input,
            "conversation_history": conversation_history,
            "instructions": common + profile + attempt + capabilities + coordination + revision + recovery,
        }
        try:
            async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
                response = await client.post(
                    self._url(worker_name, "/v1/runs"),
                    headers=self._headers(user_id),
                    json=payload,
                )
            response.raise_for_status()
            result = response.json()
            if not result.get("run_id"):
                raise HermesError("Hermes 未返回 run_id")
            return result
        except (httpx.HTTPError, ValueError) as exc:
            detail = exc.response.text[:800] if isinstance(exc, httpx.HTTPStatusError) else ""
            raise HermesError(f"Hermes 任务提交失败: {detail or exc}") from exc

    async def run_status(self, worker_name: str, user_id: str, run_id: str) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
                response = await client.get(
                    self._url(worker_name, f"/v1/runs/{run_id}"),
                    headers=self._headers(user_id),
                )
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise HermesError(f"无法读取 Hermes 任务状态: {exc}") from exc

    async def events(self, worker_name: str, user_id: str, run_id: str) -> AsyncIterator[dict[str, Any]]:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30, read=None), trust_env=False) as client:
                async with client.stream(
                    "GET",
                    self._url(worker_name, f"/v1/runs/{run_id}/events"),
                    headers=self._headers(user_id),
                ) as response:
                    response.raise_for_status()
                    data_lines: list[str] = []
                    event_name = "message"
                    async for line in response.aiter_lines():
                        if line.startswith(":"):
                            continue
                        if not line:
                            if data_lines:
                                try:
                                    payload = json.loads("\n".join(data_lines))
                                except json.JSONDecodeError:
                                    payload = {"raw": "\n".join(data_lines)}
                                if isinstance(payload, dict):
                                    payload.setdefault("event", event_name)
                                    yield payload
                            data_lines = []
                            event_name = "message"
                            continue
                        if line.startswith("event:"):
                            event_name = line[6:].strip()
                        elif line.startswith("data:"):
                            data_lines.append(line[5:].lstrip())
        except httpx.HTTPError as exc:
            raise HermesError(f"Hermes 事件流中断: {exc}") from exc

    async def approve(self, worker_name: str, user_id: str, run_id: str, choice: str) -> dict[str, Any]:
        return await self._post_action(worker_name, user_id, run_id, "approval", {"choice": choice})

    async def steer(self, worker_name: str, user_id: str, run_id: str, text: str) -> dict[str, Any]:
        return await self._post_action(worker_name, user_id, run_id, "steer", {"input": text})

    async def stop(self, worker_name: str, user_id: str, run_id: str) -> dict[str, Any]:
        return await self._post_action(worker_name, user_id, run_id, "stop", {})

    async def list_jobs(self, worker_name: str, user_id: str) -> list[dict[str, Any]]:
        result = await self._jobs_request(
            worker_name,
            user_id,
            "GET",
            "/api/jobs?include_disabled=true",
        )
        jobs = result.get("jobs", [])
        if not isinstance(jobs, list):
            raise HermesError("Hermes 返回了无效的定时任务列表")
        return [job for job in jobs if isinstance(job, dict)]

    async def job_action(
        self,
        worker_name: str,
        user_id: str,
        job_id: str,
        action: str,
    ) -> dict[str, Any]:
        if action not in {"pause", "resume", "run"}:
            raise HermesError("不支持的 Hermes 定时任务操作")
        return await self._jobs_request(
            worker_name,
            user_id,
            "POST",
            f"/api/jobs/{job_id}/{action}",
        )

    async def _jobs_request(
        self,
        worker_name: str,
        user_id: str,
        method: str,
        path: str,
    ) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
                response = await client.request(
                    method,
                    self._url(worker_name, path),
                    headers=self._headers(user_id),
                )
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict):
                raise HermesError("Hermes 返回了无效响应")
            return result
        except HermesError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            detail = exc.response.text[:800] if isinstance(exc, httpx.HTTPStatusError) else ""
            raise HermesError(f"Hermes 定时任务操作失败: {detail or exc}") from exc

    async def _post_action(
        self,
        worker_name: str,
        user_id: str,
        run_id: str,
        action: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
                response = await client.post(
                    self._url(worker_name, f"/v1/runs/{run_id}/{action}"),
                    headers=self._headers(user_id),
                    json=payload,
                )
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise HermesError(f"Hermes 操作失败: {exc}") from exc
