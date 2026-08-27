import { useEffect, useMemo, useState } from 'react';
import {
  BrainCircuit, Check, Copy, Edit3, Loader2, LogOut, Plus, Search, Server,
  Settings2, ShieldCheck, Trash2, UserRound, Users, X,
} from 'lucide-react';
import { adminApi } from '../api';
import type { ActivationCode, AdminSettings, RuntimeSummary, User } from '../types';
import { ModelSettingsPanel } from './ModelSettingsPanel';

interface Props { token: string; admin: User; onLogout: () => void; }
interface UserForm { email: string; username: string; display_name: string; password: string; status: 'active' | 'disabled'; access_tier: 'basic' | 'vip'; }
interface ActivationForm { note: string; maxUses: number; expires: string; status: 'active' | 'disabled'; }
type Tab = 'users' | 'activation' | 'models' | 'system';

const emptyUser: UserForm = { email: '', username: '', display_name: '', password: '', status: 'active', access_tier: 'basic' };
const emptyActivation: ActivationForm = { note: '', maxUses: 1, expires: '', status: 'active' };

function dateTimeValue(timestamp: number | null) {
  if (!timestamp) return '';
  const date = new Date(timestamp - new Date(timestamp).getTimezoneOffset() * 60_000);
  return date.toISOString().slice(0, 16);
}

function registrationLink(item: ActivationCode) {
  return new URL(item.registrationPath, window.location.origin).toString();
}

