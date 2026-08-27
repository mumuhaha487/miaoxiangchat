export interface User {
  id: string;
  username: string;
  email: string;
  displayName: string;
  role: 'user' | 'admin';
  accessTier: 'basic' | 'vip';
  status: 'active' | 'disabled';
  emailVerified: boolean;
  emailBound: boolean;
  hasCustomBackground: boolean;
  createdAt: number;
  lastLoginAt: number | null;
}

export interface RuntimeInfo {
  appName: string;
  registrationEnabled: boolean;
  model: string;
  workerLimit: number;
  uploadMaxBytes: number;
  apiVersion: string;
  appDownloadUrl: string;
  windowsAgentVersion: string;
  windowsAgentDownloadUrl: string;
}

export interface Conversation {
  id: string;
  title: string;
  mode: 'chat' | 'agent';
  agentProfile: 'fast' | 'expert';
  controlDeviceId: string | null;
  controlTargetId: string | null;
  controlTargetKind: 'windows' | 'adb' | null;
  createdAt: number;
  updatedAt: number;
}

export interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  createdAt: number;
}

export type TaskStatus = 'queued' | 'starting' | 'running' | 'waiting_approval' | 'stopping' | 'completed' | 'failed' | 'cancelled';

export interface AgentTask {
  id: string;
  conversationId: string;
  source: 'user' | 'schedule';
  scheduleId: string | null;
  prompt: string;
  attachmentIds: string[];
  status: TaskStatus;
  agentProfile: 'fast' | 'expert';
  coordination: {
    status: '' | 'planning' | 'planned' | 'failed';
    plan: {
      objective?: string;
      steps?: Array<string | { title?: string; description?: string }>;
      requirements?: string[];
      acceptanceCriteria?: string[];
      risks?: string[];
    };
  };
  quality: {
    status: '' | 'reviewing' | 'revision_required' | 'revising' | 'passed' | 'exhausted';
    score: number | null;
    attempt: number;
    selectedAttempt: number | null;
    report: {
      summary?: string;
      issues?: string[];
      pageReviews?: Array<{ page: number; score: number; summary: string; issues: string[]; redo: boolean }>;
      reviewedPages?: number;
      threshold?: number;
    };
    history: QualityAttempt[];
  };
  output: string;
  error: string;
  createdAt: number;
  updatedAt: number;
  startedAt: number | null;
  completedAt: number | null;
  artifacts: TaskArtifact[];
}

export interface TaskArtifact {
  id: string;
  path: string;
  filename: string;
  mimeType: string;
  sizeBytes: number;
}

export interface QualityAttempt {
  attempt: number;
  score: number;
  passed: boolean;
  selected: boolean;
  output: string;
  report: AgentTask['quality']['report'];
  artifacts: TaskArtifact[];
  createdAt: number;
}

export interface WorkspacePreview {
  kind: 'text' | 'markdown' | 'audio' | 'image' | 'document';
  filename: string;
  mimeType: string;
  sizeBytes: number;
  sourcePath: string;
  text?: string;
  pages?: string[];
  truncated?: boolean;
}

export interface TaskEvent {
  id: number;
  type: string;
  payload: Record<string, unknown>;
  createdAt: number;
}

export interface WorkflowCategory {
  id: string;
  name: string;
  description: string;
  createdAt: number;
  updatedAt: number;
}

export interface Workflow {
  id: string;
  categoryId: string | null;
  categoryName: string | null;
  sourceConversationId: string | null;
  name: string;
  description: string;
  instructions: string;
  triggers: string[];
  status: string;
  validation: Record<string, unknown>;
  createdAt: number;
  updatedAt: number;
}

export interface SkillRecord {
  id: string;
  name: string;
  description: string;
  source: 'builtin' | 'local' | 'online' | 'shared';
  sourceRef: string;
  triggers: string[];
  status: string;
  validation: Record<string, unknown>;
  createdAt: number;
  updatedAt: number;
}

export interface Attachment {
  id: string;
  conversationId: string | null;
  filename: string;
  mimeType: string;
  sizeBytes: number;
  createdAt: number;
  workspacePath?: string;
}

export interface Schedule {
  id: string;
  name: string | null;
  prompt: string | null;
  enabled: boolean;
  state: string | null;
  schedule_display: string | null;
  schedule?: { kind?: string; display?: string; expr?: string };
  next_run_at: string | null;
  last_run_at: string | null;
  last_error?: string | null;
}

