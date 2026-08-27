import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { ArrowLeft, Loader2, LockKeyhole, ShieldCheck } from 'lucide-react';
import { authApi, conversationApi } from './api';
import type { RuntimeInfo, User } from './types';
import { AuthScreen } from './components/AuthScreen';
import { clearGuestArchiveAfterImport, guestArchiveImportPayload, Workspace } from './components/Workspace';
import { isNativeApp, isWechatMiniProgramWebView, returnToWechatLogin, syncNativeAuth } from './native';

const AdminPanel = lazy(() => import('./components/AdminPanel').then((module) => ({ default: module.AdminPanel })));

const USER_TOKEN_KEY = 'mumu-user-token-v1';
const ADMIN_TOKEN_KEY = 'mumu-admin-token-v1';

const fallbackRuntime: RuntimeInfo = {
  appName: '妙想之地',
  registrationEnabled: true,
  model: 'auto',
  workerLimit: 2,
  uploadMaxBytes: 20 * 1024 * 1024,
  apiVersion: 'v1',
  appDownloadUrl: '/downloads/AIchatMUMU-arm64.apk?v=24',
  windowsAgentVersion: '',
  windowsAgentDownloadUrl: '/downloads/MiaoxiangComputerAgent-x64.exe?v=17',
};

function normalizePath() {
  if (isNativeApp()) return '/';
  const value = window.location.pathname.replace(/\/+$/, '');
  return value || '/';
}

export default function App() {
  const [runtime, setRuntime] = useState<RuntimeInfo>(fallbackRuntime);
  const path = normalizePath();

  useEffect(() => {
    void authApi.runtime().then(setRuntime).catch(() => undefined);
  }, []);

  if (path === '/admin') return <AdminDecoy />;
  if (path === '/mmhh') return <AdminPortal />;
  if (path !== '/') return <NotFound />;
  return <ChatPortal runtime={runtime} />;
}

function ChatPortal({ runtime }: { runtime: RuntimeInfo }) {
  const handoffTicket = new URLSearchParams(window.location.search).get('handoff')?.trim() || '';
  const miniProgramWebView = isWechatMiniProgramWebView();
  const [token, setToken] = useState(() => localStorage.getItem(USER_TOKEN_KEY) || '');
  const [user, setUser] = useState<User | null>(null);
  const [checking, setChecking] = useState(Boolean(token || handoffTicket));
  const [authOpen, setAuthOpen] = useState(() => (
    !localStorage.getItem(USER_TOKEN_KEY) && Boolean(new URLSearchParams(window.location.search).get('activation'))
  ));
  const handoffAttempted = useRef(false);

  useEffect(() => {
    if (!handoffTicket || handoffAttempted.current) return;
    handoffAttempted.current = true;
    setChecking(true);
    void authApi.exchangeWebviewTicket(handoffTicket)
      .then((result) => {
        localStorage.setItem(USER_TOKEN_KEY, result.token);
        setUser(result.user);
        setToken(result.token);
        const url = new URL(window.location.href);
        url.searchParams.delete('handoff');
        window.history.replaceState(null, '', `${url.pathname}${url.search}${url.hash}`);
      })
      .catch(() => {
        localStorage.removeItem(USER_TOKEN_KEY);
        setToken('');
        setUser(null);
        setChecking(false);
        returnToWechatLogin();
      });
  }, [handoffTicket]);

  useEffect(() => {
    syncNativeAuth(token);
  }, [token]);

  useEffect(() => {
    let cancelled = false;
    if (!token) {
      setUser(null);
      if (!handoffTicket) setChecking(false);
      return () => { cancelled = true; };
    }
    setChecking(true);
    void authApi.me(token)
      .then(({ user: nextUser }) => {
        if (nextUser.role !== 'user') throw new Error('invalid user session');
        if (!cancelled) setUser(nextUser);
      })
      .catch(() => {
        localStorage.removeItem(USER_TOKEN_KEY);
        if (!cancelled) {
          setToken('');
          setUser(null);
        }
      })
      .finally(() => { if (!cancelled) setChecking(false); });
    return () => { cancelled = true; };
  }, [token]);

  async function authenticated(nextToken: string, nextUser: User) {
    const guestArchive = guestArchiveImportPayload();
    if (guestArchive) {
      try {
        await conversationApi.importGuest(nextToken, guestArchive);
        clearGuestArchiveAfterImport();
      } catch {
        // Keep the local archive and retry after a later login.
      }
    }
    localStorage.setItem(USER_TOKEN_KEY, nextToken);
    setToken(nextToken);
    setUser(nextUser);
    setChecking(false);
    setAuthOpen(false);
  }

  function logout() {
    if (token) void authApi.logout(token).catch(() => undefined);
    localStorage.removeItem(USER_TOKEN_KEY);
    syncNativeAuth('');
    setToken('');
    setUser(null);
    returnToWechatLogin();
  }

  if (miniProgramWebView && (!token || checking || !user)) {
    return <main className="appLoading"><Loader2 className="spin" size={26} /></main>;
  }

  return (
    <>
      <Workspace
        token={token}
        user={user}
        runtime={runtime}
        checkingSession={checking}
        onRequestAuth={() => setAuthOpen(true)}
        onLogout={logout}
      />
      {authOpen && (
        <AuthScreen
          appName={runtime.appName}
          registrationEnabled={runtime.registrationEnabled}
          onAuthenticated={authenticated}
          onClose={() => setAuthOpen(false)}
        />
      )}
    </>
  );
}

