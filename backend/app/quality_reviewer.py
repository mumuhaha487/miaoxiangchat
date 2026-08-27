from __future__ import annotations

import base64
import json
import mimetypes
import re
import zipfile
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from .config import Settings
from .llm_retry import LLMUpstreamExhausted
from .model_gateway import ModelGateway


OFFICE_EXTENSIONS = {".doc", ".docx", ".pdf", ".ppt", ".pptx", ".xls", ".xlsx"}
REQUIRED_CHECKS = {
    "package_verified",
    "content_re_read",
    "all_pages_rendered",
    "no_clipping_or_overlap",
    "sources_recorded",
}
CHINESE_TEXT = re.compile(r"[\u3400-\u9fff]")
GENERIC_SOURCE_TAILS = {
    "ai",
    "announcements",
    "articles",
    "blog",
    "blogs",
    "company",
    "engineering",
    "index",
    "models",
    "news",
    "product",
    "products",
    "release",
    "releases",
    "research",
    "technology",
    "updates",
    "world",
}
RECENT_WEEK_REQUEST = re.compile(
    r"最近\s*(?:一|1)\s*周|近\s*(?:七|7)\s*[日天]|过去\s*7\s*[日天]|last\s+week|past\s+week",
    re.IGNORECASE,
)
PUBLICATION_DATE = re.compile(
    r"(?:发布日期|发布日|正式发布|正式生效)\s*[：:]?\s*"
    r"(?P<year>20\d{2})\s*(?:年|[-/.])\s*(?P<month>\d{1,2})\s*(?:月|[-/.])\s*(?P<day>\d{1,2})\s*日?"
)


def _score(value: Any, fallback: int = 35) -> int:
    try:
        return max(1, min(100, int(value)))
    except (TypeError, ValueError):
        return fallback


