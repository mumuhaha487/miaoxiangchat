import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import net from 'node:net';
import path from 'node:path';
import express from 'express';
import { chromium } from 'playwright-core';
import { WebSocket, WebSocketServer } from 'ws';
import {
  AUTOPLAY_CHROMIUM_ARGS,
  AUTOPLAY_GUARD_VERSION,
  installAutoplayGuard,
} from './autoplay-guard.mjs';

const port = Number(process.env.CONTROLLER_PORT || 3001);
const cdpPublicHost = String(process.env.CDP_PUBLIC_HOST || '').trim();
const profileDir = process.env.PROFILE_DIR || '/profile';
const controllerToken = process.env.BROWSER_CONTROLLER_TOKEN || '';
const statePath = path.join(profileDir, 'mumu-browser-state.json');
const proxyServer = String(process.env.BROWSER_PROXY_SERVER || '').trim();
const conversationPattern = /^[a-zA-Z0-9_.-]{6,64}$/;

const vncBridge = new WebSocketServer({ host: '0.0.0.0', port: 6080 });
vncBridge.on('connection', (websocket) => {
  const vnc = net.createConnection({ host: '127.0.0.1', port: 5900 });
  const closeBoth = () => {
    if (websocket.readyState === WebSocket.OPEN || websocket.readyState === WebSocket.CONNECTING) websocket.close();
    if (!vnc.destroyed) vnc.destroy();
  };
  websocket.on('message', (data) => {
    if (!vnc.destroyed) vnc.write(data);
  });
  websocket.on('close', () => { if (!vnc.destroyed) vnc.destroy(); });
  websocket.on('error', closeBoth);
  vnc.on('data', (data) => {
    if (websocket.readyState === WebSocket.OPEN) websocket.send(data, { binary: true });
  });
  vnc.on('close', () => {
    if (websocket.readyState === WebSocket.OPEN) websocket.close();
  });
  vnc.on('error', closeBoth);
});
vncBridge.on('listening', () => console.log('[browser-runtime] VNC WebSocket bridge listening on 6080'));

if (controllerToken.length < 24) {
  throw new Error('BROWSER_CONTROLLER_TOKEN must contain at least 24 characters');
}

const context = await chromium.launchPersistentContext(profileDir, {
  executablePath: process.env.CHROMIUM_PATH || '/usr/bin/chromium',
  headless: false,
  locale: 'zh-CN',
  timezoneId: 'Asia/Shanghai',
  viewport: null,
  acceptDownloads: true,
  downloadsPath: path.join(profileDir, 'Downloads'),
  args: [
    '--no-sandbox',
    '--disable-dev-shm-usage',
    '--disable-gpu',
    '--no-first-run',
    '--no-default-browser-check',
    '--remote-debugging-address=0.0.0.0',
    '--remote-debugging-port=9222',
    '--remote-allow-origins=*',
    ...AUTOPLAY_CHROMIUM_ARGS,
    '--window-size=1440,900',
    '--window-position=0,0',
    ...(proxyServer ? [
      `--proxy-server=${proxyServer}`,
      '--proxy-bypass-list=<-loopback>;localhost;127.0.0.1;*.internal;mumu-api',
    ] : []),
  ],
});

await installAutoplayGuard(context);

if (!cdpPublicHost) {
  throw new Error('CDP_PUBLIC_HOST is required');
}

const app = express();
app.use(express.json({ limit: '128kb' }));

let activePage = context.pages().find((page) => !page.isClosed()) || null;
let persisted = {};
try {
  persisted = JSON.parse(await fs.readFile(statePath, 'utf8'));
} catch {
  persisted = {};
}
let restorePending = true;

async function saveState() {
  if (activePage && !activePage.isClosed()) {
    persisted = { url: activePage.url(), updatedAt: Date.now() };
  }
  await fs.writeFile(statePath, JSON.stringify(persisted, null, 2), 'utf8');
}

function observePage(page) {
  activePage = page;
  page.on('close', () => { if (activePage === page) activePage = context.pages().findLast((item) => !item.isClosed()) || null; });
  page.on('framenavigated', (frame) => { if (frame === page.mainFrame()) { activePage = page; saveState().catch(() => undefined); } });
}

for (const page of context.pages()) observePage(page);
context.on('page', observePage);

const delay = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

async function pageSnapshot(page) {
  for (let attempt = 0; attempt < 6; attempt += 1) {
    try {
      return { title: await page.title(), url: page.url() };
    } catch (error) {
      if (!String(error?.message || error).includes('Execution context was destroyed')) throw error;
      await delay(350);
    }
  }
  return { title: '', url: page.url() };
}

function validConversationId(value) {
  const conversationId = String(value || '').trim();
  if (!conversationPattern.test(conversationId)) {
    const error = new Error('Invalid conversation id');
    error.status = 400;
    throw error;
  }
  return conversationId;
}

