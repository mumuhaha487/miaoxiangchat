import { useEffect, useRef, useState } from 'react';
import { Terminal as XTerm } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import { X } from 'lucide-react';
import '@xterm/xterm/css/xterm.css';
import { terminalApi } from '../api';
import { publicWebSocketUrl } from '../native';

interface Props {
  token: string;
  onClose: () => void;
}

export function TerminalPanel({ token, onClose }: Props) {
  const hostRef = useRef<HTMLDivElement>(null);
  const [status, setStatus] = useState('正在连接');

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const terminal = new XTerm({
      cursorBlink: true,
      cursorStyle: 'block',
      convertEol: true,
      fontFamily: 'Consolas, "Cascadia Mono", "Microsoft YaHei UI", monospace',
      fontSize: 13,
      lineHeight: 1.18,
      scrollback: 5_000,
      theme: {
        background: '#181818', foreground: '#cccccc', cursor: '#f0f0f0', selectionBackground: '#264f78',
        black: '#181818', red: '#f14c4c', green: '#23d18b', yellow: '#f5f543', blue: '#3b8eea',
        magenta: '#d670d6', cyan: '#29b8db', white: '#e5e5e5', brightBlack: '#666666',
      },
    });
    const fit = new FitAddon();
    terminal.loadAddon(fit);
    terminal.open(host);
    terminal.writeln('\x1b[38;5;45m正在连接当前用户的 /workspace 终端...\x1b[0m');

    let socket: WebSocket | null = null;
    let disposed = false;
    const encoder = new TextEncoder();
    const resize = () => {
      try { fit.fit(); } catch { return; }
      if (socket?.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: 'resize', cols: terminal.cols, rows: terminal.rows }));
      }
    };
    const observer = new ResizeObserver(resize);
    observer.observe(host);
    const input = terminal.onData((data) => {
      if (socket?.readyState === WebSocket.OPEN) socket.send(encoder.encode(data));
    });

    void terminalApi.ticket(token).then(({ ticket }) => {
      if (disposed) return;
      socket = new WebSocket(publicWebSocketUrl(`/api/v1/terminal/ws?ticket=${encodeURIComponent(ticket)}`));
      socket.binaryType = 'arraybuffer';
      socket.onopen = () => {
        setStatus('bash');
        terminal.clear();
        resize();
        terminal.focus();
      };
      socket.onmessage = (event) => {
        if (event.data instanceof ArrayBuffer) terminal.write(new Uint8Array(event.data));
        else terminal.write(String(event.data));
      };
      socket.onerror = () => setStatus('连接错误');
      socket.onclose = () => {
        if (!disposed) {
          setStatus('已断开');
          terminal.writeln('\r\n\x1b[31m终端连接已断开\x1b[0m');
        }
      };
    }).catch((reason) => {
      const message = reason instanceof Error ? reason.message : '终端启动失败';
      setStatus('启动失败');
      terminal.writeln(`\r\n\x1b[31m${message}\x1b[0m`);
    });

    return () => {
      disposed = true;
      observer.disconnect();
      input.dispose();
      socket?.close();
      terminal.dispose();
    };
  }, [token]);

  return <section className="terminalPanel" aria-label="终端"><header><div className="terminalTabs"><button className="active">终端</button></div><span>{status}</span><button className="terminalClose" title="关闭终端" onClick={onClose}><X size={15} /></button></header><div ref={hostRef} className="terminalHost" /></section>;
}
