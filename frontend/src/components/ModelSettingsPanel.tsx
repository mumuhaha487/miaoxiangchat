import { useEffect, useState, type ReactNode } from 'react';
import { BrainCircuit, CheckCircle2, Eye, EyeOff, FlaskConical, Loader2, MessageSquare, Save, Wrench } from 'lucide-react';
import { adminApi } from '../api';
import type { AdminModelEndpoint, AdminModelSettings } from '../types';

type Role = 'chat' | 'coordinator' | 'executor';

interface DraftEndpoint extends AdminModelEndpoint {
  apiKey: string;
  visionApiKey: string;
}

interface Props {
  token: string;
  value: AdminModelSettings;
  onSaved: (value: AdminModelSettings) => void;
}

function endpointDraft(value: AdminModelEndpoint): DraftEndpoint {
  return { ...value, apiKey: '', visionApiKey: '' };
}

function requestEndpoint(value: DraftEndpoint) {
  return {
    base_url: value.baseUrl.trim(),
    api_key: value.apiKey.trim(),
    model: value.model.trim(),
    supports_vision: value.supportsVision,
    reasoning_enabled: value.reasoningEnabled,
    reasoning_effort: value.reasoningEffort,
    vision_base_url: value.supportsVision ? '' : value.visionBaseUrl.trim(),
    vision_api_key: value.supportsVision ? '' : value.visionApiKey.trim(),
    vision_model: value.supportsVision ? '' : value.visionModel.trim(),
  };
}

function ModelEditor({
  title, description, icon, value, disabled, busy, result, onChange, onTest,
}: {
  title: string;
  description: string;
  icon: ReactNode;
  value: DraftEndpoint;
  disabled: boolean;
  busy: boolean;
  result: string;
  onChange: (value: DraftEndpoint) => void;
  onTest: () => void;
}) {
  const patch = (next: Partial<DraftEndpoint>) => onChange({ ...value, ...next });
  return (
    <section className="modelConfigBlock">
      <header className="modelConfigHeader">
        <span className="modelRoleIcon">{icon}</span>
        <div><h3>{title}</h3><p>{description}</p></div>
        <button className="secondaryButton" type="button" disabled={disabled || busy} onClick={onTest}>
          {busy ? <Loader2 className="spin" size={15} /> : <FlaskConical size={15} />}测试连接
        </button>
      </header>
      <div className="modelFields">
        <label className="wideField"><span>API URL</span><input required type="url" value={value.baseUrl} onChange={(event) => patch({ baseUrl: event.target.value })} placeholder="https://api.example.com/v1" /></label>
        <label><span>模型名称</span><input required value={value.model} onChange={(event) => patch({ model: event.target.value })} placeholder="model-name 或 auto" /></label>
        <label><span>API Key</span><input type="password" autoComplete="new-password" value={value.apiKey} onChange={(event) => patch({ apiKey: event.target.value })} placeholder={value.apiKeyConfigured ? '已配置，留空保持不变' : '请输入 API Key'} /></label>
      </div>
      <label className="switchRow">
        <span><strong>模型支持视觉多模态</strong><small>开启后图片直接发送给当前模型</small></span>
        <input type="checkbox" checked={value.supportsVision} onChange={(event) => patch({ supportsVision: event.target.checked })} />
        <i aria-hidden="true" />
      </label>
      <div className="reasoningSettings">
        <label className="switchRow">
          <span><strong>思考模式</strong><small>让该模型在复杂任务中进行更深入的推理</small></span>
          <input type="checkbox" checked={value.reasoningEnabled} onChange={(event) => patch({ reasoningEnabled: event.target.checked })} />
          <i aria-hidden="true" />
        </label>
        {value.reasoningEnabled && <label className="reasoningEffortField"><span>思考强度</span><select value={value.reasoningEffort} onChange={(event) => patch({ reasoningEffort: event.target.value as DraftEndpoint['reasoningEffort'] })}><option value="minimal">Minimal</option><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option><option value="xhigh">XHigh</option><option value="max">Max（默认）</option><option value="ultra">Ultra</option></select></label>}
      </div>
      {!value.supportsVision && (
        <div className="visionFallbackFields">
          <div className="visionFallbackTitle"><Eye size={16} /><span><strong>视觉多模态模型</strong><small>先识别图片，再把描述交给当前文本模型</small></span></div>
          <div className="modelFields">
            <label className="wideField"><span>视觉 API URL</span><input required type="url" value={value.visionBaseUrl} onChange={(event) => patch({ visionBaseUrl: event.target.value })} placeholder="https://vision.example.com/v1" /></label>
            <label><span>视觉模型名称</span><input required value={value.visionModel} onChange={(event) => patch({ visionModel: event.target.value })} placeholder="vision-model" /></label>
            <label><span>视觉 API Key</span><input type="password" autoComplete="new-password" value={value.visionApiKey} onChange={(event) => patch({ visionApiKey: event.target.value })} placeholder={value.visionApiKeyConfigured ? '已配置，留空保持不变' : '请输入视觉 API Key'} /></label>
          </div>
        </div>
      )}
      {result && <p className="modelTestResult"><CheckCircle2 size={14} />{result}</p>}
    </section>
  );
}