function AdminPortal() {
  const [token, setToken] = useState(() => sessionStorage.getItem(ADMIN_TOKEN_KEY) || '');
  const [admin, setAdmin] = useState<User | null>(null);
  const [checking, setChecking] = useState(Boolean(token));
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    if (!token) {
      setChecking(false);
      return () => { cancelled = true; };
    }
    void authApi.me(token)
      .then(({ user }) => {
        if (user.role !== 'admin') throw new Error('invalid admin session');
        if (!cancelled) setAdmin(user);
      })
      .catch(() => {
        sessionStorage.removeItem(ADMIN_TOKEN_KEY);
        if (!cancelled) setToken('');
      })
      .finally(() => { if (!cancelled) setChecking(false); });
    return () => { cancelled = true; };
  }, [token]);

  async function login(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      const result = await authApi.adminLogin(password);
      sessionStorage.setItem(ADMIN_TOKEN_KEY, result.token);
      setToken(result.token);
      setAdmin(result.user);
      setPassword('');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '登录失败');
    } finally {
      setBusy(false);
    }
  }

  function logout() {
    sessionStorage.removeItem(ADMIN_TOKEN_KEY);
    setToken('');
    setAdmin(null);
  }

  if (checking) {
    return <main className="appLoading adminLoading"><Loader2 className="spin" size={26} /><strong>正在验证后台会话</strong></main>;
  }
  if (token && admin) return <Suspense fallback={<main className="appLoading adminLoading"><Loader2 className="spin" size={26} /></main>}><AdminPanel token={token} admin={admin} onLogout={logout} /></Suspense>;
  return (
    <main className="adminLoginShell">
      <section className="adminLoginPanel">
        <img className="adminLoginMark siteLogo" src="/assets/site-logo.jpg" alt="妙想之地" />
        <div><p className="eyebrow">MUMU CONTROL</p><h1>管理控制台</h1><p>受限访问</p></div>
        <form onSubmit={login}>
          <label><span>管理员密码</span><div className="field"><LockKeyhole size={17} /><input type="password" required minLength={8} autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} autoFocus /></div></label>
          {error && <p className="formError" role="alert">{error}</p>}
          <button className="adminPrimary" type="submit" disabled={busy}>{busy ? <Loader2 className="spin" size={17} /> : <ShieldCheck size={17} />}登录</button>
        </form>
      </section>
    </main>
  );
}

function AdminDecoy() {
  return <main className="routeNotice"><strong>管理员后台不在这里</strong><p>不要再爬取了。</p><a href="/"><ArrowLeft size={16} />返回首页</a></main>;
}

function NotFound() {
  return <main className="routeNotice"><strong>页面不存在</strong><a href="/"><ArrowLeft size={16} />返回首页</a></main>;
}
