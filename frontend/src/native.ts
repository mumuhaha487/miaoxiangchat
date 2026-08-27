import type { AppNotification, Attachment } from './types';

export interface NativeSharedFile {
  id: string;
  filename: string;
  mimeType: string;
  sizeBytes: number;
}

export interface NativeSharedUploadResult {
  conversationId: string;
  attachments: Attachment[];
}

interface VMSSAndroidBridge {
  setAuthToken(token: string): void;
  clearAuthToken(): void;
  initializeNotificationCursor(notificationId: number): void;
  showNotification(id: number, category: string, title: string, body: string): void;
  getDeviceId(): string;
  getDeviceName(): string;
  getTrustedDeviceToken(): string;
  setTrustedDeviceToken(token: string): void;
  clearTrustedDeviceToken(): void;
  checkForUpdates(): void;
  getAppVersion(): string;
  downloadAuthenticatedFile(url: string, filename: string): void;
  shareAuthenticatedFile(url: string, filename: string, mimeType: string): void;
  getPendingSharedFiles(): string;
  uploadPendingSharedFiles(conversationId: string): void;
  discardPendingSharedFiles(): void;
}

interface VMSSWindowsBridge {
  set_auth_token(token: string): Promise<{ ok: boolean; deviceName?: string }>;
  clear_auth_token(): Promise<{ ok: boolean }>;
  get_app_version(): Promise<string>;
  share_authenticated_file(url: string, filename: string, mimeType: string): Promise<{ ok: boolean; path?: string }>;
}

declare global {
  interface Window {
    VMSSAndroid?: VMSSAndroidBridge;
    pywebview?: { api?: VMSSWindowsBridge };
    wx?: { miniProgram?: {
      reLaunch(options: { url: string }): void;
      navigateTo(options: { url: string }): void;
    } };
  }
}

let pendingWindowsAuthToken = '';
let windowsBridgeListenerInstalled = false;
let wechatJssdkLoad: Promise<void> | null = null;
const WECHAT_JSSDK_URL = 'https://res.wx.qq.com/open/js/jweixin-1.3.2.js';

export function isWindowsDesktopApp() {
  try {
    return new URLSearchParams(window.location.search).get('client') === 'windows-desktop';
  } catch {
    return false;
  }
}

export function syncNativeAuth(token: string) {
  if (window.VMSSAndroid) {
    if (token) window.VMSSAndroid.setAuthToken(token);
    else window.VMSSAndroid.clearAuthToken();
    return;
  }
  if (!isWindowsDesktopApp()) return;
  pendingWindowsAuthToken = token;
  const sync = () => {
    const api = window.pywebview?.api;
    if (!api) return;
    const operation = pendingWindowsAuthToken
      ? api.set_auth_token(pendingWindowsAuthToken)
      : api.clear_auth_token();
    void operation.catch(() => undefined);
  };
  if (window.pywebview?.api) sync();
  else if (!windowsBridgeListenerInstalled) {
    windowsBridgeListenerInstalled = true;
    window.addEventListener('pywebviewready', sync, { once: true });
  }
}

const WEB_DEVICE_KEY = 'mumu-device-id-v1';
const WEB_TRUST_KEY = 'mumu-trusted-device-v1';

function storageValue(key: string) {
  try { return localStorage.getItem(key) || ''; } catch { return ''; }
}

function setStorageValue(key: string, value: string) {
  try {
    if (value) localStorage.setItem(key, value);
    else localStorage.removeItem(key);
  } catch {
    // Private browsing may deny persistent storage; login still works with email verification.
  }
}

export function isNativeApp() {
  return Boolean(window.VMSSAndroid) || isWindowsDesktopApp();
}

export function isWechatMiniProgramWebView() {
  try {
    return new URLSearchParams(window.location.search).get('client') === 'wechat-mini-program'
      || /miniProgram/i.test(navigator.userAgent);
  } catch {
    return false;
  }
}

export function returnToWechatLogin() {
  if (!isWechatMiniProgramWebView()) return;
  const reLaunch = () => window.wx?.miniProgram?.reLaunch({ url: '/pages/auth/index' });
  if (window.wx?.miniProgram) {
    reLaunch();
    return;
  }
  if (!wechatJssdkLoad) {
    wechatJssdkLoad = new Promise((resolve) => {
      const script = document.createElement('script');
      script.src = WECHAT_JSSDK_URL;
      script.async = true;
      script.addEventListener('load', () => resolve(), { once: true });
      script.addEventListener('error', () => resolve(), { once: true });
      document.head.appendChild(script);
    });
  }
  void wechatJssdkLoad.then(reLaunch);
}

const configuredPublicOrigin = String(import.meta.env.VITE_PUBLIC_APP_ORIGIN || '').trim().replace(/\/$/, '');
export const PUBLIC_APP_ORIGIN = configuredPublicOrigin || window.location.origin;

export function publicAppUrl(path: string) {
  if (!isNativeApp()) return path;
  return new URL(path, PUBLIC_APP_ORIGIN).toString();
}

export function publicWebSocketUrl(path: string) {
  if (!isNativeApp()) {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${protocol}//${window.location.host}${path}`;
  }
  const origin = new URL(PUBLIC_APP_ORIGIN);
  const protocol = origin.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${protocol}//${origin.host}${path}`;
}