export function ModelSettingsPanel({ token, value, onSaved }: Props) {
  const [splitEnabled, setSplitEnabled] = useState(value.splitEnabled);
  const [chat, setChat] = useState(() => endpointDraft(value.chat));
  const [coordinator, setCoordinator] = useState(() => endpointDraft(value.coordinator));
  const [executor, setExecutor] = useState(() => endpointDraft(value.executor));
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [results, setResults] = useState<Record<Role, string>>({ chat: '', coordinator: '', executor: '' });

  useEffect(() => {
    setSplitEnabled(value.splitEnabled);
    setChat(endpointDraft(value.chat));
    setCoordinator(endpointDraft(value.coordinator));
    setExecutor(endpointDraft(value.executor));
  }, [value]);

  async function save() {
    setBusy('save');
    setError('');
    try {
      const response = await adminApi.updateModelSettings(token, {
        split_enabled: splitEnabled,
        chat: requestEndpoint(chat),
        coordinator: requestEndpoint(coordinator),
        executor: requestEndpoint(executor),
      });
      onSaved(response.models);
      return true;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '模型配置保存失败');
      return false;
    } finally {
      setBusy('');
    }
  }

  async function test(role: Role) {
    setResults((current) => ({ ...current, [role]: '' }));
    if (!await save()) return;
    setBusy('test:' + role);
    try {
      const response = await adminApi.testModel(token, role);
      const replies = response.stages.map((stage) => stage.name + '：' + stage.reply).join('；');
      setResults((current) => ({
        ...current,
        [role]: response.model + ' · ' + response.latencyMs + ' ms · ' + replies,
      }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '模型连接测试失败');
    } finally {
      setBusy('');
    }
  }

  const activeRoles: Role[] = splitEnabled ? ['coordinator', 'executor'] : ['executor'];
  return (
    <div className="modelSettingsLayout">
      <section className="settingsSection modelEndpointsSection">
        <header><div><h2>Chat 模型</h2><p>Basic 与 VIP 用户的普通对话独立使用此连接</p></div></header>
        <ModelEditor title="Chat 模型" description="普通用户只能使用这套基础 LLM 配置" icon={<MessageSquare size={18} />} value={chat} disabled={Boolean(busy)} busy={busy === 'test:chat'} result={results.chat} onChange={setChat} onTest={() => void test('chat')} />
      </section>

      <section className="settingsSection modelModeSection">
        <header><div><h2>Agent 模型分工</h2><p>仅登录且已激活 VIP 的 Agent 任务会使用下列模型</p></div></header>
        <label className="switchRow prominent">
          <span><strong>区分统筹模型和执行模型</strong><small>{splitEnabled ? '统筹负责计划与验收，执行负责代码和工具操作' : '关闭时，统筹与执行共用同一套模型配置'}</small></span>
          <input type="checkbox" checked={splitEnabled} onChange={(event) => setSplitEnabled(event.target.checked)} />
          <i aria-hidden="true" />
        </label>
      </section>

      <section className="settingsSection modelEndpointsSection">
        <header><div><h2>{splitEnabled ? 'Agent 模型连接' : 'Agent 统一模型连接'}</h2><p>兼容 OpenAI Chat Completions API</p></div></header>
        {activeRoles.includes('coordinator') && <ModelEditor title="统筹模型" description="制定规格、执行步骤，并负责最终验收" icon={<BrainCircuit size={18} />} value={coordinator} disabled={Boolean(busy)} busy={busy === 'test:coordinator'} result={results.coordinator} onChange={setCoordinator} onTest={() => void test('coordinator')} />}
        <ModelEditor title={splitEnabled ? '执行模型' : '统一模型'} description={splitEnabled ? '编写代码、调用浏览器和执行机械性任务' : '同时承担计划、执行与验收'} icon={splitEnabled ? <Wrench size={18} /> : (executor.supportsVision ? <Eye size={18} /> : <EyeOff size={18} />)} value={executor} disabled={Boolean(busy)} busy={busy === 'test:executor'} result={results.executor} onChange={setExecutor} onTest={() => void test('executor')} />
        {error && <p className="formError modelSettingsError" role="alert">{error}</p>}
        <footer className="modelSettingsActions"><button className="adminPrimary" type="button" disabled={Boolean(busy)} onClick={() => void save()}>{busy === 'save' ? <Loader2 className="spin" size={16} /> : <Save size={16} />}保存模型配置</button></footer>
      </section>
    </div>
  );
}