export interface WorkspaceEntry {
  name: string;
  path: string;
  type: 'file' | 'directory';
  mimeType: string;
  sizeBytes: number;
  updatedAt: number;
}

export interface PreviewPort {
  port: number;
  url: string;
  listening: boolean;
  configured: boolean;
}

export interface LoginDevice {
  id: string;
  name: string;
  platform: 'web' | 'android' | 'wechat';
  trusted: boolean;
  activeSessions: number;
  current: boolean;
  createdAt: number;
  lastSeenAt: number;
}

export interface ControlTarget {
  id: string;
  kind: 'windows' | 'adb';
  name: string;
  serial?: string;
  state?: string;
}

export interface ControlDevice {
  id: string;
  name: string;
  hostname: string;
  platform: string;
  online: boolean;
  agentVersion: string;
  capabilities: string[];
  targets: ControlTarget[];
  createdAt: number;
  lastSeenAt: number;
}

export type ControlTaskStatus =
  | 'queued'
  | 'assigned'
  | 'starting'
  | 'running'
  | 'waiting_approval'
  | 'stopping'
  | 'completed'
  | 'failed'
  | 'cancelled';

export interface ControlTask {
  id: string;
  conversationId: string | null;
  deviceId: string;
  deviceName: string;
  targetId: string;
  targetKind: 'windows' | 'adb';
  instruction: string;
  status: ControlTaskStatus;
  output: string;
  error: string;
  createdAt: number;
  updatedAt: number;
  startedAt?: number | null;
  completedAt?: number | null;
}

export interface ControlTaskEvent {
  id: number;
  type: string;
  payload: Record<string, unknown>;
  frameId?: string;
  frameUrl?: string;
  createdAt: number;
}

export interface Savepoint {
  id: string;
  name: string;
  fileCount: number;
  logicalBytes: number;
  storedBytes: number;
  createdAt: number;
}

export interface NotificationPreferences {
  chatCompleted: boolean;
  agentCompleted: boolean;
  scheduleCompleted: boolean;
  taskFailed: boolean;
  approvalRequired: boolean;
  system: boolean;
}

export type NotificationCategory =
  | 'chat_completed'
  | 'agent_completed'
  | 'schedule_completed'
  | 'task_failed'
  | 'approval_required'
  | 'system';

export interface AppNotification {
  id: number;
  category: NotificationCategory;
  title: string;
  body: string;
  entityType: string;
  entityId: string;
  readAt: number | null;
  createdAt: number;
}

export interface BrowserState {
  conversationId: string;
  title: string;
  url: string;
}

export interface RuntimeSummary {
  workerLimit: number;
  workerMin: number;
  workerMax: number;
  dynamicWorkers: boolean;
  resourceBasis: {
    cpuCount: number;
    load1: number;
    cpuReserve: number;
    cpuBudgetPerWorker: number;
    cpuLimit: number;
    memoryAvailableBytes: number | null;
    memoryReserveBytes: number;
    memoryBudgetPerWorkerBytes: number;
    memoryLimit: number;
  };
  queuedTasks: number;
  workers: Array<{ name: string; status: string; userKey: string }>;
  browsers: Array<{ name: string; status: string; userKey: string }>;
}

export interface AdminSettings {
  registrationEnabled: boolean;
  models: AdminModelSettings;
}

export interface AdminModelEndpoint {
  baseUrl: string;
  model: string;
  supportsVision: boolean;
  reasoningEnabled: boolean;
  reasoningEffort: 'minimal' | 'low' | 'medium' | 'high' | 'xhigh' | 'max' | 'ultra';
  apiKeyConfigured: boolean;
  visionBaseUrl: string;
  visionModel: string;
  visionApiKeyConfigured: boolean;
}

export interface AdminModelSettings {
  splitEnabled: boolean;
  chat: AdminModelEndpoint;
  coordinator: AdminModelEndpoint;
  executor: AdminModelEndpoint;
}

export interface AdminModelTestResult {
  ok: boolean;
  role: 'chat' | 'coordinator' | 'executor';
  model: string;
  latencyMs: number;
  stages: Array<{ name: string; ok: boolean; reply: string }>;
}

export interface ActivationCode {
  id: string;
  code: string;
  codePreview: string;
  note: string;
  status: 'active' | 'disabled';
  maxUses: number;
  useCount: number;
  expiresAt: number | null;
  registrationPath: string;
  createdAt: number;
  updatedAt: number;
}
