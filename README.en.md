<p align="center">
  <img src="frontend/public/assets/site-logo.jpg" alt="Miaoxiang logo" width="144">
</p>

<h1 align="center">Miaoxiang AI Workspace</h1>

<p align="center">A self-hosted workspace for Chat, Hermes agents, browser-backed research, scheduled tasks, and multi-client collaboration</p>

<p align="center">
  <a href="README.md">中文</a> ·
  <a href="README.en.md">English</a> ·
  <a href="README.ja.md">日本語</a>
</p>

## Live demo

- Demo: [http://ai.vmss.cn/](http://ai.vmss.cn/)
- To request a test activation code, email [vrhjio4405@163.com](mailto:vrhjio4405@163.com).
- Agent mode is CPU-, memory-, and browser-intensive, so self-hosting is recommended for sustained use. In your request, state that you **accept the capacity, availability, and resource limits of the shared test environment**.

> The demo is for evaluation only. Do not upload credentials, secrets, private documents, or other sensitive data.

## Screenshots

### Desktop Agent workspace

![Desktop Agent workspace](docs/images/workspace-desktop.png)

### Mobile workspace and Top Dock settings

<table>
  <tr>
    <td width="50%"><img src="docs/images/workspace-mobile.png" alt="Mobile Agent workspace"></td>
    <td width="50%"><img src="docs/images/settings-dock.png" alt="Top Dock visibility settings"></td>
  </tr>
</table>

### Administrator model routing

![Administrator model routing](docs/images/admin-model-routing.png)

All screenshots use fixture accounts and demo tasks. They contain no real conversations, API keys, or production data.

## Overview

Miaoxiang is more than a chat shell or a thin search wrapper. It combines ordinary Chat, Hermes agents, a real browser, coordinator and executor models, artifact acceptance, scheduled tasks, and multiple clients under one account and permission system. It is intended for self-hosted research, document creation, development workflows, web operations, and controlled desktop automation.

## Why it is different

- **Browser-backed research:** agents can use isolated Chromium, CDP, screenshots, and page interaction instead of relying only on model memory or search summaries.
- **Long-running Hermes work:** per-user workspaces provide files, terminals, skills, persistent jobs, and Cron tasks.
- **Role-specific model routing:** Chat, coordinator, and executor connections can be configured independently. Planning and acceptance do not have to use the same model as tool execution.
- **Artifact quality gates:** PPTX, DOCX, video, and other deliverables have explicit file contracts. Missing or invalid artifacts can trigger a revision pass.
- **Evidence-first workflows:** research jobs can require browser inspection and source checks before producing reports or documents.
- **Selective memory:** new conversations remain clean by default and retrieve only relevant preferences when needed.
- **Shared multi-client state:** Web, Android, Windows, and WeChat clients share accounts, conversations, and permissions.
- **Controlled computer automation:** the Windows agent can combine UI Automation, OCR, visual screenshots, and optional ADB with approvals and an emergency stop.
- **Server-side Basic/VIP separation:** Basic users receive the administrator-approved Chat model; Agent tools are available only to authenticated VIP users.
- **Per-deployment cryptographic isolation:** each installation creates independent application, runtime, and activation secrets.

## Architecture

```text
Web / Android / Windows / WeChat
                 |
              FastAPI
       +---------+----------+
       |         |          |
     Chat    Coordinator  Task Queue
                            |
                    Isolated Hermes Worker
                       |             |
                 Browser Runtime   Workspace
```

| Directory | Responsibility |
| --- | --- |
| `backend/` | FastAPI, SQLite, authentication, permissions, model routing, scheduling, and acceptance |
| `frontend/` | React/Vite responsive workspace and separate admin console |
| `hermes-worker/` | Isolated Hermes worker, bundled skills, and browser tool wrappers |
| `browser-runtime/` | Chromium, CDP, VNC/noVNC runtime |
| `android-app/` | Native Android shell, secure session, notifications, files, and updates |
| `windows-client/` | Windows workspace and UIA/OCR/ADB computer agent |
| `wechat-mini-program/` | WeChat login gate and workspace shell |
| `activation-manager/` | Desktop activation-code manager using the admin API |
| `proxy-bridge/` | Optional host proxy bridge |

## Quick start

Requirements: Linux, Bash, Docker Engine, and Docker Compose v2. Dependencies, runtime data, and compiled clients are not included in the source archive.

```bash
unzip miaoxiang.zip
cd miaoxiang
bash scripts/start.sh
```

If `.env` does not exist, `start.sh` launches the interactive initializer. Later runs start directly. The initializer requests:

1. application name, deployment directory, web port, and public HTTPS origin;
2. administrator username and password;
3. executor-model API URL, model name, and API key;
4. whether a separate Chat model is required, plus its connection details;
5. whether the coordinator model is required and enabled, plus its connection details;
6. optional SMTP configuration;
7. optional WeChat AppID, AppSecret, and cloud bridge configuration.

Values such as `https://api.example.com/v1`, `your-model-name`, and `sk-your-key` are placeholders only.

```bash
bash scripts/start.sh   # initialize when needed, then start
bash scripts/status.sh  # inspect this deployment
bash scripts/stop.sh    # stop this deployment and retain persistent data
```

## Models and permissions

Model connections use an OpenAI Chat Completions-compatible interface.

| Role | Purpose | Initial relationship |
| --- | --- | --- |
| Chat | Guest, Basic, and ordinary conversations | Separate connection or executor fallback |
| Coordinator | Planning, delegation, final acceptance, and revision guidance | Optional separate connection |
| Executor | Agent tools and task execution | Primary model |

Basic users can only access Chat with models allowed by the administrator. Agent mode, browser, terminal, files, schedules, and computer control require both authentication and an active VIP entitlement. Administrators can override user permissions and update all three model routes independently.

## Rotating activation protection

- First-run initialization generates a deployment-local `ACTIVATION_SECRET`; it is never hard-coded in source.
- Every backend start creates a new cryptographic epoch and randomly selects a new activation-code digest scheme and registration-link authentication scheme. Each is guaranteed to differ from its previous scheme.
- The implementation selects from HMAC-SHA-256, HMAC-SHA3-256, and keyed BLAKE2 families, with separate digest and authentication sets.
- Every epoch receives a random salt and derives its keys from the deployment root secret. The database never stores plaintext activation codes.
- Private epoch state lives in the Git-ignored runtime data directory and is integrity-authenticated. Historical epochs remain available to validate previously issued, still-valid codes.
- Root secrets and epoch state differ between machines, so codes cannot be moved between deployments made from the same source archive.

## Security and privacy

- Never commit `.env`, databases, conversations, logs, user workspaces, model URLs, API keys, signing files, or build artifacts.
- Initialization generates application, activation, browser-runtime, Hermes, and optional WeChat bridge secrets. `.env` is written with mode `0600`.
- Model API keys are sealed at rest; admin responses expose only whether a key is configured.
- Keep backend, CDP, VNC, and worker ports inside the container network. Expose only the web entry point.
- Hermes state, browser profiles, attachments, and workspaces are isolated per user.
- Enable GitHub secret scanning and audit Git history before publication. Removing a current file does not erase a secret from old commits.

## Client configuration

- Web: set `VITE_PUBLIC_APP_ORIGIN=https://your-domain.example` at build time.
- Android: set `AICHAT_PUBLIC_ORIGIN=https://your-domain.example`; provide signing information through environment variables only.
- Windows: set `MIAOXIANG_SERVER_URL=https://your-domain.example` before first run or override it with `--server`.
- WeChat Mini Program: configure the public origin and cloud environment before release; inject private values with platform environment variables.

## Upstream projects

- [Hermes Agent](https://github.com/NousResearch/hermes-agent)
- [noVNC](https://github.com/novnc/noVNC)
- [Playwright](https://playwright.dev/)

## License and responsibility

Use the project under the license shipped with the repository. Operators are responsible for protecting model credentials, user data, and public endpoints, and for following the terms of connected model and automation services.
