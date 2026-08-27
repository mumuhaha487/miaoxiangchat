import { useEffect, useMemo, useState } from 'react';
import {
  Check, Clipboard, Download, FileCheck2, FolderPlus, Loader2, PackageSearch,
  Plus, Save, Search, Share2, ShieldCheck, Trash2, Workflow as WorkflowIcon, X,
} from 'lucide-react';
import { capabilityApi } from '../api';
import type { SkillRecord, Workflow, WorkflowCategory } from '../types';

interface Props {
  token: string;
  conversationId: string;
  onClose: () => void;
}

interface WorkflowDraft {
  id: string;
  name: string;
  description: string;
  instructions: string;
  triggers: string;
  categoryId: string;
}

interface CapabilitySnapshot {
  categories: WorkflowCategory[];
  workflows: Workflow[];
  skills: SkillRecord[];
}

const CAPABILITY_CACHE_MAX_AGE = 60_000;
let capabilityCache: { token: string; loadedAt: number; snapshot: CapabilitySnapshot } | null = null;
let capabilityRequest: { token: string; promise: Promise<CapabilitySnapshot> } | null = null;

function cachedSnapshot(token: string): CapabilitySnapshot | null {
  return capabilityCache?.token === token ? capabilityCache.snapshot : null;
}

function rememberSnapshot(token: string, snapshot: CapabilitySnapshot) {
  capabilityCache = { token, loadedAt: Date.now(), snapshot };
}

async function fetchSnapshot(token: string): Promise<CapabilitySnapshot> {
  if (capabilityCache?.token === token && Date.now() - capabilityCache.loadedAt < CAPABILITY_CACHE_MAX_AGE) {
    return capabilityCache.snapshot;
  }
  if (capabilityRequest?.token === token) return capabilityRequest.promise;
  const promise = capabilityApi.list(token).then((snapshot) => {
    rememberSnapshot(token, snapshot);
    return snapshot;
  });
  capabilityRequest = { token, promise };
  try {
    return await promise;
  } finally {
    if (capabilityRequest?.promise === promise) capabilityRequest = null;
  }
}

export async function preloadCapabilities(token: string) {
  if (token) await fetchSnapshot(token);
}

const emptyDraft: WorkflowDraft = {
  id: '', name: '', description: '', instructions: '', triggers: '', categoryId: '',
};

function workflowDraft(item: Workflow): WorkflowDraft {
  return {
    id: item.id,
    name: item.name,
    description: item.description,
    instructions: item.instructions,
    triggers: item.triggers.join('，'),
    categoryId: item.categoryId || '',
  };
}

