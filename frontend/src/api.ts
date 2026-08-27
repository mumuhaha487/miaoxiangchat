import type {
  ActivationCode, AdminModelSettings, AdminModelTestResult, AdminSettings, AgentTask, AppNotification, Attachment, BrowserState, Conversation, ControlDevice,
  ControlTask, ControlTaskEvent, Message,
  NotificationPreferences,
  RuntimeInfo, RuntimeSummary, Schedule, TaskEvent, User, WorkspaceEntry, WorkspacePreview,
  LoginDevice, PreviewPort, Savepoint, SkillRecord, Workflow, WorkflowCategory,
} from './types';
import { publicAppUrl } from './native';

interface Envelope<T> {
  ok: boolean;
  data: T;
  error?: { message?: string };
  detail?: string;
}

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

function headers(token?: string, json = true): Record<string, string> {
  return {
    Accept: 'application/json',
    ...(json ? { 'Content-Type': 'application/json' } : {}),
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

async function request<T>(path: string, options: RequestInit = {}, token?: string): Promise<T> {
  const versionedPath = path.startsWith('/api/') ? `/api/v1/${path.slice('/api/'.length)}` : path;
  const response = await fetch(publicAppUrl(versionedPath), {
    ...options,
    headers: { ...headers(token, !(options.body instanceof FormData)), ...(options.headers || {}) },
  });
  const body = await response.json().catch(() => null) as Envelope<T> | null;
  if (!response.ok || !body?.ok) {
    const detail = typeof body?.detail === 'string' ? body.detail : '';
    throw new ApiError(body?.error?.message || detail || `请求失败 (${response.status})`, response.status);
  }
  return body.data;
}

async function streamSse(
  path: string,
  options: RequestInit,
  token: string | undefined,
  onEvent: (event: string, payload: Record<string, unknown>) => void,
): Promise<void> {
  const versionedPath = path.startsWith('/api/') ? `/api/v1/${path.slice('/api/'.length)}` : path;
  const response = await fetch(publicAppUrl(versionedPath), {
    ...options,
    headers: {
      ...headers(token, !(options.body instanceof FormData)),
      Accept: 'text/event-stream',
      ...(options.headers || {}),
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null) as Envelope<unknown> | null;
    const detail = typeof body?.detail === 'string' ? body.detail : '';
    throw new ApiError(body?.error?.message || detail || `请求失败 (${response.status})`, response.status);
  }
  if (!response.body) throw new ApiError('当前浏览器不支持流式响应', 500);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    buffer = buffer.replace(/\r\n/g, '\n');
    let boundary = buffer.indexOf('\n\n');
    while (boundary >= 0) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      let event = 'message';
      const data: string[] = [];
      for (const line of block.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim();
        else if (line.startsWith('data:')) data.push(line.slice(5).trimStart());
      }
      if (data.length) {
        let payload: Record<string, unknown> = {};
        try { payload = JSON.parse(data.join('\n')) as Record<string, unknown>; }
        catch { throw new ApiError('服务端返回了无效的流式数据', 502); }
        onEvent(event, payload);
      }
      boundary = buffer.indexOf('\n\n');
    }
    if (done) break;
  }
}

export const authApi = {
  runtime: () => request<RuntimeInfo>('/api/runtime'),
  adminLogin: (password: string) => request<{ token: string; user: User }>('/api/auth/admin-login', {
    method: 'POST', body: JSON.stringify({ password }),
  }),
  requestCode: (payload: { email?: string; identifier?: string; username?: string; password?: string; purpose: 'login' | 'register' | 'reset'; activation_code?: string; activation_token?: string; device_id: string; device_name: string; client_platform: 'web' | 'android' | 'windows'; trust_token: string }) =>
    request<{ sent: boolean; verificationRequired: boolean; expiresIn?: number; token?: string; user?: User; deviceCredential?: string }>('/api/auth/request-code', { method: 'POST', body: JSON.stringify(payload) }),
  verify: (payload: { email?: string; identifier?: string; username?: string; password: string; code: string; purpose: 'login' | 'register' | 'reset'; display_name?: string; activation_code?: string; activation_token?: string; trust_device: boolean; device_id: string; device_name: string; client_platform: 'web' | 'android' | 'windows'; trust_token: string }) =>
    request<{ token: string; user: User; deviceCredential: string }>('/api/auth/verify', { method: 'POST', body: JSON.stringify(payload) }),
  me: (token: string) => request<{ user: User }>('/api/auth/me', {}, token),
  logout: (token: string) => request<Record<string, never>>('/api/auth/logout', { method: 'POST', body: '{}' }, token),
  exchangeWebviewTicket: (ticket: string) => request<{ token: string; user: User }>(
    '/api/auth/webview-ticket/exchange', { method: 'POST', body: JSON.stringify({ ticket }) },
  ),
  redeemActivation: (token: string, code: string) => request<{ user: User; alreadyActive: boolean }>(
    '/api/auth/activation/redeem', { method: 'POST', body: JSON.stringify({ code }) }, token,
  ),
};

export const deviceApi = {
  list: (token: string) => request<{ devices: LoginDevice[] }>('/api/devices', {}, token),
  logout: (token: string, id: string) => request<{ current: boolean }>(
    `/api/devices/${encodeURIComponent(id)}/logout`, { method: 'POST', body: '{}' }, token,
  ),
  remove: (token: string, id: string) => request<{ current: boolean }>(
    `/api/devices/${encodeURIComponent(id)}`, { method: 'DELETE' }, token,
  ),
};

export const controlApi = {
  devices: (token: string) => request<{ devices: ControlDevice[] }>('/api/control/devices', {}, token),
  renameDevice: (token: string, id: string, name: string) => request<{ device: ControlDevice }>(
    `/api/control/devices/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify({ name }) }, token,
  ),
  removeDevice: (token: string, id: string) => request<Record<string, never>>(
    `/api/control/devices/${encodeURIComponent(id)}`, { method: 'DELETE' }, token,
  ),
  tasks: (token: string, conversationId?: string) => request<{ tasks: ControlTask[] }>(
    `/api/control/tasks${conversationId ? `?conversation_id=${encodeURIComponent(conversationId)}` : ''}`, {}, token,
  ),
  createTask: (
    token: string,
    payload: {
      device_id: string;
      target_id: string;
      target_kind: 'windows' | 'adb';
      instruction: string;
      conversation_id?: string;
    },
  ) => request<{ task: ControlTask }>('/api/control/tasks', {
    method: 'POST', body: JSON.stringify(payload),
  }, token),
  events: (token: string, id: string, after = 0) => request<{ events: ControlTaskEvent[] }>(
    `/api/control/tasks/${encodeURIComponent(id)}/events?after=${after}`, {}, token,
  ),
  stop: (token: string, id: string) => request<Record<string, never>>(
    `/api/control/tasks/${encodeURIComponent(id)}/stop`, { method: 'POST', body: '{}' }, token,
  ),
  approval: (token: string, id: string, decision: 'approve' | 'deny') => request<Record<string, never>>(
    `/api/control/tasks/${encodeURIComponent(id)}/approval`, {
      method: 'POST', body: JSON.stringify({ decision }),
    }, token,
  ),
  frame: async (token: string, id: string): Promise<Blob> => {
    const response = await fetch(publicAppUrl(`/api/v1/control/frames/${encodeURIComponent(id)}`), {
      headers: headers(token, false),
    });
    if (!response.ok) throw new ApiError(`截图读取失败 (${response.status})`, response.status);
    return response.blob();
  },
};

export const profileApi = {
  background: async (token: string): Promise<Blob | null> => {
    const response = await fetch(publicAppUrl('/api/v1/profile/background'), { headers: headers(token, false) });
    if (response.status === 404) return null;
    if (!response.ok) throw new ApiError(`背景读取失败 (${response.status})`, response.status);
    return response.blob();
  },
  uploadBackground: async (token: string, file: File) => {
    const form = new FormData();
    form.append('file', file);
    return request<{ hasCustomBackground: boolean }>('/api/profile/background', { method: 'PUT', body: form }, token);
  },
  deleteBackground: (token: string) => request<{ hasCustomBackground: boolean }>(
    '/api/profile/background', { method: 'DELETE' }, token,
  ),
};

export const notificationApi = {
  preferences: (token: string) => request<{ preferences: NotificationPreferences }>(
    '/api/notifications/preferences', {}, token,
  ),
  updatePreferences: (token: string, payload: Record<string, boolean>) => request<{ preferences: NotificationPreferences }>(
    '/api/notifications/preferences', { method: 'PATCH', body: JSON.stringify(payload) }, token,
  ),
  list: (token: string, after = 0) => request<{ notifications: AppNotification[]; unreadCount: number }>(
    `/api/notifications?after=${after}`, {}, token,
  ),
  read: (token: string, id: number) => request<Record<string, never>>(
    `/api/notifications/${id}/read`, { method: 'POST', body: '{}' }, token,
  ),
  readAll: (token: string) => request<{ updated: number }>(
    '/api/notifications/read-all', { method: 'POST', body: '{}' }, token,
  ),
};

export const conversationApi = {
  list: (token: string) => request<{ conversations: Conversation[] }>('/api/conversations', {}, token),
  create: (token: string, title = '新对话', mode: 'chat' | 'agent' = 'chat', agentProfile: 'fast' | 'expert' = 'expert') => request<{ conversation: Conversation; browserStatus: string }>(
    '/api/conversations', { method: 'POST', body: JSON.stringify({ title, mode, agent_profile: agentProfile }) }, token,
  ),
  update: (token: string, id: string, payload: { title?: string; mode?: 'chat' | 'agent'; agent_profile?: 'fast' | 'expert' }) => request<{ conversation: Conversation }>(
    `/api/conversations/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }, token,
  ),
  bindControl: (token: string, id: string, payload: { device_id: string | null; target_id: string | null }) => request<{ conversation: Conversation }>(
    `/api/conversations/${id}/control-binding`, { method: 'PUT', body: JSON.stringify(payload) }, token,
  ),
  remove: (token: string, id: string) => request<Record<string, never>>(`/api/conversations/${id}`, { method: 'DELETE' }, token),
  messages: (token: string, id: string) => request<{ messages: Message[] }>(`/api/conversations/${id}/messages`, {}, token),
  chat: (token: string, id: string, content: string) => request<{ message: Message; conversation: Conversation; model: string; contextCompressed: boolean }>(
    `/api/conversations/${id}/chat`, { method: 'POST', body: JSON.stringify({ content }) }, token,
  ),
  chatStream: async (
    token: string,
    id: string,
    content: string,
    onDelta: (delta: string) => void,
    signal?: AbortSignal,
  ) => {
    type Result = { message: Message; conversation: Conversation; model: string; contextCompressed: boolean };
    const holder: { result?: Result } = {};
    await streamSse(
      `/api/conversations/${id}/chat/stream`,
      { method: 'POST', body: JSON.stringify({ content }), signal },
      token,
      (event, payload) => {
        if (event === 'delta') onDelta(String(payload.content || ''));
        else if (event === 'done') holder.result = payload as unknown as Result;
        else if (event === 'error') throw new ApiError(String(payload.message || '流式对话失败'), Number(payload.status || 502));
      },
    );
    if (!holder.result) throw new ApiError('流式响应在完成前中断', 502);
    return holder.result;
  },
  tasks: (token: string, id: string) => request<{ tasks: AgentTask[] }>(`/api/conversations/${id}/tasks`, {}, token),
  importGuest: (token: string, payload: object) => request<{ imported: number }>(
    '/api/conversations/import-guest', { method: 'POST', body: JSON.stringify(payload) }, token,
  ),
  createTask: (token: string, id: string, content: string, attachmentIds: string[]) => request<{ task: AgentTask; conversation: Conversation }>(
    `/api/conversations/${id}/tasks`, { method: 'POST', body: JSON.stringify({ content, attachment_ids: attachmentIds }) }, token,
  ),
  dispatchTask: (token: string, id: string, content: string, attachmentIds: string[]) => request<
    { execution: 'local'; task: AgentTask; conversation: Conversation } | { execution: 'remote'; task: ControlTask; conversation: Conversation }
  >(
    `/api/conversations/${id}/dispatch`, {
      method: 'POST', body: JSON.stringify({ content, attachment_ids: attachmentIds }),
    }, token,
  ),
  upload: async (token: string, id: string, file: File): Promise<Attachment> => {
    const form = new FormData();
    form.append('file', file);
    return (await request<{ attachment: Attachment }>(
      `/api/conversations/${id}/attachments`, { method: 'POST', body: form }, token,
    )).attachment;
  },
};