def _chinese(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    return text if CHINESE_TEXT.search(text) else fallback


def _json_object(value: str) -> dict[str, Any]:
    text = value.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("reviewer did not return JSON")
        parsed = json.loads(text[start:end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("reviewer response is not an object")
    return parsed


def _workspace_path(workspace: Path, value: Any) -> Path:
    raw = str(value or "")
    if not raw.startswith("/workspace/"):
        raise ValueError(f"不是有效的工作区路径：{raw}")
    root = workspace.resolve()
    path = (root / raw.removeprefix("/workspace/")).resolve()
    if root not in path.parents or not path.is_file() or path.stat().st_size <= 0:
        raise ValueError(f"工作区文件不存在或为空：{raw}")
    return path


def required_artifact_issues(
    plan: dict[str, Any] | None,
    artifacts: list[dict[str, Any]],
    submission: dict[str, Any] | None = None,
    *,
    require_submission_listing: bool = False,
) -> list[str]:
    required = plan.get("requiredArtifacts") if isinstance(plan, dict) else []
    if not isinstance(required, list):
        return []
    artifact_extensions = [
        Path(str(item.get("filename") or item.get("relative_path") or "")).suffix.casefold()
        for item in artifacts
        if isinstance(item, dict)
    ]
    submission_extensions = [
        Path(str(value or "")).suffix.casefold()
        for value in (submission.get("deliverables") or [])
    ] if isinstance(submission, dict) and isinstance(submission.get("deliverables"), list) else []
    issues: list[str] = []
    for spec in required:
        if not isinstance(spec, dict):
            continue
        extension = str(spec.get("extension") or "").casefold()
        if not extension.startswith("."):
            continue
        try:
            minimum = max(1, int(spec.get("minimumCount") or 1))
        except (TypeError, ValueError):
            minimum = 1
        actual = artifact_extensions.count(extension)
        if actual < minimum:
            present = "、".join(sorted(set(artifact_extensions))) or "没有交付文件"
            issues.append(
                f"交付类型硬性契约未满足：要求至少 {minimum} 个 {extension} 文件，实际为 {actual} 个；"
                f"当前检测到 {present}。其他扩展名或同类型重复文件不能替代 {extension}。"
            )
        elif require_submission_listing and submission_extensions.count(extension) < minimum:
            issues.append(
                f"quality-submission.json 未列出计划要求的 {extension} 最终文件；必须把真实文件加入 deliverables 后重新验收。"
            )
    return issues


class QualityReviewer:
    def __init__(self, settings: Settings, gateway: ModelGateway):
        self.settings = settings
        self.gateway = gateway

    async def _completion(self, content: list[dict[str, Any]]) -> dict[str, Any]:
        text, _usage, _model = await self.gateway.complete(
            "coordinator",
            [{"role": "user", "content": content}],
            max_tokens=1400,
        )
        try:
            return _json_object(text)
        except (ValueError, json.JSONDecodeError):
            repair_content = [
                *content,
                {
                    "type": "text",
                    "text": (
                        "上一份评审无法解析。根据同一证据重新返回严格 JSON。不得输出 Markdown、代码围栏、"
                        "解释、工具调用或 JSON 之外的文字。上一份无效输出：\n" + text[:12000]
                    ),
                },
            ]
            text, _usage, _model = await self.gateway.complete(
                "coordinator",
                [{"role": "user", "content": repair_content}],
                max_tokens=1400,
            )
            return _json_object(text)

    def _submission(self, workspace: Path, artifacts: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, list[str]]:
        issues: list[str] = []
        candidates = [
            workspace / str(item.get("relative_path") or "")
            for item in artifacts
            if str(item.get("filename") or "").casefold() == "quality-submission.json"
        ]
        direct = workspace / "quality-submission.json"
        if direct.is_file():
            candidates.append(direct)
        if not candidates:
            candidates = sorted(
                workspace.rglob("quality-submission.json"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )[:1]
        if not candidates:
            return None, ["缺少 quality-submission.json，无法核对交付文件和逐页预览。"]
        try:
            data = json.loads(candidates[0].read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("submission must be an object")
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            return None, [f"quality-submission.json 格式无效：{type(exc).__name__}"]
        deliverables = data.get("deliverables") or []
        previews = data.get("previews") or []
        if not isinstance(deliverables, list) or not deliverables:
            issues.append("质检清单没有列出任何交付文件。")
        if not isinstance(previews, list) or not previews:
            issues.append("质检清单没有提供逐页预览图。")
        for value in deliverables if isinstance(deliverables, list) else []:
            try:
                _workspace_path(workspace, value)
            except ValueError as exc:
                issues.append(str(exc))
        pages: list[int] = []
        for item in previews if isinstance(previews, list) else []:
            if not isinstance(item, dict):
                issues.append("逐页预览记录格式不正确。")
                continue
            page = int(item.get("page") or 0)
            pages.append(page)
            try:
                _workspace_path(workspace, item.get("path"))
            except ValueError as exc:
                issues.append(str(exc))
            if len(str(item.get("intended_highlight") or "").strip()) < 8:
                issues.append(f"第 {page} 页没有说明本页应突出的具体内容。")
        if pages != list(range(1, len(pages) + 1)):
            issues.append("逐页预览编号没有从第 1 页连续排列。")
        checks = data.get("checks") if isinstance(data.get("checks"), dict) else {}
        missing = sorted(key for key in REQUIRED_CHECKS if checks.get(key) is not True)
        if missing:
            issues.append("以下必要检查没有完成证明：" + "、".join(missing))
        return data, issues

    @staticmethod
    def _office_text(workspace: Path, artifacts: list[dict[str, Any]]) -> str:
        parts: list[str] = []
        for artifact in artifacts:
            filename = str(artifact.get("filename") or "")
            if Path(filename).suffix.casefold() not in {".pptx", ".docx", ".xlsx"}:
                continue
            try:
                path = _workspace_path(workspace, "/workspace/" + str(artifact.get("relative_path") or ""))
                with zipfile.ZipFile(path) as archive:
                    names = [
                        name for name in archive.namelist()
                        if name.endswith(".xml") and any(
                            marker in name for marker in ("ppt/slides/slide", "word/document", "xl/sharedStrings")
                        )
                    ]
                    text_values: list[str] = []
                    for name in sorted(names)[:300]:
                        xml = archive.read(name).decode("utf-8", errors="ignore")
                        text_values.extend(re.findall(r"<[^>]*:t(?:\s[^>]*)?>(.*?)</[^>]*:t>", xml, flags=re.DOTALL))
                    parts.append(filename + ":\n" + "\n".join(re.sub(r"<[^>]+>", "", value) for value in text_values))
            except (OSError, ValueError, zipfile.BadZipFile):
                continue
        return "\n\n".join(parts)[:50000]

    @staticmethod
    def _office_urls(workspace: Path, artifacts: list[dict[str, Any]]) -> list[str]:
        urls: set[str] = set()
        for artifact in artifacts:
            filename = str(artifact.get("filename") or "")
            if Path(filename).suffix.casefold() not in {".pptx", ".docx", ".xlsx"}:
                continue
            try:
                path = _workspace_path(workspace, "/workspace/" + str(artifact.get("relative_path") or ""))
                with zipfile.ZipFile(path) as archive:
                    for name in archive.namelist():
                        if not name.endswith(".rels"):
                            continue
                        root = ET.fromstring(archive.read(name))
                        for relationship in root.iter():
                            target = str(relationship.attrib.get("Target") or "").strip()
                            if target.startswith(("http://", "https://")):
                                urls.add(target)
            except (OSError, ValueError, zipfile.BadZipFile, ET.ParseError):
                continue
        office_text = QualityReviewer._office_text(workspace, artifacts)
        urls.update(re.findall(r"https?://[^\s<>\"']+", office_text))
        return sorted(urls)

    @staticmethod
    def _specific_source_urls(urls: list[str]) -> list[str]:
        specific: list[str] = []
        for value in urls:
            try:
                parsed = urlsplit(value.rstrip(".,;:!?)]}，。；：！？）】"))
            except ValueError:
                continue
            segments = [segment.casefold() for segment in parsed.path.split("/") if segment]
            if len(segments) < 2 or segments[-1] in GENERIC_SOURCE_TAILS:
                continue
            specific.append(value)
        return sorted(set(specific))

    @staticmethod
    def _recent_week_issue(request: str, office_text: str, today: date) -> str:
        if not RECENT_WEEK_REQUEST.search(request):
            return ""
        publication_dates: set[date] = set()
        for match in PUBLICATION_DATE.finditer(office_text):
            try:
                publication_dates.add(date(
                    int(match.group("year")),
                    int(match.group("month")),
                    int(match.group("day")),
                ))
            except ValueError:
                continue
        earliest = today - timedelta(days=7)
        outside = sorted(value for value in publication_dates if value < earliest or value > today)
        if not outside:
            return ""
        formatted = "、".join(value.isoformat() for value in outside)
        return (
            f"原始要求限定最近一周（{earliest.isoformat()} 至 {today.isoformat()}，含首尾），"
            f"但 Office 正文把以下超出窗口的日期标作发布日期或正式生效日期：{formatted}。"
            "这些事项不得列入本周更新；必须删除、移到明确的历史背景区，或替换为窗口内且由具体来源页支持的更新。"
        )

    async def review(
        self,
        *,
        request: str,
        output: str,
        workspace: Path,
        artifacts: list[dict[str, Any]],
        plan: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        acceptance_context = json.dumps(plan or {}, ensure_ascii=False)[:16000]
        authoritative_now = datetime.now().astimezone()
        authoritative_time = authoritative_now.isoformat(timespec="seconds")
        time_context = (
            f"验收运行时的权威当前时间是 {authoritative_time}。这是服务器真实时间；"
            "不得根据模型训练时间、常识年份或主观猜测将同日/同月时间戳判为未来或异常。"
        )
        required = plan.get("requiredArtifacts") if isinstance(plan, dict) else []
        office = bool(required) or any(
            Path(str(item.get("filename") or "")).suffix.casefold() in OFFICE_EXTENSIONS for item in artifacts
        )
        submission, structural_issues = self._submission(workspace, artifacts) if office else (None, [])
        structural_issues.extend(required_artifact_issues(
            plan, artifacts, submission, require_submission_listing=True
        ))
        page_reviews: list[dict[str, Any]] = []
        previews = submission.get("previews") if isinstance(submission, dict) else []
        if isinstance(previews, list):
            for item in previews[: self.settings.expert_max_review_images]:
                if not isinstance(item, dict):
                    continue
                try:
                    image = _workspace_path(workspace, item.get("path"))
                    mime = mimetypes.guess_type(image.name)[0] or "image/png"
                    encoded = base64.b64encode(image.read_bytes()).decode("ascii")
                    review = await self._completion([
                        {
                            "type": "text",
                            "text": (
                                "你是一名没有任何对话上下文的独立文档设计质检员。根据原始要求和本页职责，"
                                "检查可读性、裁切、重叠、层级、密度、间距、字体、视觉一致性、素材相关性、"
                                "证据清晰度和专业完成度。summary 与 issues 必须使用简体中文。只返回 JSON："
                                '{"score":0-100,"summary":"...","issues":["..."],"redo":true|false}.\n\n'
                                f"原始要求：{request[:12000]}\n"
                                f"{time_context}\n"
                                f"统筹计划与验收条件：{acceptance_context}\n"
                                f"页码：{item.get('page')}\n页面职责：{item.get('role', '')}\n"
                                f"预期重点：{item.get('intended_highlight', '')}\n"
                                f"素材适配说明：{item.get('asset_fit', '')}"
                            ),
                        },
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
                    ])
                    score = _score(review.get("score"))
                    raw_issues = review.get("issues") if isinstance(review.get("issues"), list) else []
                    page_reviews.append({
                        "page": int(item.get("page") or 0),
                        "score": score,
                        "summary": _chinese(review.get("summary"), "本页仍有影响交付质量的问题。"),
                        "issues": [
                            _chinese(value, f"第 {index + 1} 项评审意见需要进一步修正。")
                            for index, value in enumerate(raw_issues)
                        ],
                        "redo": bool(review.get("redo")) or score < self.settings.expert_quality_threshold,
                    })
                except (OSError, ValueError, httpx.HTTPError, LLMUpstreamExhausted):
                    page_reviews.append({
                        "page": int(item.get("page") or 0),
                        "score": 35,
                        "summary": "本页自动质检未能完成，需要重新生成并复查。",
                        "issues": [f"第 {int(item.get('page') or 0)} 页质检服务异常。"],
                        "redo": True,
                    })
        office_text = self._office_text(workspace, artifacts) if office else ""
        capability_route = plan.get("capabilityRoute") if isinstance(plan, dict) else []
        office_urls = self._office_urls(workspace, artifacts) if office else []
        specific_source_urls = self._specific_source_urls(office_urls)
        recent_week_issue = self._recent_week_issue(request, office_text, authoritative_now.date()) if office else ""
        if recent_week_issue:
            structural_issues.append(recent_week_issue)
        if office and isinstance(capability_route, list) and "tool:browser-CDP" in capability_route and len(office_urls) < 2:
            structural_issues.append(
                "研究类 Office 文件必须在正文或超链接中保留至少 2 个可复核的完整 http(s) 来源 URL；"
                f"当前检测到 {len(office_urls)} 个。仅写机构或媒体名称不算来源证据。"
            )
        elif (
            office
            and isinstance(capability_route, list)
            and "tool:browser-CDP" in capability_route
            and len(specific_source_urls) < 2
        ):
            structural_issues.append(
                "研究类 Office 文件必须保留至少 2 个直接支撑具体事实的文章、公告或发布页 URL；"
                f"当前仅检测到 {len(specific_source_urls)} 个具体证据页。机构首页、新闻栏目页、模型组织主页和"
                "搜索结果页不能替代具体来源，也不得把推断日期写成来源发布日期。"
            )
        if not office or office_text:
            try:
                holistic = await self._completion([{
                    "type": "text",
                    "text": (
                        "你是一名没有任何对话上下文的独立交付验收员。逐项对照原始要求和统筹验收条件，判断"
                        "结果是否完整、准确、可验证。对伪工具调用、源码/终端日志泄漏、进度承诺、反复道歉、"
                        "没有证据的成功声明、过时或无日期的时效数据、缺少来源、缺少文件和内容空洞严格扣分。"
                        "必须交叉核对结果文字与 Office 文件中提取的实际文字；日期、数字、结论、来源或完成声明"
                        "存在矛盾时必须列为具体问题并判定 redo=true，不得仅凭结果文字声称已修复。"
                        "只报告可操作的具体缺陷；issues 应指出事实、页面/章节、缺失证据和修复方式。"
                        "summary 与 issues 必须使用简体中文。只返回 JSON："
                        '{"score":0-100,"summary":"...","issues":["..."],"redo":true|false}.\n\n'
                        f"原始要求：{request[:16000]}\n\n结果：{output[:30000]}\n\n"
                        f"{time_context}\n\n"
                        f"统筹计划与验收条件：{acceptance_context}\n\n"
                        f"已验证的文件名：{[item.get('filename') for item in artifacts]}\n\n"
                        f"Office 文件中提取的实际文字：{office_text}"
                    ),
                }])
                score = _score(holistic.get("score"))
                raw_issues = holistic.get("issues") if isinstance(holistic.get("issues"), list) else []
                page_reviews.append({
                    "page": 0,
                    "score": score,
                    "summary": _chinese(holistic.get("summary"), "当前结果仍有需要重做的问题。"),
                    "issues": [
                        _chinese(value, f"第 {index + 1} 项评审意见需要进一步修正。")
                        for index, value in enumerate(raw_issues)
                    ],
                    "redo": bool(holistic.get("redo")) or score < self.settings.expert_quality_threshold,
                })
            except (ValueError, httpx.HTTPError, LLMUpstreamExhausted) as exc:
                structural_issues.append(f"独立质检服务异常，需要重新生成后再次检查（{type(exc).__name__}）。")
        scores = [int(item["score"]) for item in page_reviews]
        score = max(1, round(sum(scores) / len(scores))) if scores else 35
        if structural_issues:
            score = min(score, 59)
        issues = [*structural_issues]
        for item in page_reviews:
            page_label = "整体结果" if int(item["page"]) == 0 else f"第 {item['page']} 页"
            issues.extend(f"{page_label}：{value}" for value in item["issues"])
        passed = score >= self.settings.expert_quality_threshold and not structural_issues and not any(
            item["redo"] for item in page_reviews
        )
        return {
            "score": score,
            "passed": passed,
            "threshold": self.settings.expert_quality_threshold,
            "summary": "独立空白上下文质检已通过。" if passed else "独立质检发现问题，已进入自动重做。",
            "issues": issues[:200],
            "pageReviews": page_reviews,
            "reviewedPages": len(page_reviews),
            "submissionFound": submission is not None,
            "sourceUrlCount": len(office_urls),
            "specificSourceUrlCount": len(specific_source_urls),
        }