export function AdminPanel({ token, admin, onLogout }: Props) {
  const [tab, setTab] = useState<Tab>('users');
  const [users, setUsers] = useState<User[]>([]);
  const [activationCodes, setActivationCodes] = useState<ActivationCode[]>([]);
  const [settings, setSettings] = useState<AdminSettings | null>(null);
  const [runtimes, setRuntimes] = useState<RuntimeSummary | null>(null);
  const [query, setQuery] = useState('');
  const [userModal, setUserModal] = useState<'create' | 'edit' | null>(null);
  const [activationModal, setActivationModal] = useState<'create' | 'edit' | null>(null);
  const [editingUser, setEditingUser] = useState<User | null>(null);
  const [editingActivation, setEditingActivation] = useState<ActivationCode | null>(null);
  const [userForm, setUserForm] = useState<UserForm>(emptyUser);
  const [activationForm, setActivationForm] = useState<ActivationForm>(emptyActivation);
  const [created, setCreated] = useState<{ code: string; link: string } | null>(null);
  const [copied, setCopied] = useState('');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  async function load() {
    setError('');
    try {
      const [userResult, activationResult, settingsResult, runtimeResult] = await Promise.all([
        adminApi.users(token), adminApi.activationCodes(token), adminApi.settings(token), adminApi.runtimes(token),
      ]);
      setUsers(userResult.users);
      setActivationCodes(activationResult.activationCodes);
      setSettings(settingsResult);
      setRuntimes(runtimeResult);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '无法读取后台数据');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void load(); }, [token]);

  const filteredUsers = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return normalized ? users.filter((user) => `${user.displayName} ${user.username} ${user.email}`.toLowerCase().includes(normalized)) : users;
  }, [query, users]);
  const filteredActivationCodes = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return normalized ? activationCodes.filter((item) => `${item.note} ${item.codePreview}`.toLowerCase().includes(normalized)) : activationCodes;
  }, [activationCodes, query]);

  function openUserCreate() { setUserForm(emptyUser); setEditingUser(null); setError(''); setUserModal('create'); }
  function openUserEdit(user: User) { setEditingUser(user); setUserForm({ email: user.email, username: user.username, display_name: user.displayName, password: '', status: user.status, access_tier: user.accessTier }); setError(''); setUserModal('edit'); }
  function openActivationCreate() { setActivationForm(emptyActivation); setEditingActivation(null); setError(''); setActivationModal('create'); }
  function openActivationEdit(item: ActivationCode) { setEditingActivation(item); setActivationForm({ note: item.note, maxUses: item.maxUses, expires: dateTimeValue(item.expiresAt), status: item.status }); setError(''); setActivationModal('edit'); }

  async function saveUser(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError('');
    try {
      if (userModal === 'create') {
        const result = await adminApi.createUser(token, userForm);
        setUsers((items) => [result.user, ...items]);
      } else if (editingUser) {
        const result = await adminApi.updateUser(token, editingUser.id, { display_name: userForm.display_name, status: userForm.status, access_tier: userForm.access_tier, ...(userForm.password ? { password: userForm.password } : {}) });
        setUsers((items) => items.map((item) => item.id === editingUser.id ? result.user : item));
      }
      setUserModal(null);
    } catch (reason) { setError(reason instanceof Error ? reason.message : '保存失败'); }
    finally { setBusy(false); }
  }

  async function removeUser(user: User) {
    if (!window.confirm(`确认删除 ${user.email}？该用户的会话与持久文件也会删除。`)) return;
    setError('');
    try { await adminApi.deleteUser(token, user.id); setUsers((items) => items.filter((item) => item.id !== user.id)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : '删除失败'); }
  }

  async function saveActivation(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError('');
    const expiresAt = activationForm.expires ? new Date(activationForm.expires).getTime() : null;
    if (expiresAt !== null && (!Number.isFinite(expiresAt) || expiresAt <= Date.now())) {
      setError('过期时间必须晚于当前时间'); setBusy(false); return;
    }
    try {
      if (activationModal === 'create') {
        const result = await adminApi.createActivationCode(token, { note: activationForm.note, max_uses: activationForm.maxUses, ...(expiresAt ? { expires_at: expiresAt } : {}) });
        setActivationCodes((items) => [{ ...result.activationCode, code: '' }, ...items]);
        setCreated({ code: result.activationCode.code, link: registrationLink(result.activationCode) });
      } else if (editingActivation) {
        const result = await adminApi.updateActivationCode(token, editingActivation.id, { note: activationForm.note, max_uses: activationForm.maxUses, status: activationForm.status, expires_at: expiresAt });
        setActivationCodes((items) => items.map((item) => item.id === editingActivation.id ? result.activationCode : item));
      }
      setActivationModal(null);
    } catch (reason) { setError(reason instanceof Error ? reason.message : '保存失败'); }
    finally { setBusy(false); }
  }

  async function toggleActivation(item: ActivationCode) {
    setError('');
    try {
      const result = await adminApi.updateActivationCode(token, item.id, { status: item.status === 'active' ? 'disabled' : 'active' });
      setActivationCodes((items) => items.map((entry) => entry.id === item.id ? result.activationCode : entry));
    } catch (reason) { setError(reason instanceof Error ? reason.message : '更新失败'); }
  }

  async function removeActivation(item: ActivationCode) {
    if (!window.confirm(`确认删除激活码 ${item.codePreview}？`)) return;
    setError('');
    try { await adminApi.deleteActivationCode(token, item.id); setActivationCodes((items) => items.filter((entry) => entry.id !== item.id)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : '删除失败'); }
  }

  async function copy(value: string, key: string) {
    await navigator.clipboard.writeText(value);
    setCopied(key);
    window.setTimeout(() => setCopied(''), 1800);
  }

  return (
    <main className="adminShell">
      <aside className="adminSidebar">
        <div className="adminBrand"><img className="adminBrandMark siteLogo" src="/assets/site-logo.jpg" alt="" /><div><strong>妙想之地</strong><small>管理控制台</small></div></div>
        <nav>
          <button className={tab === 'users' ? 'active' : ''} onClick={() => { setTab('users'); setQuery(''); }}><Users size={17} />用户管理</button>
          <button className={tab === 'activation' ? 'active' : ''} onClick={() => { setTab('activation'); setQuery(''); }}><ShieldCheck size={17} />VIP 激活码</button>
          <button className={tab === 'models' ? 'active' : ''} onClick={() => { setTab('models'); setQuery(''); }}><BrainCircuit size={17} />模型配置</button>
          <button className={tab === 'system' ? 'active' : ''} onClick={() => { setTab('system'); setQuery(''); }}><Settings2 size={17} />系统状态</button>
        </nav>
        <div className="adminAccount"><span className="avatar"><UserRound size={16} /></span><div><strong>{admin.displayName}</strong><small>Administrator</small></div><button className="iconButton" onClick={onLogout} title="退出"><LogOut size={16} /></button></div>
      </aside>

      <section className="adminContent">
        <header className="adminHeader">
          <div><p className="eyebrow">ADMINISTRATION</p><h1>{tab === 'users' ? '用户管理' : tab === 'activation' ? 'VIP 激活码' : tab === 'models' ? '模型配置' : '系统状态'}</h1></div>
          {tab === 'users' && <button className="adminPrimary" onClick={openUserCreate}><Plus size={17} />新增用户</button>}
          {tab === 'activation' && <button className="adminPrimary" onClick={openActivationCreate}><Plus size={17} />生成激活码</button>}
        </header>

        {(tab === 'users' || tab === 'activation') && <div className="adminToolbar"><div className="searchField"><Search size={17} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={tab === 'users' ? '搜索用户或邮箱' : '搜索备注或代码'} /></div>{tab === 'users' ? <div className="userStats"><span>VIP <strong>{users.filter((item) => item.accessTier === 'vip').length}</strong></span><span>Basic <strong>{users.filter((item) => item.accessTier === 'basic').length}</strong></span></div> : <div className="userStats"><span>可用 <strong>{activationCodes.filter((item) => item.status === 'active' && item.useCount < item.maxUses && (!item.expiresAt || item.expiresAt > Date.now())).length}</strong></span><span>总计 <strong>{activationCodes.length}</strong></span></div>}</div>}
        {error && !userModal && !activationModal && <p className="formError adminError" role="alert">{error}</p>}

        {created && tab === 'activation' && <div className="oneTimeCode"><div><strong>激活码已创建，完整激活码仅展示一次</strong><code>{created.code}</code><code>{created.link}</code></div><button className="secondaryButton" onClick={() => void copy(created.code, 'created-code')}>{copied === 'created-code' ? <Check size={16} /> : <Copy size={16} />}复制激活码</button><button className="secondaryButton" onClick={() => void copy(created.link, 'created-link')}>{copied === 'created-link' ? <Check size={16} /> : <Copy size={16} />}复制注册链接</button><button className="iconButton" title="关闭" onClick={() => setCreated(null)}><X size={16} /></button></div>}

        {tab === 'users' && <div className="adminTableWrap"><table className="adminTable"><thead><tr><th>用户</th><th>权限</th><th>状态</th><th>邮箱验证</th><th>最近登录</th><th /></tr></thead><tbody>{filteredUsers.map((user) => <tr key={user.id}><td><div className="userCell"><span className="avatar light"><UserRound size={16} /></span><div><strong>{user.displayName}</strong><small>@{user.username} · {user.email || '微信账户'}</small></div></div></td><td><span className={`statusPill ${user.accessTier === 'vip' ? 'active' : 'disabled'}`}>{user.accessTier === 'vip' ? 'VIP' : 'Basic'}</span></td><td><span className={`statusPill ${user.status}`}>{user.status === 'active' ? '正常' : '已停用'}</span></td><td>{user.emailVerified ? '已验证' : '未绑定'}</td><td>{user.lastLoginAt ? new Date(user.lastLoginAt).toLocaleString('zh-CN') : '从未'}</td><td><div className="rowActions"><button className="iconButton" title="编辑" onClick={() => openUserEdit(user)}><Edit3 size={16} /></button><button className="iconButton danger" title="删除" onClick={() => void removeUser(user)}><Trash2 size={16} /></button></div></td></tr>)}</tbody></table>{loading && <div className="tableEmpty"><Loader2 className="spin" size={20} />正在读取</div>}{!loading && !filteredUsers.length && <div className="tableEmpty">没有匹配的用户</div>}</div>}

        {tab === 'activation' && <div className="adminTableWrap"><table className="adminTable activationTable"><thead><tr><th>激活码</th><th>备注</th><th>使用次数</th><th>有效期</th><th>状态</th><th /></tr></thead><tbody>{filteredActivationCodes.map((item) => { const exhausted = item.useCount >= item.maxUses; const expired = Boolean(item.expiresAt && item.expiresAt <= Date.now()); return <tr key={item.id}><td><code>{item.codePreview}</code></td><td>{item.note || '-'}</td><td>{item.useCount} / {item.maxUses}</td><td>{item.expiresAt ? new Date(item.expiresAt).toLocaleString('zh-CN') : '永久'}</td><td><span className={`statusPill ${item.status === 'active' && !exhausted && !expired ? 'active' : 'disabled'}`}>{expired ? '已过期' : exhausted ? '已用完' : item.status === 'active' ? '可用' : '已停用'}</span></td><td><div className="rowActions"><button className="iconButton" title={copied === item.id ? '已复制' : '复制注册链接'} onClick={() => void copy(registrationLink(item), item.id)}>{copied === item.id ? <Check size={16} /> : <Copy size={16} />}</button><button className="secondaryButton compact" onClick={() => void toggleActivation(item)}>{item.status === 'active' ? '停用' : '启用'}</button><button className="iconButton" title="编辑" onClick={() => openActivationEdit(item)}><Edit3 size={16} /></button><button className="iconButton danger" title="删除" onClick={() => void removeActivation(item)}><Trash2 size={16} /></button></div></td></tr>; })}</tbody></table>{loading && <div className="tableEmpty"><Loader2 className="spin" size={20} />正在读取</div>}{!loading && !filteredActivationCodes.length && <div className="tableEmpty">暂无匹配的激活码</div>}</div>}

        {tab === 'models' && settings?.models && <ModelSettingsPanel token={token} value={settings.models} onSaved={(models) => setSettings((current) => current ? { ...current, models } : current)} />}

        {tab === 'system' && <div className="settingsLayout"><section className="settingsSection"><header><div><h2>注册状态</h2><p>部署级账户注册设置</p></div></header><div className="settingRow"><div><strong>邮箱注册</strong><span>新账户可不填写激活码并注册为 Basic</span></div><span className={`statusPill ${settings?.registrationEnabled ? 'active' : 'disabled'}`}>{settings?.registrationEnabled ? '已开启' : '已关闭'}</span></div></section><section className="settingsSection"><header><div><h2>运行容量</h2><p>动态容器池状态</p></div></header><div className="runtimeMetrics"><div><Server size={18} /><span>Worker</span><strong>{runtimes?.workers.filter((item) => item.status === 'running').length || 0} / {runtimes?.workerLimit || 0}</strong></div><div><Server size={18} /><span>浏览器</span><strong>{runtimes?.browsers.filter((item) => item.status === 'running').length || 0}</strong></div><div><Server size={18} /><span>排队任务</span><strong>{runtimes?.queuedTasks || 0}</strong></div></div></section></div>}
      </section>

      {userModal && <div className="modalBackdrop" onMouseDown={(event) => { if (event.currentTarget === event.target) setUserModal(null); }}><section className="modal" role="dialog" aria-modal="true"><header><div><h2>{userModal === 'create' ? '新增用户' : '编辑用户'}</h2><p>{userModal === 'create' ? '创建已验证的平台账户' : `@${editingUser?.username}`}</p></div><button className="iconButton" onClick={() => setUserModal(null)} title="关闭"><X size={17} /></button></header><form onSubmit={saveUser} className="modalForm"><label><span>显示名称</span><input required value={userForm.display_name} onChange={(event) => setUserForm({ ...userForm, display_name: event.target.value })} /></label>{userModal === 'create' && <label><span>用户名</span><input required minLength={3} maxLength={32} pattern="[A-Za-z0-9][A-Za-z0-9_.-]{2,31}" value={userForm.username} onChange={(event) => setUserForm({ ...userForm, username: event.target.value })} /></label>}{userModal === 'create' && <label><span>邮箱</span><input type="email" required value={userForm.email} onChange={(event) => setUserForm({ ...userForm, email: event.target.value })} /></label>}<label><span>{userModal === 'create' ? '初始密码' : '新密码（留空不变）'}</span><input type="password" required={userModal === 'create'} minLength={8} value={userForm.password} onChange={(event) => setUserForm({ ...userForm, password: event.target.value })} /></label><label><span>权限</span><select value={userForm.access_tier} onChange={(event) => setUserForm({ ...userForm, access_tier: event.target.value as UserForm['access_tier'] })}><option value="basic">Basic · 仅 Chat</option><option value="vip">VIP · Agent 全功能</option></select></label><label><span>状态</span><select value={userForm.status} onChange={(event) => setUserForm({ ...userForm, status: event.target.value as UserForm['status'] })}><option value="active">正常</option><option value="disabled">停用</option></select></label>{error && <p className="formError">{error}</p>}<footer><button type="button" className="secondaryButton" onClick={() => setUserModal(null)}>取消</button><button className="adminPrimary" disabled={busy}>{busy ? <Loader2 className="spin" size={16} /> : null}保存</button></footer></form></section></div>}

      {activationModal && <div className="modalBackdrop" onMouseDown={(event) => { if (event.currentTarget === event.target) setActivationModal(null); }}><section className="modal" role="dialog" aria-modal="true"><header><div><h2>{activationModal === 'create' ? '生成激活码' : '编辑激活码'}</h2><p>{activationModal === 'edit' ? editingActivation?.codePreview : '每个激活码同时生成可分享的注册链接'}</p></div><button className="iconButton" onClick={() => setActivationModal(null)} title="关闭"><X size={17} /></button></header><form onSubmit={saveActivation} className="modalForm"><label><span>备注</span><input value={activationForm.note} maxLength={160} onChange={(event) => setActivationForm({ ...activationForm, note: event.target.value })} /></label><label><span>最多使用次数</span><input type="number" required min={Math.max(1, editingActivation?.useCount || 1)} max={10000} value={activationForm.maxUses} onChange={(event) => setActivationForm({ ...activationForm, maxUses: Number(event.target.value) })} /></label><label><span>过期时间（留空为永久）</span><input type="datetime-local" value={activationForm.expires} onChange={(event) => setActivationForm({ ...activationForm, expires: event.target.value })} /></label>{activationModal === 'edit' && <label><span>状态</span><select value={activationForm.status} onChange={(event) => setActivationForm({ ...activationForm, status: event.target.value as ActivationForm['status'] })}><option value="active">启用</option><option value="disabled">停用</option></select></label>}{error && <p className="formError">{error}</p>}<footer><button type="button" className="secondaryButton" onClick={() => setActivationModal(null)}>取消</button><button className="adminPrimary" disabled={busy}>{busy ? <Loader2 className="spin" size={16} /> : null}保存</button></footer></form></section></div>}
    </main>
  );
}
