import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import {
  ArrowUp, Bell, Bot, BrainCircuit, CalendarClock, Check, ChevronDown, CircleStop, Clock3, Download, Eye,
  ArchiveRestore, Cable, Cpu, ExternalLink, File, FileText, Folder, FolderOpen, History, ImagePlus, Loader2, LogIn, LogOut, MessageCircle,
  Globe2, Laptop, MessageSquare, Monitor, PanelLeft, PanelTop, Paperclip, Pencil, Play, Plus, Power, Send, ShieldAlert, Smartphone,
  LibraryBig, RadioTower, RefreshCw, RotateCcw, Settings2, SquarePen, SquareTerminal, Trash2, Search, Upload, UserRound, X, Zap,
  Wifi, WifiOff, KeyRound, Share2,
} from 'lucide-react';
import { authApi, chatApi, controlApi, conversationApi, deviceApi, notificationApi, profileApi, savepointApi, scheduleApi, taskApi, terminalApi, workspaceApi } from '../api';
import type {
  AgentTask, Attachment, Conversation, ControlDevice, ControlTarget, ControlTask, ControlTaskEvent,
  LoginDevice, Message, NotificationPreferences, PreviewPort, RuntimeInfo, Savepoint, Schedule, TaskArtifact, TaskEvent,
  TaskStatus, User, WorkspaceEntry, WorkspacePreview,
} from '../types';
import {
  checkForNativeUpdate, initializeNativeNotificationCursor, isNativeApp, isVersionOutdated, isWechatMiniProgramWebView, isWindowsDesktopApp, nativeAppVersion,
  discardNativeSharedFiles, nativeAuthenticatedDownload, nativeAuthenticatedShare, pendingNativeSharedFiles, persistTrustedDeviceToken, showNativeNotification, uploadNativeSharedFiles,
} from '../native';
import type { NativeSharedFile, NativeSharedUploadResult } from '../native';
import { groupConversationsByDate, summarizeConversationTitle } from '../conversation';
import { MarkdownContent } from './MarkdownContent';

const BrowserPanel = lazy(() => import('./BrowserPanel').then((module) => ({ default: module.BrowserPanel })));
const TerminalPanel = lazy(() => import('./TerminalPanel').then((module) => ({ default: module.TerminalPanel })));
const loadCapabilityCenter = () => import('./CapabilityCenter');
const CapabilityCenter = lazy(() => loadCapabilityCenter().then((module) => ({ default: module.CapabilityCenter })));

interface Props {
  token: string;
  user: User | null;
  runtime: RuntimeInfo;
  checkingSession: boolean;
  onRequestAuth: () => void;
  onLogout: () => void;
}

interface GuestArchive {
  conversations: Conversation[];
  messages: Record<string, Message[]>;
  selectedId: string;
}

export const GUEST_ARCHIVE_KEY = 'mumu-guest-conversations-v1';
const GUEST_IMPORT_ID_KEY = 'mumu-guest-import-id-v1';
const APP_DOWNLOAD_DISMISSED_KEY = 'mumu-app-download-dismissed-v1';
const TOP_DOCK_PREFERENCES_KEY = 'mumu-top-dock-preferences-v1';

interface TopDockPreferences {
  browser: boolean;
  computer: boolean;
  terminal: boolean;
  ports: boolean;
  workspace: boolean;
  schedules: boolean;
  downloads: boolean;
}

type TopDockItem = keyof TopDockPreferences;

const defaultTopDockPreferences: TopDockPreferences = {
  browser: true,
  computer: true,
  terminal: false,
  ports: false,
  workspace: false,
  schedules: false,
  downloads: false,
};

function readTopDockPreferences(): TopDockPreferences {
  try {
    const parsed = JSON.parse(localStorage.getItem(TOP_DOCK_PREFERENCES_KEY) || '{}') as Partial<TopDockPreferences>;
    return {
      browser: typeof parsed.browser === 'boolean' ? parsed.browser : defaultTopDockPreferences.browser,
      computer: typeof parsed.computer === 'boolean' ? parsed.computer : defaultTopDockPreferences.computer,
      terminal: typeof parsed.terminal === 'boolean' ? parsed.terminal : defaultTopDockPreferences.terminal,
      ports: typeof parsed.ports === 'boolean' ? parsed.ports : defaultTopDockPreferences.ports,
      workspace: typeof parsed.workspace === 'boolean' ? parsed.workspace : defaultTopDockPreferences.workspace,
      schedules: typeof parsed.schedules === 'boolean' ? parsed.schedules : defaultTopDockPreferences.schedules,
      downloads: typeof parsed.downloads === 'boolean' ? parsed.downloads : defaultTopDockPreferences.downloads,
    };
  } catch {
    return { ...defaultTopDockPreferences };
  }
}

const activeStatuses = new Set<TaskStatus>(['queued', 'starting', 'running', 'waiting_approval', 'stopping']);
const statusText: Record<TaskStatus, string> = {
  queued: '排队中', starting: '正在启动', running: '执行中', waiting_approval: '等待审批',
  stopping: '正在停止', completed: '已完成', failed: '失败', cancelled: '已取消',
};
const chatStarters = [
  { title: '规划今天的重点', detail: '把目标整理为三项清晰任务', prompt: '帮我梳理今天最重要的三件事' },
  { title: '解释复杂概念', detail: '用简单、准确的方式说明', prompt: '用简单的话解释一个复杂概念' },
  { title: '制定行动计划', detail: '给出可以直接执行的步骤', prompt: '帮我写一份清晰的行动计划' },
];
const agentStarters = [
  { title: '调研一个主题', detail: '查找资料并整理关键结论', prompt: '调研一个主题并整理结论' },
  { title: '完成网页操作', detail: '使用实时浏览器执行任务', prompt: '打开网页完成一项操作' },
  { title: '处理工作区文件', detail: '读取文件并输出最终结果', prompt: '处理文件并输出结果' },
];
const defaultNotificationPreferences: NotificationPreferences = {
  chatCompleted: true,
  agentCompleted: true,
  scheduleCompleted: true,
  taskFailed: true,
  approvalRequired: true,
  system: true,
};
const notificationPreferenceApiKeys: Record<keyof NotificationPreferences, string> = {
  chatCompleted: 'chat_completed',
  agentCompleted: 'agent_completed',
  scheduleCompleted: 'schedule_completed',
  taskFailed: 'task_failed',
  approvalRequired: 'approval_required',
  system: 'system',
};