async function ensurePage(conversationIdValue) {
  const conversationId = validConversationId(conversationIdValue);
  let page = activePage;
  if (!page || page.isClosed()) {
    page = await context.newPage();
    activePage = page;
  }
  if (restorePending) {
    restorePending = false;
    const savedUrl = String(persisted.url || 'about:blank');
    if (
      (page.url() === 'about:blank' || page.url() === 'chrome://newtab/')
      && (savedUrl.startsWith('http://') || savedUrl.startsWith('https://'))
    ) {
      await page.goto(savedUrl, { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => undefined);
    }
  }
  return { conversationId, page };
}

function safeEqual(actual, expected) {
  const left = Buffer.from(String(actual || ''));
  const right = Buffer.from(String(expected || ''));
  return left.length === right.length && crypto.timingSafeEqual(left, right);
}

app.use((request, response, next) => {
  if (request.path === '/health' || request.path === '/cdp/json/version') return next();
  if (!safeEqual(request.get('X-Browser-Token'), controllerToken)) {
    return response.status(401).json({ ok: false, error: 'Unauthorized' });
  }
  return next();
});

app.get('/health', (_request, response) => {
  response.json({
    ok: true,
    pages: context.pages().filter((page) => !page.isClosed()).length,
    autoplayBlocked: true,
    autoplayGuardVersion: AUTOPLAY_GUARD_VERSION,
  });
});

app.get('/cdp/json/version', async (_request, response, next) => {
  try {
    const upstream = await fetch('http://127.0.0.1:9222/json/version');
    if (!upstream.ok) throw new Error(`Chromium discovery returned ${upstream.status}`);
    const payload = await upstream.json();
    const websocketUrl = new URL(String(payload.webSocketDebuggerUrl || ''));
    websocketUrl.hostname = cdpPublicHost;
    websocketUrl.port = String(port);
    response.set('Cache-Control', 'no-store');
    response.json({ ...payload, webSocketDebuggerUrl: websocketUrl.toString() });
  } catch (error) {
    next(error);
  }
});

app.post('/pages/ensure', async (request, response, next) => {
  try {
    const { conversationId, page } = await ensurePage(request.body.conversationId);
    response.json({ ok: true, conversationId, ...await pageSnapshot(page) });
  } catch (error) {
    next(error);
  }
});

app.post('/pages/action', async (request, response, next) => {
  try {
    const { page } = await ensurePage(request.body.conversationId);
    const action = String(request.body.action || 'focus');
    if (action === 'reload') await page.reload({ waitUntil: 'domcontentloaded', timeout: 30000 });
    else if (action === 'back') await page.goBack({ waitUntil: 'domcontentloaded', timeout: 30000 });
    else if (action === 'forward') await page.goForward({ waitUntil: 'domcontentloaded', timeout: 30000 });
    else if (action !== 'focus') {
      const error = new Error('Unsupported browser action');
      error.status = 400;
      throw error;
    }
    if (action === 'focus') await page.bringToFront();
    response.json({ ok: true, ...await pageSnapshot(page) });
  } catch (error) {
    next(error);
  }
});

app.post('/pages/close', async (request, response, next) => {
  try {
    validConversationId(request.body.conversationId);
    response.json({ ok: true });
  } catch (error) {
    next(error);
  }
});

app.use((error, _request, response, _next) => {
  const status = Number(error.status || 500);
  console.error(`[browser-runtime] ${error.name || 'Error'}: ${error.message || error}`);
  response.status(status).json({ ok: false, error: status >= 500 ? 'Browser operation failed' : error.message });
});

const server = app.listen(port, '0.0.0.0', () => {
  console.log(`[browser-runtime] controller listening on ${port}`);
});

// Chromium rejects non-local Host headers at its raw CDP port. Terminate the
// user's WebSocket here and reconnect internally so that the worker still
// controls exactly the Chromium instance shown over VNC.
const cdpBridge = new WebSocketServer({ noServer: true });
server.on('upgrade', (request, socket, head) => {
  const pathname = new URL(request.url || '/', 'http://browser.invalid').pathname;
  if (!pathname.startsWith('/devtools/browser/')) {
    socket.destroy();
    return;
  }

  const upstream = new WebSocket(`ws://127.0.0.1:9222${request.url}`);
  upstream.once('open', () => {
    cdpBridge.handleUpgrade(request, socket, head, (downstream) => {
      const closeBoth = () => {
        if (downstream.readyState === WebSocket.OPEN) downstream.close();
        if (upstream.readyState === WebSocket.OPEN) upstream.close();
      };
      downstream.on('message', (data, isBinary) => {
        if (upstream.readyState === WebSocket.OPEN) upstream.send(data, { binary: isBinary });
      });
      upstream.on('message', (data, isBinary) => {
        if (downstream.readyState === WebSocket.OPEN) downstream.send(data, { binary: isBinary });
      });
      downstream.on('close', () => { if (upstream.readyState === WebSocket.OPEN) upstream.close(); });
      upstream.on('close', () => { if (downstream.readyState === WebSocket.OPEN) downstream.close(); });
      downstream.on('error', closeBoth);
      upstream.on('error', closeBoth);
    });
  });
  upstream.once('error', () => socket.destroy());
});

async function shutdown() {
  server.close();
  vncBridge.close();
  cdpBridge.close();
  await saveState().catch(() => undefined);
  await context.close().catch(() => undefined);
  process.exit(0);
}

process.on('SIGTERM', shutdown);
process.on('SIGINT', shutdown);
