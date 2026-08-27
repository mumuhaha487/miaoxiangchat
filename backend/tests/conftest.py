from __future__ import annotations

import os


os.environ.setdefault("APP_NAME", "MUMU Browser AI Test")
os.environ.setdefault("APP_SECRET", "test-app-secret-" + "a" * 40)
os.environ.setdefault("ACTIVATION_SECRET", "test-activation-secret-" + "f" * 40)
os.environ.setdefault("INTERNAL_BROWSER_KEY", "test-browser-secret-" + "b" * 40)
os.environ.setdefault("HERMES_API_KEY", "test-hermes-key-" + "c" * 40)
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-password")
os.environ.setdefault("ADMIN_USERNAME", "project-admin")
os.environ.setdefault("DATA_DIR", "/tmp/mumu-browser-agent-tests")
os.environ.setdefault("PROJECT_HOST_DIR", "/tmp/mumu-browser-agent-tests-project")
os.environ.setdefault("SMTP_USERNAME", "admin@example.com")
os.environ.setdefault("PUBLIC_APP_ORIGIN", "https://example.com")
os.environ.setdefault("LLM_BASE_URL", "https://llm.example.test/v1")
os.environ.setdefault("LLM_API_KEY", "test-llm-key-" + "d" * 40)
os.environ.setdefault("LLM_MODEL", "test-chat-model")
os.environ.setdefault("WECHAT_APP_ID", "wx-test-app-id")
os.environ.setdefault("WECHAT_APP_SECRET", "test-wechat-secret")
os.environ.setdefault("WECHAT_CLOUD_BRIDGE_SECRET", "test-wechat-cloud-bridge-" + "e" * 40)
