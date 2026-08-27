import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const repository = path.resolve(root, '..');
const require = createRequire(import.meta.url);

function walk(directory) {
  if (!fs.existsSync(directory)) return [];
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const target = path.join(directory, entry.name);
    if (entry.isDirectory() && !['node_modules', 'miniprogram_npm'].includes(entry.name)) return walk(target);
    return entry.isFile() ? [target] : [];
  });
}

function sha256(file) {
  return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex').toUpperCase();
}

const app = JSON.parse(fs.readFileSync(path.join(root, 'app.json'), 'utf8'));
assert.deepEqual(app.pages, ['pages/auth/index', 'pages/workbench/index', 'pages/file/index']);

const activeFiles = [
  'app.js', 'app.json', 'app.wxss',
  'utils/api.js', 'utils/config.js', 'utils/device.js', 'utils/storage.js',
  ...app.pages.flatMap((page) => ['js', 'json', 'wxml', 'wxss'].map((extension) => `${page}.${extension}`)),
].map((relative) => path.join(root, relative));

for (const file of activeFiles) assert.ok(fs.existsSync(file), `missing ${path.relative(root, file)}`);
for (const file of activeFiles.filter((file) => file.endsWith('.json'))) JSON.parse(fs.readFileSync(file, 'utf8'));
for (const file of activeFiles.filter((file) => file.endsWith('.js'))) {
  execFileSync(process.execPath, ['--check', file], { stdio: 'pipe' });
}

const pageDefinitions = [];
let appDefinition = null;
let loginCalls = 0;
let cloudCalls = 0;
let requestedTicket = false;
let redirectUrl = '';
let downloadedFile = false;
let sharedFile = false;
const storage = new Map();
const appInstance = { globalData: {} };

globalThis.wx = {
  getStorageSync: (key) => storage.get(key) || '',
  setStorageSync: (key, value) => storage.set(key, value),
  removeStorageSync: (key) => storage.delete(key),
  canIUse: () => true,
  getDeviceInfo: () => ({ brand: 'Test', model: 'Phone' }),
  getSystemInfoSync: () => ({ brand: 'Test', model: 'Phone' }),
  getUpdateManager: () => ({ onUpdateReady: () => {}, applyUpdate: () => {} }),
  showModal: () => {},
  login: ({ success }) => { loginCalls += 1; success({ code: 'wx-login-code' }); },
  redirectTo: ({ url, success }) => { redirectUrl = url; success({}); },
  reLaunch: () => {},
  downloadFile: ({ url, header, success }) => {
    assert.match(url, /\/api\/v1\/workspace\/download\?/);
    assert.equal(header.Authorization, 'Bearer wechat-user-token');
    downloadedFile = true;
    success({ statusCode: 200, tempFilePath: 'wxfile://temporary/report.docx' });
  },
  shareFileMessage: ({ filePath, fileName, success }) => {
    assert.equal(filePath, 'wxfile://temporary/report.docx');
    assert.equal(fileName, 'report.docx');
    sharedFile = true;
    success();
  },
  request: ({ url, header, success }) => {
    assert.match(url, /\/api\/v1\/auth\/webview-ticket$/);
    assert.equal(header.Authorization, 'Bearer wechat-user-token');
    requestedTicket = true;
    success({ statusCode: 200, data: { ok: true, data: { ticket: 'one-time-handoff' } } });
  },
  cloud: {
    init: () => {},
    callFunction: ({ name, data, success }) => {
      cloudCalls += 1;
      assert.equal(name, 'wechatLogin');
      assert.equal(data.client_platform, 'wechat');
      success({ result: { ok: true, data: { token: 'wechat-user-token', deviceCredential: 'trusted' } } });
    },
  },
};
globalThis.getApp = () => appInstance;
globalThis.App = (definition) => { appDefinition = definition; appInstance.globalData = definition.globalData; };
globalThis.Page = (definition) => pageDefinitions.push(definition);

require(path.join(root, 'app.js'));
for (const page of app.pages) require(path.join(root, `${page}.js`));
assert.ok(appDefinition);
assert.equal(pageDefinitions.length, 3);

const authPage = pageDefinitions[0];
const authContext = {
  ...authPage,
  data: { ...authPage.data },
  setData(values) { Object.assign(this.data, values); },
};
await authContext.onLoad();
assert.equal(loginCalls, 1, 'launch must automatically call wx.login');
assert.equal(cloudCalls, 1, 'launch must exchange the WeChat identity through the cloud function');
assert.equal(requestedTicket, true, 'authenticated launch must request a one-time web-view ticket');
assert.equal(redirectUrl, '/pages/workbench/index?ticket=one-time-handoff');
assert.equal(storage.get('mumu.wechat.token.v1'), 'wechat-user-token');

