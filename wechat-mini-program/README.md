# AIchatMUMU 微信小程序

小程序打开后立即自动执行微信登录，不提供邮箱、注册、重置密码或访客入口。认证成功后使用 120 秒单次票据，在 `web-view` 中加载 Android 3.6.1 APK 内同一份 React、JavaScript、CSS 和图片资源；认证失败时只显示重试入口，首页不会渲染。

登录后的固定入口是 `https://example.com/app-shell/android-3.6.1/index.html`。该目录来自正式发布的 Android 3.6.1 前端成品，不由小程序原生 WXML/WXSS 重写，因此工作台内容与 APP 使用同一实现。

## 本地构建

```bash
npm ci
npm test
```

在微信开发者工具中导入本目录。首次真机联调前，需要在微信公众平台的“开发管理 -> 开发设置 -> 服务器域名”中，把 `https://example.com` 同时配置为 `request`、`uploadFile` 和 `downloadFile` 合法域名，并把同一地址配置为登录后工作台使用的业务域名。

小程序不提供电脑端或手机 APP 的软件下载入口。代码中的下载能力只用于用户主动下载自己工作区内的业务文件。

## CI 上传

上传私钥不属于项目源码。通过环境变量传入外部路径：

```bash
WECHAT_PRIVATE_KEY_PATH=/secure/private.<appid>.key \
WECHAT_APPID=<appid> \
npm run upload -- --version 2.3.0 --desc "自动微信登录与 Android 3.6.1 同版工作台"
```

上传成功后，非敏感结果保存在 `upload-result.json`。私钥内容、SMTP、LLM 和服务器登录凭据均不会进入小程序包。小程序包没有运行时 npm 依赖，也不再包含旧聊天页、Markdown 渲染器或原生工作台副本。

微信登录默认由已有云开发环境中的 `wechatLogin` 云函数完成。部署时把同一个随机桥接密钥分别写入 Ubuntu 的 `WECHAT_CLOUD_BRIDGE_SECRET` 和云函数环境，源码及小程序包中都不保存该值：

```bash
WECHAT_PRIVATE_KEY_PATH=/secure/private.<appid>.key \
WECHAT_CLOUD_BRIDGE_SECRET=<至少32字符随机值> \
npm run upload:cloud-login
```

云函数通过 `getWXContext()` 读取官方 OpenID，再使用 HMAC、120 秒时钟窗口和一次性 nonce 调用固定 HTTPS 后端。上传私钥只用于 CI 鉴权和云函数部署，不充当用户登录凭据。

## 界面边界

登录后所有 Chat、Agent、文件、定时任务和设置界面均由版本化 H5 资源提供。微信客户端会在 `web-view` 外保留平台原生导航区域；官方文档明确说明 `web-view` 自动铺满页面且页面的 `navigationStyle: custom` 对它不生效，因此该原生区域不属于 APP WebView 内容。其下方工作台使用 Android 3.6.1 的原始构建。

更完整的平台约束和已阅读的官方资料见 [docs/OFFICIAL-DOCS.md](docs/OFFICIAL-DOCS.md)。