export const capabilityApi = {
  list: (token: string) => request<{ categories: WorkflowCategory[]; workflows: Workflow[]; skills: SkillRecord[] }>(
    '/api/capabilities', {}, token,
  ),
  createCategory: (token: string, name: string, description = '') => request<{ category: WorkflowCategory }>(
    '/api/workflow-categories', { method: 'POST', body: JSON.stringify({ name, description }) }, token,
  ),
  deleteCategory: (token: string, id: string) => request<{ deleted: boolean }>(
    `/api/workflow-categories/${encodeURIComponent(id)}`, { method: 'DELETE' }, token,
  ),
  createWorkflow: (token: string, payload: { name: string; description: string; instructions: string; triggers: string[]; category_id: string | null }) => request<{ workflow: Workflow }>(
    '/api/workflows', { method: 'POST', body: JSON.stringify(payload) }, token,
  ),
  fromConversation: (token: string, conversationId: string, categoryId: string | null, name = '') => request<{ workflow: Workflow }>(
    '/api/workflows/from-conversation', { method: 'POST', body: JSON.stringify({ conversation_id: conversationId, category_id: categoryId, name }) }, token,
  ),
  updateWorkflow: (token: string, id: string, payload: { name?: string; description?: string; instructions?: string; triggers?: string[]; category_id?: string | null }) => request<{ workflow: Workflow }>(
    `/api/workflows/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(payload) }, token,
  ),
  deleteWorkflow: (token: string, id: string) => request<{ deleted: boolean }>(
    `/api/workflows/${encodeURIComponent(id)}`, { method: 'DELETE' }, token,
  ),
  searchSkills: (token: string, query: string, source = '') => request<{ ok: boolean; output: string }>(
    '/api/skills/search', { method: 'POST', body: JSON.stringify({ query, source }) }, token,
  ),
  installSkill: (token: string, sourceRef: string, force = false, probe = true) => request<{ skill: SkillRecord; validation: Record<string, unknown> }>(
    '/api/skills/install', { method: 'POST', body: JSON.stringify({ source_ref: sourceRef, force, probe }) }, token,
  ),
  auditSkills: (token: string) => request<{ ok: boolean; output: string; skills: SkillRecord[] }>(
    '/api/skills/audit', { method: 'POST', body: '{}' }, token,
  ),
  deleteSkill: (token: string, id: string) => request<{ deleted: boolean }>(
    `/api/skills/${encodeURIComponent(id)}`, { method: 'DELETE' }, token,
  ),
  share: (token: string, kind: 'workflow' | 'skill', itemId: string) => request<{ code: string; shareId: string; sha256: string }>(
    '/api/capabilities/share', { method: 'POST', body: JSON.stringify({ kind, item_id: itemId }) }, token,
  ),
  import: (token: string, code: string) => request<{ kind: 'workflow' | 'skill'; item: Workflow | SkillRecord }>(
    '/api/capabilities/import', { method: 'POST', body: JSON.stringify({ code }) }, token,
  ),
};

export const chatApi = {
  complete: (messages: Array<{ role: 'user' | 'assistant'; content: string }>) => request<{
    message: { role: 'assistant'; content: string; createdAt: number };
    model: string;
    contextCompressed: boolean;
  }>('/api/chat/completions', { method: 'POST', body: JSON.stringify({ messages }) }),
  completeStream: async (
    messages: Array<{ role: 'user' | 'assistant'; content: string }>,
    onDelta: (delta: string) => void,
    signal?: AbortSignal,
  ) => {
    type Result = { message: { role: 'assistant'; content: string; createdAt: number }; model: string; contextCompressed: boolean };
    const holder: { result?: Result } = {};
    await streamSse(
      '/api/chat/completions/stream',
      { method: 'POST', body: JSON.stringify({ messages }), signal },
      undefined,
      (event, payload) => {
        if (event === 'delta') onDelta(String(payload.content || ''));
        else if (event === 'done') holder.result = payload as unknown as Result;
        else if (event === 'error') throw new ApiError(String(payload.message || '流式对话失败'), Number(payload.status || 502));
      },
    );
    if (!holder.result) throw new ApiError('流式响应在完成前中断', 502);
    return holder.result;
  },
};

export const taskApi = {
  get: (token: string, id: string) => request<{ task: AgentTask }>(`/api/tasks/${id}`, {}, token),
  events: (token: string, id: string, after = 0) => request<{ events: TaskEvent[] }>(`/api/tasks/${id}/events?after=${after}`, {}, token),
  stream: (
    token: string,
    id: string,
    after: number,
    callbacks: { onEvent: (event: TaskEvent) => void; onTask: (task: AgentTask) => void },
    signal?: AbortSignal,
  ) => streamSse(
    `/api/tasks/${id}/stream?after=${after}`,
    { method: 'GET', signal },
    token,
    (event, payload) => {
      if (event === 'task-event' && payload.event) callbacks.onEvent(payload.event as unknown as TaskEvent);
      else if ((event === 'task' || event === 'done') && payload.task) callbacks.onTask(payload.task as unknown as AgentTask);
      else if (event === 'error') throw new ApiError(String(payload.message || '任务流中断'), Number(payload.status || 502));
    },
  ),
  approve: (token: string, id: string, decision: 'once' | 'session' | 'always' | 'deny') => request<Record<string, unknown>>(
    `/api/tasks/${id}/approval`, { method: 'POST', body: JSON.stringify({ decision }) }, token,
  ),
  stop: (token: string, id: string) => request<Record<string, unknown>>(`/api/tasks/${id}/stop`, { method: 'POST', body: '{}' }, token),
  steer: (token: string, id: string, content: string) => request<Record<string, unknown>>(
    `/api/tasks/${id}/steer`, { method: 'POST', body: JSON.stringify({ content }) }, token,
  ),
};

export const browserApi = {
  state: (token: string, conversationId: string) => request<{ browser: BrowserState }>(
    `/api/conversations/${conversationId}/browser`, {}, token,
  ),
  action: (token: string, conversationId: string, action: 'focus' | 'reload' | 'back' | 'forward') =>
    request<{ browser: BrowserState }>(`/api/conversations/${conversationId}/browser/action`, {
      method: 'POST', body: JSON.stringify({ action }),
    }, token),
  ticket: (token: string, conversationId: string) => request<{ ticket: string; expiresIn: number }>(
    `/api/conversations/${conversationId}/browser/ticket`, { method: 'POST', body: '{}' }, token,
  ),
};

export const workspaceApi = {
  list: (token: string, conversationId: string, path = '') => request<{ path: string; entries: WorkspaceEntry[] }>(
    `/api/workspace?conversation_id=${encodeURIComponent(conversationId)}&path=${encodeURIComponent(path)}`, {}, token,
  ),
  mentions: (token: string, conversationId: string, query = '') => request<{ entries: WorkspaceEntry[] }>(
    `/api/workspace/mentions?conversation_id=${encodeURIComponent(conversationId)}&query=${encodeURIComponent(query)}`, {}, token,
  ),
  upload: async (token: string, conversationId: string, path: string, file: File): Promise<WorkspaceEntry> => {
    const form = new FormData();
    form.append('file', file);
    return (await request<{ entry: WorkspaceEntry }>(
      `/api/workspace/upload?conversation_id=${encodeURIComponent(conversationId)}&path=${encodeURIComponent(path)}`,
      { method: 'POST', body: form }, token,
    )).entry;
  },
  downloadUrl: (conversationId: string, path: string) =>
    publicAppUrl(`/api/v1/workspace/download?conversation_id=${encodeURIComponent(conversationId)}&path=${encodeURIComponent(path)}`),
  fileBlob: async (token: string, conversationId: string, path: string): Promise<Blob> => {
    const response = await fetch(workspaceApi.downloadUrl(conversationId, path), {
      headers: headers(token, false),
    });
    if (!response.ok) throw new ApiError(`文件读取失败 (${response.status})`, response.status);
    return response.blob();
  },
  preview: (token: string, conversationId: string, path: string) => request<{ preview: WorkspacePreview }>(
    `/api/workspace/preview?conversation_id=${encodeURIComponent(conversationId)}&path=${encodeURIComponent(path)}`,
    { method: 'POST', body: '{}' }, token,
  ),
};

export const terminalApi = {
  ticket: (token: string) => request<{ ticket: string; expiresIn: number }>(
    '/api/terminal/ticket', { method: 'POST', body: '{}' }, token,
  ),
  ports: (token: string) => request<{ ports: PreviewPort[] }>('/api/ports', {}, token),
  openPort: (token: string, port: number) => request<{ port: PreviewPort }>(
    '/api/ports/open', { method: 'POST', body: JSON.stringify({ port }) }, token,
  ),
  removePort: (token: string, port: number) => request<Record<string, never>>(
    `/api/ports/${port}`, { method: 'DELETE' }, token,
  ),
};

export const savepointApi = {
  list: (token: string) => request<{ savepoints: Savepoint[] }>('/api/savepoints', {}, token),
  create: (token: string, name: string) => request<{ savepoint: Savepoint }>(
    '/api/savepoints', { method: 'POST', body: JSON.stringify({ name }) }, token,
  ),
  restore: (token: string, id: string) => request<{ savepoint: Savepoint }>(
    `/api/savepoints/${encodeURIComponent(id)}/restore`, { method: 'POST', body: '{}' }, token,
  ),
  remove: (token: string, id: string) => request<Record<string, never>>(
    `/api/savepoints/${encodeURIComponent(id)}`, { method: 'DELETE' }, token,
  ),
};

export const scheduleApi = {
  list: (token: string) => request<{ schedules: Schedule[] }>('/api/schedules', {}, token),
  action: (token: string, id: string, action: 'pause' | 'resume' | 'run') => request<{ schedule: Schedule }>(
    `/api/schedules/${encodeURIComponent(id)}/${action}`, { method: 'POST', body: '{}' }, token,
  ),
};

export const adminApi = {
  users: (token: string) => request<{ users: User[] }>('/api/admin/users', {}, token),
  runtimes: (token: string) => request<RuntimeSummary>('/api/admin/runtimes', {}, token),
  createUser: (token: string, payload: object) => request<{ user: User }>(
    '/api/admin/users', { method: 'POST', body: JSON.stringify(payload) }, token,
  ),
  updateUser: (token: string, id: string, payload: object) => request<{ user: User }>(
    `/api/admin/users/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }, token,
  ),
  deleteUser: (token: string, id: string) => request<Record<string, never>>(
    `/api/admin/users/${id}`, { method: 'DELETE' }, token,
  ),
  settings: (token: string) => request<AdminSettings>('/api/admin/settings', {}, token),
  updateModelSettings: (token: string, payload: object) => request<{ models: AdminModelSettings }>(
    '/api/admin/model-settings', { method: 'PATCH', body: JSON.stringify(payload) }, token,
  ),
  testModel: (token: string, role: 'chat' | 'coordinator' | 'executor') => request<AdminModelTestResult>(
    '/api/admin/model-settings/test', { method: 'POST', body: JSON.stringify({ role }) }, token,
  ),
  activationCodes: (token: string) => request<{ activationCodes: ActivationCode[] }>('/api/admin/activation-codes', {}, token),
  createActivationCode: (token: string, payload: { note: string; max_uses: number; expires_at?: number }) => request<{ activationCode: ActivationCode }>(
    '/api/admin/activation-codes', { method: 'POST', body: JSON.stringify(payload) }, token,
  ),
  updateActivationCode: (token: string, id: string, payload: object) => request<{ activationCode: ActivationCode }>(
    `/api/admin/activation-codes/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }, token,
  ),
  deleteActivationCode: (token: string, id: string) => request<Record<string, never>>(
    `/api/admin/activation-codes/${id}`, { method: 'DELETE' }, token,
  ),
};