const authTemplate = fs.readFileSync(path.join(root, 'pages/auth/index.wxml'), 'utf8');
assert.match(authTemplate, /微信登录/);
assert.match(authTemplate, /bindtap="authenticate"/);
assert.doesNotMatch(authTemplate, /邮箱|密码|注册|验证码|激活码|首页/);

const workbenchSource = fs.readFileSync(path.join(root, 'pages/workbench/index.js'), 'utf8');
const workbenchTemplate = fs.readFileSync(path.join(root, 'pages/workbench/index.wxml'), 'utf8');
assert.match(workbenchSource, /app-shell\/android-3\.8\.1\/index\.html/);
assert.match(workbenchSource, /client=wechat-mini-program/);
assert.match(workbenchSource, /#wechat_redirect/);
assert.match(workbenchTemplate, /<web-view/);

const filePage = pageDefinitions[2];
const fileContext = {
  ...filePage,
  data: { ...filePage.data },
  setData(values) { Object.assign(this.data, values); },
};
fileContext.onLoad({ conversationId: 'conversation-1', path: 'report.docx', filename: 'report.docx' });
assert.equal(downloadedFile, true, 'file share page must download through the authenticated workspace endpoint');
assert.equal(sharedFile, true, 'file share page must call wx.shareFileMessage with the temporary path');

const routedPageFiles = walk(path.join(root, 'pages'));
assert.equal(routedPageFiles.length, 12, 'only the login gate, workbench, and file share pages may contain source files');
const activeSource = activeFiles.map((file) => fs.readFileSync(file, 'utf8')).join('\n');
assert.doesNotMatch(activeSource, /从一个问题开始|输入和输出均支持 Markdown|或使用邮箱/);
assert.doesNotMatch(activeSource, /http:\/\//i);
assert.doesNotMatch(activeSource, /BEGIN (?:RSA )?PRIVATE KEY/);

const shell = path.join(repository, 'frontend', 'public', 'app-shell', 'android-3.8.1');
const androidAssets = path.join(repository, 'android-app', 'app', 'src', 'main', 'assets', 'assets');
const shellIndex = fs.readFileSync(path.join(shell, 'index.html'), 'utf8');
const shellBootstrap = fs.readFileSync(path.join(shell, 'bootstrap.js'), 'utf8');
const mainMatch = shellBootstrap.match(/import\('\.\/assets\/(index-[^']+\.js)'\)/);
const cssMatch = shellIndex.match(/\.\/assets\/(index-[^"']+\.css)/);
assert.ok(mainMatch, 'versioned shell must import its main JavaScript bundle');
assert.ok(cssMatch, 'versioned shell must load its main CSS bundle');
const shellAssets = fs.readdirSync(path.join(shell, 'assets')).sort();
const androidUiAssets = fs.readdirSync(androidAssets).sort();
assert.deepEqual(shellAssets, androidUiAssets, 'Mini Program shell and Android must contain the same UI asset names');
for (const name of shellAssets) {
  assert.equal(
    sha256(path.join(shell, 'assets', name)),
    sha256(path.join(androidAssets, name)),
    `Mini Program and Android asset differ: ${name}`,
  );
}
assert.match(fs.readFileSync(path.join(shell, 'assets', mainMatch[1]), 'utf8'), /能力中心/);
assert.match(shellIndex, /\.\/bootstrap\.js/);
assert.match(shellIndex, new RegExp(`\\.\\/assets\\/${cssMatch[1].replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`));
assert.doesNotMatch(shellIndex, /src="\.\/assets\/index-[^"]+\.js"/);
assert.match(shellBootstrap, /webview-ticket\/exchange/);
assert.match(shellBootstrap, /import\('\.\/assets\/index-[^']+\.js'\)/);
assert.match(shellBootstrap, /Storage\.prototype\.removeItem/);
assert.match(shellBootstrap, /wx\?\.miniProgram\?\.reLaunch/);

const project = JSON.parse(fs.readFileSync(path.join(root, 'project.config.json'), 'utf8'));
assert.match(project.appid, /^wx[a-zA-Z0-9]{16}$/);
assert.equal(project.compileType, 'miniprogram');
const manifest = JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8'));
assert.equal(manifest.version, '2.5.1');
assert.deepEqual(manifest.dependencies, {});

process.stdout.write('Validated automatic WeChat gate, native file sharing, and byte-identical Android 3.8.1 UI assets.\n');
