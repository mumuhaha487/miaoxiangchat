# Miaoxiang AI Workspace / 妙想之地

[中文](#中文) | [English](#english) | [日本語](#日本語)

Miaoxiang is a self-hosted AI workspace that combines ordinary chat, Hermes agents,
browser-backed research, scheduled tasks, artifact generation, and controlled desktop
automation. This repository contains source code only. Runtime data, conversations,
credentials, model endpoints, dependencies, signing material, and compiled clients are
intentionally excluded.

## 中文

### 项目定位

妙想之地不是单纯的聊天壳，也不是只会机械调用搜索接口的 Agent。它将 Chat、
Hermes Agent、真实浏览器、统筹模型、质量验收、定时任务和多端工作台放在同一套
权限与会话体系中，适合需要持续研究、文档生产、自动化和远程协作的自托管场景。

### 核心优势

- **真实浏览器研究**：Agent 可通过隔离 Chromium、CDP、截图和页面交互查询资料，
  不只依赖模型记忆或单一搜索摘要。研究型任务可要求浏览器证据后再生成结论。
- **Hermes 长任务能力**：每个用户拥有隔离工作区，支持终端、文件、浏览器、技能、
  持久任务和 Cron 定时任务；复杂工作可以持续执行，而不是在一次请求后中断。
- **统筹、执行与 Chat 分工**：管理员可以让 Chat、统筹和执行使用不同的
  OpenAI-compatible 模型。统筹模型负责拆解、约束和最终验收，执行模型负责工具与
  产出，普通 Chat 保持低延迟和低成本。
- **多维质量门控**：PPT、DOCX、视频等混合交付物使用明确的文件类型契约；缺少文件、
  类型错误或验收不通过时可进入修订流程，降低“要求 PPT 却只得到 DOC”的概率。
- **选择性跨会话记忆**：新对话默认保持干净，由模型判断是否需要检索历史偏好以及
  注入哪些记忆，减少上下文污染和无意义 token 消耗。
- **统一多端体验**：Web、Android、Windows 和微信小程序复用同一套账户、会话和权限；
  客户端只负责安全存储、通知、下载、系统桥接等原生能力。
- **受控电脑操作**：Windows Agent 使用 UI Automation、OCR、截图视觉和可选 ADB，
  高风险操作需要批准，并保留托盘急停和本地输入暂停机制。
- **Basic/VIP 权限隔离**：Basic 用户只能使用管理员指定的 Chat 模型；VIP 才能启用
  Agent、浏览器、文件、定时任务和电脑控制。管理员可独立调整用户权限和模型。
- **部署级密钥隔离**：首次初始化在当前机器生成独立的会话密钥、内部服务密钥和
  激活根密钥。不同机器即使使用同一份源码，也不会共享可互换的激活码或内部令牌。

### 架构

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

- `backend/`：FastAPI、SQLite、认证、权限、模型路由、任务调度和质量验收。
- `frontend/`：React/Vite 响应式工作台和独立管理员后台。
- `hermes-worker/`：隔离 Hermes Worker、内置技能和浏览器工具包装。
- `browser-runtime/`：Chromium、CDP、VNC/noVNC 运行环境。
- `android-app/`：Android 原生容器、安全会话、通知、文件和在线更新。
- `windows-client/`：Windows 桌面工作台、UIA/OCR/ADB Computer Agent。
- `wechat-mini-program/`：微信原生登录门和工作台容器。
- `activation-manager/`：通过管理 API 创建和管理 VIP 激活码的桌面工具。
- `proxy-bridge/`：可选的宿主机代理桥。

### 首次启动

要求：Linux、Bash、Docker Engine、Docker Compose v2。源码包不包含依赖或构建产物。

```bash
unzip miaoxiang.zip
cd miaoxiang
bash scripts/start.sh
```

当 `.env` 不存在时，`start.sh` 会自动调用 `scripts/initialize.sh`。初始化过程会询问：

1. 应用名称、部署路径、Web 端口和公网 HTTPS 地址；
2. 管理员用户名和管理员密码；
3. 主执行模型的 API URL、模型名和 API Key；
4. 是否单独配置 Chat 模型；
5. 是否开启独立统筹模型及其 URL、模型名和 API Key；
6. 可选 SMTP 邮箱服务；
7. 可选微信 AppID、AppSecret 和云函数桥接配置。

以下密钥不会要求操作者手工复用，初始化时会为当前部署分别随机生成：

- `APP_SECRET`：登录会话、可信设备和服务端摘要；
- `ACTIVATION_SECRET`：VIP 激活码及注册链接；
- `INTERNAL_BROWSER_KEY`：浏览器运行时内部认证；
- `HERMES_API_KEY`：Hermes Worker 内部认证；
- `WECHAT_CLOUD_BRIDGE_SECRET`：仅在启用微信登录时生成。

`.env` 权限会设置为 `0600`，`data/users` 设置为 `0700`。不要提交 `.env`、数据库、
日志、用户工作区、APK/EXE、签名文件或任何导出数据。

常用命令：

```bash
bash scripts/start.sh   # 初始化（如有需要）并启动
bash scripts/status.sh  # 查看项目容器和动态运行时
bash scripts/stop.sh    # 停止本项目容器，保留持久数据
```

### 模型配置

所有模型连接均为 OpenAI Chat Completions 兼容接口。README 和 `.env.example` 中的
`https://api.example.com/v1`、`your-model-name`、`sk-your-key` 都只是格式示例。

| 角色 | 用途 | 初始配置 |
| --- | --- | --- |
| Chat | 访客、Basic 与普通对话 | 可独立配置或继承主模型 |
| Coordinator | 计划、分工、验收、修订引导 | 可关闭或使用独立模型 |
| Executor | Agent 工具调用和任务执行 | 主模型 |

管理员可在后台再次更新三类连接。API Key 加密保存，管理接口只返回“是否已配置”，
不会返回密钥明文。

### 客户端构建配置

- Web：构建时可设置 `VITE_PUBLIC_APP_ORIGIN=https://your-domain.example`。
- Android：构建时设置 `AICHAT_PUBLIC_ORIGIN=https://your-domain.example`；签名信息只从
  `AICHAT_KEYSTORE` 和 `AICHAT_KEYSTORE_PASSWORD` 环境变量读取。
- Windows：首次运行前设置 `MIAOXIANG_SERVER_URL=https://your-domain.example`，也可以
  使用 `--server` 覆盖。
- 微信小程序：发布前修改 `wechat-mini-program/utils/config.js` 中的公开域名和云环境 ID，
  云函数秘密通过平台环境变量注入。

### 安全与隐私

- 对外只需暴露 Web 入口；后端、CDP、VNC 和 Worker 内部连接应保留在 Docker 网络。
- Windows 凭据使用当前用户 DPAPI；服务端模型密钥使用部署密钥派生后密封保存。
- 激活码数据库只保存带部署密钥的摘要，不保存可再次读取的完整激活码。
- 每个用户的 Hermes、浏览器配置、附件和工作区按用户隔离。
- Git 忽略规则默认排除依赖、运行数据、密钥、聊天数据库和构建产物。
- 公开仓库前仍应使用 GitHub secret scanning，并复核历史提交；删除当前文件不能清除
  旧 commit 中已经提交过的秘密。

### 动态激活算法说明

当前源码已经把激活根密钥与应用会话密钥分离，并在每次首次初始化时随机生成，因此
不同部署的激活码不能互用。后续“多种变换方式随机选择”的算法接口将在规则确定后再
实现；在此之前不会把未确认的算法写死或宣称已完成。

## English

### Overview

Miaoxiang is a self-hosted workspace for Chat and Hermes agents. It adds browser-backed
research, scheduled execution, role-based model routing, artifact acceptance, selective
memory, and controlled desktop automation to a shared multi-client account system.

### Why it is different

- Research can be performed in an isolated real browser instead of relying only on model
  memory or search snippets.
- Hermes workers support files, terminal tools, skills, long-running work, and Cron tasks.
- Chat, coordinator, and executor models can be configured independently.
- Required artifacts are checked by type and missing deliverables can trigger revision.
- Cross-conversation memory is selected by an LLM and is not injected by default.
- Basic and VIP capabilities are separated on the server, not merely hidden in the UI.
- Web, Android, Windows, and WeChat clients share accounts, conversations, and permissions.
- Every installation receives independent session, runtime, and activation secrets.

### Quick start

```bash
unzip miaoxiang.zip
cd miaoxiang
bash scripts/start.sh
```

On the first run, an interactive initializer creates `.env`, asks for administrator and
model settings, optionally configures SMTP and WeChat, and generates deployment-specific
secrets. Later runs start directly. Use `scripts/status.sh` and `scripts/stop.sh` for status
and shutdown. Never commit `.env`, `data/`, credentials, databases, logs, or binaries.

All URLs and keys in examples are placeholders. Client origins must be supplied at build
or first-run time as described in the Chinese section.

## 日本語

### 概要

妙想之地は、通常の Chat、Hermes Agent、実ブラウザー調査、定期タスク、成果物検査、
選択的メモリー、Windows 操作を一つにまとめたセルフホスト型 AI ワークスペースです。

### 特長

- 隔離された Chromium を利用し、モデルの記憶だけに依存しない調査ができます。
- Hermes Worker はファイル、端末、スキル、長時間タスク、Cron を扱えます。
- Chat・統括・実行モデルを個別に設定できます。
- PPT/DOCX などの必須成果物を種類ごとに検査し、不足時は修正工程へ戻せます。
- 会話をまたぐメモリーは LLM が必要なものだけを選びます。
- Basic/VIP 権限はサーバー側で分離されています。
- Web、Android、Windows、WeChat で同じアカウントと会話を共有します。
- 初期化ごとに導入固有の秘密鍵を生成し、別の導入環境とは共有しません。

### 起動

```bash
unzip miaoxiang.zip
cd miaoxiang
bash scripts/start.sh
```

初回のみ対話式の初期化が実行され、管理者、モデル、任意の SMTP/WeChat 設定を入力
します。実際の URL、API Key、パスワードはソースコードに含まれていません。

## Upstream projects

- [Hermes Agent](https://github.com/NousResearch/hermes-agent)
- [noVNC](https://github.com/novnc/noVNC)
- [Playwright](https://playwright.dev/)
