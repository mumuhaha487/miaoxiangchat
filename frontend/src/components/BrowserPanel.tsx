import { useEffect, useRef, useState } from 'react';
import { ArrowLeft, ArrowRight, Globe2, Loader2, RefreshCw, RotateCw, ShieldCheck, X } from 'lucide-react';
import RFB from '@novnc/novnc/lib/rfb.js';
import { browserApi } from '../api';
import { publicWebSocketUrl } from '../native';
import type { BrowserState } from '../types';

interface Props {
  token: string;
  conversationId: string;
  refreshKey: number;
  onClose: () => void;
}

export function BrowserPanel({ token, conversationId, refreshKey, onClose }: Props) {
  const screenRef = useRef<HTMLDivElement>(null);
  const rfbRef = useRef<RFB | null>(null);
  const [state, setState] = useState<BrowserState | null>(null);
  const [connection, setConnection] = useState<'starting' | 'connected' | 'disconnected' | 'error'>('starting');
  const [error, setError] = useState('');
  const [generation, setGeneration] = useState(0);
  const [actionBusy, setActionBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let rfb: RFB | null = null;
    setConnection('starting');
    setError('');

    async function connect() {
      try {
        const [stateResult, ticketResult] = await Promise.all([
          browserApi.state(token, conversationId),
          browserApi.ticket(token, conversationId),
        ]);
        if (cancelled || !screenRef.current) return;
        setState(stateResult.browser);
        const url = publicWebSocketUrl(`/api/v1/conversations/${conversationId}/browser/vnc?ticket=${encodeURIComponent(ticketResult.ticket)}`);
        rfb = new RFB(screenRef.current, url, { credentials: { password: '' } });
        rfb.scaleViewport = true;
        rfb.clipViewport = false;
        rfb.resizeSession = true;
        rfb.viewOnly = false;
        rfb.background = '#ffffff';
        rfb.addEventListener('connect', () => {
          if (!cancelled) setConnection('connected');
        });
        rfb.addEventListener('disconnect', () => {
          if (!cancelled) setConnection('disconnected');
        });
        rfb.addEventListener('securityfailure', () => {
          if (!cancelled) {
            setError('浏览器安全握手失败');
            setConnection('error');
          }
        });
        rfbRef.current = rfb;
      } catch (reason) {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : '浏览器启动失败');
          setConnection('error');
        }
      }
    }

    void connect();
    return () => {
      cancelled = true;
      if (rfb) rfb.disconnect();
      if (rfbRef.current === rfb) rfbRef.current = null;
      if (screenRef.current) screenRef.current.replaceChildren();
    };
  }, [token, conversationId, generation]);

  useEffect(() => {
    if (!refreshKey) return;
    const timeout = window.setTimeout(() => {
      void browserApi.state(token, conversationId).then((result) => setState(result.browser)).catch(() => undefined);
    }, 500);
    return () => window.clearTimeout(timeout);
  }, [refreshKey, token, conversationId]);

  async function runAction(action: 'back' | 'forward' | 'reload' | 'focus') {
    setActionBusy(true);
    try {
      const result = await browserApi.action(token, conversationId, action);
      setState(result.browser);
      rfbRef.current?.focus();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '浏览器操作失败');
    } finally {
      setActionBusy(false);
    }
  }

  return (
    <section className="browserPanel" aria-label="实时浏览器">
      <header className="browserToolbar">
        <div className="browserActions">
          <button className="iconButton" title="后退" disabled={actionBusy} onClick={() => void runAction('back')}><ArrowLeft size={17} /></button>
          <button className="iconButton" title="前进" disabled={actionBusy} onClick={() => void runAction('forward')}><ArrowRight size={17} /></button>
          <button className="iconButton" title="刷新" disabled={actionBusy} onClick={() => void runAction('reload')}><RotateCw size={17} /></button>
        </div>
        <div className="browserAddress" title={state?.url || ''}>
          <ShieldCheck size={15} />
          <span>{state?.url || '正在连接用户浏览器'}</span>
        </div>
        <span className="searchEngineBadge"><Globe2 size={14} /> Hermes CDP</span>
        <button className="iconButton browserClose" title="关闭浏览器侧栏" onClick={onClose}><X size={17} /></button>
      </header>

      <div className="browserViewport" onClick={() => rfbRef.current?.focus()}>
        <div ref={screenRef} className="rfbScreen" />
        {connection !== 'connected' && (
          <div className="browserOverlay">
            {connection === 'starting' ? (
              <><Loader2 className="spin" size={24} /><strong>正在启动浏览器</strong></>
            ) : (
              <>
                <strong>{error || '浏览器连接已断开'}</strong>
                <button className="secondaryButton" onClick={() => setGeneration((value) => value + 1)}>
                  <RefreshCw size={16} />重新连接
                </button>
              </>
            )}
          </div>
        )}
      </div>
      <footer className="browserStatus">
        <span className={`statusDot ${connection}`} />
        <span>{connection === 'connected' ? '浏览器在线' : connection === 'starting' ? '容器启动中' : '连接中断'}</span>
        <span className="browserTitle">{state?.title || '妙想之地'}</span>
      </footer>
    </section>
  );
}
