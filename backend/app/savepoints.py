from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import stat
import uuid
from pathlib import Path
from typing import Any


class SavepointError(RuntimeError):
    pass


class SavepointStore:
    def __init__(self, max_files: int = 150_000, max_logical_bytes: int = 8 * 1024**3):
        self.max_files = max_files
        self.max_logical_bytes = max_logical_bytes

    @staticmethod
    def _store_root(paths: dict[str, Path]) -> Path:
        return paths["container_root"] / "savepoints"

    @staticmethod
    def _manifest_path(paths: dict[str, Path], savepoint_id: str) -> Path:
        return SavepointStore._store_root(paths) / "manifests" / f"{savepoint_id}.json"

    @staticmethod
    def _object_path(store_root: Path, digest: str) -> Path:
        return store_root / "objects" / digest[:2] / f"{digest}.gz"

    @staticmethod
    def _copy_to_object(source: Path, target: Path) -> int:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{uuid.uuid4().hex}.tmp")
        try:
            with source.open("rb") as input_file, gzip.open(temporary, "wb", compresslevel=6) as output_file:
                shutil.copyfileobj(input_file, output_file, length=1024 * 1024)
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass
            os.replace(temporary, target)
            return target.stat().st_size
        finally:
            if temporary.exists():
                temporary.unlink()

    def _scan_root(self, source: Path, store_root: Path) -> tuple[list[dict[str, Any]], int, int, int]:
        entries: list[dict[str, Any]] = []
        logical_bytes = 0
        stored_bytes = 0
        file_count = 0
        if not source.exists():
            return entries, file_count, logical_bytes, stored_bytes
        for current, directory_names, file_names in os.walk(source, topdown=True, followlinks=False):
            current_path = Path(current)
            directory_names.sort()
            file_names.sort()
            for directory_name in list(directory_names):
                path = current_path / directory_name
                relative = path.relative_to(source).as_posix()
                metadata = path.lstat()
                if stat.S_ISLNK(metadata.st_mode):
                    entries.append({"path": relative, "type": "symlink", "target": os.readlink(path)})
                    directory_names.remove(directory_name)
                else:
                    entries.append({"path": relative, "type": "directory", "mode": stat.S_IMODE(metadata.st_mode)})
            for file_name in file_names:
                path = current_path / file_name
                relative = path.relative_to(source).as_posix()
                metadata = path.lstat()
                if stat.S_ISLNK(metadata.st_mode):
                    entries.append({"path": relative, "type": "symlink", "target": os.readlink(path)})
                    continue
                if not stat.S_ISREG(metadata.st_mode):
                    continue
                file_count += 1
                logical_bytes += metadata.st_size
                if file_count > self.max_files:
                    raise SavepointError(f"文件数量超过保存点上限 {self.max_files}")
                if logical_bytes > self.max_logical_bytes:
                    raise SavepointError("环境内容超过单个保存点 8 GB 上限")
                digest = hashlib.sha256()
                with path.open("rb") as input_file:
                    for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
                        digest.update(chunk)
                digest_text = digest.hexdigest()
                object_path = self._object_path(store_root, digest_text)
                if not object_path.exists():
                    stored_bytes += self._copy_to_object(path, object_path)
                entries.append(
                    {
                        "path": relative,
                        "type": "file",
                        "digest": digest_text,
                        "size": metadata.st_size,
                        "mode": stat.S_IMODE(metadata.st_mode),
                        "mtime_ns": metadata.st_mtime_ns,
                    }
                )
        return entries, file_count, logical_bytes, stored_bytes

    def create(self, paths: dict[str, Path], savepoint_id: str) -> dict[str, int]:
        store_root = self._store_root(paths)
        (store_root / "manifests").mkdir(parents=True, exist_ok=True)
        roots: dict[str, list[dict[str, Any]]] = {}
        file_count = 0
        logical_bytes = 0
        stored_bytes = 0
        for key, source in (
            ("workspace", paths["container_workspace"]),
            ("hermes", paths["container_hermes"]),
        ):
            entries, root_files, root_logical, root_stored = self._scan_root(source, store_root)
            roots[key] = entries
            file_count += root_files
            logical_bytes += root_logical
            stored_bytes += root_stored
        manifest = {"version": 1, "roots": roots}
        target = self._manifest_path(paths, savepoint_id)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(manifest, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(temporary, target)
        return {"file_count": file_count, "logical_bytes": logical_bytes, "stored_bytes": stored_bytes}

    @staticmethod
    def _validated_destination(root: Path, relative: str) -> Path:
        if not relative or relative.startswith("/") or ".." in Path(relative).parts:
            raise SavepointError("保存点清单包含无效路径")
        destination = root.joinpath(*Path(relative).parts)
        if destination == root or root not in destination.parents:
            raise SavepointError("保存点路径越界")
        return destination

    def _restore_root(self, source: Path, entries: list[dict[str, Any]], store_root: Path, savepoint_id: str) -> None:
        temporary = source.parent / f".{source.name}.restore-{savepoint_id}"
        previous = source.parent / f".{source.name}.previous-{savepoint_id}"
        if temporary.exists():
            shutil.rmtree(temporary)
        temporary.mkdir(parents=True)
        try:
            for entry in sorted(entries, key=lambda item: (str(item.get("path", "")).count("/"), str(item.get("path", "")))):
                relative = str(entry.get("path") or "")
                destination = self._validated_destination(temporary, relative)
                entry_type = entry.get("type")
                if entry_type == "directory":
                    destination.mkdir(parents=True, exist_ok=True)
                    os.chmod(destination, int(entry.get("mode") or 0o755))
                elif entry_type == "symlink":
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.symlink_to(str(entry.get("target") or ""))
                elif entry_type == "file":
                    digest = str(entry.get("digest") or "")
                    if len(digest) != 64:
                        raise SavepointError("保存点文件摘要无效")
                    object_path = self._object_path(store_root, digest)
                    if not object_path.is_file():
                        raise SavepointError("保存点对象缺失")
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with gzip.open(object_path, "rb") as input_file, destination.open("wb") as output_file:
                        shutil.copyfileobj(input_file, output_file, length=1024 * 1024)
                    os.chmod(destination, int(entry.get("mode") or 0o644))
                    mtime_ns = int(entry.get("mtime_ns") or 0)
                    if mtime_ns:
                        try:
                            os.utime(destination, ns=(mtime_ns, mtime_ns), follow_symlinks=False)
                        except (OSError, NotImplementedError):
                            os.utime(destination, ns=(mtime_ns, mtime_ns))
            if previous.exists():
                shutil.rmtree(previous)
            if source.exists():
                os.replace(source, previous)
            os.replace(temporary, source)
            if previous.exists():
                shutil.rmtree(previous)
        except Exception:
            if temporary.exists():
                shutil.rmtree(temporary)
            if previous.exists() and not source.exists():
                os.replace(previous, source)
            raise

    def restore(self, paths: dict[str, Path], savepoint_id: str) -> None:
        manifest_path = self._manifest_path(paths, savepoint_id)
        if not manifest_path.is_file():
            raise SavepointError("保存点数据不存在")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SavepointError("保存点清单损坏") from exc
        if manifest.get("version") != 1 or not isinstance(manifest.get("roots"), dict):
            raise SavepointError("不支持的保存点格式")
        store_root = self._store_root(paths)
        roots = manifest["roots"]
        for key, source in (
            ("workspace", paths["container_workspace"]),
            ("hermes", paths["container_hermes"]),
        ):
            entries = roots.get(key, [])
            if not isinstance(entries, list):
                raise SavepointError("保存点清单损坏")
            self._restore_root(source, entries, store_root, savepoint_id)

    def delete(self, paths: dict[str, Path], savepoint_id: str) -> None:
        manifest_path = self._manifest_path(paths, savepoint_id)
        if manifest_path.exists():
            manifest_path.unlink()
        store_root = self._store_root(paths)
        referenced: set[str] = set()
        for candidate in (store_root / "manifests").glob("*.json"):
            try:
                manifest = json.loads(candidate.read_text(encoding="utf-8"))
                for entries in manifest.get("roots", {}).values():
                    for entry in entries:
                        if entry.get("type") == "file" and isinstance(entry.get("digest"), str):
                            referenced.add(entry["digest"])
            except (OSError, json.JSONDecodeError, AttributeError, TypeError):
                continue
        objects_root = store_root / "objects"
        if objects_root.exists():
            for object_path in objects_root.glob("*/*.gz"):
                if object_path.stem not in referenced:
                    object_path.unlink()
            for directory in objects_root.iterdir():
                if directory.is_dir() and not any(directory.iterdir()):
                    directory.rmdir()
