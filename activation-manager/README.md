# 妙想之地 VIP 激活码管理器

Windows 管理员工具。输入服务器地址和后台密码后，通过 HTTPS 调用服务器管理 API 创建激活码及对应的 VIP 注册链接。

- 不包含管理员密码、AppSecret 或离线签名算法。
- 完整激活码只在创建成功时返回一次；服务器数据库只保存 HMAC 摘要和遮罩预览。
- 注册链接使用服务端签名的激活码标识，可单独复制并持续分享，不暴露完整激活码。
- 后台密码和短期管理令牌只保存在进程内存中，生成结束后立即清空密码输入框。
- 非本机地址强制使用 HTTPS。

构建：

```powershell
.\build.ps1
```

生成文件位于 `..\artifacts\windows\MiaoxiangVipActivationManager-x64.exe`。
