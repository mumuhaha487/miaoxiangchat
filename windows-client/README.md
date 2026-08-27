# 妙想之地 Windows Computer Agent

这是“妙想之地”的 Windows 桌面端。可见窗口直接加载与 `example.com` 完全相同的
Web 工作台；隐藏 Agent 只建立出站 HTTPS/WSS，账号绑定后使用每台设备独立密钥，
不开放本机监听端口，也不提供独立接收端或配对界面。

## 能力

- Windows 窗口枚举、UI Automation、截图、RapidOCR、点击、输入、按键、滚动和拖动。
- 鼠标沿连续曲线路径移动，黑色蓝晕指针逐点跟随；点击、拖拽、滚轮和 Unicode 键盘输入使用带停顿的原生事件。
- 每轮只执行一个工具；每次动作引用最新 `observationId`，动作执行后必须重新截图，最终截图直接回传到对话。
- 模型规划请求在服务端和客户端分别对瞬时限流、连接故障和 `5xx` 做有限重试，不会因单次网关抖动立即终止任务。
- ADB 设备发现、UIAutomator、截图/OCR、点击、滑动、按键和 ASCII 文本输入。
- 网页/Android 端选择具体电脑或 ADB 子设备，查看任务事件和最新画面。
- Chat、Agent、文件、浏览器、定时任务和电脑控制复用生产网页的 HTML、CSS 和 JavaScript。
- 共享登录页、首次邮箱验证码、同账号自动登记、DPAPI 凭据、远端高风险动作确认和全局急停。
- 远控期间显示四边蓝色渐变光晕、顶部中文提示和黑色蓝晕指针。

## 源码运行

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m vmss_agent.main
```

首次运行显示妙想之地工作台。用户在该共享界面完成登录和邮箱验证后，受限同源桥接会将会话交给隐藏 Agent，本机自动出现在同账号的“电脑控制”设备列表，不需要配对码或第二套登录界面。默认服务地址为 `https://example.com`，默认设备名为 Windows 主机名。

发布包可执行离线自检。它只加载本地运行库和 OCR 模型并写出报告，不连接服务器或执行键鼠动作：

```powershell
.\MiaoxiangComputerAgent-x64.exe --self-test .\self-test.json
```

## 安全边界

- 不支持锁屏桌面或 UAC 安全桌面。
- 默认不提供 PowerShell、任意命令、任意文件写入或强制结束进程工具。
- ADB 仅允许已发现序列号上的固定动作，不接受任意 shell。
- 普通远程任务不弹出本机确认框；高风险动作仍需控制端批准，托盘和 `Ctrl+Alt+Pause` 可急停。
- 设备密钥只以 DPAPI 密文保存在当前 Windows 用户目录。
- 默认以标准用户运行；只有需要控制已提权窗口时才由用户主动以管理员身份启动，UAC 安全桌面始终不可控制。

详见 `../docs/COMPUTER-USE-SECURITY-AUDIT.md`、`../docs/COMPUTER-USE-PROTOCOL.md` 和 `THIRD_PARTY_NOTICES.md`。
