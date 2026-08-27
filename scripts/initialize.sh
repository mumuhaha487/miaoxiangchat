#!/usr/bin/env bash
set -Eeuo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"
umask 077

if [[ -s .env ]]; then
  echo "Project already initialized: $project_dir/.env"
  exit 0
fi

random_hex() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
  else
    od -An -N32 -tx1 /dev/urandom | tr -d ' \n'
  fi
}

ask() {
  local variable="$1" prompt="$2" default_value="${3:-}" value=""
  if [[ -n "$default_value" ]]; then
    read -r -p "$prompt [$default_value]: " value
    value="${value:-$default_value}"
  else
    while [[ -z "$value" ]]; do read -r -p "$prompt: " value; done
  fi
  printf -v "$variable" '%s' "$value"
}

ask_secret() {
  local variable="$1" prompt="$2" value="" confirm=""
  while [[ -z "$value" || "$value" != "$confirm" ]]; do
    read -r -s -p "$prompt: " value; echo
    read -r -s -p "Confirm / 确认: " confirm; echo
    [[ "$value" == "$confirm" ]] || echo "Values do not match / 两次输入不一致。" >&2
  done
  printf -v "$variable" '%s' "$value"
}

ask_optional() {
  local variable="$1" prompt="$2" value=""
  read -r -p "$prompt: " value
  printf -v "$variable" '%s' "$value"
}

dotenv() {
  local key="$1" value="$2"
  value="${value//$'\r'/}"
  value="${value//$'\n'/}"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  printf '%s="%s"\n' "$key" "$value" >> .env.tmp
}

echo "Miaoxiang first-run initialization / 妙想之地首次初始化"
echo "Examples are illustrative only. Do not enter production secrets on a shared terminal."

ask app_name "Application name / 应用名称" "妙想之地"
ask app_port "Web port / Web 端口" "53138"
ask project_host_dir "Absolute install path / 项目绝对路径" "$project_dir"
ask public_origin "Public HTTPS origin (example: https://example.com) / 公网地址" "https://example.com"
ask admin_username "Administrator username / 管理员用户名" "project-admin"
ask_secret admin_password "Administrator password / 管理员密码"

echo "Primary executor LLM / 主执行模型"
ask llm_url "OpenAI-compatible API URL (example: https://api.example.com/v1)" "https://api.example.com/v1"
ask llm_model "Model name / 模型名称" "your-model-name"
ask_secret llm_key "Model API Key (example: sk-your-key) / 模型 API Key"

read -r -p "Use a separate Chat model? / 单独配置 Chat 模型？ [y/N]: " split_chat
if [[ "$split_chat" =~ ^[Yy]$ ]]; then
  ask chat_url "Chat API URL" "$llm_url"
  ask chat_model "Chat model name" "$llm_model"
  ask_secret chat_key "Chat API Key"
else
  chat_url="$llm_url"; chat_model="$llm_model"; chat_key="$llm_key"
fi

read -r -p "Enable a separate coordinator LLM? / 开启独立统筹模型？ [y/N]: " split_coordinator
if [[ "$split_coordinator" =~ ^[Yy]$ ]]; then
  model_split_enabled=true
  ask coordinator_url "Coordinator API URL / 统筹模型 API URL" "$llm_url"
  ask coordinator_model "Coordinator model name / 统筹模型名称" "$llm_model"
  ask_secret coordinator_key "Coordinator API Key / 统筹模型 API Key"
else
  model_split_enabled=false
  coordinator_url="$llm_url"; coordinator_model="$llm_model"; coordinator_key="$llm_key"
fi

read -r -p "Configure SMTP email now? / 现在配置邮件服务？ [y/N]: " configure_smtp
smtp_host=""; smtp_port="465"; smtp_username=""; smtp_password=""; smtp_from=""
if [[ "$configure_smtp" =~ ^[Yy]$ ]]; then
  ask smtp_host "SMTP host" "smtp.example.com"
  ask smtp_port "SMTP port" "465"
  ask_optional smtp_username "SMTP username"
  ask_secret smtp_password "SMTP password"
  ask_optional smtp_from "SMTP From address"
fi

