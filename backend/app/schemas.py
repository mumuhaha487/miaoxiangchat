from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AdminLoginRequest(BaseModel):
    username: str = Field(default="", max_length=80)
    password: str


class RequestCodeRequest(BaseModel):
    email: str = ""
    identifier: str = Field(default="", max_length=254)
    username: str = Field(default="", max_length=32)
    password: str = ""
    purpose: Literal["login", "register", "reset"]
    activation_code: str = Field(default="", max_length=80)
    activation_token: str = Field(default="", max_length=128)
    device_id: str = Field(default="", max_length=256)
    device_name: str = Field(default="", max_length=120)
    client_platform: Literal["web", "android", "wechat", "windows"] = "web"
    trust_token: str = Field(default="", max_length=256)


class VerifyCodeRequest(RequestCodeRequest):
    code: str = Field(min_length=6, max_length=6)
    display_name: str = Field(default="", max_length=80)
    trust_device: bool = False


class WechatLoginRequest(BaseModel):
    code: str = Field(min_length=1, max_length=160)
    device_id: str = Field(default="", max_length=256)
    device_name: str = Field(default="微信小程序", max_length=120)


class WechatCloudLoginRequest(BaseModel):
    app_id: str = Field(min_length=1, max_length=64)
    open_id: str = Field(min_length=1, max_length=128)
    union_id: str = Field(default="", max_length=128)
    timestamp: int = Field(ge=1)
    nonce: str = Field(min_length=24, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    signature: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$")
    device_id: str = Field(default="", max_length=256)
    device_name: str = Field(default="微信小程序", max_length=120)


class BindEmailRequest(BaseModel):
    email: str
    password: str


class BindEmailVerifyRequest(BindEmailRequest):
    code: str = Field(min_length=6, max_length=6)
    device_id: str = Field(default="", max_length=256)
    device_name: str = Field(default="微信小程序", max_length=120)
    trust_device: bool = True


class ActivationRedeemRequest(BaseModel):
    code: str = Field(min_length=1, max_length=80)


class WebviewTicketExchangeRequest(BaseModel):
    ticket: str = Field(min_length=32, max_length=256)


class PortOpenRequest(BaseModel):
    port: int = Field(ge=1024, le=65535)


class GuestImportMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=50_000)
    created_at: int = Field(default=0, ge=0)


class GuestImportConversation(BaseModel):
    client_id: str = Field(min_length=1, max_length=120)
    title: str = Field(default="访客对话", max_length=80)
    messages: list[GuestImportMessage] = Field(default_factory=list, max_length=80)
    created_at: int = Field(default=0, ge=0)


class GuestArchiveImportRequest(BaseModel):
    client_import_id: str = Field(min_length=8, max_length=120)
    conversations: list[GuestImportConversation] = Field(default_factory=list, max_length=50)


class ConversationCreateRequest(BaseModel):
    title: str = Field(default="新对话", max_length=80)
    mode: Literal["chat", "agent"] = "chat"
    agent_profile: Literal["fast", "expert"] = "expert"


class ConversationUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=80)
    mode: Literal["chat", "agent"] | None = None
    agent_profile: Literal["fast", "expert"] | None = None


class ConversationControlBindingRequest(BaseModel):
    device_id: str | None = Field(default=None, max_length=64)
    target_id: str | None = Field(default=None, max_length=200)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=50_000)


class GuestChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=80)


class ConversationChatRequest(BaseModel):
    content: str = Field(min_length=1, max_length=50_000)


class TaskCreateRequest(BaseModel):
    content: str = Field(min_length=1, max_length=50_000)
    attachment_ids: list[str] = Field(default_factory=list, max_length=8)


class TaskApprovalRequest(BaseModel):
    decision: Literal["once", "session", "always", "deny"]


class TaskSteerRequest(BaseModel):
    content: str = Field(min_length=1, max_length=10_000)


class WorkflowCategoryCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    description: str = Field(default="", max_length=500)


class WorkflowCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=1000)
    instructions: str = Field(min_length=20, max_length=100_000)
    triggers: list[str] = Field(default_factory=list, max_length=30)
    category_id: str | None = Field(default=None, max_length=80)


class WorkflowFromConversationRequest(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=80)
    name: str = Field(default="", max_length=80)
    category_id: str | None = Field(default=None, max_length=80)


class WorkflowUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=1000)
    instructions: str | None = Field(default=None, min_length=20, max_length=100_000)
    triggers: list[str] | None = Field(default=None, max_length=30)
    category_id: str | None = Field(default=None, max_length=80)


class SkillSearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=200)
    source: str = Field(default="", max_length=80)


class SkillInstallRequest(BaseModel):
    source_ref: str = Field(min_length=2, max_length=1000)
    force: bool = False
    probe: bool = True


class CapabilityShareRequest(BaseModel):
    kind: Literal["workflow", "skill"]
    item_id: str = Field(min_length=1, max_length=80)


class CapabilityImportRequest(BaseModel):
    code: str = Field(min_length=12, max_length=24)


class BrowserActionRequest(BaseModel):
    action: Literal["focus", "reload", "back", "forward"] = "focus"


class ControlDeviceRegisterRequest(BaseModel):
    installation_id: str = Field(min_length=12, max_length=200)
    hostname: str = Field(default="Windows-PC", min_length=1, max_length=120)
    name: str = Field(default="", max_length=120)
    agent_version: str = Field(default="", max_length=40)


class ControlDeviceUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class ControlTaskCreateRequest(BaseModel):
    device_id: str = Field(min_length=16, max_length=64)
    target_id: str = Field(default="desktop", min_length=1, max_length=200)
    instruction: str = Field(min_length=1, max_length=8000)
    conversation_id: str | None = Field(default=None, min_length=1, max_length=80)


class ControlTaskApprovalRequest(BaseModel):
    decision: Literal["approve", "deny"]


class ScheduleCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    prompt: str = Field(min_length=1, max_length=50_000)
    cron: str = Field(min_length=5, max_length=120)
    timezone: str = Field(default="Asia/Shanghai", max_length=80)
    conversation_id: str | None = None


class ScheduleUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=100)
    prompt: str | None = Field(default=None, min_length=1, max_length=50_000)
    cron: str | None = Field(default=None, min_length=5, max_length=120)
    timezone: str | None = Field(default=None, max_length=80)
    status: Literal["active", "paused"] | None = None


class SavepointCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class NotificationPreferencesUpdateRequest(BaseModel):
    chat_completed: bool | None = None
    agent_completed: bool | None = None
    schedule_completed: bool | None = None
    task_failed: bool | None = None
    approval_required: bool | None = None
    system: bool | None = None


class AdminUserCreateRequest(BaseModel):
    email: str
    username: str = Field(default="", max_length=32)
    display_name: str = Field(default="", max_length=80)
    password: str
    status: Literal["active", "disabled"] = "active"
    access_tier: Literal["basic", "vip"] = "basic"


class AdminUserUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, max_length=80)
    password: str | None = None
    status: Literal["active", "disabled"] | None = None
    access_tier: Literal["basic", "vip"] | None = None


class AdminModelEndpointRequest(BaseModel):
    base_url: str = Field(min_length=8, max_length=1000)
    api_key: str = Field(default="", max_length=4000)
    model: str = Field(min_length=1, max_length=200)
    supports_vision: bool = True
    reasoning_enabled: bool = True
    reasoning_effort: Literal["minimal", "low", "medium", "high", "xhigh", "max", "ultra"] = "max"
    vision_base_url: str = Field(default="", max_length=1000)
    vision_api_key: str = Field(default="", max_length=4000)
    vision_model: str = Field(default="", max_length=200)


class AdminModelSettingsUpdateRequest(BaseModel):
    split_enabled: bool
    chat: AdminModelEndpointRequest | None = None
    coordinator: AdminModelEndpointRequest
    executor: AdminModelEndpointRequest


class AdminModelTestRequest(BaseModel):
    role: Literal["chat", "coordinator", "executor"]


class ActivationCodeCreateRequest(BaseModel):
    note: str = Field(default="", max_length=160)
    max_uses: int = Field(default=1, ge=1, le=10_000)
    expires_at: int | None = Field(default=None, ge=1)


class ActivationCodeUpdateRequest(BaseModel):
    note: str | None = Field(default=None, max_length=160)
    status: Literal["active", "disabled"] | None = None
    max_uses: int | None = Field(default=None, ge=1, le=10_000)
    expires_at: int | None = Field(default=None, ge=1)
