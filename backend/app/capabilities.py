from __future__ import annotations

import hashlib
import io
import json
import re
import secrets
import shutil
import uuid
import zipfile
from pathlib import Path
from typing import Any

import yaml

from .database import Database
from .runtime_manager import RuntimeManager


SKILL_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
SHARE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
MAX_SKILL_FILES = 300
MAX_SKILL_BYTES = 10 * 1024 * 1024


def json_list(value: Any) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return [str(item) for item in parsed if str(item).strip()] if isinstance(parsed, list) else []


def skill_frontmatter(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n") and not text.startswith("---\r\n"):
        raise ValueError("SKILL.md 缺少 YAML frontmatter")
    parts = re.split(r"^---\s*$", text, maxsplit=2, flags=re.MULTILINE)
    if len(parts) < 3:
        raise ValueError("SKILL.md frontmatter 未闭合")
    value = yaml.safe_load(parts[1]) or {}
    if not isinstance(value, dict):
        raise ValueError("SKILL.md frontmatter 必须是对象")
    name = str(value.get("name") or "").strip()
    description = str(value.get("description") or "").strip()
    if not SKILL_NAME.fullmatch(name):
        raise ValueError("Skill name 必须是小写字母、数字或连字符")
    if len(description) < 10:
        raise ValueError("Skill description 过短，无法可靠触发")
    return {**value, "name": name, "description": description}


def validate_skill_directory(root: Path) -> dict[str, Any]:
    root = root.resolve()
    metadata = skill_frontmatter(root / "SKILL.md")
    count = 0
    size = 0
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("Skill 不允许包含符号链接")
        if not path.is_file():
            continue
        resolved = path.resolve()
        if root not in resolved.parents:
            raise ValueError("Skill 文件逃逸出技能目录")
        count += 1
        size += path.stat().st_size
        if count > MAX_SKILL_FILES or size > MAX_SKILL_BYTES:
            raise ValueError("Skill 文件数量或体积超过安全限制")
    return {
        "ok": True,
        "name": metadata["name"],
        "description": metadata["description"],
        "fileCount": count,
        "sizeBytes": size,
    }


def skill_tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(child for child in root.rglob("*") if child.is_file()):
        if path.name == ".mumu-builtin.sha256":
            continue
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def rewrite_skill_name(path: Path, name: str) -> None:
    text = path.read_text(encoding="utf-8")
    parts = re.split(r"^---\s*$", text, maxsplit=2, flags=re.MULTILINE)
    if len(parts) < 3:
        raise ValueError("SKILL.md frontmatter 未闭合")
    metadata = yaml.safe_load(parts[1]) or {}
    metadata["name"] = name
    frontmatter = yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False).strip()
    path.write_text(f"---\n{frontmatter}\n---{parts[2]}", encoding="utf-8")


def _share_code() -> str:
    raw = "".join(secrets.choice(SHARE_ALPHABET) for _ in range(12))
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"


def _share_digest(code: str) -> str:
    return hashlib.sha256(code.replace("-", "").upper().encode("ascii", errors="ignore")).hexdigest()


