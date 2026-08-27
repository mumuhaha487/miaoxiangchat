import { useState } from 'react';
import { ArrowLeft, Eye, EyeOff, KeyRound, Loader2, LockKeyhole, Mail, ShieldCheck, UserRound, X } from 'lucide-react';
import { authApi } from '../api';
import { authDeviceContext, persistTrustedDeviceToken } from '../native';
import type { User } from '../types';

type Mode = 'login' | 'register' | 'reset';

interface Props {
  appName: string;
  registrationEnabled: boolean;
  onAuthenticated: (token: string, user: User) => void | Promise<void>;
  onClose: () => void;
}

export function AuthScreen({ appName, registrationEnabled, onAuthenticated, onClose }: Props) {
  const activationToken = new URLSearchParams(window.location.search).get('activation')?.trim() || '';
  const [mode, setMode] = useState<Mode>(activationToken ? 'register' : 'login');
  const [stage, setStage] = useState<'credentials' | 'code'>('credentials');
  const [identifier, setIdentifier] = useState('');
  const [username, setUsername] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [displayName, setDisplayName] = useState('');
  const [activationCode, setActivationCode] = useState('');
  const [code, setCode] = useState('');
  const [trustDevice, setTrustDevice] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  function switchMode(next: Mode) {
    setMode(next);
    setStage('credentials');
    setCode('');
    setPassword('');
    setTrustDevice(false);
    setError('');
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError('');
    const activation_code = activationCode.trim().toUpperCase();
    const device = authDeviceContext();
    try {
      if (stage === 'credentials') {
        const result = await authApi.requestCode(mode === 'register'
          ? { email, username, password, purpose: 'register', activation_code, activation_token: activationToken, ...device }
          : { identifier, password: mode === 'login' ? password : '', purpose: mode, ...device });
        if (!result.verificationRequired && result.token && result.user) {
          if (result.deviceCredential) persistTrustedDeviceToken(result.deviceCredential);
          await onAuthenticated(result.token, result.user);
          return;
        }
        if (mode === 'reset') setPassword('');
        setStage('code');
      } else {
        const result = await authApi.verify({
          ...(mode === 'register' ? { email, username } : { identifier }),
          password,
          code,
          purpose: mode,
          trust_device: trustDevice,
          ...device,
          ...(mode === 'register' ? { display_name: displayName, activation_code, activation_token: activationToken } : {}),
        });
        persistTrustedDeviceToken(result.deviceCredential || '');
        await onAuthenticated(result.token, result.user);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '请求失败');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="authOverlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="authDialog" role="dialog" aria-modal="true" aria-label="账户登录">
        <button className="iconButton authClose" type="button" onClick={onClose} title="关闭"><X size={18} /></button>
        <header className="authBrand"><img className="siteLogo authLogo" src="/assets/site-logo.jpg" alt="" /><div><strong>{appName}</strong><small>账户中心</small></div></header>
        <div className="authTabs" role="tablist" aria-label="账户操作">
          <button className={mode !== 'register' ? 'active' : ''} onClick={() => switchMode('login')} type="button">登录</button>
          {registrationEnabled && <button className={mode === 'register' ? 'active' : ''} onClick={() => switchMode('register')} type="button">注册</button>}
        </div>
        <form onSubmit={submit} className="authForm">
          <div className="formHeading">
            {stage === 'code' && <button className="iconButton quiet" type="button" onClick={() => setStage('credentials')} title="返回"><ArrowLeft size={18} /></button>}
            <div><h1>{stage === 'code' ? mode === 'reset' ? '重置密码' : '邮箱验证' : mode === 'register' ? '创建账户' : mode === 'reset' ? '找回密码' : '欢迎回来'}</h1><p>{stage === 'code' ? '验证码已发送至绑定邮箱' : mode === 'reset' ? '验证绑定邮箱后设置新密码' : mode === 'register' ? (activationToken || activationCode ? '注册完成后自动开通 VIP' : '新账户默认使用基础 Chat') : '登录后同步账户数据'}</p></div>
          </div>
          {mode === 'register' && stage === 'credentials' && <label><span>显示名称</span><div className="field"><UserRound size={17} /><input required value={displayName} onChange={(event) => setDisplayName(event.target.value)} maxLength={80} autoComplete="name" /></div></label>}
          {mode === 'register' && stage === 'credentials' && <label><span>用户名</span><div className="field"><UserRound size={17} /><input required minLength={3} maxLength={32} pattern="[A-Za-z0-9][A-Za-z0-9_.-]{2,31}" value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" /></div></label>}
          {mode === 'register' && stage === 'credentials' && <label><span>邮箱</span><div className="field"><Mail size={17} /><input type="email" required value={email} onChange={(event) => setEmail(event.target.value)} autoComplete="email" /></div></label>}
          {mode !== 'register' && stage === 'credentials' && <label><span>使用用户名/邮箱</span><div className="field"><UserRound size={17} /><input aria-label="使用用户名/邮箱" required value={identifier} onChange={(event) => setIdentifier(event.target.value)} autoComplete="username" /></div></label>}
          {stage === 'credentials' && mode !== 'reset' && <label><span>密码</span><div className="field"><LockKeyhole size={17} /><input type={showPassword ? 'text' : 'password'} required minLength={8} value={password} onChange={(event) => setPassword(event.target.value)} autoComplete={mode === 'register' ? 'new-password' : 'current-password'} /><button className="fieldAction" type="button" onClick={() => setShowPassword((value) => !value)} title={showPassword ? '隐藏密码' : '显示密码'}>{showPassword ? <EyeOff size={16} /> : <Eye size={16} />}</button></div>{mode === 'login' && <button className="forgotPassword" type="button" onClick={() => switchMode('reset')}>忘记密码</button>}</label>}
          {mode === 'register' && stage === 'credentials' && !activationToken && <label><span>VIP 激活码（选填）</span><div className="field"><KeyRound size={17} /><input value={activationCode} onChange={(event) => setActivationCode(event.target.value.toUpperCase())} placeholder="VIP-XXXX-XXXX-XXXX-XXXX" maxLength={24} autoComplete="off" /></div></label>}
          {stage === 'code' && <label><span>验证码</span><div className="field codeField"><KeyRound size={17} /><input inputMode="numeric" pattern="[0-9]{6}" maxLength={6} required value={code} onChange={(event) => setCode(event.target.value.replace(/\D/g, ''))} autoFocus /></div></label>}
          {stage === 'code' && <label className="trustDeviceChoice"><input type="checkbox" checked={trustDevice} onChange={(event) => setTrustDevice(event.target.checked)} /><span><strong>信任此设备</strong><small>以后在这台设备登录不再发送邮箱验证码</small></span></label>}
          {stage === 'code' && mode === 'reset' && <label><span>新密码</span><div className="field"><LockKeyhole size={17} /><input type={showPassword ? 'text' : 'password'} required minLength={8} value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="new-password" /><button className="fieldAction" type="button" onClick={() => setShowPassword((value) => !value)} title={showPassword ? '隐藏密码' : '显示密码'}>{showPassword ? <EyeOff size={16} /> : <Eye size={16} />}</button></div></label>}
          {error && <p className="formError" role="alert">{error}</p>}
          <button className="primaryButton authSubmit" disabled={busy} type="submit">{busy ? <Loader2 className="spin" size={18} /> : <ShieldCheck size={18} />}{stage === 'code' ? mode === 'reset' ? '重置并登录' : '验证并进入' : mode === 'login' ? '登录' : '发送验证码'}</button>
          {mode === 'reset' && <button className="authBackToLogin" type="button" onClick={() => switchMode('login')}>返回登录</button>}
        </form>
      </section>
    </div>
  );
}