export function checkForNativeUpdate() {
  window.VMSSAndroid?.checkForUpdates();
}

export function nativeAppVersion() {
  if (window.VMSSAndroid) return window.VMSSAndroid.getAppVersion();
  if (isWindowsDesktopApp()) {
    try { return new URLSearchParams(window.location.search).get('desktopVersion') || ''; } catch { return ''; }
  }
  return '';
}

export function isVersionOutdated(currentVersion: string, latestVersion: string) {
  const latest = latestVersion.trim().replace(/^v/i, '').split(/[+-]/, 1)[0].split('.');
  if (!latestVersion.trim() || latest.some((item) => !/^\d+$/.test(item))) return false;
  const current = currentVersion.trim().replace(/^v/i, '').split(/[+-]/, 1)[0].split('.');
  if (!currentVersion.trim() || current.some((item) => !/^\d+$/.test(item))) return true;
  const width = Math.max(current.length, latest.length);
  for (let index = 0; index < width; index += 1) {
    const currentPart = Number(current[index] || 0);
    const latestPart = Number(latest[index] || 0);
    if (currentPart !== latestPart) return currentPart < latestPart;
  }
  return false;
}

export function nativeAuthenticatedDownload(url: string, filename: string) {
  if (!window.VMSSAndroid) return false;
  window.VMSSAndroid.downloadAuthenticatedFile(publicAppUrl(url), filename);
  return true;
}

export async function nativeAuthenticatedShare(url: string, filename: string, mimeType: string) {
  const absoluteUrl = publicAppUrl(url);
  if (window.VMSSAndroid) {
    window.VMSSAndroid.shareAuthenticatedFile(absoluteUrl, filename, mimeType);
    return true;
  }
  if (isWindowsDesktopApp() && window.pywebview?.api) {
    await window.pywebview.api.share_authenticated_file(absoluteUrl, filename, mimeType);
    return true;
  }
  if (!isWechatMiniProgramWebView()) return false;
  const navigate = () => {
    const parsed = new URL(absoluteUrl, PUBLIC_APP_ORIGIN);
    const conversationId = parsed.searchParams.get('conversation_id') || '';
    const path = parsed.searchParams.get('path') || '';
    window.wx?.miniProgram?.navigateTo({
      url: `/pages/file/index?conversationId=${encodeURIComponent(conversationId)}&path=${encodeURIComponent(path)}&filename=${encodeURIComponent(filename)}&mimeType=${encodeURIComponent(mimeType)}`,
    });
  };
  if (window.wx?.miniProgram) {
    navigate();
    return true;
  }
  if (!wechatJssdkLoad) {
    wechatJssdkLoad = new Promise((resolve) => {
      const script = document.createElement('script');
      script.src = WECHAT_JSSDK_URL;
      script.async = true;
      script.addEventListener('load', () => resolve(), { once: true });
      script.addEventListener('error', () => resolve(), { once: true });
      document.head.appendChild(script);
    });
  }
  await wechatJssdkLoad;
  if (!window.wx?.miniProgram) return false;
  navigate();
  return true;
}

export function pendingNativeSharedFiles(): NativeSharedFile[] {
  if (!window.VMSSAndroid) return [];
  try {
    const value = JSON.parse(window.VMSSAndroid.getPendingSharedFiles());
    return Array.isArray(value) ? value : [];
  } catch {
    return [];
  }
}

export function uploadNativeSharedFiles(conversationId: string) {
  window.VMSSAndroid?.uploadPendingSharedFiles(conversationId);
}

export function discardNativeSharedFiles() {
  window.VMSSAndroid?.discardPendingSharedFiles();
}

export function authDeviceContext() {
  if (window.VMSSAndroid) {
    return {
      device_id: window.VMSSAndroid.getDeviceId(),
      device_name: window.VMSSAndroid.getDeviceName(),
      client_platform: 'android' as const,
      trust_token: window.VMSSAndroid.getTrustedDeviceToken(),
    };
  }
  const windowsDesktop = isWindowsDesktopApp();
  let deviceId = storageValue(WEB_DEVICE_KEY);
  if (!deviceId) {
    deviceId = typeof crypto !== 'undefined' && crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
    setStorageValue(WEB_DEVICE_KEY, deviceId);
  }
  return {
    device_id: deviceId,
    device_name: windowsDesktop ? '妙想之地 Windows 桌面端' : ((navigator as Navigator & { userAgentData?: { platform?: string } }).userAgentData?.platform || navigator.platform || '网页浏览器'),
    client_platform: windowsDesktop ? 'windows' as const : 'web' as const,
    trust_token: storageValue(WEB_TRUST_KEY),
  };
}

export function persistTrustedDeviceToken(token: string) {
  if (window.VMSSAndroid) {
    if (token) window.VMSSAndroid.setTrustedDeviceToken(token);
    else window.VMSSAndroid.clearTrustedDeviceToken();
    return;
  }
  setStorageValue(WEB_TRUST_KEY, token);
}

export function showNativeNotification(notification: AppNotification) {
  window.VMSSAndroid?.showNotification(
    notification.id,
    notification.category,
    notification.title,
    notification.body,
  );
}

export function initializeNativeNotificationCursor(notificationId: number) {
  window.VMSSAndroid?.initializeNotificationCursor(notificationId);
}
