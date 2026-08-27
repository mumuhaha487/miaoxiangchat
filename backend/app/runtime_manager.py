from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import socket
import re
import time
from pathlib import Path
from typing import Any

import docker
import httpx
from docker.errors import APIError, NotFound

from .config import Settings
from .security import browser_controller_token, internal_runtime_token


MANAGED_LABEL = "com.mumu-hermes.managed"
TYPE_LABEL = "com.mumu-hermes.type"
USER_LABEL = "com.mumu-hermes.user"
SPEC_LABEL = "com.mumu-hermes.runtime-spec"
WORKER_SPEC_VERSION = "worker-v3.8.7-memory-gate"
GIB = 1024 ** 3


_PROC_NET_PORTS = r"""
from pathlib import Path

for path, host in (("/proc/net/tcp", "0.0.0.0"), ("/proc/net/tcp6", "[::]")):
    try:
        lines = Path(path).read_text(encoding="ascii").splitlines()[1:]
    except OSError:
        continue
    for line in lines:
        fields = line.split()
        if len(fields) < 4 or fields[3] != "0A":
            continue
        address, port = fields[1].rsplit(":", 1)
        if int(address, 16) == 0:
            print(f"{host}:{int(port, 16)}")
"""


def parse_listening_ports(output: str) -> list[int]:
    ports: set[int] = set()
    for line in output.splitlines():
        fields = line.split()
        local_address = fields[3] if len(fields) >= 4 else (fields[0] if len(fields) == 1 else "")
        match = re.fullmatch(r"(?:0\.0\.0\.0|\*|\[::\]|::):(\d{1,5})", local_address)
        if match:
            port = int(match.group(1))
            if 1024 <= port <= 65535 and port != 8642:
                ports.add(port)
    return sorted(ports)