read -r -p "Configure WeChat login now? / 现在配置微信登录？ [y/N]: " configure_wechat
wechat_app_id=""; wechat_app_secret=""; wechat_bridge_secret=""
if [[ "$configure_wechat" =~ ^[Yy]$ ]]; then
  ask wechat_app_id "WeChat AppID"
  ask_secret wechat_app_secret "WeChat AppSecret"
  wechat_bridge_secret="$(random_hex)"
fi

: > .env.tmp
dotenv APP_NAME "$app_name"
dotenv APP_PORT "$app_port"
dotenv PROJECT_HOST_DIR "$project_host_dir"
dotenv PUBLIC_APP_ORIGIN "${public_origin%/}"
dotenv APP_SECRET "$(random_hex)"
dotenv ACTIVATION_SECRET "$(random_hex)"
dotenv INTERNAL_BROWSER_KEY "$(random_hex)"
dotenv HERMES_API_KEY "$(random_hex)"
dotenv ADMIN_USERNAME "$admin_username"
dotenv ADMIN_PASSWORD "$admin_password"
dotenv SMTP_HOST "$smtp_host"
dotenv SMTP_PORT "$smtp_port"
dotenv SMTP_USERNAME "$smtp_username"
dotenv SMTP_PASSWORD "$smtp_password"
dotenv SMTP_FROM "$smtp_from"
dotenv WECHAT_APP_ID "$wechat_app_id"
dotenv WECHAT_APP_SECRET "$wechat_app_secret"
dotenv WECHAT_CLOUD_BRIDGE_SECRET "$wechat_bridge_secret"
dotenv LLM_BASE_URL "${llm_url%/}"
dotenv LLM_API_KEY "$llm_key"
dotenv LLM_MODEL "$llm_model"
dotenv MODEL_SPLIT_ENABLED "$model_split_enabled"
dotenv CHAT_LLM_BASE_URL "${chat_url%/}"
dotenv CHAT_LLM_API_KEY "$chat_key"
dotenv CHAT_LLM_MODEL "$chat_model"
dotenv COORDINATOR_LLM_BASE_URL "${coordinator_url%/}"
dotenv COORDINATOR_LLM_API_KEY "$coordinator_key"
dotenv COORDINATOR_LLM_MODEL "$coordinator_model"
cat >> .env.tmp <<'EOF'
LLM_CONTEXT_LENGTH="131072"
LLM_MAX_RETRIES="8"
LLM_CONCURRENCY_LIMIT="12"
LLM_VERIFY_TLS="true"
LLM_PROXY=""
OUTBOUND_PROXY_ENABLED="false"
OUTBOUND_PROXY_URL="http://127.0.0.1:10808"
OUTBOUND_PROXY_CONTAINER_URL="http://host.docker.internal:10809"
PROXY_BRIDGE_BIND_HOST="172.17.0.1"
PROXY_BRIDGE_BIND_PORT="10809"
HERMES_IMAGE="mumu-hermes-worker:local"
HERMES_DYNAMIC_WORKERS="true"
HERMES_MIN_ACTIVE_WORKERS="2"
HERMES_MAX_ACTIVE_WORKERS="8"
HERMES_MEMORY_RESERVE_GIB="4"
HERMES_WORKER_MEMORY_BUDGET_GIB="1.5"
HERMES_CPU_RESERVE="2"
HERMES_IDLE_MINUTES="30"
HERMES_MEMORY_LIMIT="3g"
HERMES_CPU_LIMIT="2"
HERMES_MAX_TURNS="1200"
HERMES_GATEWAY_TIMEOUT="43200"
HERMES_GATEWAY_NOTIFY_INTERVAL="180"
EXPERT_QUALITY_THRESHOLD="82"
EXPERT_MAX_REVISIONS="3"
EXPERT_MAX_REVIEW_IMAGES="200"
BROWSER_IMAGE="mumu-browser-runtime:local"
BROWSER_IDLE_MINUTES="60"
BROWSER_MEMORY_LIMIT="1100m"
BROWSER_CPU_LIMIT="1"
UPLOAD_MAX_BYTES="104857600"
REGISTRATION_ENABLED="true"
EOF

mv .env.tmp .env
chmod 600 .env
mkdir -p data/users
chmod 700 data/users
echo "Initialization complete. Secrets are local to this deployment. / 初始化完成。密钥仅适用于当前部署。"