class CapabilityManager:
    def __init__(self, database: Database, runtimes: RuntimeManager):
        self.database = database
        self.runtimes = runtimes

    def _paths(self, user_id: str) -> dict[str, Path]:
        return self.runtimes._ensure_user_dirs_sync(user_id)

    def sync_skill_records(self, user_id: str) -> list[dict[str, Any]]:
        if hasattr(self.runtimes, "user_paths"):
            skills_root = self.runtimes.user_paths(user_id)["container_skills"]
        else:
            skills_root = self._paths(user_id)["container_skills"]
        skills_root.mkdir(parents=True, exist_ok=True)
        discovered: list[dict[str, Any]] = []
        for directory in sorted(item for item in skills_root.iterdir() if item.is_dir()):
            try:
                report = validate_skill_directory(directory)
            except (OSError, UnicodeError, ValueError) as exc:
                report = {"ok": False, "error": str(exc)}
            name = str(report.get("name") or directory.name)
            description = str(report.get("description") or "")
            record = self.database.upsert_skill_record(
                user_id=user_id,
                name=name,
                description=description,
                source="builtin" if (directory / ".mumu-builtin.sha256").is_file() else "local",
                source_ref="",
                relative_path=directory.name,
                triggers=[name, *re.findall(r"[A-Za-z0-9_.+-]{3,}|[\u4e00-\u9fff]{2,8}", description)[:20]],
                status="validated" if report.get("ok") else "invalid",
                validation_report=report,
            )
            discovered.append(record)
        return discovered

    async def search_online(self, user_id: str, query: str, source: str = "") -> dict[str, Any]:
        command = ["hermes", "skills", "search", query]
        if source:
            command.extend(["--source", source])
        result = await self.runtimes.exec_worker(user_id, command, timeout_seconds=120)
        return {"ok": result["exitCode"] == 0, "query": query, "source": source, **result}

    async def install_online(
        self,
        user_id: str,
        source_ref: str,
        *,
        force: bool = False,
        probe: bool = True,
    ) -> dict[str, Any]:
        source_ref = source_ref.strip()
        if not source_ref or len(source_ref) > 1000 or any(char in source_ref for char in "\r\n\0"):
            raise ValueError("Skill 来源无效")
        skills_root = self._paths(user_id)["container_skills"]
        before = {
            directory.name: skill_tree_digest(directory)
            for directory in skills_root.iterdir()
            if directory.is_dir()
        }
        inspect = await self.runtimes.exec_worker(
            user_id, ["hermes", "skills", "inspect", source_ref], timeout_seconds=120
        )
        if inspect["exitCode"] != 0:
            raise ValueError(f"Skill 预检失败：{inspect['output'][-1200:]}")
        command = ["hermes", "skills", "install", source_ref]
        if force:
            command.append("--force")
        installed = await self.runtimes.exec_worker(user_id, command, timeout_seconds=300)
        if installed["exitCode"] != 0:
            raise ValueError(f"Skill 安装失败：{installed['output'][-1600:]}")
        records = self.sync_skill_records(user_id)
        changed_paths = {
            directory.name
            for directory in skills_root.iterdir()
            if directory.is_dir()
            and before.get(directory.name) != skill_tree_digest(directory)
        }
        changed = [item for item in records if str(item.get("relative_path") or "") in changed_paths]
        target = changed[0] if len(changed) == 1 else None
        if not target:
            raise ValueError("Hermes 报告安装成功，但持久技能目录中没有可验证文件")
        validation = json.loads(str(target.get("validation_report_json") or "{}"))
        validation.update({"inspectOutput": inspect["output"][-2000:], "installOutput": installed["output"][-2000:]})
        if probe:
            name = str(target["name"])
            probe_result = await self.runtimes.exec_worker(
                user_id,
                [
                    "hermes", "--skills", name, "chat", "-q",
                    f"Capability probe only. Load the {name} skill and reply exactly: SKILL_PROBE_OK:{name}",
                ],
                timeout_seconds=240,
            )
            probe_ok = probe_result["exitCode"] == 0 and f"SKILL_PROBE_OK:{name}" in probe_result["output"]
            validation.update({"probeOk": probe_ok, "probeOutput": probe_result["output"][-2000:]})
            status = "validated" if probe_ok else "probe_failed"
        else:
            status = "installed"
        updated = self.database.upsert_skill_record(
            user_id=user_id,
            name=str(target["name"]),
            description=str(target.get("description") or ""),
            source="online",
            source_ref=source_ref,
            relative_path=str(target["relative_path"]),
            triggers=json_list(target.get("triggers_json")),
            status=status,
            validation_report=validation,
        )
        return {"skill": updated, "validation": validation}

    async def audit(self, user_id: str) -> dict[str, Any]:
        result = await self.runtimes.exec_worker(
            user_id, ["hermes", "skills", "audit"], timeout_seconds=240
        )
        records = self.sync_skill_records(user_id)
        return {"ok": result["exitCode"] == 0, "output": result["output"], "skills": records}

    def create_workflow_file(
        self,
        user_id: str,
        *,
        name: str,
        description: str,
        instructions: str,
        triggers: list[str],
        category_id: str | None,
        source_conversation_id: str | None,
    ) -> dict[str, Any]:
        name = name.strip()
        description = description.strip()
        instructions = instructions.strip()
        triggers = list(dict.fromkeys(value.strip() for value in triggers if value.strip()))[:30]
        if not name or len(name) > 80:
            raise ValueError("工作流名称无效")
        if len(instructions) < 20:
            raise ValueError("工作流指令过短，无法可靠执行")
        if category_id and not any(
            str(item["id"]) == category_id for item in self.database.list_workflow_categories(user_id)
        ):
            raise ValueError("工作流分类不存在")
        workflow_id = str(uuid.uuid4())
        root = self._paths(user_id)["container_hermes"] / "workflows" / workflow_id
        root.mkdir(parents=True, exist_ok=False)
        body = (
            "---\n"
            f"name: {json.dumps(name, ensure_ascii=False)}\n"
            f"description: {json.dumps(description, ensure_ascii=False)}\n"
            f"triggers: {json.dumps(triggers, ensure_ascii=False)}\n"
            "---\n\n"
            f"# {name}\n\n{instructions.strip()}\n"
        )
        target = root / "WORKFLOW.md"
        target.write_text(body, encoding="utf-8")
        persisted = target.read_text(encoding="utf-8")
        digest = hashlib.sha256(persisted.encode("utf-8")).hexdigest()
        validation = {"ok": persisted == body, "sha256": digest, "sizeBytes": target.stat().st_size}
        try:
            return self.database.create_workflow(
                user_id=user_id,
                name=name,
                description=description,
                instructions=instructions,
                triggers=triggers,
                relative_path=f"workflows/{workflow_id}/WORKFLOW.md",
                category_id=category_id,
                source_conversation_id=source_conversation_id,
                validation_report=validation,
            )
        except Exception:
            shutil.rmtree(root, ignore_errors=True)
            raise

    def update_workflow_file(self, user_id: str, workflow_id: str, **values: Any) -> dict[str, Any]:
        item = self.database.get_workflow(workflow_id, user_id)
        if not item:
            raise ValueError("工作流不存在")
        name = str(values.get("name", item["name"])).strip()
        description = str(values.get("description", item["description"])).strip()
        instructions = str(values.get("instructions", item["instructions"])).strip()
        triggers = values.get("triggers", json_list(item.get("triggers_json")))
        category_id = values.get("category_id", item.get("category_id"))
        if category_id and not any(
            str(category["id"]) == category_id for category in self.database.list_workflow_categories(user_id)
        ):
            raise ValueError("工作流分类不存在")
        if not name or len(name) > 80 or len(instructions) < 20:
            raise ValueError("工作流名称或指令无效")
        if any(
            str(workflow["id"]) != workflow_id and str(workflow["name"]).casefold() == name.casefold()
            for workflow in self.database.list_workflows(user_id)
        ):
            raise ValueError("工作流名称已存在")
        target = (self._paths(user_id)["container_hermes"] / str(item["relative_path"])).resolve()
        workflows_root = (self._paths(user_id)["container_hermes"] / "workflows").resolve()
        if workflows_root not in target.parents:
            raise ValueError("工作流文件路径无效")
        body = (
            "---\n"
            f"name: {json.dumps(name, ensure_ascii=False)}\n"
            f"description: {json.dumps(description, ensure_ascii=False)}\n"
            f"triggers: {json.dumps(triggers, ensure_ascii=False)}\n"
            "---\n\n"
            f"# {name}\n\n{instructions}\n"
        )
        target.write_text(body, encoding="utf-8")
        validation = {
            "ok": target.read_text(encoding="utf-8") == body,
            "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "sizeBytes": target.stat().st_size,
        }
        updated = self.database.update_workflow(
            workflow_id,
            user_id,
            name=name,
            description=description,
            instructions=instructions,
            triggers_json=json.dumps(triggers, ensure_ascii=False),
            category_id=category_id,
            validation_report_json=json.dumps(validation, ensure_ascii=False),
        )
        if not updated:
            raise ValueError("工作流更新失败")
        return updated

    def delete_workflow(self, user_id: str, workflow_id: str) -> None:
        item = self.database.delete_workflow(workflow_id, user_id)
        if not item:
            raise ValueError("工作流不存在")
        target = (self._paths(user_id)["container_hermes"] / str(item["relative_path"])).resolve()
        workflows_root = (self._paths(user_id)["container_hermes"] / "workflows").resolve()
        if workflows_root in target.parents:
            shutil.rmtree(target.parent, ignore_errors=True)

    def delete_skill(self, user_id: str, skill_id: str) -> None:
        item = self.database.get_skill_record(skill_id, user_id)
        if not item:
            raise ValueError("Skill 不存在")
        if str(item.get("source") or "") == "builtin":
            raise ValueError("内置 Skill 不能删除")
        skills_root = self._paths(user_id)["container_skills"].resolve()
        target = (skills_root / str(item["relative_path"])).resolve()
        if skills_root not in target.parents:
            raise ValueError("Skill 路径无效")
        shutil.rmtree(target)
        self.database.delete_skill_record(skill_id, user_id)

    def capability_context(self, user_id: str, prompt: str) -> str:
        prompt_folded = prompt.casefold()
        workflows = self.database.list_workflows(user_id)
        skills = self.database.list_skill_records(user_id)
        if not skills:
            skills = self.sync_skill_records(user_id)
        manifest = ["Available reusable capabilities (scan now and again after research):"]
        for item in skills[:80]:
            manifest.append(f"- skill:{item['name']} [{item['status']}] {item.get('description', '')}")
        scored: list[tuple[int, dict[str, Any]]] = []
        for workflow in workflows:
            terms = [str(workflow.get("name") or ""), *json_list(workflow.get("triggers_json"))]
            score = sum(3 if term.casefold() in prompt_folded else 0 for term in terms if len(term.strip()) >= 2)
            description_terms = re.findall(r"[A-Za-z0-9_.+-]{3,}|[\u4e00-\u9fff]{2,8}", str(workflow.get("description") or ""))
            score += sum(1 for term in description_terms if term.casefold() in prompt_folded)
            if score:
                scored.append((score, workflow))
            manifest.append(f"- workflow:{workflow['id']} {workflow['name']} - {workflow['description']}")
        for _score, workflow in sorted(scored, key=lambda value: (-value[0], -int(value[1]["updated_at"])))[:5]:
            manifest.extend([
                "",
                f"Selected workflow: {workflow['name']}",
                str(workflow["instructions"]),
            ])
        return "\n".join(manifest)[:80_000]

    def create_share(self, user_id: str, kind: str, item_id: str) -> tuple[str, dict[str, Any]]:
        code = _share_code()
        if kind == "workflow":
            item = self.database.get_workflow(item_id, user_id)
            if not item:
                raise ValueError("工作流不存在")
            payload = {
                "name": item["name"], "description": item["description"],
                "instructions": item["instructions"], "triggers": json_list(item["triggers_json"]),
            }
            archive_relative_path = ""
            raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        elif kind == "skill":
            item = self.database.get_skill_record(item_id, user_id)
            if not item:
                raise ValueError("Skill 不存在")
            skill_root = (self._paths(user_id)["container_skills"] / str(item["relative_path"])).resolve()
            report = validate_skill_directory(skill_root)
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for path in sorted(child for child in skill_root.rglob("*") if child.is_file()):
                    if path.name == ".mumu-builtin.sha256":
                        continue
                    archive.write(path, path.relative_to(skill_root).as_posix())
            raw = buffer.getvalue()
            if len(raw) > MAX_SKILL_BYTES:
                raise ValueError("Skill 分享包超过 10 MiB")
            share_name = f"{uuid.uuid4()}.zip"
            share_root = self.runtimes.settings.data_dir / "capability-shares"
            share_root.mkdir(parents=True, exist_ok=True)
            archive_path = share_root / share_name
            archive_path.write_bytes(raw)
            archive_relative_path = share_name
            payload = {"name": report["name"], "description": report["description"]}
        else:
            raise ValueError("不支持的分享类型")
        share = self.database.create_capability_share(
            owner_user_id=user_id,
            kind=kind,
            item_id=item_id,
            code_hash=_share_digest(code),
            code_preview=f"{code[:4]}-****-{code[-4:]}",
            payload=payload,
            archive_relative_path=archive_relative_path,
            sha256=hashlib.sha256(raw).hexdigest(),
        )
        return code, share

    def import_share(self, user_id: str, code: str) -> dict[str, Any]:
        share = self.database.get_capability_share(_share_digest(code))
        if not share:
            raise ValueError("分享码无效或已停用")
        payload = json.loads(str(share.get("payload_json") or "{}"))
        if share["kind"] == "workflow":
            base_name = str(payload.get("name") or "导入工作流")
            existing = {str(item["name"]).casefold() for item in self.database.list_workflows(user_id)}
            name = base_name
            index = 2
            while name.casefold() in existing:
                name = f"{base_name} ({index})"
                index += 1
            item = self.create_workflow_file(
                user_id,
                name=name,
                description=str(payload.get("description") or ""),
                instructions=str(payload.get("instructions") or ""),
                triggers=[str(value) for value in payload.get("triggers") or []],
                category_id=None,
                source_conversation_id=None,
            )
        else:
            archive = self.runtimes.settings.data_dir / "capability-shares" / str(share["archive_relative_path"])
            raw = archive.read_bytes()
            if hashlib.sha256(raw).hexdigest() != share["sha256"]:
                raise ValueError("Skill 分享包校验失败")
            base_name = str(payload.get("name") or "imported-skill")
            skills_root = self._paths(user_id)["container_skills"]
            name = base_name
            index = 2
            while (skills_root / name).exists():
                name = f"{base_name}-{index}"
                index += 1
            target = skills_root / name
            target.mkdir(parents=True, exist_ok=False)
            try:
                with zipfile.ZipFile(io.BytesIO(raw)) as package:
                    files = [member for member in package.infolist() if not member.is_dir()]
                    if len(files) > MAX_SKILL_FILES or sum(member.file_size for member in files) > MAX_SKILL_BYTES:
                        raise ValueError("Skill 分享包解压后超过安全限制")
                    for member in package.infolist():
                        member_path = Path(member.filename)
                        if member.is_dir():
                            continue
                        if member_path.is_absolute() or ".." in member_path.parts:
                            raise ValueError("Skill 分享包包含不安全路径")
                        destination = (target / member_path).resolve()
                        if target.resolve() not in destination.parents:
                            raise ValueError("Skill 分享包路径逃逸")
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        destination.write_bytes(package.read(member))
                rewrite_skill_name(target / "SKILL.md", name)
                report = validate_skill_directory(target)
                item = self.database.upsert_skill_record(
                    user_id=user_id,
                    name=str(report["name"]),
                    description=str(report["description"]),
                    source="shared",
                    source_ref=str(share["id"]),
                    relative_path=name,
                    triggers=[str(report["name"])],
                    status="validated",
                    validation_report=report,
                )
            except Exception:
                shutil.rmtree(target, ignore_errors=True)
                raise
        self.database.record_capability_import(user_id, str(share["id"]), str(item["id"]))
        return {"kind": share["kind"], "item": item}