function makeId(prefix: string) {
  return `${prefix}-${typeof crypto !== 'undefined' && crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`}`;
}

function freshGuestArchive(): GuestArchive {
  const now = Date.now();
  const id = makeId('guest');
  return {
    conversations: [{
      id, title: '新对话', mode: 'chat', agentProfile: 'expert', controlDeviceId: null, controlTargetId: null,
      controlTargetKind: null, createdAt: now, updatedAt: now,
    }],
    messages: { [id]: [] },
    selectedId: id,
  };
}

function readGuestArchive(): GuestArchive {
  try {
    const parsed = JSON.parse(localStorage.getItem(GUEST_ARCHIVE_KEY) || '') as GuestArchive;
    if (!Array.isArray(parsed.conversations) || !parsed.conversations.length || !parsed.selectedId) throw new Error('invalid archive');
    return {
      conversations: parsed.conversations.map((item) => ({
        ...item,
        mode: 'chat' as const,
        agentProfile: item.agentProfile === 'fast' ? 'fast' as const : 'expert' as const,
        controlDeviceId: null,
        controlTargetId: null,
        controlTargetKind: null,
      })),
      messages: parsed.messages && typeof parsed.messages === 'object' ? parsed.messages : {},
      selectedId: parsed.conversations.some((item) => item.id === parsed.selectedId) ? parsed.selectedId : parsed.conversations[0].id,
    };
  } catch {
    return freshGuestArchive();
  }
}

export function guestArchiveImportPayload() {
  let raw = '';
  try { raw = localStorage.getItem(GUEST_ARCHIVE_KEY) || ''; } catch { return null; }
  if (!raw) return null;
  const archive = readGuestArchive();
  const conversations = archive.conversations.map((conversation) => ({
    client_id: conversation.id,
    title: conversation.title,
    created_at: conversation.createdAt,
    messages: (archive.messages[conversation.id] || []).map((message) => ({
      role: message.role,
      content: message.content,
      created_at: message.createdAt,
    })),
  })).filter((conversation) => conversation.messages.length > 0);
  if (!conversations.length) return null;
  let clientImportId = '';
  try {
    clientImportId = localStorage.getItem(GUEST_IMPORT_ID_KEY) || '';
    if (!clientImportId) {
      clientImportId = makeId('import');
      localStorage.setItem(GUEST_IMPORT_ID_KEY, clientImportId);
    }
  } catch {
    clientImportId = makeId('import');
  }
  return { client_import_id: clientImportId, conversations };
}

export function clearGuestArchiveAfterImport() {
  try {
    localStorage.removeItem(GUEST_ARCHIVE_KEY);
    localStorage.removeItem(GUEST_IMPORT_ID_KEY);
  } catch {
    // The imported server copy is already durable.
  }
}

function formatSize(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function guestContext(messages: Message[]) {
  const result: Array<{ role: 'user' | 'assistant'; content: string }> = [];
  let characters = 0;
  for (const message of messages.slice(-80).reverse()) {
    if (result.length && characters + message.content.length > 420_000) break;
    result.push({ role: message.role, content: message.content });
    characters += message.content.length;
  }
  return result.reverse();
}

function eventLabel(event: TaskEvent) {
  const payload = event.payload;
  if (event.type === 'tool.started') return `${String(payload.tool || '工具')} · ${String(payload.preview || '执行中')}`;
  if (event.type === 'tool.completed') return `${String(payload.tool || '工具')} · ${payload.error ? '失败' : '完成'}`;
  if (event.type === 'approval.request') return `等待批准：${String(payload.command || payload.preview || '敏感操作')}`;
  if (event.type.startsWith('subagent.')) return event.type === 'subagent.start' ? '子任务已启动' : '子任务已完成';
  if (event.type === 'task.started') return 'Hermes Worker 已就绪';
  if (event.type === 'task.queued') return '任务已进入队列';
  if (event.type === 'run.steered') return '已发送追加指令';
  if (event.type === 'coordination.started') return '统筹模型正在制定执行计划';
  if (event.type === 'coordination.planned') return '执行计划与验收条件已生成';
  if (event.type === 'coordination.failed') return '统筹计划生成失败';
  if (event.type === 'quality.started') return `统筹模型验收 · 第 ${Number(payload.attempt || 1)} 次`;
  if (event.type === 'quality.reviewed') return `验收评分 ${Number(payload.score || 0)} · ${payload.passed ? '通过' : '准备重做'}`;
  if (event.type === 'quality.revision_started') return `已启动第 ${Number(payload.nextAttempt || 2)} 次生成`;
  if (event.type === 'quality.exhausted') return '自动重做次数已达上限';
  return '';
}

function TaskCard({
  task, events, token, userName, onApprove, onStop, onSteer, onDownloadArtifact,
}: {
  task: AgentTask;
  events: TaskEvent[];
  token: string;
  userName: string;
  onApprove: (task: AgentTask, decision: 'once' | 'deny') => void;
  onStop: (task: AgentTask) => void;
  onSteer: (task: AgentTask, text: string) => void;
  onDownloadArtifact: (artifact: TaskArtifact) => void;
}) {
  const [steer, setSteer] = useState('');
  const [previewArtifact, setPreviewArtifact] = useState<TaskArtifact | null>(null);
  const deltas = events.filter((event) => event.type === 'message.delta').map((event) => String(event.payload.delta || '')).join('');
  const timeline = events.filter((event) => eventLabel(event)).slice(-8);
  const qualityStatus = task.quality?.status || '';
  const qualityHasFinalSelection = qualityStatus === 'passed' || qualityStatus === 'exhausted';
  const answer = qualityStatus && !qualityHasFinalSelection ? '' : (task.output || deltas);
  return (
    <article className="taskCard">
      <div className="messageLine userLine">
        <span className="messageAvatar"><UserRound size={17} /></span>
        <div><header><strong>{userName}</strong><time>{new Date(task.createdAt).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}</time></header><MarkdownContent content={task.prompt} />{task.attachmentIds.length > 0 && <small><Paperclip size={13} />{task.attachmentIds.length} 个附件</small>}</div>
      </div>
      <div className="messageLine assistantLine">
        <span className="messageAvatar agent"><img className="siteLogo" src="/assets/site-logo.jpg" alt="" /></span>
        <div>
          <header><strong>Hermes</strong><span className={`taskStatus ${task.status}`}>{activeStatuses.has(task.status) && task.status !== 'waiting_approval' ? <Loader2 className="spin" size={12} /> : null}{statusText[task.status]}</span></header>
          {timeline.length > 0 && <div className="eventTimeline">{timeline.map((event) => <div key={event.id}><span /><p>{eventLabel(event)}</p></div>)}</div>}
          {task.coordination?.status && <CoordinationPanel task={task} />}
          {task.quality?.status && <QualityReviewPanel task={task} onPreview={setPreviewArtifact} onDownload={onDownloadArtifact} />}
          {task.status === 'waiting_approval' && <div className="approvalBar"><ShieldAlert size={17} /><span>需要批准</span><button className="primaryButton compact" onClick={() => onApprove(task, 'once')}><Check size={15} />允许一次</button><button className="secondaryButton compact" onClick={() => onApprove(task, 'deny')}><X size={15} />拒绝</button></div>}
          {answer && <MarkdownContent className="agentOutput" content={answer} />}
          {task.artifacts?.length > 0 && <div className="taskArtifacts" aria-label="任务生成的内容">{task.artifacts.map((artifact) => isInlineImageArtifact(artifact) ? <TaskImageArtifact key={artifact.id} token={token} conversationId={task.conversationId} artifact={artifact} onPreview={() => setPreviewArtifact(artifact)} onDownload={() => onDownloadArtifact(artifact)} /> : <button className="taskArtifactFile" key={artifact.id} type="button" onClick={() => setPreviewArtifact(artifact)}><FileText size={17} /><span><strong>{artifact.filename}</strong><small>{formatSize(artifact.sizeBytes)}</small></span><Eye size={15} /></button>)}</div>}
          {task.error && <p className="taskError">{task.error}</p>}
          {task.status === 'running' && <form className="steerBar" onSubmit={(event) => { event.preventDefault(); if (steer.trim()) { onSteer(task, steer.trim()); setSteer(''); } }}><input value={steer} onChange={(event) => setSteer(event.target.value)} placeholder="追加指令" /><button className="iconButton" title="发送追加指令" disabled={!steer.trim()}><Send size={15} /></button></form>}
          {activeStatuses.has(task.status) && task.status !== 'stopping' && <button className="stopTaskButton" onClick={() => onStop(task)}><CircleStop size={14} />停止任务</button>}
        </div>
      </div>
      {previewArtifact && <FilePreviewModal token={token} conversationId={task.conversationId} artifact={previewArtifact} onClose={() => setPreviewArtifact(null)} onDownload={() => onDownloadArtifact(previewArtifact)} />}
    </article>
  );
}

const inlineImageMimeTypes = new Set([
  'image/avif', 'image/bmp', 'image/gif', 'image/jpeg', 'image/png', 'image/webp',
]);

function isInlineImageArtifact(artifact: TaskArtifact) {
  return inlineImageMimeTypes.has(artifact.mimeType.toLowerCase().split(';', 1)[0].trim());
}

function CoordinationPanel({ task }: { task: AgentTask }) {
  const plan = task.coordination.plan;
  const steps = Array.isArray(plan.steps) ? plan.steps : [];
  const criteria = Array.isArray(plan.acceptanceCriteria) ? plan.acceptanceCriteria : [];
  return (
    <details className={`coordinationPanel ${task.coordination.status}`}>
      <summary><BrainCircuit size={15} /><span><strong>统筹计划</strong><small>{String(plan.objective || (task.coordination.status === 'planning' ? '正在生成' : '已生成'))}</small></span></summary>
      {(steps.length > 0 || criteria.length > 0) && <div><ol>{steps.slice(0, 12).map((step, index) => <li key={index}>{typeof step === 'string' ? step : String(step.title || step.description || '')}</li>)}</ol>{criteria.length > 0 && <><h4>验收条件</h4><ul>{criteria.slice(0, 12).map((item, index) => <li key={index}>{String(item)}</li>)}</ul></>}</div>}
    </details>
  );
}

function QualityReviewPanel({
  task, onPreview, onDownload,
}: {
  task: AgentTask;
  onPreview: (artifact: TaskArtifact) => void;
  onDownload: (artifact: TaskArtifact) => void;
}) {
  const quality = task.quality;
  const history = quality.history || [];
  const label = quality.status === 'passed' ? '验收通过'
    : quality.status === 'exhausted' ? '已采用最高分版本'
      : quality.status === 'revising' ? '正在重做'
        : quality.status === 'reviewing' ? '正在验收' : '需要重做';
  return (
    <div className="qualityReviewGroup">
      <div className={`qualityReview ${quality.status}`}>
        <ShieldAlert size={16} />
        <span><strong>{label}</strong><small>第 {quality.attempt} 次{quality.score !== null ? ` · ${quality.score} 分` : ''}{quality.selectedAttempt ? ` · 采用第 ${quality.selectedAttempt} 次` : ''}</small></span>
        {(quality.report?.issues?.length || 0) > 0 && <details><summary>问题</summary><ul>{quality.report.issues!.slice(0, 20).map((issue, index) => <li key={`${index}:${issue}`}>{issue}</li>)}</ul></details>}
      </div>
      {history.length > 0 && <details className="qualityHistory">
        <summary><RotateCcw size={13} />重做记录 <small>{history.length} 个版本</small></summary>
        <div className="qualityAttemptList">
          {[...history].reverse().map((attempt) => <details className={`qualityAttempt${attempt.selected ? ' selected' : ''}`} key={attempt.attempt}>
            <summary><span>第 {attempt.attempt} 次 · {attempt.score} 分</span><small>{attempt.selected ? '最终采用' : attempt.passed ? '已通过' : '未采用'}</small></summary>
            <div>
              {attempt.report.summary && <p>{attempt.report.summary}</p>}
              {(attempt.report.issues?.length || 0) > 0 && <ul>{attempt.report.issues!.slice(0, 20).map((issue, index) => <li key={`${attempt.attempt}:${index}`}>{issue}</li>)}</ul>}
              {attempt.output && <div className="qualityAttemptOutput"><h4>本次输出</h4><MarkdownContent content={attempt.output} /></div>}
              {attempt.artifacts.length > 0 && <div className="qualityAttemptFiles">{attempt.artifacts.map((artifact) => <span key={artifact.id}><button type="button" onClick={() => onPreview(artifact)} title={`预览 ${artifact.filename}`}><Eye size={13} />{artifact.filename}</button><button type="button" onClick={() => onDownload(artifact)} title={`下载 ${artifact.filename}`}><Download size={13} /></button></span>)}</div>}
            </div>
          </details>)}
        </div>
      </details>}
    </div>
  );
}

function PreviewBlobAsset({
  token, conversationId, path, kind, label,
}: {
  token: string;
  conversationId: string;
  path: string;
  kind: 'image' | 'audio';
  label: string;
}) {
  const [url, setUrl] = useState('');
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let disposed = false;
    let objectUrl = '';
    setUrl('');
    setFailed(false);
    void workspaceApi.fileBlob(token, conversationId, path).then((blob) => {
      if (disposed) return;
      objectUrl = URL.createObjectURL(blob);
      setUrl(objectUrl);
    }).catch(() => { if (!disposed) setFailed(true); });
    return () => {
      disposed = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [conversationId, path, token]);
  if (failed) return <div className="filePreviewAssetError">预览内容读取失败</div>;
  if (!url) return <div className="filePreviewAssetLoading"><Loader2 className="spin" size={20} /></div>;
  return kind === 'audio'
    ? <audio className="filePreviewAudio" src={url} controls preload="metadata">当前环境不支持音频播放。</audio>
    : <img className="filePreviewImage" src={url} alt={label} />;
}

function FilePreviewModal({
  token, conversationId, artifact, onClose, onDownload,
}: {
  token: string;
  conversationId: string;
  artifact: TaskArtifact;
  onClose: () => void;
  onDownload: () => void;
}) {
  const [preview, setPreview] = useState<WorkspacePreview | null>(null);
  const [error, setError] = useState('');
  const [sharing, setSharing] = useState(false);
  useEffect(() => {
    let disposed = false;
    setPreview(null);
    setError('');
    void workspaceApi.preview(token, conversationId, artifact.path).then((result) => {
      if (!disposed) setPreview(result.preview);
    }).catch((reason) => {
      if (!disposed) setError(reason instanceof Error ? reason.message : '文件预览失败');
    });
    return () => { disposed = true; };
  }, [artifact.path, conversationId, token]);

  async function share() {
    if (sharing) return;
    setSharing(true);
    setError('');
    try {
      const url = workspaceApi.downloadUrl(conversationId, artifact.path);
      if (await nativeAuthenticatedShare(url, artifact.filename, artifact.mimeType)) return;
      const blob = await workspaceApi.fileBlob(token, conversationId, artifact.path);
      const file = new window.File([blob], artifact.filename, { type: artifact.mimeType || blob.type });
      const shareNavigator = navigator as Navigator & {
        canShare?: (data: ShareData) => boolean;
        share?: (data: ShareData) => Promise<void>;
      };
      const shareData: ShareData = { title: artifact.filename, files: [file] };
      if (shareNavigator.share && (!shareNavigator.canShare || shareNavigator.canShare(shareData))) {
        await shareNavigator.share(shareData);
      } else {
        onDownload();
        setError('当前环境不支持直接分享到应用，文件已转为下载。');
      }
    } catch (reason) {
      if ((reason as Error)?.name !== 'AbortError') setError(reason instanceof Error ? reason.message : '文件分享失败');
    } finally {
      setSharing(false);
    }
  }

  return (
    <div className="modalBackdrop filePreviewBackdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="modal filePreviewModal" role="dialog" aria-modal="true" aria-label={`预览 ${artifact.filename}`}>
        <header>
          <div><FileText size={18} /><span><strong>{artifact.filename}</strong><small>{formatSize(artifact.sizeBytes)} · 最大预览 15MB</small></span></div>
          <nav><button type="button" className="iconButton" title="分享到其他应用" onClick={() => void share()} disabled={sharing}>{sharing ? <Loader2 className="spin" size={16} /> : <Share2 size={16} />}</button><button type="button" className="iconButton" title="下载文件" onClick={onDownload}><Download size={16} /></button><button type="button" className="iconButton" title="关闭预览" onClick={onClose}><X size={17} /></button></nav>
        </header>
        {error && <p className="filePreviewError">{error}</p>}
        <div className="filePreviewBody">
          {!preview && !error && <div className="filePreviewLoading"><Loader2 className="spin" size={22} /><span>正在生成预览...</span></div>}
          {preview?.kind === 'markdown' && <div className="filePreviewText markdown"><MarkdownContent content={preview.text || ''} /></div>}
          {preview?.kind === 'text' && <pre className="filePreviewText">{preview.text || ''}</pre>}
          {preview?.kind === 'image' && <PreviewBlobAsset token={token} conversationId={conversationId} path={preview.sourcePath} kind="image" label={preview.filename} />}
          {preview?.kind === 'audio' && <div className="filePreviewAudioWrap"><FileText size={34} /><strong>{preview.filename}</strong><PreviewBlobAsset token={token} conversationId={conversationId} path={preview.sourcePath} kind="audio" label={preview.filename} /></div>}
          {preview?.kind === 'document' && <div className="filePreviewPages">{(preview.pages || []).map((path, index) => <figure key={path}><PreviewBlobAsset token={token} conversationId={conversationId} path={path} kind="image" label={`${preview.filename} 第 ${index + 1} 页`} /><figcaption>第 {index + 1} 页</figcaption></figure>)}</div>}
          {preview?.truncated && <p className="filePreviewNotice">预览内容较长，仅显示前一部分；完整内容可下载查看。</p>}
        </div>
      </section>
    </div>
  );
}

function TaskImageArtifact({
  token, conversationId, artifact, onPreview, onDownload,
}: {
  token: string;
  conversationId: string;
  artifact: TaskArtifact;
  onPreview: () => void;
  onDownload: () => void;
}) {
  const [url, setUrl] = useState('');
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let cancelled = false;
    let objectUrl = '';
    setUrl('');
    setFailed(false);
    void workspaceApi.fileBlob(token, conversationId, artifact.path).then((blob) => {
      if (cancelled) return;
      objectUrl = URL.createObjectURL(blob);
      setUrl(objectUrl);
    }).catch(() => { if (!cancelled) setFailed(true); });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [artifact.path, conversationId, token]);

  if (!url) return <div className={`taskImageArtifact loading${failed ? ' failed' : ''}`}>{failed ? <><ImagePlus size={18} /><span>图片读取失败</span><button type="button" onClick={onDownload}><Download size={14} />下载文件</button></> : <Loader2 className="spin" size={18} />}</div>;
  return (
    <figure className="taskImageArtifact">
      <button type="button" className="taskImagePreviewButton" onClick={onPreview} title="预览图片"><img src={url} alt={artifact.filename} /></button>
      <figcaption><span><ImagePlus size={14} /><strong>{artifact.filename}</strong><small>{formatSize(artifact.sizeBytes)}</small></span><button type="button" onClick={onDownload} title="下载图片"><Download size={15} />下载</button></figcaption>
    </figure>
  );
}

function RemoteTaskCard({
  task, events, token, userName, onApprove, onStop, onOpen,
}: {
  task: ControlTask;
  events: ControlTaskEvent[];
  token: string;
  userName: string;
  onApprove: (task: ControlTask, decision: 'approve' | 'deny') => void;
  onStop: (task: ControlTask) => void;
  onOpen: () => void;
}) {
  const timeline = events.slice(-8);
  const latestFrame = [...events].reverse().find((event) => event.frameId);
  return (
    <article className="taskCard remoteTaskCard">
      <div className="messageLine userLine">
        <span className="messageAvatar"><UserRound size={17} /></span>
        <div><header><strong>{userName}</strong><time>{new Date(task.createdAt).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}</time></header><MarkdownContent content={task.instruction} /></div>
      </div>
      <div className="messageLine assistantLine">
        <span className="messageAvatar remote"><Laptop size={16} /></span>
        <div>
          <header><strong>{task.deviceName || '远程设备'} · {task.targetKind === 'adb' ? 'ADB' : 'Windows'}</strong><span className={`taskStatus ${task.status}`}>{controlActiveStatuses.has(task.status) && task.status !== 'waiting_approval' ? <Loader2 className="spin" size={12} /> : null}{controlStatusText[task.status]}</span></header>
          {timeline.length > 0 && <div className="eventTimeline">{timeline.map((event) => <div key={event.id}><span /><p>{controlEventSummary(event)}</p></div>)}</div>}
          {task.status === 'waiting_approval' && <div className="approvalBar"><ShieldAlert size={17} /><span>需要批准</span><button className="primaryButton compact" onClick={() => onApprove(task, 'approve')}><Check size={15} />允许一次</button><button className="secondaryButton compact" onClick={() => onApprove(task, 'deny')}><X size={15} />拒绝</button></div>}
          {task.output && <MarkdownContent className="agentOutput" content={task.output} />}
          {latestFrame?.frameId && <RemoteFrameResult token={token} task={task} event={latestFrame} onOpen={onOpen} />}
          {task.error && <p className="taskError">{task.error}</p>}
          <div className="remoteTaskActions"><button className="stopTaskButton" onClick={onOpen}><Monitor size={14} />查看详情</button>{controlActiveStatuses.has(task.status) && task.status !== 'stopping' && <button className="stopTaskButton" onClick={() => onStop(task)}><CircleStop size={14} />停止任务</button>}</div>
        </div>
      </div>
    </article>
  );
}

function RemoteFrameResult({
  token, task, event, onOpen,
}: {
  token: string;
  task: ControlTask;
  event: ControlTaskEvent;
  onOpen: () => void;
}) {
  const [url, setUrl] = useState('');
  useEffect(() => {
    let cancelled = false;
    let objectUrl = '';
    if (!event.frameId) return undefined;
    void controlApi.frame(token, event.frameId).then((blob) => {
      if (cancelled) return;
      objectUrl = URL.createObjectURL(blob);
      setUrl(objectUrl);
    }).catch(() => { if (!cancelled) setUrl(''); });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [event.frameId, token]);

  if (!url) return <div className="remoteFrameResult loading"><Loader2 className="spin" size={18} /></div>;
  return (
    <figure className="remoteFrameResult">
      <button type="button" title="查看电脑控制详情" onClick={onOpen}><img src={url} alt={`${task.deviceName || '远程电脑'}返回的截图`} /></button>
      <figcaption><span><Monitor size={14} />电脑截图 · {new Date(event.createdAt).toLocaleTimeString('zh-CN')}</span><a href={url} download={`电脑截图-${task.id.slice(0, 8)}.jpg`} title="下载截图"><Download size={15} />下载</a></figcaption>
    </figure>
  );
}

export function Workspace({ token, user, runtime, checkingSession, onRequestAuth, onLogout }: Props) {
  const initial = useRef<GuestArchive | null>(null);
  if (!initial.current) initial.current = readGuestArchive();
  const [conversations, setConversations] = useState<Conversation[]>(initial.current.conversations);
  const [selectedId, setSelectedId] = useState(initial.current.selectedId);
  const [deletingConversationIds, setDeletingConversationIds] = useState<Set<string>>(() => new Set());
  const [messageMap, setMessageMap] = useState<Record<string, Message[]>>(initial.current.messages);
  const [dataOwner, setDataOwner] = useState<'guest' | 'user' | 'loading'>(token ? 'loading' : 'guest');
  const [tasks, setTasks] = useState<AgentTask[]>([]);
  const [events, setEvents] = useState<Record<string, TaskEvent[]>>({});
  const [remoteTasks, setRemoteTasks] = useState<ControlTask[]>([]);
  const [remoteEvents, setRemoteEvents] = useState<Record<string, ControlTaskEvent[]>>({});
  const [controlDevices, setControlDevices] = useState<ControlDevice[]>([]);
  const [input, setInput] = useState('');
  const [composerPreview, setComposerPreview] = useState(false);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [loading, setLoading] = useState(Boolean(token));
  const [sending, setSending] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [switchingMode, setSwitchingMode] = useState(false);
  const [error, setError] = useState('');
  const [contextCompressed, setContextCompressed] = useState(false);
  const [conversationQuery, setConversationQuery] = useState('');
  const [searchOpen, setSearchOpen] = useState(false);
  const [leftOpen, setLeftOpen] = useState(() => window.innerWidth > 860);
  const [browserOpen, setBrowserOpen] = useState(false);
  const [browserMounted, setBrowserMounted] = useState(false);
  const [browserRefreshKey, setBrowserRefreshKey] = useState(0);
  const [workspaceOpen, setWorkspaceOpen] = useState(false);
  const [scheduleOpen, setScheduleOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [capabilityOpen, setCapabilityOpen] = useState(false);
  const [computerControlOpen, setComputerControlOpen] = useState(false);
  const [conversationBindingOpen, setConversationBindingOpen] = useState(false);
  const [terminalOpen, setTerminalOpen] = useState(false);
  const [portsOpen, setPortsOpen] = useState(false);
  const [appDownloadPromptOpen, setAppDownloadPromptOpen] = useState(false);
  const [windowsUpdatePromptOpen, setWindowsUpdatePromptOpen] = useState(false);
  const [topDockPreferences, setTopDockPreferences] = useState<TopDockPreferences>(readTopDockPreferences);
  const [sharedFiles, setSharedFiles] = useState<NativeSharedFile[]>([]);
  const [sharedDialogOpen, setSharedDialogOpen] = useState(false);
  const [sharedUploadBusy, setSharedUploadBusy] = useState(false);
  const [backgroundUrl, setBackgroundUrl] = useState('');
  const [mentionEntries, setMentionEntries] = useState<WorkspaceEntry[]>([]);
  const [mentionIndex, setMentionIndex] = useState(0);
  const [mentionRange, setMentionRange] = useState<{ start: number; end: number } | null>(null);
  const eventCursors = useRef<Record<string, number>>({});
  const remoteEventCursors = useRef<Record<string, number>>({});
  const mentionRequest = useRef(0);
  const notificationCursor = useRef(0);
  const messageListRef = useRef<HTMLDivElement>(null);
  const messageEndRef = useRef<HTMLDivElement>(null);
  const followMessageTailRef = useRef(true);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const selected = conversations.find((item) => item.id === selectedId) || null;
  const messages = messageMap[selectedId] || [];
  const isGuest = !token || !user;
  const canUseAgent = Boolean(user && user.accessTier === 'vip');
  const mode = canUseAgent ? selected?.mode || 'chat' : 'chat';
  const experience: 'chat' | 'fast' | 'expert' = mode === 'chat' ? 'chat' : selected?.agentProfile || 'expert';
  const selectedControlDevice = controlDevices.find((item) => item.id === selected?.controlDeviceId) || null;
  const agentTimeline = [
    ...tasks.map((task) => ({ kind: 'local' as const, createdAt: task.createdAt, task })),
    ...remoteTasks.map((task) => ({ kind: 'remote' as const, createdAt: task.createdAt, task })),
  ].sort((left, right) => left.createdAt - right.createdAt);
  const conversationGroups = groupConversationsByDate(conversations);
  const visibleConversations = groupConversationsByDate(conversations)
    .flatMap((group) => group.conversations)
    .filter((conversation) => conversation.title.toLocaleLowerCase().includes(conversationQuery.trim().toLocaleLowerCase()));
  const hasConversationContent = mode === 'chat' ? messages.length > 0 : agentTimeline.length > 0;
  const emptyView = !loading && !checkingSession && !hasConversationContent;
  const starters = mode === 'agent' ? agentStarters : chatStarters;

  useEffect(() => {
    try { localStorage.setItem(TOP_DOCK_PREFERENCES_KEY, JSON.stringify(topDockPreferences)); }
    catch { /* Browser privacy settings can disable local storage. */ }
  }, [topDockPreferences]);

  useEffect(() => {
    if (!topDockPreferences.browser) { setBrowserOpen(false); setBrowserMounted(false); }
    if (!topDockPreferences.computer) setConversationBindingOpen(false);
    if (!topDockPreferences.terminal) setTerminalOpen(false);
    if (!topDockPreferences.ports) setPortsOpen(false);
    if (!topDockPreferences.workspace) setWorkspaceOpen(false);
    if (!topDockPreferences.schedules) setScheduleOpen(false);
  }, [topDockPreferences]);

  useEffect(() => {
    if (isGuest || isNativeApp() || isWechatMiniProgramWebView() || !window.matchMedia('(max-width: 860px)').matches) return;
    let permanentlyDismissed = false;
    try { permanentlyDismissed = localStorage.getItem(APP_DOWNLOAD_DISMISSED_KEY) === '1'; } catch { /* no-op */ }
    if (!permanentlyDismissed) setAppDownloadPromptOpen(true);
  }, [isGuest]);

  useEffect(() => {
    if (!isWindowsDesktopApp()) return;
    setWindowsUpdatePromptOpen(isVersionOutdated(nativeAppVersion(), runtime.windowsAgentVersion));
  }, [runtime.windowsAgentVersion]);

  useEffect(() => {
    if (!isNativeApp() || !canUseAgent) return;
    const refresh = () => {
      const pending = pendingNativeSharedFiles();
      setSharedFiles(pending);
      if (pending.length) setSharedDialogOpen(true);
    };
    const uploaded = (event: Event) => {
      const detail = (event as CustomEvent<NativeSharedUploadResult>).detail;
      const uploadedAttachments = Array.isArray(detail?.attachments) ? detail.attachments : [];
      if (detail?.conversationId) {
        setSelectedId(detail.conversationId);
        setConversations((items) => items.map((item) => item.id === detail.conversationId ? { ...item, mode: 'agent' } : item));
      }
      if (uploadedAttachments.length) {
        setAttachments((items) => [...items, ...uploadedAttachments].slice(0, 8));
        const mentions = uploadedAttachments
          .map((item) => item.workspacePath ? `@<${item.workspacePath}>` : '')
          .filter(Boolean)
          .join('\n');
        if (mentions) setInput((value) => value.trim() ? `${value.trim()}\n${mentions}` : `请帮我整理或修改以下文件：\n${mentions}`);
      }
      setSharedUploadBusy(false);
      setSharedDialogOpen(false);
      refresh();
    };
    const failed = (event: Event) => {
      setSharedUploadBusy(false);
      setError((event as CustomEvent<{ message?: string }>).detail?.message || '分享文件上传失败');
    };
    refresh();
    window.addEventListener('vmss-shared-files-changed', refresh);
    window.addEventListener('vmss-shared-files-uploaded', uploaded);
    window.addEventListener('vmss-shared-files-failed', failed);
    return () => {
      window.removeEventListener('vmss-shared-files-changed', refresh);
      window.removeEventListener('vmss-shared-files-uploaded', uploaded);
      window.removeEventListener('vmss-shared-files-failed', failed);
    };
  }, [canUseAgent]);

  useEffect(() => {
    if (!canUseAgent || !token) return;
    const timer = window.setTimeout(() => {
      void loadCapabilityCenter().then((module) => module.preloadCapabilities(token)).catch(() => undefined);
    }, 500);
    return () => window.clearTimeout(timer);
  }, [canUseAgent, token]);

  useEffect(() => {
    let cancelled = false;
    setError('');
    setBrowserOpen(false);
    setBrowserMounted(false);
    setTerminalOpen(false);
    setPortsOpen(false);
    setTasks([]);
    setEvents({});
    setRemoteTasks([]);
    setRemoteEvents({});
    setControlDevices([]);
    if (!token) {
      const archive = readGuestArchive();
      setConversations(archive.conversations);
      setMessageMap(archive.messages);
      setSelectedId(archive.selectedId);
      setDataOwner('guest');
      setLoading(false);
      return () => { cancelled = true; };
    }
    setDataOwner('loading');
    setLoading(true);
    void conversationApi.list(token).then(async (result) => {
      let items = canUseAgent ? result.conversations : result.conversations.filter((item) => item.mode === 'chat');
      if (!items.length) items = [(await conversationApi.create(token, '新对话', 'chat')).conversation];
      if (!cancelled) {
        setConversations(items);
        setSelectedId(items[0].id);
        setMessageMap({});
        setDataOwner('user');
      }
    }).catch((reason) => {
      if (!cancelled) setError(reason instanceof Error ? reason.message : '无法读取会话');
    }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [canUseAgent, token]);

  useEffect(() => {
    if (!token || dataOwner !== 'user' || !canUseAgent) return;
    let cancelled = false;
    async function refreshDevices() {
      if (document.hidden) return;
      try {
        const result = await controlApi.devices(token);
        if (!cancelled) setControlDevices(result.devices);
      } catch {
        // The conversation surface reports binding and task errors when the user acts.
      }
    }
    void refreshDevices();
    const timer = window.setInterval(() => void refreshDevices(), 5_000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [canUseAgent, dataOwner, token]);

  useEffect(() => {
    let cancelled = false;
    let initialized = false;
    notificationCursor.current = 0;
    if (!token) return () => { cancelled = true; };
    async function pollNotifications() {
      if (document.hidden) return;
      try {
        const result = await notificationApi.list(token, notificationCursor.current);
        if (cancelled) return;
        if (!initialized) {
          const latest = result.notifications.reduce((maximum, item) => Math.max(maximum, item.id), 0);
          notificationCursor.current = latest;
          initializeNativeNotificationCursor(latest);
          initialized = true;
          return;
        }
        for (const notification of result.notifications) {
          showNativeNotification(notification);
          notificationCursor.current = Math.max(notificationCursor.current, notification.id);
        }
      } catch {
        // Session validation and the main workspace surface connection errors.
      }
    }
    void pollNotifications();
    const timer = window.setInterval(() => void pollNotifications(), 10_000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [token]);

  useEffect(() => {
    if (dataOwner !== 'guest') return;
    localStorage.setItem(GUEST_ARCHIVE_KEY, JSON.stringify({ conversations, messages: messageMap, selectedId } satisfies GuestArchive));
  }, [conversations, dataOwner, messageMap, selectedId]);

  useEffect(() => {
    if (!token || !selectedId || mode !== 'chat' || dataOwner !== 'user') return;
    if (Object.prototype.hasOwnProperty.call(messageMap, selectedId)) return;
    let cancelled = false;
    setLoading(true);
    void conversationApi.messages(token, selectedId)
      .then((result) => { if (!cancelled) setMessageMap((value) => ({ ...value, [selectedId]: result.messages })); })
      .catch((reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : '无法读取对话'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [dataOwner, mode, selectedId, token]);

  useEffect(() => {
    if (!token || !selectedId || mode !== 'agent' || dataOwner !== 'user') return;
    let cancelled = false;
    let refreshing = false;
    setTasks([]);
    setEvents({});
    setRemoteTasks([]);
    setRemoteEvents({});
    setAttachments([]);
    eventCursors.current = {};
    remoteEventCursors.current = {};
    async function refresh() {
      if (document.hidden || refreshing) return;
      refreshing = true;
      try {
        const [localResult, remoteResult] = await Promise.all([
          conversationApi.tasks(token, selectedId),
          controlApi.tasks(token, selectedId),
        ]);
        const current = localResult.tasks;
        const currentRemote = remoteResult.tasks;
        if (cancelled) return;
        setTasks(current);
        setRemoteTasks(currentRemote);
        setError((value) => /network error|failed to fetch|network request failed|任务流连接中断|网络.*(?:中断|错误)/i.test(value) ? '' : value);
        const remoteBatches = await Promise.all(currentRemote.map(async (task) => {
            const cursor = remoteEventCursors.current[task.id] || 0;
            const received = (await controlApi.events(token, task.id, cursor)).events;
            return { taskId: task.id, received };
          }));
        if (cancelled) return;
        const remoteUpdates = remoteBatches.filter((batch) => batch.received.length);
        for (const batch of remoteUpdates) remoteEventCursors.current[batch.taskId] = batch.received[batch.received.length - 1].id;
        if (remoteUpdates.length) setRemoteEvents((value) => {
          const next = { ...value };
          for (const batch of remoteUpdates) next[batch.taskId] = [...(next[batch.taskId] || []), ...batch.received];
          return next;
        });
      } catch (reason) {
        if (!cancelled) setError(reason instanceof Error ? reason.message : '任务状态刷新失败');
      } finally {
        refreshing = false;
      }
    }
    void refresh();
    const timer = window.setInterval(() => void refresh(), 5000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [dataOwner, mode, selectedId, token]);

  const taskStreamKey = tasks
    .filter((task) => task.conversationId === selectedId)
    .map((task) => `${task.id}:${task.status}`)
    .join('|');

  useEffect(() => {
    if (!token || !selectedId || mode !== 'agent' || dataOwner !== 'user') return;
    let cancelled = false;
    const controllers: AbortController[] = [];
    const selectedTasks = tasks.filter((task) => task.conversationId === selectedId);
    for (const initialTask of selectedTasks) {
      const controller = new AbortController();
      controllers.push(controller);
      void (async () => {
        let terminal = ['completed', 'failed', 'cancelled'].includes(initialTask.status);
        do {
          try {
            await taskApi.stream(
              token,
              initialTask.id,
              eventCursors.current[initialTask.id] || 0,
              {
                onEvent: (event) => {
                  setError((value) => /network error|failed to fetch|network request failed|任务流连接中断|网络.*(?:中断|错误)/i.test(value) ? '' : value);
                  eventCursors.current[initialTask.id] = Math.max(eventCursors.current[initialTask.id] || 0, event.id);
                  setEvents((value) => {
                    const current = value[initialTask.id] || [];
                    if (current.some((item) => item.id === event.id)) return value;
                    return { ...value, [initialTask.id]: [...current, event].slice(-500) };
                  });
                  if (String(event.payload.tool || '').startsWith('browser_')) {
                    setBrowserRefreshKey((value) => value + 1);
                  }
                },
                onTask: (nextTask) => {
                  setError((value) => /network error|failed to fetch|network request failed|任务流连接中断|网络.*(?:中断|错误)/i.test(value) ? '' : value);
                  terminal = ['completed', 'failed', 'cancelled'].includes(nextTask.status);
                  setTasks((items) => items.map((item) => {
                    if (item.id !== nextTask.id) return item;
                    if (item.updatedAt === nextTask.updatedAt && item.status === nextTask.status) return item;
                    return nextTask;
                  }));
                },
              },
              controller.signal,
            );
          } catch (reason) {
            if (!cancelled && !controller.signal.aborted && (reason as Error)?.name !== 'AbortError') {
              setError(reason instanceof Error ? reason.message : '任务流连接中断，正在重连');
            }
          }
          if (!cancelled && !controller.signal.aborted && !terminal) {
            await new Promise((resolve) => window.setTimeout(resolve, 1000));
          }
        } while (!cancelled && !controller.signal.aborted && !terminal);
      })();
    }
    return () => {
      cancelled = true;
      for (const controller of controllers) controller.abort();
    };
  }, [dataOwner, mode, selectedId, taskStreamKey, token]);

  useEffect(() => {
    followMessageTailRef.current = true;
  }, [selectedId]);

  useEffect(() => {
    if (!followMessageTailRef.current) return;
    const frame = window.requestAnimationFrame(() => {
      messageEndRef.current?.scrollIntoView({ behavior: 'auto', block: 'end' });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [events, messageMap, remoteEvents, remoteTasks, selectedId, tasks]);

  useEffect(() => {
    const keepTailVisible = () => {
      if (!followMessageTailRef.current || document.activeElement !== textareaRef.current) return;
      window.requestAnimationFrame(() => {
        const list = messageListRef.current;
        if (list) list.scrollTop = list.scrollHeight;
      });
    };
    window.addEventListener('vmss-viewport-change', keepTailVisible);
    return () => window.removeEventListener('vmss-viewport-change', keepTailVisible);
  }, []);

  useEffect(() => {
    mentionRequest.current += 1;
    setMentionEntries([]);
    setMentionRange(null);
  }, [mode, selectedId, token]);

  useEffect(() => {
    let cancelled = false;
    if (!token) {
      setBackgroundUrl('');
      return () => { cancelled = true; };
    }
    void profileApi.background(token).then((blob) => {
      if (!cancelled) setBackgroundUrl(blob ? URL.createObjectURL(blob) : '');
    }).catch(() => undefined);
    return () => { cancelled = true; };
  }, [token]);

  useEffect(() => () => {
    if (backgroundUrl.startsWith('blob:')) URL.revokeObjectURL(backgroundUrl);
  }, [backgroundUrl]);

  useEffect(() => {
    if (!searchOpen) return;
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        setSearchOpen(false);
        setConversationQuery('');
      }
    }
    window.addEventListener('keydown', closeOnEscape);
    return () => window.removeEventListener('keydown', closeOnEscape);
  }, [searchOpen]);

  async function createConversation() {
    setError('');
    if (isGuest) {
      const now = Date.now();
      const id = makeId('guest');
      const conversation: Conversation = {
        id, title: '新对话', mode: 'chat', agentProfile: 'expert', controlDeviceId: null, controlTargetId: null,
        controlTargetKind: null, createdAt: now, updatedAt: now,
      };
      setConversations((items) => [conversation, ...items]);
      setMessageMap((value) => ({ ...value, [id]: [] }));
      setSelectedId(id);
      setLeftOpen(window.innerWidth > 860);
      return;
    }
    try {
      const created = (await conversationApi.create(token, '新对话', mode, selected?.agentProfile || 'expert')).conversation;
      setConversations((items) => [created, ...items]);
      setSelectedId(created.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '创建会话失败');
    }
  }

  async function removeConversation(id: string) {
    if (deletingConversationIds.has(id)) return;
    setError('');
    setDeletingConversationIds((value) => new Set(value).add(id));
    if (isGuest) {
      let next = conversations.filter((item) => item.id !== id);
      let nextId = selectedId;
      if (!next.length) {
        const archive = freshGuestArchive();
        next = archive.conversations;
        nextId = archive.selectedId;
        setMessageMap(archive.messages);
      } else {
        setMessageMap((value) => { const copy = { ...value }; delete copy[id]; return copy; });
        if (id === selectedId) nextId = next[0].id;
      }
      setConversations(next);
      setSelectedId(nextId);
      setDeletingConversationIds((value) => { const copy = new Set(value); copy.delete(id); return copy; });
      return;
    }
    try {
      await conversationApi.remove(token, id);
      let next = (await conversationApi.list(token)).conversations;
      if (!canUseAgent) next = next.filter((item) => item.mode === 'chat');
      if (!next.length) next = [(await conversationApi.create(token, '新对话', 'chat')).conversation];
      setConversations(next);
      setSelectedId((value) => value === id || !next.some((item) => item.id === value) ? next[0].id : value);
      setMessageMap((value) => { const copy = { ...value }; delete copy[id]; return copy; });
      setTasks((value) => value.filter((task) => task.conversationId !== id));
      setRemoteTasks((value) => value.filter((task) => task.conversationId !== id));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '删除失败');
    } finally {
      setDeletingConversationIds((value) => { const copy = new Set(value); copy.delete(id); return copy; });
    }
  }

  async function changeExperience(nextExperience: 'chat' | 'fast' | 'expert') {
    const nextMode = nextExperience === 'chat' ? 'chat' : 'agent';
    const nextProfile = nextExperience === 'fast' ? 'fast' : 'expert';
    if (!selected || (nextMode === mode && (nextMode === 'chat' || selected.agentProfile === nextProfile))) return;
    if (isGuest) {
      onRequestAuth();
      return;
    }
    if (nextMode === 'agent' && !canUseAgent) {
      setSettingsOpen(true);
      setError('输入 VIP 激活码后即可使用 Agent、浏览器和电脑控制');
      return;
    }
    setSwitchingMode(true);
    setError('');
    try {
      const result = await conversationApi.update(token, selected.id, { mode: nextMode, ...(nextMode === 'agent' ? { agent_profile: nextProfile } : {}) });
      setConversations((items) => items.map((item) => item.id === selected.id ? result.conversation : item));
      setAttachments([]);
      setContextCompressed(false);
      if (nextMode === 'chat') {
        setBrowserOpen(false);
        setBrowserMounted(false);
        setTerminalOpen(false);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '切换模式失败');
    } finally {
      setSwitchingMode(false);
    }
  }

  async function upload(files: FileList | File[] | null) {
    if (!files || !selectedId || isGuest || mode !== 'agent') return;
    setUploading(true);
    setError('');
    try {
      const uploaded: Attachment[] = [];
      for (const file of Array.from(files).slice(0, 8 - attachments.length)) uploaded.push(await conversationApi.upload(token, selectedId, file));
      setAttachments((items) => [...items, ...uploaded]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '附件上传失败');
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  }

  async function downloadArtifact(artifact: TaskArtifact) {
    if (!selectedId) return;
    const url = workspaceApi.downloadUrl(selectedId, artifact.path);
    if (nativeAuthenticatedDownload(url, artifact.filename)) return;
    try {
      const response = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
      if (!response.ok) throw new Error('文件下载失败');
      const blobUrl = URL.createObjectURL(await response.blob());
      const anchor = document.createElement('a');
      anchor.href = blobUrl;
      anchor.download = artifact.filename;
      anchor.click();
      window.setTimeout(() => URL.revokeObjectURL(blobUrl), 1_000);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '文件下载失败');
    }
  }

  async function uploadSharedFiles(conversationId: string) {
    const target = conversations.find((item) => item.id === conversationId);
    if (!target || !token || sharedUploadBusy) return;
    setSharedUploadBusy(true);
    setError('');
    try {
      if (target.mode !== 'agent') {
        const result = await conversationApi.update(token, target.id, { mode: 'agent' });
        setConversations((items) => items.map((item) => item.id === target.id ? result.conversation : item));
      }
      uploadNativeSharedFiles(target.id);
    } catch (reason) {
      setSharedUploadBusy(false);
      setError(reason instanceof Error ? reason.message : '无法准备分享文件上传');
    }
  }

  function discardSharedFiles() {
    if (sharedUploadBusy) return;
    discardNativeSharedFiles();
    setSharedFiles([]);
    setSharedDialogOpen(false);
  }

  function closeMentions() {
    mentionRequest.current += 1;
    setMentionEntries([]);
    setMentionRange(null);
  }

  async function updateComposer(value: string, caret: number) {
    setInput(value);
    if (isGuest || mode !== 'agent' || !selectedId) {
      closeMentions();
      return;
    }
    const beforeCaret = value.slice(0, caret);
    const match = beforeCaret.match(/(?:^|\s)@(?:<([^>]*)|([^\s@<>]*))$/);
    if (!match) {
      closeMentions();
      return;
    }
    const query = (match[1] ?? match[2] ?? '').trim();
    const requestId = ++mentionRequest.current;
    setMentionRange({ start: beforeCaret.lastIndexOf('@'), end: caret });
    try {
      const result = await workspaceApi.mentions(token, selectedId, query);
      if (mentionRequest.current !== requestId) return;
      setMentionEntries(result.entries);
      setMentionIndex(0);
    } catch {
      if (mentionRequest.current === requestId) setMentionEntries([]);
    }
  }

  function chooseMention(entry: WorkspaceEntry) {
    if (!mentionRange) return;
    const replacement = `@<${entry.path}> `;
    const next = `${input.slice(0, mentionRange.start)}${replacement}${input.slice(mentionRange.end)}`;
    const caret = mentionRange.start + replacement.length;
    setInput(next);
    closeMentions();
    window.requestAnimationFrame(() => {
      textareaRef.current?.focus();
      textareaRef.current?.setSelectionRange(caret, caret);
    });
  }

  function composerKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (mentionRange && mentionEntries.length) {
      if (event.key === 'ArrowDown') {
        event.preventDefault();
        setMentionIndex((value) => (value + 1) % mentionEntries.length);
        return;
      }
      if (event.key === 'ArrowUp') {
        event.preventDefault();
        setMentionIndex((value) => (value - 1 + mentionEntries.length) % mentionEntries.length);
        return;
      }
      if (event.key === 'Enter' || event.key === 'Tab') {
        event.preventDefault();
        chooseMention(mentionEntries[mentionIndex]);
        return;
      }
      if (event.key === 'Escape') {
        event.preventDefault();
        closeMentions();
        return;
      }
    }
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      void send();
    }
  }

  function pasteIntoComposer(event: React.ClipboardEvent<HTMLTextAreaElement>) {
    if (isGuest || mode !== 'agent') return;
    const images = Array.from(event.clipboardData.files).filter((file) => file.type.startsWith('image/'));
    if (!images.length) return;
    event.preventDefault();
    void upload(images);
  }

  function toggleComposerPreview() {
    closeMentions();
    setComposerPreview((value) => {
      const next = !value;
      if (!next) window.requestAnimationFrame(() => textareaRef.current?.focus());
      return next;
    });
  }

  function updateTitle(content: string) {
    if (!selected) return;
    const title = summarizeConversationTitle(content);
    setConversations((items) => items.map((item) => item.id === selected.id ? { ...item, title, updatedAt: Date.now() } : item));
  }

  async function send() {
    const content = input.trim();
    if (!content || !selectedId || sending) return;
    if (mode === 'agent' && isGuest) {
      onRequestAuth();
      return;
    }
    setSending(true);
    followMessageTailRef.current = true;
    setError('');
    setContextCompressed(false);
    if (mode === 'chat') {
      const pending: Message = { id: makeId('pending'), role: 'user', content, createdAt: Date.now() };
      const history = [...messages, pending];
      const streamId = makeId('stream-answer');
      let streamContent = '';
      const streamingAssistant: Message = { id: streamId, role: 'assistant', content: '', createdAt: Date.now() };
      setMessageMap((value) => ({ ...value, [selectedId]: [...history, streamingAssistant] }));
      setInput('');
      setComposerPreview(false);
      updateTitle(content);
      const appendDelta = (delta: string) => {
        if (!delta) return;
        streamContent += delta;
        setMessageMap((value) => ({
          ...value,
          [selectedId]: (value[selectedId] || []).map((item) => item.id === streamId ? { ...item, content: streamContent } : item),
        }));
      };
      try {
        if (isGuest) {
          const result = await chatApi.completeStream(guestContext(history), appendDelta);
          const assistant: Message = { id: makeId('guest-answer'), ...result.message };
          setMessageMap((value) => ({ ...value, [selectedId]: (value[selectedId] || []).map((item) => item.id === streamId ? assistant : item) }));
          setContextCompressed(result.contextCompressed);
        } else {
          const result = await conversationApi.chatStream(token, selectedId, content, appendDelta);
          setMessageMap((value) => ({ ...value, [selectedId]: (value[selectedId] || []).map((item) => item.id === streamId ? result.message : item) }));
          setConversations((items) => items.map((item) => item.id === result.conversation.id ? result.conversation : item));
          setContextCompressed(result.contextCompressed);
        }
      } catch (reason) {
        if (!streamContent) {
          setMessageMap((value) => ({ ...value, [selectedId]: (value[selectedId] || []).filter((item) => item.id !== streamId) }));
        }
        setError(reason instanceof Error ? reason.message : '对话请求失败');
      } finally {
        setSending(false);
      }
      return;
    }
    try {
      const result = await conversationApi.dispatchTask(token, selectedId, content, attachments.map((item) => item.id));
      if (result.execution === 'remote') {
        setRemoteTasks((items) => [result.task, ...items.filter((item) => item.id !== result.task.id)]);
      } else {
        setTasks((items) => [...items, result.task]);
      }
      setConversations((items) => items.map((item) => item.id === result.conversation.id ? result.conversation : item));
      setInput('');
      setComposerPreview(false);
      setAttachments([]);
      updateTitle(content);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '任务提交失败');
    } finally {
      setSending(false);
    }
  }

  async function approveRemote(task: ControlTask, decision: 'approve' | 'deny') {
    try { await controlApi.approval(token, task.id, decision); }
    catch (reason) { setError(reason instanceof Error ? reason.message : '远程操作审批失败'); }
  }

  async function stopRemote(task: ControlTask) {
    try { await controlApi.stop(token, task.id); }
    catch (reason) { setError(reason instanceof Error ? reason.message : '远程任务停止失败'); }
  }

  async function approve(task: AgentTask, decision: 'once' | 'deny') {
    try { await taskApi.approve(token, task.id, decision); } catch (reason) { setError(reason instanceof Error ? reason.message : '审批失败'); }
  }
  async function stop(task: AgentTask) {
    try { await taskApi.stop(token, task.id); } catch (reason) { setError(reason instanceof Error ? reason.message : '停止失败'); }
  }
  async function steer(task: AgentTask, content: string) {
    try { await taskApi.steer(token, task.id, content); } catch (reason) { setError(reason instanceof Error ? reason.message : '追加指令失败'); }
  }

  function closeBrowser() {
    setBrowserOpen(false);
    setBrowserMounted(false);
  }

  function toggleBrowser() {
    if (browserOpen) {
      closeBrowser();
      return;
    }
    setBrowserMounted(true);
    setBrowserOpen(true);
  }

  return (
    <main className={`workspaceShell ${leftOpen ? '' : 'leftCollapsed'} ${browserMounted ? 'browserMounted' : ''} ${browserOpen ? 'browserOpen' : ''}`} style={backgroundUrl ? { '--workspace-wallpaper': `url("${backgroundUrl}")` } as React.CSSProperties : undefined}>
      {leftOpen && <button className="mobileSidebarBackdrop" aria-label="关闭对话列表" onClick={() => setLeftOpen(false)} />}
      <aside className="conversationSidebar" aria-label="对话列表">
        <header className="workspaceBrand"><img className="siteLogo workspaceLogo" src="/assets/site-logo.jpg" alt="" /><strong>妙想之地</strong><button className="sidebarToggleButton" title="收起对话列表" onClick={() => setLeftOpen(false)}><PanelLeft size={16} /></button></header>
        <div className="sidebarPrimary">
          <button className="sidebarNavButton" onClick={() => void createConversation()}><SquarePen size={16} />新对话</button>
          <button className="sidebarNavButton" onClick={() => setSearchOpen(true)}><Search size={16} />搜索</button>
          {canUseAgent && <button className={`sidebarNavButton ${mode === 'agent' ? 'active' : ''}`} onClick={() => void changeExperience(selected?.agentProfile || 'expert')}><Bot size={16} />Agent</button>}
          {canUseAgent && <button className="sidebarNavButton" onClick={() => setCapabilityOpen(true)}><LibraryBig size={16} />能力中心</button>}
          {canUseAgent && <button className="sidebarNavButton" onClick={() => setComputerControlOpen(true)}><Laptop size={16} />电脑控制</button>}
          {!isGuest && <button className="sidebarNavButton" onClick={() => setSettingsOpen(true)}><Settings2 size={16} />设置</button>}
        </div>
        <div className="conversationList">
          {conversationGroups.map((group) => <section className="conversationGroup" key={group.label}><p className="conversationDateLabel">{group.label}</p>{group.conversations.map((conversation) => { const deleting = deletingConversationIds.has(conversation.id); return <div className={`conversationItem ${conversation.id === selectedId ? 'active' : ''}`} key={conversation.id}><button type="button" className="conversationSelect" onClick={() => { setSelectedId(conversation.id); if (window.innerWidth <= 860) setLeftOpen(false); }}><span>{conversation.title}</span></button><button type="button" className="conversationDelete" title={deleting ? '正在删除' : '删除对话'} aria-label={deleting ? '正在删除对话' : `删除对话：${conversation.title}`} disabled={deleting} onPointerDown={(event) => event.stopPropagation()} onClick={(event) => { event.stopPropagation(); void removeConversation(conversation.id); }}>{deleting ? <Loader2 className="spin" size={14} /> : <Trash2 size={14} />}</button></div>; })}</section>)}
        </div>
        <footer className="accountFooter">
          {isGuest ? <><span className="avatar"><UserRound size={16} /></span><div><strong>访客</strong><small>基础对话</small></div><button className="iconButton accountAction" title="登录" onClick={onRequestAuth}><LogIn size={17} /></button></> : <><span className="avatar"><UserRound size={16} /></span><div><strong>{user.displayName}</strong><small>{user.accessTier === 'vip' ? 'VIP' : 'Basic · 仅 Chat'}</small></div><button className="iconButton accountAction" title="退出登录" onClick={onLogout}><LogOut size={17} /></button></>}
        </footer>
      </aside>

      {!leftOpen && <aside className="miniRail" aria-label="快捷导航"><div><button title="展开对话列表" onClick={() => setLeftOpen(true)}><img className="siteLogo railLogo" src="/assets/site-logo.jpg" alt="" /><PanelLeft className="railHoverIcon" size={16} /></button><button title="新对话" onClick={() => void createConversation()}><SquarePen size={16} /></button><button title="搜索" onClick={() => setSearchOpen(true)}><Search size={16} /></button>{canUseAgent && <button title="能力中心" onClick={() => setCapabilityOpen(true)}><LibraryBig size={16} /></button>}{canUseAgent && <button title="电脑控制" onClick={() => setComputerControlOpen(true)}><Laptop size={16} /></button>}{!isGuest && <button title="设置" onClick={() => setSettingsOpen(true)}><Settings2 size={16} /></button>}</div><button title={isGuest ? '登录' : '退出登录'} onClick={isGuest ? onRequestAuth : onLogout}><UserRound size={17} /></button></aside>}
      {!leftOpen && <button className="mobileSidebarToggle" title="展开对话列表" onClick={() => setLeftOpen(true)}><PanelLeft size={17} /></button>}

      <section className={`chatPanel ${emptyView ? 'emptyState' : ''} ${terminalOpen ? 'terminalOpen' : ''}`}>
        <header className="chatHeader">
          <div className="chatTitle">{mode === 'agent' && !isGuest && (topDockPreferences.terminal || topDockPreferences.ports) && <div className="chatUtilityButtons">{topDockPreferences.terminal && <button className={`iconButton ${terminalOpen ? 'active' : ''}`} title="终端" onClick={() => setTerminalOpen((value) => !value)}><SquareTerminal size={17} /></button>}{topDockPreferences.ports && <button className="iconButton" title="端口" onClick={() => setPortsOpen(true)}><RadioTower size={17} /></button>}</div>}<h1>{hasConversationContent ? selected?.title || '新对话' : ''}</h1></div>
          <div className="chatTools">
            {topDockPreferences.downloads && !isNativeApp() && !isWechatMiniProgramWebView() && <AppDownloadMenu androidUrl={runtime.appDownloadUrl} windowsUrl={runtime.windowsAgentDownloadUrl} />}
            {topDockPreferences.computer && mode === 'agent' && !isGuest && <button className={`iconButton conversationBindingButton ${selected?.controlDeviceId ? 'bound' : ''}`} title={selectedControlDevice ? `已绑定：${selectedControlDevice.name}，点击更改` : '绑定当前对话的远程设备'} onClick={() => setConversationBindingOpen(true)}><Laptop size={18} /><span aria-hidden="true" /></button>}
            {mode === 'agent' && !isGuest && <>{topDockPreferences.workspace && <button className="iconButton" title="工作区文件" onClick={() => setWorkspaceOpen(true)}><FolderOpen size={17} /></button>}{topDockPreferences.schedules && <button className="iconButton" title="定时任务" onClick={() => setScheduleOpen(true)}><CalendarClock size={17} /></button>}{topDockPreferences.browser && <button className={`iconButton ${browserOpen ? 'active' : ''}`} title={browserOpen ? '收起实时浏览器' : '打开实时浏览器'} onClick={toggleBrowser}><Globe2 size={18} /></button>}</>}
            {isGuest && <button className="loginButton" onClick={onRequestAuth}><LogIn size={16} />登录</button>}
          </div>
        </header>

        <div className="conversationStage">
          <div
            ref={messageListRef}
            className="messageList"
            onPointerDown={() => { followMessageTailRef.current = false; }}
            onTouchStart={() => { followMessageTailRef.current = false; }}
            onWheel={() => { followMessageTailRef.current = false; }}
            onScroll={(event) => {
              const list = event.currentTarget;
              followMessageTailRef.current = list.scrollHeight - list.scrollTop - list.clientHeight <= 96;
            }}
          >
            {loading || checkingSession ? <div className="emptyChat"><Loader2 className="spin" size={25} /></div> : mode === 'chat' && messages.length === 0 ? <div className="emptyChat"><img className="siteLogo emptyMark" src="/assets/site-logo.jpg" alt="" /><h2>{runtime.appName}</h2></div> : mode === 'agent' && tasks.length === 0 ? <div className="emptyChat"><img className="siteLogo emptyMark" src="/assets/site-logo.jpg" alt="" /><h2>{experience === 'expert' ? 'Expert Agent' : 'Fast Agent'}</h2></div> : null}
            {mode === 'chat' && messages.map((message) => <article className={`chatMessage ${message.role}`} key={message.id}>{message.role === 'assistant' && <span className="messageAvatar agent"><img className="siteLogo" src="/assets/site-logo.jpg" alt="" /></span>}<div>{message.role === 'assistant' && <header><strong>{runtime.appName}</strong><time>{new Date(message.createdAt).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}</time></header>}<MarkdownContent content={message.content} /></div></article>)}
            {mode === 'agent' && agentTimeline.map((entry) => entry.kind === 'local'
              ? <TaskCard key={`local:${entry.task.id}`} task={entry.task} events={events[entry.task.id] || []} token={token} userName={user?.displayName || '用户'} onApprove={(item, decision) => void approve(item, decision)} onStop={(item) => void stop(item)} onSteer={(item, text) => void steer(item, text)} onDownloadArtifact={(artifact) => void downloadArtifact(artifact)} />
              : <RemoteTaskCard key={`remote:${entry.task.id}`} task={entry.task} events={remoteEvents[entry.task.id] || []} token={token} userName={user?.displayName || '用户'} onApprove={(item, decision) => void approveRemote(item, decision)} onStop={(item) => void stopRemote(item)} onOpen={() => setComputerControlOpen(true)} />)}
            <div ref={messageEndRef} />
          </div>

          <div className="composerDock">
            {attachments.length > 0 && <div className="attachmentTray">{attachments.map((item) => <span key={item.id}><FileText size={13} />{item.filename}<button title="移除" onClick={() => setAttachments((items) => items.filter((entry) => entry.id !== item.id))}><X size={12} /></button></span>)}</div>}
            {error && <p className="inlineError" role="alert">{error}</p>}
            {contextCompressed && <p className="contextNotice"><Zap size={13} />上下文已压缩</p>}
            <div className="composer">
              {mentionRange && mentionEntries.length > 0 && <div className="mentionMenu" role="listbox" aria-label="工作区文件">{mentionEntries.map((entry, index) => <button type="button" role="option" aria-selected={index === mentionIndex} className={index === mentionIndex ? 'active' : ''} key={entry.path} onMouseDown={(event) => { event.preventDefault(); chooseMention(entry); }}><File size={15} /><span>{entry.name}</span><small>{entry.path}</small></button>)}</div>}
              {composerPreview
                ? <div className="composerPreview" role="region" aria-label="Markdown 预览"><MarkdownContent content={input} /></div>
                : <textarea ref={textareaRef} value={input} onChange={(event) => void updateComposer(event.target.value, event.target.selectionStart)} onKeyDown={composerKeyDown} onPaste={pasteIntoComposer} placeholder={mode === 'agent' ? '向 Agent 下达任务' : '输入消息'} rows={1} />}
              <div className="composerActions">
                {canUseAgent && <div className="modeSwitch" role="tablist" aria-label="对话模式"><button className={experience === 'chat' ? 'active' : ''} disabled={switchingMode} onClick={() => void changeExperience('chat')}><MessageSquare size={15} />Chat</button><button className={experience === 'fast' ? 'active' : ''} disabled={switchingMode} onClick={() => void changeExperience('fast')}><Zap size={15} />Fast</button><button className={experience === 'expert' ? 'active' : ''} disabled={switchingMode} onClick={() => void changeExperience('expert')}><Bot size={15} />Expert</button></div>}
                {mode === 'agent' && !isGuest && <><input ref={fileInputRef} type="file" hidden multiple onChange={(event) => void upload(event.target.files)} /><button className="iconButton quiet" title="添加附件" disabled={uploading || attachments.length >= 8} onClick={() => fileInputRef.current?.click()}>{uploading ? <Loader2 className="spin" size={18} /> : <Paperclip size={18} />}</button></>}
                <button className={`iconButton quiet ${composerPreview ? 'active' : ''}`} title={composerPreview ? '编辑 Markdown' : '预览 Markdown'} aria-pressed={composerPreview} onClick={toggleComposerPreview}>{composerPreview ? <SquarePen size={17} /> : <Eye size={17} />}</button>
                <span className="modelLabel">{mode === 'agent' ? (experience === 'expert' ? 'Expert' : 'Fast') : '自动模型'}</span>
                <button className="sendButton" disabled={!input.trim() || sending} title={mode === 'agent' ? '提交任务' : '发送消息'} onClick={() => void send()}>{sending ? <Loader2 className="spin" size={18} /> : <ArrowUp size={19} strokeWidth={2.4} />}</button>
              </div>
            </div>
          </div>

          {emptyView && <div className="starterPrompts"><p><Zap size={13} />推荐</p>{starters.map((starter) => <button type="button" key={starter.prompt} onClick={() => setInput(starter.prompt)}><strong>{starter.title}</strong><span>{starter.detail}</span></button>)}</div>}
        </div>
        {terminalOpen && mode === 'agent' && !isGuest && <Suspense fallback={<div className="terminalLoading"><Loader2 className="spin" size={18} /></div>}><TerminalPanel token={token} onClose={() => setTerminalOpen(false)} /></Suspense>}
      </section>

      {browserMounted && !isGuest && mode === 'agent' && selectedId && <aside className="browserPane" aria-hidden={!browserOpen}><Suspense fallback={<div className="browserLazyLoading"><Loader2 className="spin" size={22} /></div>}><BrowserPanel token={token} conversationId={selectedId} refreshKey={browserRefreshKey} onClose={closeBrowser} /></Suspense></aside>}
      {workspaceOpen && !isGuest && <WorkspaceDialog token={token} conversationId={selectedId} onClose={() => setWorkspaceOpen(false)} />}
      {scheduleOpen && !isGuest && <ScheduleDialog token={token} conversationId={selectedId} onClose={() => setScheduleOpen(false)} />}
      {portsOpen && !isGuest && <PortsDialog token={token} onClose={() => setPortsOpen(false)} />}
      {settingsOpen && !isGuest && <PersonalSettingsDialog token={token} user={user!} backgroundUrl={backgroundUrl} topDockPreferences={topDockPreferences} onBackgroundChange={(blob) => setBackgroundUrl(blob ? URL.createObjectURL(blob) : '')} onTopDockChange={(item, visible) => setTopDockPreferences((value) => ({ ...value, [item]: visible }))} onLogout={onLogout} onClose={() => setSettingsOpen(false)} />}
      {computerControlOpen && canUseAgent && <ComputerControlDialog token={token} windowsAgentUrl={runtime.windowsAgentDownloadUrl} onClose={() => setComputerControlOpen(false)} />}
      {capabilityOpen && canUseAgent && <Suspense fallback={<div className="capabilityLazyLoading"><Loader2 className="spin" size={20} /></div>}><CapabilityCenter token={token} conversationId={selectedId} onClose={() => setCapabilityOpen(false)} /></Suspense>}
      {conversationBindingOpen && selected && !isGuest && <ConversationControlBindingDialog token={token} conversation={selected} devices={controlDevices} onSaved={(conversation) => { setConversations((items) => items.map((item) => item.id === conversation.id ? conversation : item)); setConversationBindingOpen(false); }} onClose={() => setConversationBindingOpen(false)} />}
      {appDownloadPromptOpen && !isGuest && !isNativeApp() && !isWechatMiniProgramWebView() && <AppDownloadPrompt url={runtime.appDownloadUrl} onClose={() => setAppDownloadPromptOpen(false)} onDismissPermanently={() => { try { localStorage.setItem(APP_DOWNLOAD_DISMISSED_KEY, '1'); } catch { /* no-op */ } setAppDownloadPromptOpen(false); }} />}
      {windowsUpdatePromptOpen && <WindowsUpdatePrompt version={runtime.windowsAgentVersion} url={runtime.windowsAgentDownloadUrl} onClose={() => setWindowsUpdatePromptOpen(false)} />}
      {sharedDialogOpen && sharedFiles.length > 0 && !isGuest && <SharedFilesDialog files={sharedFiles} conversations={conversations} selectedId={selectedId} busy={sharedUploadBusy} onUpload={(conversationId) => void uploadSharedFiles(conversationId)} onDiscard={discardSharedFiles} />}
      {searchOpen && <div className="searchOverlay" onMouseDown={(event) => { if (event.currentTarget === event.target) { setSearchOpen(false); setConversationQuery(''); } }}><section className="searchDialog" role="dialog" aria-modal="true" aria-label="搜索对话"><header><Search size={18} /><input aria-label="搜索对话" autoFocus placeholder="搜索" value={conversationQuery} onChange={(event) => setConversationQuery(event.target.value)} /><button className="iconButton" title="关闭" onClick={() => { setSearchOpen(false); setConversationQuery(''); }}><X size={17} /></button></header><div className="searchResults"><p>对话</p>{visibleConversations.map((conversation) => <button key={conversation.id} onClick={() => { setSelectedId(conversation.id); setSearchOpen(false); setConversationQuery(''); setLeftOpen(window.innerWidth > 860); }}><MessageCircle size={15} /><span>{conversation.title}</span></button>)}{visibleConversations.length === 0 && <div className="searchEmpty">没有匹配的对话</div>}</div></section></div>}
    </main>
  );
}

const controlActiveStatuses = new Set<ControlTask['status']>([
  'queued', 'assigned', 'starting', 'running', 'waiting_approval', 'stopping',
]);

const controlStatusText: Record<ControlTask['status'], string> = {
  queued: '等待设备',
  assigned: '已分配',
  starting: '正在启动',
  running: '执行中',
  waiting_approval: '等待批准',
  stopping: '正在停止',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
};

function controlEventSummary(event: ControlTaskEvent) {
  const payload = event.payload || {};
  const detail = payload.message || payload.description || payload.preview || payload.action
    || payload.tool || payload.command || payload.text || payload.error;
  if (detail) return String(detail).replace(/\s+/g, ' ').trim().slice(0, 220);
  if (event.frameId || event.frameUrl || event.type.includes('frame') || event.type.includes('screenshot')) return '画面已更新';
  const labels: Record<string, string> = {
    'task.queued': '任务已进入设备队列',
    'task.assigned': '任务已发送到设备',
    'task.accepted': '设备已接收任务',
    'task.started': '电脑控制已开始',
    'task.completed': '电脑控制已完成',
    'task.failed': '电脑控制执行失败',
    'task.cancelled': '电脑控制已取消',
    'approval.request': '设备请求批准操作',
    'approval.responded': '审批结果已发送',
  };
  return labels[event.type] || event.type.replace(/[._-]+/g, ' ');
}

function targetLabel(target: ControlTarget) {
  const kind = target.kind === 'adb' ? 'ADB' : 'Windows';
  const serial = target.serial ? ` · ${target.serial}` : '';
  return `${kind} · ${target.name}${serial}`;
}

function bindingTargets(device: ControlDevice | null): ControlTarget[] {
  if (!device) return [];
  const targets = [...device.targets];
  if (!targets.some((item) => item.id === 'desktop')) {
    targets.unshift({ id: 'desktop', kind: 'windows', name: 'Windows 桌面', state: 'device' });
  }
  return targets.filter((item, index) => targets.findIndex((candidate) => candidate.id === item.id) === index);
}

function ConversationControlBindingDialog({
  token, conversation, devices, onSaved, onClose,
}: {
  token: string;
  conversation: Conversation;
  devices: ControlDevice[];
  onSaved: (conversation: Conversation) => void;
  onClose: () => void;
}) {
  const initialDeviceId = devices.some((item) => item.id === conversation.controlDeviceId)
    ? conversation.controlDeviceId || ''
    : devices[0]?.id || '';
  const [selectedDeviceId, setSelectedDeviceId] = useState(initialDeviceId);
  const selectedDevice = devices.find((item) => item.id === selectedDeviceId) || null;
  const targets = bindingTargets(selectedDevice);
  const initialTargetId = selectedDeviceId === conversation.controlDeviceId
    && targets.some((item) => item.id === conversation.controlTargetId)
    ? conversation.controlTargetId || ''
    : targets[0]?.id || '';
  const [selectedTargetId, setSelectedTargetId] = useState(initialTargetId);
  const selectedTarget = targets.find((item) => item.id === selectedTargetId) || null;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (selectedDevice || devices.length === 0) return;
    const nextDevice = devices.find((item) => item.id === conversation.controlDeviceId) || devices[0];
    const nextTargets = bindingTargets(nextDevice);
    setSelectedDeviceId(nextDevice.id);
    setSelectedTargetId(nextTargets.find((item) => item.id === conversation.controlTargetId)?.id || nextTargets[0]?.id || '');
  }, [conversation.controlDeviceId, conversation.controlTargetId, devices, selectedDevice]);

  useEffect(() => {
    if (!selectedDevice || selectedTarget) return;
    setSelectedTargetId(targets.find((item) => item.id === conversation.controlTargetId)?.id || targets[0]?.id || '');
  }, [conversation.controlTargetId, selectedDevice, selectedTarget, targets]);

  function chooseDevice(deviceId: string) {
    setSelectedDeviceId(deviceId);
    const device = devices.find((item) => item.id === deviceId) || null;
    const nextTargets = bindingTargets(device);
    const savedTarget = deviceId === conversation.controlDeviceId ? conversation.controlTargetId : null;
    setSelectedTargetId(nextTargets.some((item) => item.id === savedTarget) ? savedTarget || '' : nextTargets[0]?.id || '');
  }

  async function save() {
    if (!selectedDevice || !selectedTarget || busy) return;
    setBusy(true); setError('');
    try {
      const result = await conversationApi.bindControl(token, conversation.id, {
        device_id: selectedDevice.id,
        target_id: selectedTarget.id,
      });
      onSaved(result.conversation);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '远程设备绑定失败');
    } finally { setBusy(false); }
  }

  async function unbind() {
    if (busy) return;
    setBusy(true); setError('');
    try {
      const result = await conversationApi.bindControl(token, conversation.id, { device_id: null, target_id: null });
      onSaved(result.conversation);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '解除绑定失败');
    } finally { setBusy(false); }
  }

  return (
    <div className="modalBackdrop bindingBackdrop" onMouseDown={(event) => { if (!busy && event.currentTarget === event.target) onClose(); }}>
      <section className="modal conversationBindingModal" role="dialog" aria-modal="true" aria-label="绑定远程设备">
        <header><div className="computerControlTitle"><Laptop size={20} /><div><h2>绑定远程设备</h2><p>{conversation.title}</p></div></div><button className="iconButton" title="关闭" disabled={busy} onClick={onClose}><X size={17} /></button></header>
        <div className="conversationBindingBody">
          {devices.length > 0 ? <>
            <label><span>电脑</span><select value={selectedDeviceId} disabled={busy} onChange={(event) => chooseDevice(event.target.value)}>{devices.map((device) => <option key={device.id} value={device.id}>{device.online ? '在线' : '离线'} · {device.name}</option>)}</select></label>
            <label><span>控制目标</span><select value={selectedTargetId} disabled={busy || !selectedDevice} onChange={(event) => setSelectedTargetId(event.target.value)}>{targets.map((target) => <option key={target.id} value={target.id}>{targetLabel(target)}</option>)}</select></label>
            {selectedDevice && <div className="bindingDeviceSummary"><span className={`controlPresence ${selectedDevice.online ? 'online' : ''}`}>{selectedDevice.online ? <Wifi size={15} /> : <WifiOff size={15} />}</span><div><strong>{selectedDevice.name}</strong><small>{selectedDevice.hostname} · {selectedDevice.online ? '在线' : '离线'}</small></div></div>}
          </> : <div className="bindingEmpty"><Cable size={24} /><strong>暂无可控设备</strong><span>请先在 Windows 客户端登录当前账号</span></div>}
          {error && <p className="formError" role="alert">{error}</p>}
        </div>
        <footer>{conversation.controlDeviceId && <button className="secondaryButton unbindButton" disabled={busy} onClick={() => void unbind()}>解除绑定</button>}<button className="secondaryButton" disabled={busy} onClick={onClose}>取消</button><button className="primaryButton" disabled={busy || !selectedDevice || !selectedTarget} onClick={() => void save()}>{busy ? <Loader2 className="spin" size={16} /> : <Check size={16} />}绑定</button></footer>
      </section>
    </div>
  );
}

function ComputerControlDialog({ token, windowsAgentUrl, onClose }: { token: string; windowsAgentUrl: string; onClose: () => void }) {
  const [devices, setDevices] = useState<ControlDevice[]>([]);
  const [tasks, setTasks] = useState<ControlTask[]>([]);
  const [selectedDeviceId, setSelectedDeviceId] = useState('');
  const [selectedTargetId, setSelectedTargetId] = useState('');
  const [selectedTaskId, setSelectedTaskId] = useState('');
  const [deviceQuery, setDeviceQuery] = useState('');
  const [taskEvents, setTaskEvents] = useState<ControlTaskEvent[]>([]);
  const [instruction, setInstruction] = useState('');
  const [editingDeviceId, setEditingDeviceId] = useState('');
  const [deviceName, setDeviceName] = useState('');
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [frameUrl, setFrameUrl] = useState('');
  const [frameLoading, setFrameLoading] = useState(false);
  const eventCursor = useRef<Record<string, number>>({});

  const selectedDevice = devices.find((device) => device.id === selectedDeviceId) || null;
  const selectedTarget = selectedDevice?.targets.find((target) => target.id === selectedTargetId) || null;
  const selectedTask = tasks.find((task) => task.id === selectedTaskId) || null;
  const onlineCount = devices.filter((device) => device.online).length;
  const normalizedDeviceQuery = deviceQuery.trim().toLocaleLowerCase('zh-CN');
  const filteredDevices = normalizedDeviceQuery
    ? devices.filter((device) => [device.name, device.hostname, device.platform, ...device.targets.map((target) => target.name)]
      .some((value) => String(value || '').toLocaleLowerCase('zh-CN').includes(normalizedDeviceQuery)))
    : devices;
  const activeCount = tasks.filter((task) => controlActiveStatuses.has(task.status)).length;
  const latestFrameEvent = [...taskEvents].reverse().find((event) => event.frameId || event.frameUrl);
  const latestFrameId = latestFrameEvent?.frameId || '';
  const latestFramePath = latestFrameEvent?.frameUrl || '';

  function applySummary(nextDevices: ControlDevice[], nextTasks: ControlTask[]) {
    const orderedTasks = [...nextTasks].sort((left, right) => right.createdAt - left.createdAt);
    setDevices(nextDevices);
    setTasks(orderedTasks);
    setSelectedDeviceId((current) => {
      if (nextDevices.some((device) => device.id === current)) return current;
      return nextDevices.find((device) => device.online)?.id || nextDevices[0]?.id || '';
    });
    setSelectedTaskId((current) => orderedTasks.some((task) => task.id === current) ? current : orderedTasks[0]?.id || '');
  }

  async function refreshSummary(showProgress = false) {
    if (showProgress) setRefreshing(true);
    try {
      const [deviceResult, taskResult] = await Promise.all([controlApi.devices(token), controlApi.tasks(token)]);
      applySummary(deviceResult.devices || [], taskResult.tasks || []);
      setError('');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '电脑控制状态读取失败');
    } finally {
      setLoading(false);
      if (showProgress) setRefreshing(false);
    }
  }

  useEffect(() => {
    let cancelled = false;
    let polling = false;
    async function poll() {
      if (polling || document.hidden) return;
      polling = true;
      try {
        const [deviceResult, taskResult] = await Promise.all([controlApi.devices(token), controlApi.tasks(token)]);
        if (!cancelled) {
          applySummary(deviceResult.devices || [], taskResult.tasks || []);
          setError('');
          setLoading(false);
        }
      } catch (reason) {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : '电脑控制状态读取失败');
          setLoading(false);
        }
      } finally {
        polling = false;
      }
    }
    void poll();
    const timer = window.setInterval(() => void poll(), 3_000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [token]);

  useEffect(() => {
    const targets = selectedDevice?.targets || [];
    setSelectedTargetId((current) => targets.some((target) => target.id === current) ? current : targets[0]?.id || '');
  }, [selectedDeviceId, selectedDevice?.targets]);

  useEffect(() => {
    if (!selectedTaskId) {
      setTaskEvents([]);
      return;
    }
    let cancelled = false;
    let polling = false;
    eventCursor.current[selectedTaskId] = 0;
    setTaskEvents([]);
    async function pollEvents() {
      if (polling || document.hidden) return;
      polling = true;
      try {
        const result = await controlApi.events(token, selectedTaskId, eventCursor.current[selectedTaskId] || 0);
        if (cancelled || !result.events?.length) return;
        eventCursor.current[selectedTaskId] = result.events[result.events.length - 1].id;
        setTaskEvents((current) => {
          const seen = new Set(current.map((event) => event.id));
          return [...current, ...result.events.filter((event) => !seen.has(event.id))].slice(-300);
        });
      } catch (reason) {
        if (!cancelled) setError(reason instanceof Error ? reason.message : '任务事件读取失败');
      } finally {
        polling = false;
      }
    }
    void pollEvents();
    const timer = window.setInterval(() => void pollEvents(), 1_500);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [selectedTaskId, token]);

  useEffect(() => {
    let cancelled = false;
    let objectUrl = '';
    if (!latestFrameId && !latestFramePath) {
      setFrameUrl('');
      setFrameLoading(false);
      return () => { cancelled = true; };
    }
    if (!latestFrameId) {
      setFrameUrl(latestFramePath);
      setFrameLoading(false);
      return () => { cancelled = true; };
    }
    setFrameLoading(true);
    void controlApi.frame(token, latestFrameId).then((blob) => {
      if (cancelled) return;
      objectUrl = URL.createObjectURL(blob);
      setFrameUrl(objectUrl);
    }).catch((reason) => {
      if (!cancelled) setError(reason instanceof Error ? reason.message : '截图读取失败');
    }).finally(() => {
      if (!cancelled) setFrameLoading(false);
    });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [latestFrameId, latestFramePath, token]);

  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === 'Escape' && !busy) onClose(); };
    window.addEventListener('keydown', closeOnEscape);
    return () => window.removeEventListener('keydown', closeOnEscape);
  }, [busy, onClose]);

  function beginRename(device: ControlDevice) {
    setEditingDeviceId(device.id);
    setDeviceName(device.name);
  }

  async function saveDeviceName(device: ControlDevice) {
    const name = deviceName.trim();
    if (!name || name === device.name) { setEditingDeviceId(''); return; }
    setBusy(`rename:${device.id}`); setError('');
    try {
      const result = await controlApi.renameDevice(token, device.id, name);
      setDevices((items) => items.map((item) => item.id === device.id ? result.device : item));
      setEditingDeviceId('');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '设备改名失败');
    } finally { setBusy(''); }
  }

  async function removeDevice(device: ControlDevice) {
    if (!window.confirm(`撤销“${device.name}”的电脑控制授权？`)) return;
    setBusy(`remove:${device.id}`); setError('');
    try {
      await controlApi.removeDevice(token, device.id);
      setDevices((items) => items.filter((item) => item.id !== device.id));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '设备撤销失败');
    } finally { setBusy(''); }
  }

  async function submitTask(event: React.FormEvent) {
    event.preventDefault();
    const content = instruction.trim();
    if (!content || !selectedDevice || !selectedTarget) return;
    setBusy('create'); setError('');
    try {
      const result = await controlApi.createTask(token, {
        device_id: selectedDevice.id,
        target_id: selectedTarget.id,
        target_kind: selectedTarget.kind,
        instruction: content,
      });
      setTasks((items) => [result.task, ...items.filter((item) => item.id !== result.task.id)]);
      setSelectedTaskId(result.task.id);
      setInstruction('');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '电脑控制任务提交失败');
    } finally { setBusy(''); }
  }

  async function stopTask(task: ControlTask) {
    setBusy(`stop:${task.id}`); setError('');
    try { await controlApi.stop(token, task.id); await refreshSummary(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : '停止任务失败'); }
    finally { setBusy(''); }
  }

  async function approveTask(task: ControlTask, decision: 'approve' | 'deny') {
    setBusy(`approval:${task.id}`); setError('');
    try { await controlApi.approval(token, task.id, decision); await refreshSummary(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : '审批失败'); }
    finally { setBusy(''); }
  }

  return (
    <div className="modalBackdrop controlBackdrop" onMouseDown={(event) => { if (!busy && event.currentTarget === event.target) onClose(); }}>
      <section className="modal computerControlModal" role="dialog" aria-modal="true" aria-label="电脑控制">
        <header>
          <div className="computerControlTitle"><Laptop size={20} /><div><h2>电脑控制</h2><p>{onlineCount} 台在线 · {activeCount} 个任务进行中</p></div></div>
          <div className="modalHeaderActions">
            <a className="secondaryButton compact controlWindowsDownload" title="下载 Windows 客户端" href={windowsAgentUrl} download><Download size={15} /><span>Windows 客户端</span></a>
            <button className="iconButton" title="刷新" disabled={refreshing} onClick={() => void refreshSummary(true)}><RefreshCw className={refreshing ? 'spin' : ''} size={16} /></button>
            <button className="iconButton" title="关闭" disabled={Boolean(busy)} onClick={onClose}><X size={17} /></button>
          </div>
        </header>

        <div className="computerControlContent">
          <form className="controlCommandBand" onSubmit={submitTask}>
            <div className="controlTargetFields">
              <label><span>电脑</span><select value={selectedDeviceId} onChange={(event) => setSelectedDeviceId(event.target.value)} disabled={loading || Boolean(busy)}><option value="">选择电脑</option>{devices.map((device) => <option key={device.id} value={device.id}>{device.online ? '在线' : '离线'} · {device.name}</option>)}</select></label>
              <label><span>目标</span><select value={selectedTargetId} onChange={(event) => setSelectedTargetId(event.target.value)} disabled={!selectedDevice || Boolean(busy)}><option value="">选择 Windows / ADB</option>{(selectedDevice?.targets || []).map((target) => <option key={target.id} value={target.id}>{targetLabel(target)}</option>)}</select></label>
            </div>
            <div className="controlInstruction"><textarea value={instruction} maxLength={50_000} rows={2} placeholder="输入要在所选设备上完成的任务" onChange={(event) => setInstruction(event.target.value)} /><button className="primaryButton" disabled={!instruction.trim() || !selectedDevice?.online || !selectedTarget || Boolean(busy)}>{busy === 'create' ? <Loader2 className="spin" size={16} /> : <Play size={16} />}执行</button></div>
          </form>

          {error && <p className="controlError formError" role="alert">{error}</p>}

          <div className="computerControlBody">
            <aside className="controlInventory">
              <section className="controlDeviceSection">
                <header><strong>可控设备</strong><span>{filteredDevices.length}{filteredDevices.length !== devices.length ? ` / ${devices.length}` : ''}</span></header>
                <label className="controlDeviceSearch"><Search size={14} /><input value={deviceQuery} onChange={(event) => setDeviceQuery(event.target.value)} aria-label="搜索设备" placeholder="搜索设备" /></label>
                <div className="controlDeviceList">
                  {filteredDevices.map((device) => <div className={`controlDeviceRow ${device.id === selectedDeviceId ? 'active' : ''}`} key={device.id}>
                    <button className="controlDeviceSelect" onClick={() => setSelectedDeviceId(device.id)}>
                      <span className={`controlPresence ${device.online ? 'online' : ''}`}>{device.online ? <Wifi size={15} /> : <WifiOff size={15} />}</span>
                      <span>{editingDeviceId === device.id ? <input autoFocus value={deviceName} maxLength={120} onClick={(event) => event.stopPropagation()} onChange={(event) => setDeviceName(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); void saveDeviceName(device); } if (event.key === 'Escape') setEditingDeviceId(''); }} /> : <><strong>{device.name}</strong><small>{device.hostname || device.platform} · {device.agentVersion || '客户端版本未知'}</small></>}</span>
                    </button>
                    <div className="controlDeviceActions">
                      {editingDeviceId === device.id ? <button className="iconButton" title="保存名称" disabled={busy === `rename:${device.id}`} onClick={() => void saveDeviceName(device)}><Check size={14} /></button> : <button className="iconButton" title="修改名称" onClick={() => beginRename(device)}><Pencil size={14} /></button>}
                      <button className="iconButton danger" title="撤销设备" disabled={busy === `remove:${device.id}`} onClick={() => void removeDevice(device)}><Trash2 size={14} /></button>
                    </div>
                    {device.id === selectedDeviceId && device.capabilities.length > 0 && <div className="controlCapabilities">{device.capabilities.slice(0, 6).map((capability) => <span key={capability}>{capability}</span>)}</div>}
                  </div>)}
                  {!loading && devices.length === 0 && <div className="controlEmpty"><Cable size={20} /><span>在 Windows 客户端登录当前账号</span></div>}
                  {!loading && devices.length > 0 && filteredDevices.length === 0 && <div className="controlEmpty"><Search size={20} /><span>未找到匹配设备</span></div>}
                  {loading && <div className="controlEmpty"><Loader2 className="spin" size={20} /></div>}
                </div>
              </section>

              <section className="controlTaskSection">
                <header><strong>控制任务</strong><span>{tasks.length}</span></header>
                <div className="controlTaskList">
                  {tasks.map((task) => <button className={`controlTaskRow ${task.id === selectedTaskId ? 'active' : ''}`} key={task.id} onClick={() => setSelectedTaskId(task.id)}><span className={`controlTaskKind ${task.targetKind}`}><Cpu size={15} /></span><span><strong>{task.instruction}</strong><small>{task.deviceName} · {new Date(task.createdAt).toLocaleString('zh-CN')}</small></span><em className={`controlStatus ${task.status}`}>{controlStatusText[task.status]}</em></button>)}
                  {!loading && tasks.length === 0 && <div className="controlEmpty"><Monitor size={20} /><span>暂无控制任务</span></div>}
                </div>
              </section>
            </aside>

            <section className="controlTaskDetail">
              {selectedTask ? <>
                <header className="controlTaskHeader"><div><span className={`controlStatus ${selectedTask.status}`}>{controlStatusText[selectedTask.status]}</span><h3>{selectedTask.instruction}</h3><p>{selectedTask.deviceName} · {selectedTask.targetKind === 'adb' ? 'ADB' : 'Windows'} · {selectedTask.targetId}</p></div><div>{selectedTask.status === 'waiting_approval' && <><button className="primaryButton compact" disabled={Boolean(busy)} onClick={() => void approveTask(selectedTask, 'approve')}><Check size={15} />批准</button><button className="secondaryButton compact" disabled={Boolean(busy)} onClick={() => void approveTask(selectedTask, 'deny')}><X size={15} />拒绝</button></>}{controlActiveStatuses.has(selectedTask.status) && selectedTask.status !== 'stopping' && <button className="secondaryButton compact controlStopButton" disabled={Boolean(busy)} onClick={() => void stopTask(selectedTask)}><CircleStop size={15} />停止</button>}</div></header>

                <div className="controlFrameSection"><header><strong>最新画面</strong>{latestFrameEvent && <time>{new Date(latestFrameEvent.createdAt).toLocaleTimeString('zh-CN')}</time>}</header><div className="controlFrameViewport">{frameLoading ? <Loader2 className="spin" size={24} /> : frameUrl ? <img src={frameUrl} alt="电脑控制最新截图" /> : <><Monitor size={32} /><span>等待画面</span></>}</div></div>

                {(selectedTask.output || selectedTask.error) && <section className="controlTaskResult"><header><strong>{selectedTask.error ? '执行异常' : '执行结果'}</strong></header>{selectedTask.output && <MarkdownContent content={selectedTask.output} />}{selectedTask.error && <p>{selectedTask.error}</p>}</section>}

                <section className="controlEventSection"><header><strong>执行记录</strong><span>{taskEvents.length}</span></header><div className="controlEventList">{[...taskEvents].reverse().map((event) => <div key={event.id}><span className={`controlEventDot ${event.type.includes('failed') || event.payload.error ? 'error' : ''}`} /><p><strong>{controlEventSummary(event)}</strong><small>{new Date(event.createdAt).toLocaleTimeString('zh-CN')} · {event.type}</small></p>{(event.frameId || event.frameUrl) && <Monitor size={14} />}</div>)}{taskEvents.length === 0 && <div className="controlEmpty"><Clock3 size={20} /><span>等待设备事件</span></div>}</div></section>
              </> : <div className="controlDetailEmpty"><Monitor size={34} /><strong>选择一个控制任务</strong></div>}
            </section>
          </div>
        </div>
      </section>
    </div>
  );
}

function SharedFilesDialog({ files, conversations, selectedId, busy, onUpload, onDiscard }: { files: NativeSharedFile[]; conversations: Conversation[]; selectedId: string; busy: boolean; onUpload: (conversationId: string) => void; onDiscard: () => void }) {
  const [target, setTarget] = useState(selectedId || conversations[0]?.id || '');
  const [pickerOpen, setPickerOpen] = useState(false);
  const selectedConversation = conversations.find((conversation) => conversation.id === target) || null;
  const groups = groupConversationsByDate(conversations);
  return <div className="modalBackdrop sharedFilesBackdrop" onMouseDown={(event) => { if (!busy && event.currentTarget === event.target) onDiscard(); }}><section className="modal sharedFilesModal" role="dialog" aria-modal="true" aria-label="上传分享文件"><header><div><h2>上传分享文件</h2><p>{files.length} 个文件 · 仅当前会话暂存</p></div><button className="iconButton" title="取消并删除待上传文件" disabled={busy} onClick={onDiscard}><X size={18} /></button></header><div className="sharedFilesContent"><div className="sharedFilesList">{files.map((file) => <div key={file.id}><span className="sharedFileIcon"><FileText size={19} /></span><span><strong>{file.filename}</strong><small>{formatSize(file.sizeBytes)}</small></span></div>)}</div><div className="sharedConversationField"><span>选择对话</span><button type="button" className={`sharedConversationTrigger ${pickerOpen ? 'open' : ''}`} aria-haspopup="listbox" aria-expanded={pickerOpen} disabled={busy} onClick={() => setPickerOpen((value) => !value)}><MessageCircle size={18} /><span><strong>{selectedConversation?.title || '选择对话'}</strong><small>{selectedConversation ? groupConversationsByDate([selectedConversation])[0]?.label : ''}</small></span><ChevronDown size={18} /></button>{pickerOpen && <div className="sharedConversationPicker" role="listbox" aria-label="选择对话">{groups.map((group) => <section key={group.label}><p>{group.label}</p>{group.conversations.map((conversation) => <button type="button" role="option" aria-selected={conversation.id === target} className={conversation.id === target ? 'active' : ''} key={conversation.id} onClick={() => { setTarget(conversation.id); setPickerOpen(false); }}><span>{conversation.title}</span>{conversation.id === target && <Check size={16} />}</button>)}</section>)}</div>}</div><footer><button className="primaryButton sharedUploadButton" disabled={!target || busy} onClick={() => onUpload(target)}>{busy ? <Loader2 className="spin" size={17} /> : <Upload size={17} />}上传到该对话</button></footer></div></section></div>;
}

function AppDownloadMenu({ androidUrl, windowsUrl }: { androidUrl: string; windowsUrl: string }) {
  const [open, setOpen] = useState(false);
  return <div className="appDownloadMenu" onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}><button className="iconButton" title="下载客户端" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(true)}><Smartphone size={18} /></button>{open && <div className="appDownloadDropdown" role="menu"><a href={androidUrl} download role="menuitem"><Smartphone size={17} /><span><strong>Android</strong><small>ARM64 安装包</small></span><Download size={15} /></a><a href={windowsUrl} download role="menuitem"><Laptop size={17} /><span><strong>Windows</strong><small>x64 Computer Agent</small></span><Download size={15} /></a><div className="disabled" aria-disabled="true"><Laptop size={17} /><span><strong>Linux</strong><small>暂未提供</small></span></div></div>}</div>;
}

function AppDownloadPrompt({ url, onClose, onDismissPermanently }: { url: string; onClose: () => void; onDismissPermanently: () => void }) {
  return <div className="modalBackdrop appDownloadBackdrop"><section className="modal appDownloadPrompt" role="dialog" aria-modal="true" aria-label="下载 Android APP"><div className="appDownloadPromptIcon"><Smartphone size={24} /></div><h2>下载 APP 版</h2><p>在 Android 手机上使用完整的通知和设备管理功能。</p><div className="appDownloadPromptActions"><a className="primaryButton" href={url} download onClick={onClose}><Download size={17} />下载</a><button className="secondaryButton" onClick={onClose}>取消</button><button className="quietButton" onClick={onDismissPermanently}>永久取消</button></div></section></div>;
}

function WindowsUpdatePrompt({ version, url, onClose }: { version: string; url: string; onClose: () => void }) {
  return <div className="modalBackdrop appDownloadBackdrop"><section className="modal appDownloadPrompt" role="dialog" aria-modal="true" aria-label="更新 Windows 客户端"><div className="appDownloadPromptIcon"><Monitor size={24} /></div><h2>发现新版本 {version}</h2><p>下载完成后关闭当前客户端，并运行新版程序完成替换。</p><div className="appDownloadPromptActions"><a className="primaryButton" href={url} download onClick={onClose}><Download size={17} />下载更新</a><button className="secondaryButton" onClick={onClose}>稍后</button></div></section></div>;
}

function PortsDialog({ token, onClose }: { token: string; onClose: () => void }) {
  const [ports, setPorts] = useState<PreviewPort[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [customPort, setCustomPort] = useState('');
  async function load() {
    setLoading(true); setError('');
    try { setPorts((await terminalApi.ports(token)).ports); }
    catch (reason) { setError(reason instanceof Error ? reason.message : '端口读取失败'); }
    finally { setLoading(false); }
  }
  useEffect(() => { void load(); }, [token]);
  async function addPort(event: React.FormEvent) {
    event.preventDefault();
    const value = Number(customPort);
    if (!Number.isInteger(value) || value < 1024 || value > 65535 || value === 8642) { setError('请输入 1024 至 65535 的可用端口'); return; }
    setLoading(true); setError('');
    try {
      const result = await terminalApi.openPort(token, value);
      setPorts((items) => [...items.filter((item) => item.port !== value), result.port].sort((a, b) => a.port - b.port));
      setCustomPort('');
    } catch (reason) { setError(reason instanceof Error ? reason.message : '端口配置失败'); }
    finally { setLoading(false); }
  }
  async function removePort(port: number) {
    setLoading(true); setError('');
    try { await terminalApi.removePort(token, port); await load(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : '端口移除失败'); }
    finally { setLoading(false); }
  }
  return <div className="modalBackdrop" onMouseDown={(event) => { if (event.currentTarget === event.target) onClose(); }}><section className="modal portsModal" role="dialog" aria-modal="true" aria-label="端口"><header><div><h2>端口</h2><p>通过用户路径复用公开端口</p></div><div className="modalHeaderActions"><button className="iconButton" title="刷新" disabled={loading} onClick={() => void load()}><RefreshCw className={loading ? 'spin' : ''} size={16} /></button><button className="iconButton" title="关闭" onClick={onClose}><X size={17} /></button></div></header><form className="customPortForm" onSubmit={addPort}><input type="number" min={1024} max={65535} value={customPort} onChange={(event) => setCustomPort(event.target.value)} placeholder="自定义端口，例如 21374" /><button className="primaryButton" disabled={loading || !customPort}><Plus size={16} />添加</button></form><div className="portList">{ports.map((item) => <div className="portRow" key={item.port}><a href={item.url} target={isNativeApp() ? '_self' : '_blank'} rel="noreferrer"><RadioTower size={17} /><strong>{item.port}</strong><span>{item.url}</span><small className={item.listening ? 'listening' : ''}>{item.listening ? '监听中' : '等待服务'}</small><ExternalLink size={15} /></a>{item.configured && <button className="iconButton danger" title="移除手动端口" onClick={() => void removePort(item.port)}><Trash2 size={14} /></button>}</div>)}{loading && !ports.length && <div className="tableEmpty"><Loader2 className="spin" size={18} />正在检测开放端口</div>}{!loading && !ports.length && !error && <div className="tableEmpty">暂未检测到公开监听端口</div>}{error && <p className="formError">{error}</p>}</div></section></div>;
}

function NotificationToggle({
  enabled, title, detail, icon, disabled, onToggle,
}: {
  enabled: boolean;
  title: string;
  detail: string;
  icon: React.ReactNode;
  disabled: boolean;
  onToggle: () => void;
}) {
  return <div className="notificationRow"><span className="notificationIcon">{icon}</span><div><strong>{title}</strong><small>{detail}</small></div><button type="button" role="switch" aria-checked={enabled} aria-label={`${title}${enabled ? '已开启' : '已关闭'}`} className={`toggleSwitch ${enabled ? 'on' : ''}`} disabled={disabled} onClick={onToggle}><span /></button></div>;
}

function NotificationSettings({ token }: { token: string }) {
  const [preferences, setPreferences] = useState(defaultNotificationPreferences);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState<keyof NotificationPreferences | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    void notificationApi.preferences(token)
      .then((result) => { if (!cancelled) setPreferences(result.preferences); })
      .catch((reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : '通知设置读取失败'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [token]);

  async function toggle(key: keyof NotificationPreferences) {
    setSaving(key); setError('');
    try {
      const result = await notificationApi.updatePreferences(token, {
        [notificationPreferenceApiKeys[key]]: !preferences[key],
      });
      setPreferences(result.preferences);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '通知设置保存失败');
    } finally {
      setSaving(null);
    }
  }

  const disabled = loading || saving !== null;
  return <section className="notificationSettings"><div className="notificationGroup"><header><span>对话</span></header><NotificationToggle enabled={preferences.chatCompleted} title="对话完成" detail="普通对话生成回复后通知" icon={<MessageCircle size={17} />} disabled={disabled} onToggle={() => void toggle('chatCompleted')} /></div><div className="notificationGroup"><header><span>自动化</span></header><NotificationToggle enabled={preferences.agentCompleted} title="Agent 任务完成" detail="Hermes 手动任务完成后通知" icon={<Bot size={17} />} disabled={disabled} onToggle={() => void toggle('agentCompleted')} /><NotificationToggle enabled={preferences.scheduleCompleted} title="定时任务完成" detail="后台计划执行结束后通知" icon={<CalendarClock size={17} />} disabled={disabled} onToggle={() => void toggle('scheduleCompleted')} /></div><div className="notificationGroup"><header><span>需要关注</span></header><NotificationToggle enabled={preferences.taskFailed} title="任务失败或取消" detail="执行异常、中止或取消时通知" icon={<CircleStop size={17} />} disabled={disabled} onToggle={() => void toggle('taskFailed')} /><NotificationToggle enabled={preferences.approvalRequired} title="等待审批" detail="Hermes 请求执行敏感操作时通知" icon={<ShieldAlert size={17} />} disabled={disabled} onToggle={() => void toggle('approvalRequired')} /></div><div className="notificationGroup"><header><span>系统</span></header><NotificationToggle enabled={preferences.system} title="系统通知" detail="账户、安全和服务状态通知" icon={<Bell size={17} />} disabled={disabled} onToggle={() => void toggle('system')} /></div>{loading && <div className="notificationLoading"><Loader2 className="spin" size={17} /></div>}{error && <p className="formError" role="alert">{error}</p>}</section>;
}

function DeviceSettings({ token, onLogout }: { token: string; onLogout: () => void }) {
  const [devices, setDevices] = useState<LoginDevice[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  async function load() {
    setLoading(true); setError('');
    try { setDevices((await deviceApi.list(token)).devices); }
    catch (reason) { setError(reason instanceof Error ? reason.message : '设备读取失败'); }
    finally { setLoading(false); }
  }
  useEffect(() => { void load(); }, [token]);

  async function logoutDevice(item: LoginDevice) {
    if (!window.confirm(`下线“${item.name}”上的全部会话？`)) return;
    setLoading(true); setError('');
    try {
      const result = await deviceApi.logout(token, item.id);
      if (result.current) { onLogout(); return; }
      await load();
    } catch (reason) { setError(reason instanceof Error ? reason.message : '设备下线失败'); setLoading(false); }
  }

  async function removeDevice(item: LoginDevice) {
    if (!window.confirm(`删除“${item.name}”？该设备下次登录必须验证邮箱。`)) return;
    setLoading(true); setError('');
    try {
      const result = await deviceApi.remove(token, item.id);
      if (result.current) { persistTrustedDeviceToken(''); onLogout(); return; }
      await load();
    } catch (reason) { setError(reason instanceof Error ? reason.message : '设备删除失败'); setLoading(false); }
  }

  return <section className="deviceSettings"><header><strong>登录设备</strong><button className="iconButton" title="刷新设备" disabled={loading} onClick={() => void load()}><RefreshCw className={loading ? 'spin' : ''} size={15} /></button></header><div className="deviceList">{devices.map((item) => <article className="deviceRow" key={item.id}>{item.platform === 'android' ? <Smartphone size={18} /> : <Monitor size={18} />}<div><strong>{item.name}{item.current && <em>当前设备</em>}</strong><small>{item.trusted ? '已信任' : '未信任'} · {item.activeSessions ? `${item.activeSessions} 个在线会话` : '已下线'} · {new Date(item.lastSeenAt).toLocaleString('zh-CN')}</small></div><button className="iconButton" title="下线设备" disabled={loading || !item.activeSessions} onClick={() => void logoutDevice(item)}><Power size={15} /></button><button className="iconButton danger" title="删除设备" disabled={loading} onClick={() => void removeDevice(item)}><Trash2 size={15} /></button></article>)}{loading && !devices.length && <div className="tableEmpty"><Loader2 className="spin" size={17} />正在读取设备</div>}{!loading && !devices.length && !error && <div className="tableEmpty">暂无登录设备</div>}</div>{error && <p className="formError" role="alert">{error}</p>}</section>;
}

function PersonalSettingsDialog({
  token, user, backgroundUrl, topDockPreferences, onBackgroundChange, onTopDockChange, onLogout, onClose,
}: {
  token: string;
  user: User;
  backgroundUrl: string;
  topDockPreferences: TopDockPreferences;
  onBackgroundChange: (blob: Blob | null) => void;
  onTopDockChange: (item: TopDockItem, visible: boolean) => void;
  onLogout: () => void;
  onClose: () => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [section, setSection] = useState<'membership' | 'appearance' | 'dock' | 'notifications' | 'savepoints' | 'devices' | 'updates'>('membership');
  const [activationCode, setActivationCode] = useState('');
  const [savepoints, setSavepoints] = useState<Savepoint[]>([]);
  const [savepointName, setSavepointName] = useState('');

  async function loadSavepoints() {
    setBusy(true); setError('');
    try { setSavepoints((await savepointApi.list(token)).savepoints); }
    catch (reason) { setError(reason instanceof Error ? reason.message : '保存点读取失败'); }
    finally { setBusy(false); }
  }

  useEffect(() => { if (section === 'savepoints') void loadSavepoints(); }, [section, token]);

  async function upload(file: File | undefined) {
    if (!file) return;
    setBusy(true);
    setError('');
    try {
      await profileApi.uploadBackground(token, file);
      onBackgroundChange(await profileApi.background(token));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '背景上传失败');
    } finally {
      setBusy(false);
      if (inputRef.current) inputRef.current.value = '';
    }
  }

  async function reset() {
    setBusy(true);
    setError('');
    try {
      await profileApi.deleteBackground(token);
      onBackgroundChange(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '恢复默认背景失败');
    } finally {
      setBusy(false);
    }
  }

  async function createSavepoint(event: React.FormEvent) {
    event.preventDefault();
    const name = savepointName.trim();
    if (!name) return;
    setBusy(true); setError('');
    try {
      const result = await savepointApi.create(token, name);
      setSavepoints((items) => [result.savepoint, ...items]);
      setSavepointName('');
    } catch (reason) { setError(reason instanceof Error ? reason.message : '保存点创建失败'); }
    finally { setBusy(false); }
  }

  async function restoreSavepoint(item: Savepoint) {
    if (!window.confirm(`恢复到“${item.name}”？当前环境内容将被替换。`)) return;
    setBusy(true); setError('');
    try { await savepointApi.restore(token, item.id); }
    catch (reason) { setError(reason instanceof Error ? reason.message : '保存点恢复失败'); }
    finally { setBusy(false); }
  }

  async function removeSavepoint(item: Savepoint) {
    if (!window.confirm(`删除保存点“${item.name}”？`)) return;
    setBusy(true); setError('');
    try { await savepointApi.remove(token, item.id); setSavepoints((items) => items.filter((value) => value.id !== item.id)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : '保存点删除失败'); }
    finally { setBusy(false); }
  }

  async function activate(event: React.FormEvent) {
    event.preventDefault();
    if (!activationCode.trim()) return;
    setBusy(true); setError('');
    try {
      await authApi.redeemActivation(token, activationCode.trim());
      window.location.reload();
    } catch (reason) { setError(reason instanceof Error ? reason.message : '激活失败'); setBusy(false); }
  }

  const subtitle = section === 'membership' ? '账户权限' : section === 'appearance' ? '个人偏好' : section === 'dock' ? '顶部 Dock 栏显示' : section === 'notifications' ? '分类通知' : section === 'devices' ? '登录安全' : section === 'updates' ? '应用版本' : '环境保存点';
  return (
    <div className="modalBackdrop" onMouseDown={(event) => { if (event.currentTarget === event.target) onClose(); }}>
      <section className="modal personalSettingsModal" role="dialog" aria-modal="true" aria-label="设置">
        <header><div><h2>设置</h2><p>{subtitle}</p></div><button className="iconButton" title="关闭" onClick={onClose}><X size={17} /></button></header>
        <div className="personalSettingsLayout">
          <nav aria-label="设置分类">
            <button className={section === 'membership' ? 'active' : ''} onClick={() => setSection('membership')}><KeyRound size={16} />账户权限</button>
            <button className={section === 'appearance' ? 'active' : ''} onClick={() => setSection('appearance')}><ImagePlus size={16} />外观</button>
            <button className={section === 'dock' ? 'active' : ''} onClick={() => setSection('dock')}><PanelTop size={16} />顶部 Dock</button>
            <button className={section === 'notifications' ? 'active' : ''} onClick={() => setSection('notifications')}><Bell size={16} />通知</button>
            {isNativeApp() && user.accessTier === 'vip' && <button className={section === 'devices' ? 'active' : ''} onClick={() => setSection('devices')}><Smartphone size={16} />登录设备</button>}
            {isNativeApp() && <button className={section === 'updates' ? 'active' : ''} onClick={() => setSection('updates')}><Download size={16} />应用更新</button>}
            {user.accessTier === 'vip' && <button className={section === 'savepoints' ? 'active' : ''} onClick={() => setSection('savepoints')}><History size={16} />保存点</button>}
          </nav>
          <div className="personalSettingsBody">
            {section === 'membership' ? (
              <section className="membershipSettings"><header><strong className={`membershipStatus ${user.accessTier === 'vip' ? 'vip' : 'inactive'}`}>{user.accessTier === 'vip' ? 'VIP 已激活' : 'VIP 未激活'}</strong></header><p>{user.accessTier === 'vip' ? 'Agent、浏览器、终端、自动化与电脑控制均已开放。' : '当前仅可使用默认模型进行 Chat。输入激活码可开放完整功能。'}</p>{user.accessTier !== 'vip' && <form className="savepointCreate" onSubmit={activate}><input maxLength={80} value={activationCode} onChange={(event) => setActivationCode(event.target.value.toUpperCase())} placeholder="VIP-XXXX-XXXX-XXXX-XXXX" /><button className="primaryButton" disabled={busy || !activationCode.trim()}>{busy ? <Loader2 className="spin" size={16} /> : <KeyRound size={16} />}激活</button></form>}</section>
            ) : section === 'appearance' ? (
              <section>
                <header><strong>背景图片</strong></header>
                <div className="wallpaperPreview" style={backgroundUrl ? { backgroundImage: `url("${backgroundUrl}")` } : undefined} />
                <input ref={inputRef} type="file" hidden accept="image/jpeg,image/png,image/webp,.jpg,.jpeg,.png,.webp" onChange={(event) => void upload(event.target.files?.[0])} />
                <div className="settingsActions"><button className="primaryButton" disabled={busy} onClick={() => inputRef.current?.click()}>{busy ? <Loader2 className="spin" size={16} /> : <ImagePlus size={16} />}上传背景</button><button className="secondaryButton" disabled={busy || !backgroundUrl} onClick={() => void reset()}><RotateCcw size={16} />恢复默认</button></div>
              </section>
            ) : section === 'dock' ? (
              <section className="dockSettings">
                <header><strong>顶部 Dock 栏显示</strong></header>
                <div className="dockSettingList">
                  <label className="dockSettingRow locked"><span className="dockSettingIcon"><PanelLeft size={17} /></span><span><strong>侧边栏</strong><small>固定开启，不允许关闭</small></span><input type="checkbox" checked disabled aria-label="侧边栏固定开启" /></label>
                  <label className="dockSettingRow"><span className="dockSettingIcon"><Globe2 size={17} /></span><span><strong>浏览器</strong><small>实时浏览器入口</small></span><input type="checkbox" checked={topDockPreferences.browser} onChange={(event) => onTopDockChange('browser', event.target.checked)} /></label>
                  <label className="dockSettingRow"><span className="dockSettingIcon"><Laptop size={17} /></span><span><strong>电脑远程控制</strong><small>当前对话的远程设备入口</small></span><input type="checkbox" checked={topDockPreferences.computer} onChange={(event) => onTopDockChange('computer', event.target.checked)} /></label>
                  <label className="dockSettingRow"><span className="dockSettingIcon"><SquareTerminal size={17} /></span><span><strong>终端</strong><small>任务终端入口</small></span><input type="checkbox" checked={topDockPreferences.terminal} onChange={(event) => onTopDockChange('terminal', event.target.checked)} /></label>
                  <label className="dockSettingRow"><span className="dockSettingIcon"><RadioTower size={17} /></span><span><strong>端口</strong><small>预览端口入口</small></span><input type="checkbox" checked={topDockPreferences.ports} onChange={(event) => onTopDockChange('ports', event.target.checked)} /></label>
                  <label className="dockSettingRow"><span className="dockSettingIcon"><FolderOpen size={17} /></span><span><strong>工作区文件</strong><small>文件与文件夹入口</small></span><input type="checkbox" checked={topDockPreferences.workspace} onChange={(event) => onTopDockChange('workspace', event.target.checked)} /></label>
                  <label className="dockSettingRow"><span className="dockSettingIcon"><CalendarClock size={17} /></span><span><strong>定时任务</strong><small>任务计划入口</small></span><input type="checkbox" checked={topDockPreferences.schedules} onChange={(event) => onTopDockChange('schedules', event.target.checked)} /></label>
                  <label className="dockSettingRow"><span className="dockSettingIcon"><Download size={17} /></span><span><strong>客户端下载</strong><small>应用下载入口</small></span><input type="checkbox" checked={topDockPreferences.downloads} onChange={(event) => onTopDockChange('downloads', event.target.checked)} /></label>
                </div>
              </section>
            ) : section === 'notifications' ? (
              <NotificationSettings token={token} />
            ) : section === 'devices' ? (
              <DeviceSettings token={token} onLogout={onLogout} />
            ) : section === 'updates' ? (
              <section className="nativeUpdateSettings">
                <header><strong>妙想之地 {nativeAppVersion()}</strong></header>
                <p>自动检查正式版本；安装前会校验下载文件的 SHA-256。</p>
                <div className="settingsActions"><button className="primaryButton" onClick={checkForNativeUpdate}><RefreshCw size={16} />检查更新</button></div>
              </section>
            ) : (
              <section className="savepointSection">
                <form className="savepointCreate" onSubmit={createSavepoint}><input maxLength={60} value={savepointName} onChange={(event) => setSavepointName(event.target.value)} placeholder="保存点名称" /><button className="primaryButton" disabled={busy || !savepointName.trim()}>{busy ? <Loader2 className="spin" size={16} /> : <Plus size={16} />}创建</button></form>
                <div className="savepointList">{savepoints.map((item) => <article className="savepointRow" key={item.id}><History size={17} /><div><strong>{item.name}</strong><small>{new Date(item.createdAt).toLocaleString('zh-CN')} · {item.fileCount} 个文件 · {formatSize(item.logicalBytes)} · 新增 {formatSize(item.storedBytes)}</small></div><button className="iconButton" title="恢复此保存点" disabled={busy} onClick={() => void restoreSavepoint(item)}><ArchiveRestore size={16} /></button><button className="iconButton danger" title="删除保存点" disabled={busy} onClick={() => void removeSavepoint(item)}><Trash2 size={15} /></button></article>)}{busy && !savepoints.length && <div className="tableEmpty"><Loader2 className="spin" size={18} />正在读取</div>}{!busy && !savepoints.length && <div className="tableEmpty">暂无保存点</div>}</div>
              </section>
            )}
            {error && <p className="formError" role="alert">{error}</p>}
          </div>
        </div>
      </section>
    </div>
  );
}

function WorkspaceDialog({ token, conversationId, onClose }: { token: string; conversationId: string; onClose: () => void }) {
  const uploadInputRef = useRef<HTMLInputElement>(null);
  const [path, setPath] = useState('');
  const [entries, setEntries] = useState<WorkspaceEntry[]>([]);
  const [previewEntry, setPreviewEntry] = useState<WorkspaceEntry | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  async function load(next = '') {
    setLoading(true); setError('');
    try { const result = await workspaceApi.list(token, conversationId, next); setPath(result.path); setEntries(result.entries); }
    catch (reason) { setError(reason instanceof Error ? reason.message : '目录读取失败'); }
    finally { setLoading(false); }
  }
  useEffect(() => { void load(); }, [conversationId, token]);
  async function uploadFiles(files: FileList | null) {
    if (!files?.length) return;
    setUploading(true); setError('');
    try {
      for (const file of Array.from(files).slice(0, 8)) await workspaceApi.upload(token, conversationId, path, file);
      await load(path);
    } catch (reason) { setError(reason instanceof Error ? reason.message : '文件上传失败'); }
    finally {
      setUploading(false);
      if (uploadInputRef.current) uploadInputRef.current.value = '';
    }
  }
  async function download(entry: WorkspaceEntry) {
    const downloadUrl = workspaceApi.downloadUrl(conversationId, entry.path);
    if (nativeAuthenticatedDownload(downloadUrl, entry.name)) return;
    const response = await fetch(downloadUrl, { headers: { Authorization: `Bearer ${token}` } });
    if (!response.ok) { setError('文件下载失败'); return; }
    const url = URL.createObjectURL(await response.blob());
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = entry.name; anchor.click(); URL.revokeObjectURL(url);
  }
  const previewArtifact: TaskArtifact | null = previewEntry ? {
    id: `workspace:${previewEntry.path}`,
    path: previewEntry.path,
    filename: previewEntry.name,
    mimeType: previewEntry.mimeType,
    sizeBytes: previewEntry.sizeBytes,
  } : null;
  const parent = path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '';
  const displayPath = `/workspace${path ? `/${path}` : ''}`;
  return <>
    <div className="modalBackdrop" onMouseDown={(event) => { if (event.currentTarget === event.target) onClose(); }}>
      <section className="modal toolModal" role="dialog" aria-modal="true">
        <header>
          <div><h2>工作区</h2><p>{displayPath}</p></div>
          <div className="modalHeaderActions">
            <input ref={uploadInputRef} type="file" hidden multiple onChange={(event) => void uploadFiles(event.target.files)} />
            <button className="iconButton" title="上传文件" disabled={uploading} onClick={() => uploadInputRef.current?.click()}>{uploading ? <Loader2 className="spin" size={16} /> : <Upload size={16} />}</button>
            <button className="iconButton" title="关闭" onClick={onClose}><X size={17} /></button>
          </div>
        </header>
        <div className="fileList">
          {path && <button className="fileRow" onClick={() => void load(parent)}><Folder size={17} /><strong>..</strong></button>}
          {entries.map((entry) => <button className="fileRow" key={entry.path} onClick={() => entry.type === 'directory' ? void load(entry.path) : setPreviewEntry(entry)}>{entry.type === 'directory' ? <Folder size={17} /> : <File size={17} />}<strong>{entry.name}</strong><span>{entry.type === 'file' ? formatSize(entry.sizeBytes) : ''}</span>{entry.type === 'file' && <Eye size={15} />}</button>)}
          {loading && <div className="tableEmpty"><Loader2 className="spin" size={18} />正在读取</div>}
          {!loading && !entries.length && !error && <div className="tableEmpty">目录为空</div>}
          {error && <p className="formError">{error}</p>}
        </div>
      </section>
    </div>
    {previewArtifact && <FilePreviewModal token={token} conversationId={conversationId} artifact={previewArtifact} onClose={() => setPreviewEntry(null)} onDownload={() => void download(previewEntry!)} />}
  </>;
}

function ScheduleDialog({ token, onClose }: { token: string; conversationId: string; onClose: () => void }) {
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  async function load() { setBusy(true); setError(''); try { setSchedules((await scheduleApi.list(token)).schedules); } catch (reason) { setError(reason instanceof Error ? reason.message : '读取失败'); } finally { setBusy(false); } }
  useEffect(() => { void load(); }, [token]);
  async function action(item: Schedule, nextAction: 'pause' | 'resume' | 'run') { setBusy(true); setError(''); try { const result = await scheduleApi.action(token, item.id, nextAction); setSchedules((items) => items.map((value) => value.id === item.id ? result.schedule : value)); } catch (reason) { setError(reason instanceof Error ? reason.message : '更新失败'); } finally { setBusy(false); } }
  return <div className="modalBackdrop" onMouseDown={(event) => { if (event.currentTarget === event.target) onClose(); }}><section className="modal scheduleModal" role="dialog" aria-modal="true"><header><div><h2>定时任务</h2><p>由 Hermes 管理</p></div><button className="iconButton" title="关闭" onClick={onClose}><X size={17} /></button></header>{error && <p className="formError scheduleError">{error}</p>}<div className="scheduleList">{schedules.map((item) => <div className="scheduleRow" key={item.id}><Clock3 size={17} /><div><strong>{item.name || '未命名任务'}</strong><small>{item.schedule_display || item.schedule?.display || item.schedule?.expr || '未提供执行计划'} · {item.next_run_at ? new Date(item.next_run_at).toLocaleString('zh-CN') : item.enabled ? '等待调度' : '已暂停'}</small>{item.last_error && <small className="scheduleFailure">上次错误：{item.last_error}</small>}</div><button className="secondaryButton compact" disabled={busy} onClick={() => void action(item, 'run')}>立即运行</button><button className="secondaryButton compact" disabled={busy} onClick={() => void action(item, item.enabled ? 'pause' : 'resume')}>{item.enabled ? '暂停' : '启用'}</button></div>)}{busy && !schedules.length && <div className="tableEmpty"><Loader2 className="spin" size={18} />正在读取 Hermes 任务</div>}{!busy && !schedules.length && <div className="tableEmpty">Hermes 尚未创建定时任务</div>}</div></section></div>;
}