export function CapabilityCenter({ token, conversationId, onClose }: Props) {
  const initialSnapshot = cachedSnapshot(token);
  const [tab, setTab] = useState<'workflows' | 'skills'>('workflows');
  const [categories, setCategories] = useState<WorkflowCategory[]>(initialSnapshot?.categories || []);
  const [workflows, setWorkflows] = useState<Workflow[]>(initialSnapshot?.workflows || []);
  const [skills, setSkills] = useState<SkillRecord[]>(initialSnapshot?.skills || []);
  const [draft, setDraft] = useState<WorkflowDraft>(() => initialSnapshot?.workflows[0] ? workflowDraft(initialSnapshot.workflows[0]) : emptyDraft);
  const [categoryName, setCategoryName] = useState('');
  const [skillQuery, setSkillQuery] = useState('');
  const [skillSource, setSkillSource] = useState('');
  const [installSource, setInstallSource] = useState('');
  const [consoleOutput, setConsoleOutput] = useState('');
  const [shareCode, setShareCode] = useState('');
  const [importCode, setImportCode] = useState('');
  const [busy, setBusy] = useState(initialSnapshot ? '' : 'load');
  const [error, setError] = useState('');

  const selectedWorkflow = useMemo(
    () => workflows.find((item) => item.id === draft.id) || null,
    [draft.id, workflows],
  );

  function commitSnapshot(snapshot: CapabilitySnapshot) {
    rememberSnapshot(token, snapshot);
    setCategories(snapshot.categories);
    setWorkflows(snapshot.workflows);
    setSkills(snapshot.skills);
  }

  function commitWorkflow(item: Workflow) {
    const nextWorkflows = [item, ...workflows.filter((workflow) => workflow.id !== item.id)];
    commitSnapshot({ categories, workflows: nextWorkflows, skills });
    setDraft(workflowDraft(item));
  }

  function commitSkill(item: SkillRecord) {
    const nextSkills = [item, ...skills.filter((skill) => skill.id !== item.id)];
    commitSnapshot({ categories, workflows, skills: nextSkills });
  }

  async function load() {
    const showLoading = !cachedSnapshot(token);
    if (showLoading) setBusy('load');
    setError('');
    try {
      const result = await fetchSnapshot(token);
      commitSnapshot(result);
      const selected = result.workflows.find((item) => item.id === draft.id)
        || result.workflows[0];
      if (selected) setDraft(workflowDraft(selected));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '能力清单读取失败');
    } finally {
      if (showLoading) setBusy('');
    }
  }

  useEffect(() => { void load(); }, [token]);

  async function createCategory() {
    if (!categoryName.trim()) return;
    setBusy('category');
    setError('');
    try {
      const result = await capabilityApi.createCategory(token, categoryName.trim());
      commitSnapshot({ categories: [...categories, result.category], workflows, skills });
      setCategoryName('');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '分类创建失败');
    } finally {
      setBusy('');
    }
  }

  async function compileConversation() {
    setBusy('compile');
    setError('');
    try {
      const result = await capabilityApi.fromConversation(token, conversationId, draft.categoryId || null);
      commitWorkflow(result.workflow);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '工作流生成失败');
    } finally {
      setBusy('');
    }
  }

  async function saveWorkflow() {
    const payload = {
      name: draft.name.trim(),
      description: draft.description.trim(),
      instructions: draft.instructions.trim(),
      triggers: draft.triggers.split(/[，,\n]/).map((item) => item.trim()).filter(Boolean),
      category_id: draft.categoryId || null,
    };
    setBusy('save');
    setError('');
    try {
      const result = draft.id
        ? await capabilityApi.updateWorkflow(token, draft.id, payload)
        : await capabilityApi.createWorkflow(token, payload);
      commitWorkflow(result.workflow);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '工作流保存失败');
    } finally {
      setBusy('');
    }
  }

  async function removeWorkflow() {
    if (!draft.id || !window.confirm(`删除工作流“${draft.name}”？`)) return;
    setBusy('delete');
    try {
      await capabilityApi.deleteWorkflow(token, draft.id);
      const nextWorkflows = workflows.filter((item) => item.id !== draft.id);
      commitSnapshot({ categories, workflows: nextWorkflows, skills });
      setDraft(nextWorkflows[0] ? workflowDraft(nextWorkflows[0]) : emptyDraft);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '工作流删除失败');
    } finally {
      setBusy('');
    }
  }

  async function share(kind: 'workflow' | 'skill', itemId: string) {
    setBusy(`share:${itemId}`);
    setError('');
    try {
      const result = await capabilityApi.share(token, kind, itemId);
      setShareCode(result.code);
      await navigator.clipboard?.writeText(result.code).catch(() => undefined);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '分享失败');
    } finally {
      setBusy('');
    }
  }

  async function importCapability() {
    if (!importCode.trim()) return;
    setBusy('import');
    setError('');
    try {
      const result = await capabilityApi.import(token, importCode.trim());
      setTab(result.kind === 'workflow' ? 'workflows' : 'skills');
      setImportCode('');
      if (result.kind === 'workflow') commitWorkflow(result.item as Workflow);
      else commitSkill(result.item as SkillRecord);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '导入失败');
    } finally {
      setBusy('');
    }
  }

  async function searchSkills() {
    if (!skillQuery.trim()) return;
    setBusy('search');
    setError('');
    try {
      const result = await capabilityApi.searchSkills(token, skillQuery.trim(), skillSource.trim());
      setConsoleOutput(result.output || '没有搜索结果');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Skill 搜索失败');
    } finally {
      setBusy('');
    }
  }

  async function installSkill() {
    if (!installSource.trim()) return;
    setBusy('install');
    setError('');
    try {
      const result = await capabilityApi.installSkill(token, installSource.trim());
      setConsoleOutput(JSON.stringify(result.validation, null, 2));
      setInstallSource('');
      commitSkill(result.skill);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Skill 安装失败');
    } finally {
      setBusy('');
    }
  }

  async function auditSkills() {
    setBusy('audit');
    setError('');
    try {
      const result = await capabilityApi.auditSkills(token);
      setConsoleOutput(result.output || (result.ok ? '审计通过' : '审计失败'));
      commitSnapshot({ categories, workflows, skills: result.skills });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Skill 审计失败');
    } finally {
      setBusy('');
    }
  }

  async function removeSkill(item: SkillRecord) {
    if (!window.confirm(`删除 Skill“${item.name}”？`)) return;
    setBusy(`delete:${item.id}`);
    setError('');
    try {
      await capabilityApi.deleteSkill(token, item.id);
      commitSnapshot({ categories, workflows, skills: skills.filter((skill) => skill.id !== item.id) });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Skill 删除失败');
    } finally {
      setBusy('');
    }
  }

  return (
    <div className="modalBackdrop capabilityBackdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="modal capabilityModal" role="dialog" aria-modal="true" aria-label="能力中心">
        <header>
          <div className="capabilityTitle"><WorkflowIcon size={19} /><div><h2>能力中心</h2><p>{workflows.length} 个工作流 · {skills.length} 个 Skill</p></div></div>
          <div className="capabilityImport"><input value={importCode} onChange={(event) => setImportCode(event.target.value)} placeholder="分享码" /><button className="secondaryButton" disabled={Boolean(busy) || !importCode.trim()} onClick={() => void importCapability()}>{busy === 'import' ? <Loader2 className="spin" size={15} /> : <Download size={15} />}导入</button></div>
          <button className="iconButton" title="关闭" onClick={onClose}><X size={18} /></button>
        </header>
        <nav className="capabilityTabs" aria-label="能力类型"><button className={tab === 'workflows' ? 'active' : ''} onClick={() => setTab('workflows')}><WorkflowIcon size={15} />工作流</button><button className={tab === 'skills' ? 'active' : ''} onClick={() => setTab('skills')}><PackageSearch size={15} />Skill</button></nav>
        {error && <p className="capabilityError" role="alert">{error}</p>}
        {shareCode && <div className="shareCodeBar"><Check size={15} /><strong>{shareCode}</strong><button className="iconButton" title="复制分享码" onClick={() => void navigator.clipboard?.writeText(shareCode)}><Clipboard size={15} /></button><button className="iconButton" title="关闭分享码" onClick={() => setShareCode('')}><X size={14} /></button></div>}
        {tab === 'workflows' ? (
          <div className="capabilityWorkflowBody">
            <aside className="workflowIndex">
              <div className="categoryCreator"><input value={categoryName} onChange={(event) => setCategoryName(event.target.value)} placeholder="新分类" onKeyDown={(event) => { if (event.key === 'Enter') void createCategory(); }} /><button className="iconButton" title="创建分类" disabled={busy === 'category' || !categoryName.trim()} onClick={() => void createCategory()}>{busy === 'category' ? <Loader2 className="spin" size={15} /> : <FolderPlus size={15} />}</button></div>
              <div className="workflowIndexActions"><button className="secondaryButton" onClick={() => setDraft(emptyDraft)}><Plus size={15} />新建</button><button className="primaryButton" disabled={Boolean(busy) || !conversationId} onClick={() => void compileConversation()}>{busy === 'compile' ? <Loader2 className="spin" size={15} /> : <WorkflowIcon size={15} />}从当前对话生成</button></div>
              <div className="workflowList">{workflows.map((item) => <button className={draft.id === item.id ? 'active' : ''} key={item.id} onClick={() => setDraft(workflowDraft(item))}><span><strong>{item.name}</strong><small>{item.categoryName || '未分类'} · {item.status}</small></span><FileCheck2 size={15} /></button>)}</div>
            </aside>
            <form className="workflowEditor" onSubmit={(event) => { event.preventDefault(); void saveWorkflow(); }}>
              <div className="workflowEditorGrid"><label><span>名称</span><input required maxLength={80} value={draft.name} onChange={(event) => setDraft((value) => ({ ...value, name: event.target.value }))} /></label><label><span>分类</span><select value={draft.categoryId} onChange={(event) => setDraft((value) => ({ ...value, categoryId: event.target.value }))}><option value="">未分类</option>{categories.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label></div>
              <label><span>描述</span><textarea className="workflowDescription" value={draft.description} onChange={(event) => setDraft((value) => ({ ...value, description: event.target.value }))} /></label>
              <label><span>触发词</span><input value={draft.triggers} onChange={(event) => setDraft((value) => ({ ...value, triggers: event.target.value }))} /></label>
              <label className="workflowInstructions"><span>执行指令</span><textarea required minLength={20} value={draft.instructions} onChange={(event) => setDraft((value) => ({ ...value, instructions: event.target.value }))} /></label>
              <footer><span>{selectedWorkflow ? `已验证 · ${new Date(selectedWorkflow.updatedAt).toLocaleString('zh-CN')}` : '新工作流'}</span>{draft.id && <button type="button" className="iconButton danger" title="删除工作流" onClick={() => void removeWorkflow()}><Trash2 size={16} /></button>}{draft.id && <button type="button" className="secondaryButton" disabled={Boolean(busy)} onClick={() => void share('workflow', draft.id)}><Share2 size={15} />分享</button>}<button className="primaryButton" disabled={Boolean(busy)}>{busy === 'save' ? <Loader2 className="spin" size={15} /> : <Save size={15} />}保存</button></footer>
            </form>
          </div>
        ) : (
          <div className="capabilitySkillBody">
            <div className="skillCommands">
              <form onSubmit={(event) => { event.preventDefault(); void searchSkills(); }}><Search size={16} /><input value={skillQuery} onChange={(event) => setSkillQuery(event.target.value)} placeholder="搜索在线 Skill" /><input className="skillSourceInput" value={skillSource} onChange={(event) => setSkillSource(event.target.value)} placeholder="来源（可选）" /><button className="secondaryButton" disabled={Boolean(busy) || !skillQuery.trim()}>{busy === 'search' ? <Loader2 className="spin" size={15} /> : <Search size={15} />}搜索</button></form>
              <form onSubmit={(event) => { event.preventDefault(); void installSkill(); }}><Download size={16} /><input value={installSource} onChange={(event) => setInstallSource(event.target.value)} placeholder="Skill 来源、URL 或 GitHub 仓库" /><button className="primaryButton" disabled={Boolean(busy) || !installSource.trim()}>{busy === 'install' ? <Loader2 className="spin" size={15} /> : <Download size={15} />}安装并验证</button><button type="button" className="secondaryButton" disabled={Boolean(busy)} onClick={() => void auditSkills()}>{busy === 'audit' ? <Loader2 className="spin" size={15} /> : <ShieldCheck size={15} />}审计</button></form>
            </div>
            {consoleOutput && <pre className="skillConsole">{consoleOutput}</pre>}
            <div className="skillList">{skills.map((item) => <div key={item.id}><PackageSearch size={17} /><span><strong>{item.name}</strong><small>{item.source} · {item.status}</small><p>{item.description}</p></span><button className="iconButton" title="分享 Skill" disabled={Boolean(busy)} onClick={() => void share('skill', item.id)}><Share2 size={15} /></button><button className="iconButton danger" title="删除 Skill" disabled={item.source === 'builtin' || Boolean(busy)} onClick={() => void removeSkill(item)}><Trash2 size={15} /></button></div>)}</div>
          </div>
        )}
        {busy === 'load' && <div className="capabilityLoading"><Loader2 className="spin" size={22} /></div>}
      </section>
    </div>
  );
}