def calculate_worker_limit(
    *,
    minimum: int,
    maximum: int,
    dynamic: bool,
    cpu_count: int,
    load_one: float,
    cpu_reserve: float,
    cpu_per_worker: float,
    memory_available_bytes: int | None,
    memory_reserve_bytes: int,
    memory_per_worker_bytes: int,
) -> tuple[int, int, int]:
    """Return the current limit and the independent CPU/memory limits."""
    minimum = max(2, minimum)
    maximum = max(minimum, maximum)
    if not dynamic:
        return maximum, maximum, maximum

    cpu_headroom = max(0.0, float(cpu_count) - max(0.0, load_one) - max(0.0, cpu_reserve))
    cpu_limit = int(cpu_headroom // max(0.5, cpu_per_worker))

    if memory_available_bytes is None:
        memory_limit = maximum
    else:
        memory_headroom = max(0, memory_available_bytes - max(0, memory_reserve_bytes))
        memory_limit = memory_headroom // max(1, memory_per_worker_bytes)

    current = max(minimum, min(maximum, cpu_limit, int(memory_limit)))
    return current, cpu_limit, int(memory_limit)


def read_available_memory_bytes() -> int | None:
    try:
        for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


class RuntimeUnavailable(RuntimeError):
    pass


class WorkerPoolBusy(RuntimeUnavailable):
    pass


class RuntimeManager:
    def __init__(self, settings: Settings, docker_client: Any | None = None):
        self.settings = settings
        self._client = docker_client
        self._user_locks: dict[str, asyncio.Lock] = {}
        self._pool_lock = asyncio.Lock()
        self._last_used: dict[str, float] = {}

    @property
    def client(self):
        if self._client is None:
            self._client = docker.from_env()
        return self._client

    @staticmethod
    def user_key(user_id: str) -> str:
        return hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:16]

    def network_name(self, user_id: str) -> str:
        return f"mumu-user-{self.user_key(user_id)}"

    def browser_name(self, user_id: str) -> str:
        return f"mumu-browser-{self.user_key(user_id)}"

    def worker_name(self, user_id: str) -> str:
        return f"mumu-hermes-{self.user_key(user_id)}"

    def browser_token(self, user_id: str) -> str:
        return browser_controller_token(self.settings.internal_browser_key, user_id)

    def worker_api_key(self, user_id: str) -> str:
        return internal_runtime_token(self.settings.internal_hermes_key, user_id)

    def _container_root(self, user_id: str) -> Path:
        return self.settings.data_dir / "users" / self.user_key(user_id)

    def _host_root(self, user_id: str) -> Path:
        return self.settings.project_host_dir / "data" / "users" / self.user_key(user_id)

    def user_paths(self, user_id: str) -> dict[str, Path]:
        container_root = self._container_root(user_id)
        host_root = self._host_root(user_id)
        return {
            "container_root": container_root,
            "container_hermes": container_root / "hermes",
            "container_skills": container_root / "hermes" / "skills",
            "container_memories": container_root / "hermes" / "memories",
            "container_sessions": container_root / "hermes" / "sessions",
            "container_cron": container_root / "hermes" / "cron",
            "container_workspace": container_root / "WORKSPACE",
            "container_attachments": container_root / "attachments",
            "container_browser": container_root / "browser-profile",
            "container_profile": container_root / "profile",
            "host_root": host_root,
            "host_hermes": host_root / "hermes",
            "host_skills": host_root / "hermes" / "skills",
            "host_memories": host_root / "hermes" / "memories",
            "host_sessions": host_root / "hermes" / "sessions",
            "host_cron": host_root / "hermes" / "cron",
            "host_workspace": host_root / "WORKSPACE",
            "host_attachments": host_root / "attachments",
            "host_browser": host_root / "browser-profile",
            "host_profile": host_root / "profile",
        }

    def _worker_config(self) -> str:
        model = "mumu-execution"
        context = self.settings.llm_context_length
        return f"""_config_version: 40
model:
  default: {model}
  provider: custom:codex-inc-re
  context_length: {context}
  max_tokens: 8192
  supports_vision: true

providers:
  codex-inc-re:
    name: codex.inc.re
    api: http://mumu-api:8000/api/internal/llm/v1
    key_env: MUMU_INFERENCE_TOKEN
    transport: chat_completions
    models:
      {model}:
        context_length: {context}

model_overrides:
  custom:codex-inc-re:
    {model}:
      context_window: {context}
      supports_tools: true
      supports_vision: true

reasoning_effort: max

agent:
  max_turns: {self.settings.hermes_max_turns}
  gateway_timeout: {self.settings.hermes_gateway_timeout}
  gateway_timeout_warning: {max(0, self.settings.hermes_gateway_timeout - 1800) if self.settings.hermes_gateway_timeout else 0}
  gateway_notify_interval: {self.settings.hermes_gateway_notify_interval}
  api_max_retries: 1
  tool_use_enforcement: true

platform_toolsets:
  api_server:
    - hermes-api-server

gateway:
  api_server:
    max_concurrent_runs: 1

terminal:
  backend: local

approvals:
  mode: "off"
  cron_mode: approve
  single_query_mode: approve

compression:
  enabled: true
  threshold: 0.50
  target_ratio: 0.20
  protect_last_n: 20
  protect_first_n: 3
  hygiene_hard_message_limit: 5000
  max_attempts: 6
  proactive_prune_tokens: 48000
  proactive_prune_min_result_chars: 8000
  proactive_prune_min_reclaim_tokens: 4096

auxiliary:
  vision:
    provider: main
    model: {model}
  compression:
    provider: main
    model: {model}

memory:
  # Cross-conversation memory is selected by the coordinator LLM in the API.
  # Disable Hermes' unconditional automatic injection to keep new tasks isolated.
  memory_enabled: false
  user_profile_enabled: false

curator:
  enabled: true
  interval_hours: 168
  min_idle_hours: 2
  stale_after_days: 30
  archive_after_days: 90
  prune_builtins: false
  consolidate: true
  backup:
    enabled: true
    keep: 5

security:
  tirith_enabled: true
  allow_private_urls: true

browser:
  allow_private_urls: true

display:
  show_reasoning: false
  show_cost: false
"""

    @staticmethod
    def _skill_tree_digest(root: Path) -> str:
        digest = hashlib.sha256()
        for path in sorted(item for item in root.rglob("*") if item.is_file() and item.name != ".mumu-builtin.sha256"):
            digest.update(path.relative_to(root).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
        return digest.hexdigest()

    def _sync_builtin_skills_sync(self, target_root: Path) -> None:
        source_root = self.settings.builtin_skills_dir
        if not source_root.is_dir():
            return
        for source in sorted(item for item in source_root.iterdir() if item.is_dir()):
            if not (source / "SKILL.md").is_file():
                continue
            target = target_root / source.name
            source_digest = self._skill_tree_digest(source)
            marker = target / ".mumu-builtin.sha256"
            if target.exists():
                if not (target / "SKILL.md").is_file():
                    for source_path in sorted(source.rglob("*")):
                        destination = target / source_path.relative_to(source)
                        if source_path.is_dir():
                            destination.mkdir(parents=True, exist_ok=True)
                        elif not destination.exists():
                            destination.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(source_path, destination)
                    marker.write_text(source_digest, encoding="ascii")
                    continue
                installed_digest = marker.read_text(encoding="ascii").strip() if marker.is_file() else ""
                if not installed_digest or self._skill_tree_digest(target) != installed_digest:
                    continue
                shutil.rmtree(target)
            shutil.copytree(source, target)
            marker.write_text(source_digest, encoding="ascii")

    def _ensure_user_dirs_sync(self, user_id: str) -> dict[str, Path]:
        paths = self.user_paths(user_id)
        for key in (
            "container_hermes",
            "container_skills",
            "container_memories",
            "container_sessions",
            "container_cron",
            "container_workspace",
            "container_attachments",
            "container_browser",
            "container_profile",
        ):
            paths[key].mkdir(parents=True, exist_ok=True)
        (paths["container_browser"] / "Downloads").mkdir(parents=True, exist_ok=True)
        self._sync_builtin_skills_sync(paths["container_skills"])

        config_path = paths["container_hermes"] / "config.yaml"
        config_content = self._worker_config()
        config_path.write_text(config_content, encoding="utf-8")
        workspace_hermes = paths["container_workspace"] / ".hermes"
        workspace_hermes.mkdir(parents=True, exist_ok=True)
        workspace_config = workspace_hermes / "config.yaml"
        workspace_config.write_text(config_content, encoding="utf-8")
        workspace_readme = paths["container_workspace"] / "README.md"
        if not workspace_readme.exists():
            workspace_readme.write_text(
                "# WORKSPACE\n\nThis directory is persistent. Keep project files, virtual environments, and downloaded dependencies here.\n",
                encoding="utf-8",
            )

        for root, directories, files in os.walk(paths["container_root"]):
            for name in directories:
                try:
                    os.chown(Path(root) / name, 10000, 10000)
                except OSError:
                    pass
            for name in files:
                try:
                    os.chown(Path(root) / name, 10000, 10000)
                except OSError:
                    pass
        try:
            os.chown(paths["container_root"], 10000, 10000)
        except OSError:
            pass
        config_path.chmod(0o600)
        workspace_config.chmod(0o600)
        return paths

    async def ensure_user_dirs(self, user_id: str) -> dict[str, Path]:
        return await asyncio.to_thread(self._ensure_user_dirs_sync, user_id)

    def _sync_builtin_skills_for_users_sync(self, user_ids: list[str]) -> int:
        synced = 0
        for user_id in dict.fromkeys(str(value).strip() for value in user_ids):
            if not user_id:
                continue
            target_root = self.user_paths(user_id)["container_skills"]
            target_root.mkdir(parents=True, exist_ok=True)
            self._sync_builtin_skills_sync(target_root)
            chown = getattr(os, "chown", None)
            if chown is None:
                synced += 1
                continue
            for root, directories, files in os.walk(target_root):
                for name in directories:
                    try:
                        chown(Path(root) / name, 10000, 10000)
                    except OSError:
                        pass
                for name in files:
                    try:
                        chown(Path(root) / name, 10000, 10000)
                    except OSError:
                        pass
            try:
                chown(target_root, 10000, 10000)
            except OSError:
                pass
            synced += 1
        return synced

    async def sync_builtin_skills_for_users(self, user_ids: list[str]) -> int:
        return await asyncio.to_thread(self._sync_builtin_skills_for_users_sync, user_ids)

    def _backend_container(self):
        hostname = socket.gethostname()
        try:
            return self.client.containers.get(hostname)
        except NotFound:
            return self.client.containers.get("mumu-hermes-api")

    def _ensure_network_sync(self, user_id: str):
        name = self.network_name(user_id)
        expected = self.user_key(user_id)
        try:
            network = self.client.networks.get(name)
            labels = network.attrs.get("Labels") or {}
            if labels.get(MANAGED_LABEL) != "true" or labels.get(USER_LABEL) != expected:
                raise RuntimeUnavailable(f"Docker network name collision: {name}")
        except NotFound:
            network = self.client.networks.create(
                name,
                driver="bridge",
                labels={MANAGED_LABEL: "true", TYPE_LABEL: "network", USER_LABEL: expected},
            )

        backend = self._backend_container()
        network.reload()
        connected = network.attrs.get("Containers") or {}
        if backend.id not in connected:
            try:
                network.connect(backend, aliases=["mumu-api"])
            except APIError as exc:
                if "already exists" not in str(exc).lower():
                    raise RuntimeUnavailable(f"Cannot connect backend to user network: {exc}") from exc
        return network

    async def ensure_network(self, user_id: str):
        return await asyncio.to_thread(self._ensure_network_sync, user_id)

    def _validate_container(self, container: Any, user_id: str, runtime_type: str) -> None:
        labels = container.labels or {}
        if (
            labels.get(MANAGED_LABEL) != "true"
            or labels.get(TYPE_LABEL) != runtime_type
            or labels.get(USER_LABEL) != self.user_key(user_id)
        ):
            raise RuntimeUnavailable(f"Container name collision: {container.name}")

    def _ensure_browser_sync(self, user_id: str):
        paths = self._ensure_user_dirs_sync(user_id)
        network = self._ensure_network_sync(user_id)
        name = self.browser_name(user_id)
        try:
            container = self.client.containers.get(name)
            self._validate_container(container, user_id, "browser")
            container.reload()
            if container.status != "running":
                container.start()
            return container
        except NotFound:
            pass

        try:
            return self.client.containers.run(
                self.settings.browser_image,
                detach=True,
                name=name,
                hostname=name,
                network=network.name,
                user="10000:10000",
                restart_policy={"Name": "no"},
                labels={MANAGED_LABEL: "true", TYPE_LABEL: "browser", USER_LABEL: self.user_key(user_id)},
                environment={
                    "BROWSER_CONTROLLER_TOKEN": self.browser_token(user_id),
                    "CDP_PUBLIC_HOST": name,
                    "HOME": "/profile",
                    "XDG_CONFIG_HOME": "/profile/.config",
                    "XDG_CACHE_HOME": "/profile/.cache",
                    "TZ": "Asia/Shanghai",
                    **({"BROWSER_PROXY_SERVER": self.settings.outbound_proxy_container_url}
                       if self.settings.outbound_proxy_enabled and self.settings.outbound_proxy_container_url else {}),
                },
                extra_hosts={"host.docker.internal": "host-gateway"},
                volumes={str(paths["host_browser"]): {"bind": "/profile", "mode": "rw"}},
                mem_limit=self.settings.browser_memory_limit,
                nano_cpus=int(self.settings.browser_cpu_limit * 1_000_000_000),
                shm_size="512m",
                pids_limit=512,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges"],
            )
        except (APIError, docker.errors.ImageNotFound) as exc:
            raise RuntimeUnavailable(f"Cannot start browser runtime: {getattr(exc, 'explanation', str(exc))}") from exc

    async def ensure_browser(self, user_id: str) -> str:
        lock = self._user_locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            container = await asyncio.to_thread(self._ensure_browser_sync, user_id)
            deadline = time.monotonic() + 60
            last_error = "browser controller did not respond"
            async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
                while time.monotonic() < deadline:
                    await asyncio.to_thread(container.reload)
                    if container.status in {"exited", "dead"}:
                        logs = await asyncio.to_thread(container.logs, tail=30)
                        detail = logs.decode("utf-8", errors="replace")[-1200:]
                        raise RuntimeUnavailable(f"Browser runtime exited during startup: {detail}")
                    try:
                        response = await client.get(f"http://{container.name}:3001/health")
                        if response.status_code == 200:
                            self._last_used[user_id] = time.monotonic()
                            return container.name
                        last_error = f"browser health returned {response.status_code}"
                    except httpx.HTTPError as exc:
                        last_error = str(exc)
                    await asyncio.sleep(1)
            raise RuntimeUnavailable(last_error)

    def _running_workers(self) -> list[Any]:
        return self.client.containers.list(
            all=True,
            filters={"label": [f"{MANAGED_LABEL}=true", f"{TYPE_LABEL}=worker"]},
        )

    def worker_capacity(self) -> dict[str, Any]:
        cpu_count = max(1, os.cpu_count() or 1)
        try:
            load_one = max(0.0, os.getloadavg()[0])
        except (AttributeError, OSError):
            load_one = 0.0
        memory_available = read_available_memory_bytes()
        memory_reserve = int(self.settings.hermes_memory_reserve_gib * GIB)
        memory_per_worker = int(self.settings.hermes_worker_memory_budget_gib * GIB)
        cpu_per_worker = self.settings.hermes_cpu_limit + self.settings.browser_cpu_limit
        current, cpu_limit, memory_limit = calculate_worker_limit(
            minimum=self.settings.hermes_min_active_workers,
            maximum=self.settings.hermes_max_active_workers,
            dynamic=self.settings.hermes_dynamic_workers,
            cpu_count=cpu_count,
            load_one=load_one,
            cpu_reserve=self.settings.hermes_cpu_reserve,
            cpu_per_worker=cpu_per_worker,
            memory_available_bytes=memory_available,
            memory_reserve_bytes=memory_reserve,
            memory_per_worker_bytes=memory_per_worker,
        )
        return {
            "workerLimit": current,
            "workerMin": self.settings.hermes_min_active_workers,
            "workerMax": self.settings.hermes_max_active_workers,
            "dynamicWorkers": self.settings.hermes_dynamic_workers,
            "resourceBasis": {
                "cpuCount": cpu_count,
                "load1": round(load_one, 2),
                "cpuReserve": self.settings.hermes_cpu_reserve,
                "cpuBudgetPerWorker": cpu_per_worker,
                "cpuLimit": cpu_limit,
                "memoryAvailableBytes": memory_available,
                "memoryReserveBytes": memory_reserve,
                "memoryBudgetPerWorkerBytes": memory_per_worker,
                "memoryLimit": memory_limit,
            },
        }

    def current_worker_limit(self) -> int:
        return int(self.worker_capacity()["workerLimit"])

    def _remove_container_sync(self, name: str, user_id: str, runtime_type: str) -> None:
        try:
            container = self.client.containers.get(name)
            self._validate_container(container, user_id, runtime_type)
            container.remove(force=True)
        except NotFound:
            return

    def _ensure_worker_sync(self, user_id: str, busy_user_ids: set[str]):
        paths = self._ensure_user_dirs_sync(user_id)
        network = self._ensure_network_sync(user_id)
        browser = self._ensure_browser_sync(user_id)
        name = self.worker_name(user_id)
        try:
            container = self.client.containers.get(name)
            self._validate_container(container, user_id, "worker")
            container.reload()
            if (container.labels or {}).get(SPEC_LABEL) != WORKER_SPEC_VERSION:
                container.remove(force=True)
            else:
                if container.status != "running":
                    container.start()
                return container
        except NotFound:
            pass

        workers = self._running_workers()
        if len(workers) >= self.current_worker_limit():
            busy_keys = {self.user_key(value) for value in busy_user_ids}
            candidates = [worker for worker in workers if (worker.labels or {}).get(USER_LABEL) not in busy_keys]
            if not candidates:
                raise WorkerPoolBusy("Hermes Worker 池已满，任务已进入队列")
            candidate = min(candidates, key=lambda item: item.attrs.get("State", {}).get("StartedAt", ""))
            candidate.remove(force=True)

        token = self.worker_api_key(user_id)
        browser_cdp_url = f"http://{browser.name}:3001/cdp"
        no_proxy = f"127.0.0.1,localhost,mumu-api,.local,.internal,{browser.name}"
        try:
            return self.client.containers.run(
                self.settings.hermes_image,
                command=["gateway", "run"],
                detach=True,
                name=name,
                hostname=name,
                working_dir="/workspace",
                network=network.name,
                restart_policy={"Name": "no"},
                labels={
                    MANAGED_LABEL: "true",
                    TYPE_LABEL: "worker",
                    USER_LABEL: self.user_key(user_id),
                    SPEC_LABEL: WORKER_SPEC_VERSION,
                },
                environment={
                    "API_SERVER_ENABLED": "true",
                    "API_SERVER_HOST": "0.0.0.0",
                    "API_SERVER_PORT": "8642",
                    "API_SERVER_KEY": token,
                    "MUMU_INFERENCE_TOKEN": token,
                    "BROWSER_CDP_URL": browser_cdp_url,
                    "TERMINAL_ENV": "local",
                    "TERMINAL_CWD": "/workspace",
                    "PYTHONUSERBASE": "/workspace/.local",
                    "PIP_TARGET": "/workspace/.python-packages",
                    "PYTHONPATH": "/workspace/.python-packages",
                    "NODE_PATH": "/usr/local/lib/node_modules",
                    "NPM_CONFIG_PREFIX": "/workspace/.npm-global",
                    "CARGO_HOME": "/workspace/.cargo",
                    "GOPATH": "/workspace/go",
                    "XDG_CACHE_HOME": "/workspace/.cache",
                    "UV_CACHE_DIR": "/workspace/.cache/uv",
                    "HERMES_WRITE_SAFE_ROOT": "/workspace",
                    "PATH": "/workspace/.local/bin:/workspace/.python-packages/bin:/workspace/.npm-global/bin:/workspace/.cargo/bin:/workspace/go/bin:/opt/hermes/bin:/opt/hermes/.venv/bin:/opt/data/.local/bin:/usr/local/bin:/usr/bin:/bin",
                    "HERMES_DASHBOARD": "0",
                    "HERMES_YOLO_MODE": "1",
                    "HERMES_UID": "10000",
                    "HERMES_GID": "10000",
                    "TZ": "Asia/Shanghai",
                    "NO_PROXY": no_proxy,
                    "no_proxy": no_proxy,
                    **({
                        "HTTP_PROXY": self.settings.outbound_proxy_container_url,
                        "HTTPS_PROXY": self.settings.outbound_proxy_container_url,
                        "ALL_PROXY": self.settings.outbound_proxy_container_url,
                    } if self.settings.outbound_proxy_enabled and self.settings.outbound_proxy_container_url else {}),
                },
                extra_hosts={"host.docker.internal": "host-gateway"},
                volumes={
                    str(paths["host_hermes"]): {"bind": "/opt/data", "mode": "rw"},
                    str(paths["host_workspace"]): {"bind": "/workspace", "mode": "rw"},
                    str(paths["host_attachments"]): {"bind": "/attachments", "mode": "ro"},
                },
                mem_limit=self.settings.hermes_memory_limit,
                nano_cpus=int(self.settings.hermes_cpu_limit * 1_000_000_000),
                shm_size="512m",
                pids_limit=768,
                security_opt=["no-new-privileges"],
            )
        except (APIError, docker.errors.ImageNotFound) as exc:
            raise RuntimeUnavailable(f"Cannot start Hermes Worker: {getattr(exc, 'explanation', str(exc))}") from exc

    async def ensure_worker(self, user_id: str, busy_user_ids: set[str]) -> str:
        async with self._pool_lock:
            container = await asyncio.to_thread(self._ensure_worker_sync, user_id, busy_user_ids)
        deadline = time.monotonic() + 90
        token = self.worker_api_key(user_id)
        async with httpx.AsyncClient(timeout=4, headers={"Authorization": f"Bearer {token}"}, trust_env=False) as client:
            while time.monotonic() < deadline:
                try:
                    response = await client.get(f"http://{container.name}:8642/health")
                    if response.status_code == 200:
                        self._last_used[user_id] = time.monotonic()
                        return container.name
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(1)
        raise RuntimeUnavailable("Hermes Worker 启动超时")

    def _exec_worker_sync(
        self,
        user_id: str,
        command: list[str],
        *,
        timeout_seconds: int = 180,
    ) -> dict[str, Any]:
        container = self.client.containers.get(self.worker_name(user_id))
        self._validate_container(container, user_id, "worker")
        bounded_timeout = max(5, min(timeout_seconds, 900))
        result = container.exec_run(
            ["timeout", "--signal=TERM", f"{bounded_timeout}s", *command],
            workdir="/workspace",
            environment={"TERM": "dumb", "NO_COLOR": "1"},
            demux=False,
        )
        raw = result.output or b""
        output = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
        self._last_used[user_id] = time.monotonic()
        return {"exitCode": int(result.exit_code), "output": output[-100_000:]}

    async def exec_worker(
        self,
        user_id: str,
        command: list[str],
        *,
        timeout_seconds: int = 180,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._exec_worker_sync,
            user_id,
            command,
            timeout_seconds=timeout_seconds,
        )

    def open_terminal(self, user_id: str, columns: int = 100, rows: int = 28):
        name = self.worker_name(user_id)
        try:
            container = self.client.containers.get(name)
            self._validate_container(container, user_id, "worker")
            exec_result = self.client.api.exec_create(
                container.id,
                cmd=["/bin/bash", "-l"],
                stdin=True,
                stdout=True,
                stderr=True,
                tty=True,
                workdir="/workspace",
                environment={"TERM": "xterm-256color", "COLORTERM": "truecolor"},
            )
            exec_id = str(exec_result["Id"])
            stream = self.client.api.exec_start(exec_id, tty=True, socket=True)
            self.client.api.exec_resize(exec_id, height=max(2, rows), width=max(10, columns))
            self._last_used[user_id] = time.monotonic()
            return exec_id, stream
        except (APIError, NotFound, KeyError) as exc:
            raise RuntimeUnavailable(f"无法打开终端: {exc}") from exc

    def resize_terminal(self, exec_id: str, columns: int, rows: int) -> None:
        try:
            self.client.api.exec_resize(
                exec_id,
                height=max(2, min(rows, 300)),
                width=max(10, min(columns, 500)),
            )
        except APIError:
            pass

    def _list_ports_sync(self, user_id: str) -> list[int]:
        name = self.worker_name(user_id)
        try:
            container = self.client.containers.get(name)
            self._validate_container(container, user_id, "worker")
            result = container.exec_run(
                ["/bin/sh", "-lc", "ss -ltnH 2>/dev/null || netstat -ltn 2>/dev/null || true"],
                workdir="/workspace",
            )
            output = (result.output or b"").decode("utf-8", errors="replace")
            if not output.strip():
                result = container.exec_run(["python3", "-c", _PROC_NET_PORTS], workdir="/workspace")
                output = (result.output or b"").decode("utf-8", errors="replace")
        except (APIError, NotFound) as exc:
            raise RuntimeUnavailable(f"无法读取开放端口: {exc}") from exc
        self._last_used[user_id] = time.monotonic()
        return parse_listening_ports(output)

    async def list_ports(self, user_id: str) -> list[int]:
        return await asyncio.to_thread(self._list_ports_sync, user_id)

    async def controller_request(self, user_id: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        name = await self.ensure_browser(user_id)
        try:
            async with httpx.AsyncClient(timeout=55, trust_env=False) as client:
                response = await client.post(
                    f"http://{name}:3001{path}",
                    headers={"X-Browser-Token": self.browser_token(user_id)},
                    json=payload,
                )
            response.raise_for_status()
            value = response.json()
            if not isinstance(value, dict):
                raise RuntimeUnavailable("Browser controller returned invalid JSON")
            self._last_used[user_id] = time.monotonic()
            return value
        except (httpx.HTTPError, ValueError) as exc:
            raise RuntimeUnavailable(f"Browser controller request failed: {exc}") from exc

    async def ensure_page(self, user_id: str, conversation_id: str) -> dict[str, Any]:
        return await self.controller_request(user_id, "/pages/ensure", {"conversationId": conversation_id})

    async def browser_action(self, user_id: str, conversation_id: str, action: str) -> dict[str, Any]:
        return await self.controller_request(
            user_id,
            "/pages/action",
            {"conversationId": conversation_id, "action": action},
        )

    async def close_page(self, user_id: str, conversation_id: str) -> None:
        def existing_browser_name() -> str:
            try:
                container = self.client.containers.get(self.browser_name(user_id))
                self._validate_container(container, user_id, "browser")
                container.reload()
                return container.name if container.status == "running" else ""
            except (APIError, NotFound, RuntimeUnavailable):
                return ""

        name = await asyncio.to_thread(existing_browser_name)
        if not name:
            return
        try:
            async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
                response = await client.post(
                    f"http://{name}:3001/pages/close",
                    headers={"X-Browser-Token": self.browser_token(user_id)},
                    json={"conversationId": conversation_id},
                )
            response.raise_for_status()
        except httpx.HTTPError:
            pass

    async def vnc_url(self, user_id: str, conversation_id: str) -> str:
        name = await self.ensure_browser(user_id)
        await self.ensure_page(user_id, conversation_id)
        return f"ws://{name}:6080/websockify"

    async def stop_user_runtimes(self, user_id: str) -> None:
        await asyncio.to_thread(self._remove_container_sync, self.worker_name(user_id), user_id, "worker")
        await asyncio.to_thread(self._remove_container_sync, self.browser_name(user_id), user_id, "browser")
        self._last_used.pop(user_id, None)

    async def stop_worker(self, user_id: str) -> None:
        await asyncio.to_thread(self._remove_container_sync, self.worker_name(user_id), user_id, "worker")
        self._last_used.pop(user_id, None)

    def mark_used(self, user_id: str) -> None:
        self._last_used[user_id] = time.monotonic()

    async def cleanup_idle(self, known_user_ids: list[str], busy_user_ids: set[str]) -> None:
        now = time.monotonic()
        for user_id in known_user_ids:
            if user_id in busy_user_ids:
                continue
            last_used = self._last_used.get(user_id)
            if last_used is None:
                continue
            if now - last_used >= self.settings.hermes_idle_minutes * 60:
                await asyncio.to_thread(
                    self._remove_container_sync,
                    self.worker_name(user_id),
                    user_id,
                    "worker",
                )
            if now - last_used >= self.settings.browser_idle_minutes * 60:
                await asyncio.to_thread(
                    self._remove_container_sync,
                    self.browser_name(user_id),
                    user_id,
                    "browser",
                )
                self._last_used.pop(user_id, None)

    async def remove_user(self, user_id: str) -> None:
        await self.stop_user_runtimes(user_id)
        network_name = self.network_name(user_id)

        def remove_network_and_storage() -> None:
            try:
                network = self.client.networks.get(network_name)
                labels = network.attrs.get("Labels") or {}
                if labels.get(MANAGED_LABEL) == "true" and labels.get(USER_LABEL) == self.user_key(user_id):
                    try:
                        network.disconnect(self._backend_container(), force=True)
                    except (APIError, NotFound):
                        pass
                    network.remove()
            except NotFound:
                pass

            root = self._container_root(user_id).resolve()
            users_root = (self.settings.data_dir / "users").resolve()
            if root.parent == users_root and root.name == self.user_key(user_id) and root.exists():
                shutil.rmtree(root)

        await asyncio.to_thread(remove_network_and_storage)

    def runtime_summary(self) -> dict[str, Any]:
        workers = self.client.containers.list(all=True, filters={"label": [f"{MANAGED_LABEL}=true", f"{TYPE_LABEL}=worker"]})
        browsers = self.client.containers.list(all=True, filters={"label": [f"{MANAGED_LABEL}=true", f"{TYPE_LABEL}=browser"]})
        return {
            **self.worker_capacity(),
            "workers": [{"name": item.name, "status": item.status, "userKey": (item.labels or {}).get(USER_LABEL, "")} for item in workers],
            "browsers": [{"name": item.name, "status": item.status, "userKey": (item.labels or {}).get(USER_LABEL, "")} for item in browsers],
        }
