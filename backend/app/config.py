from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


def _required(name: str, minimum: int = 1) -> str:
    value = os.getenv(name, "").strip()
    if len(value) < minimum:
        raise RuntimeError(f"{name} is required and must contain at least {minimum} characters")
    return value


def _bool(name: str, default: bool) -> bool:
    fallback = "true" if default else "false"
    return os.getenv(name, fallback).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    app_name: str
    app_secret: str
    activation_secret: str
    internal_browser_key: str
    internal_hermes_key: str
    admin_username: str
    admin_password: str
    data_dir: Path
    project_host_dir: Path
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    smtp_from: str
    registration_enabled: bool
    public_app_origin: str
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    model_split_enabled: bool
    chat_llm_base_url: str
    chat_llm_api_key: str
    chat_llm_model: str
    coordinator_llm_base_url: str
    coordinator_llm_api_key: str
    coordinator_llm_model: str
    llm_context_length: int
    llm_max_retries: int
    llm_concurrency_limit: int
    llm_verify_tls: bool
    llm_proxy: str
    outbound_proxy_enabled: bool
    outbound_proxy_url: str
    outbound_proxy_container_url: str
    hermes_image: str
    hermes_max_turns: int
    hermes_gateway_timeout: int
    hermes_gateway_notify_interval: int
    hermes_dynamic_workers: bool
    hermes_min_active_workers: int
    hermes_max_active_workers: int
    hermes_memory_reserve_gib: float
    hermes_worker_memory_budget_gib: float
    hermes_cpu_reserve: float
    hermes_idle_minutes: int
    hermes_memory_limit: str
    hermes_cpu_limit: float
    browser_image: str
    browser_idle_minutes: int
    browser_memory_limit: str
    browser_cpu_limit: float
    upload_max_bytes: int
    expert_quality_threshold: int
    expert_max_revisions: int
    expert_max_review_images: int
    builtin_skills_dir: Path
    wechat_app_id: str
    wechat_app_secret: str
    wechat_cloud_bridge_secret: str

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = Path(os.getenv("DATA_DIR", "/app/data")).resolve()
        hermes_min_active_workers = max(2, int(os.getenv("HERMES_MIN_ACTIVE_WORKERS", "2")))
        hermes_max_active_workers = max(
            hermes_min_active_workers,
            int(os.getenv("HERMES_MAX_ACTIVE_WORKERS", "8")),
        )
        return cls(
            app_name=os.getenv("APP_NAME", "妙想之地").strip() or "妙想之地",
            app_secret=_required("APP_SECRET", 32),
            activation_secret=_required("ACTIVATION_SECRET", 32),
            internal_browser_key=_required("INTERNAL_BROWSER_KEY", 32),
            internal_hermes_key=_required("HERMES_API_KEY", 32),
            admin_username=os.getenv("ADMIN_USERNAME", "project-admin").strip() or "project-admin",
            admin_password=_required("ADMIN_PASSWORD", 8),
            data_dir=data_dir,
            project_host_dir=Path(os.getenv("PROJECT_HOST_DIR", "/opt/miaoxiang")).resolve(),
            smtp_host=os.getenv("SMTP_HOST", "smtp.example.com").strip(),
            smtp_port=int(os.getenv("SMTP_PORT", "465")),
            smtp_username=os.getenv("SMTP_USERNAME", "").strip(),
            smtp_password=os.getenv("SMTP_PASSWORD", "").strip(),
            smtp_from=os.getenv("SMTP_FROM", "").strip(),
            registration_enabled=_bool("REGISTRATION_ENABLED", True),
            public_app_origin=_required("PUBLIC_APP_ORIGIN").rstrip("/"),
            llm_base_url=_required("LLM_BASE_URL").rstrip("/"),
            llm_api_key=_required("LLM_API_KEY", 16),
            llm_model=_required("LLM_MODEL"),
            model_split_enabled=_bool("MODEL_SPLIT_ENABLED", False),
            chat_llm_base_url=os.getenv("CHAT_LLM_BASE_URL", os.getenv("LLM_BASE_URL", "")).strip().rstrip("/"),
            chat_llm_api_key=os.getenv("CHAT_LLM_API_KEY", os.getenv("LLM_API_KEY", "")).strip(),
            chat_llm_model=os.getenv("CHAT_LLM_MODEL", os.getenv("LLM_MODEL", "")).strip(),
            coordinator_llm_base_url=os.getenv(
                "COORDINATOR_LLM_BASE_URL", os.getenv("LLM_BASE_URL", "")
            ).strip().rstrip("/"),
            coordinator_llm_api_key=os.getenv(
                "COORDINATOR_LLM_API_KEY", os.getenv("LLM_API_KEY", "")
            ).strip(),
            coordinator_llm_model=os.getenv(
                "COORDINATOR_LLM_MODEL", os.getenv("LLM_MODEL", "")
            ).strip(),
            llm_context_length=max(64_000, int(os.getenv("LLM_CONTEXT_LENGTH", "131072"))),
            llm_max_retries=max(8, min(12, int(os.getenv("LLM_MAX_RETRIES", "8")))),
            llm_concurrency_limit=max(2, min(32, int(os.getenv("LLM_CONCURRENCY_LIMIT", "12")))),
            llm_verify_tls=_bool("LLM_VERIFY_TLS", True),
            llm_proxy=os.getenv("LLM_PROXY", "").strip(),
            outbound_proxy_enabled=_bool("OUTBOUND_PROXY_ENABLED", True),
            outbound_proxy_url=os.getenv("OUTBOUND_PROXY_URL", "http://127.0.0.1:10808").strip(),
            outbound_proxy_container_url=os.getenv(
                "OUTBOUND_PROXY_CONTAINER_URL", "http://host.docker.internal:10809"
            ).strip(),
            hermes_image=os.getenv("HERMES_IMAGE", "mumu-hermes-worker:local").strip(),
            hermes_max_turns=max(500, int(os.getenv("HERMES_MAX_TURNS", "1200"))),
            hermes_gateway_timeout=max(0, int(os.getenv("HERMES_GATEWAY_TIMEOUT", "43200"))),
            hermes_gateway_notify_interval=max(
                0, int(os.getenv("HERMES_GATEWAY_NOTIFY_INTERVAL", "180"))
            ),
            hermes_dynamic_workers=_bool("HERMES_DYNAMIC_WORKERS", True),
            hermes_min_active_workers=hermes_min_active_workers,
            hermes_max_active_workers=hermes_max_active_workers,
            hermes_memory_reserve_gib=max(1.0, float(os.getenv("HERMES_MEMORY_RESERVE_GIB", "4"))),
            hermes_worker_memory_budget_gib=max(
                0.5,
                float(os.getenv("HERMES_WORKER_MEMORY_BUDGET_GIB", "1.5")),
            ),
            hermes_cpu_reserve=max(1.0, float(os.getenv("HERMES_CPU_RESERVE", "2"))),
            hermes_idle_minutes=max(5, int(os.getenv("HERMES_IDLE_MINUTES", "30"))),
            hermes_memory_limit=os.getenv("HERMES_MEMORY_LIMIT", "3g").strip(),
            hermes_cpu_limit=max(0.5, float(os.getenv("HERMES_CPU_LIMIT", "2"))),
            browser_image=os.getenv("BROWSER_IMAGE", "mumu-browser-runtime:local").strip(),
            browser_idle_minutes=max(5, int(os.getenv("BROWSER_IDLE_MINUTES", "60"))),
            browser_memory_limit=os.getenv("BROWSER_MEMORY_LIMIT", "1100m").strip(),
            browser_cpu_limit=max(0.25, float(os.getenv("BROWSER_CPU_LIMIT", "1"))),
            upload_max_bytes=max(1_048_576, int(os.getenv("UPLOAD_MAX_BYTES", str(100 * 1024 * 1024)))),
            expert_quality_threshold=max(60, min(100, int(os.getenv("EXPERT_QUALITY_THRESHOLD", "82")))),
            expert_max_revisions=max(0, min(5, int(os.getenv("EXPERT_MAX_REVISIONS", "3")))),
            expert_max_review_images=max(1, min(300, int(os.getenv("EXPERT_MAX_REVIEW_IMAGES", "200")))),
            builtin_skills_dir=Path(os.getenv("BUILTIN_SKILLS_DIR", "/app/builtin-skills")).resolve(),
            wechat_app_id=os.getenv("WECHAT_APP_ID", "").strip(),
            wechat_app_secret=os.getenv("WECHAT_APP_SECRET", "").strip(),
            wechat_cloud_bridge_secret=os.getenv("WECHAT_CLOUD_BRIDGE_SECRET", "").strip(),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
