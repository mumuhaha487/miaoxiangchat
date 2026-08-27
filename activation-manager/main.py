from __future__ import annotations

import json
import os
import ssl
import threading
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import messagebox, ttk
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen


APP_TITLE = "妙想之地 VIP 激活码管理器"
DEFAULT_SERVER = os.getenv("MIAOXIANG_SERVER_URL", "https://example.com")


class ApiError(RuntimeError):
    pass


def api_request(server: str, path: str, payload: dict, token: str = "") -> dict:
    origin = server.strip().rstrip("/")
    parsed = urlparse(origin)
    if parsed.scheme != "https" and parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise ApiError("服务器地址必须使用 HTTPS")
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(
        f"{origin}{path}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=20, context=ssl.create_default_context()) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("detail")
        except (ValueError, UnicodeDecodeError):
            detail = None
        raise ApiError(str(detail or f"服务器返回 HTTP {exc.code}")) from exc
    except (URLError, TimeoutError) as exc:
        raise ApiError(f"无法连接服务器：{exc.reason if isinstance(exc, URLError) else exc}") from exc
    except (ValueError, UnicodeDecodeError) as exc:
        raise ApiError("服务器返回了无法识别的数据") from exc
    if not result.get("ok"):
        raise ApiError(str(result.get("detail") or "请求失败"))
    return result.get("data") or {}


class ActivationManager(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("680x560")
        self.minsize(620, 520)
        self.configure(background="#f3f5f6")

        self.server = tk.StringVar(value=DEFAULT_SERVER)
        self.password = tk.StringVar()
        self.note = tk.StringVar()
        self.max_uses = tk.StringVar(value="1")
        self.expiry_hours = tk.StringVar(value="0")
        self.code = tk.StringVar()
        self.registration_link = tk.StringVar()
        self.status = tk.StringVar(value="完整激活码只显示一次，请在生成后立即保存。")
        self._build_ui()

    def _build_ui(self) -> None:
        style = ttk.Style(self)
        style.theme_use("vista")
        style.configure("Title.TLabel", background="#f3f5f6", foreground="#202629", font=("Microsoft YaHei UI", 18, "bold"))
        style.configure("Hint.TLabel", background="#f3f5f6", foreground="#687176", font=("Microsoft YaHei UI", 9))
        style.configure("Form.TLabel", background="#ffffff", foreground="#343b3f", font=("Microsoft YaHei UI", 9))
        style.configure("Primary.TButton", font=("Microsoft YaHei UI", 10, "bold"), padding=(16, 9))

        shell = ttk.Frame(self, padding=(28, 24, 28, 20))
        shell.pack(fill="both", expand=True)
        ttk.Label(shell, text="VIP 激活码", style="Title.TLabel").pack(anchor="w")
        ttk.Label(shell, text="安全连接管理后台生成激活码，工具本地不保存密码、令牌或签名密钥。", style="Hint.TLabel").pack(anchor="w", pady=(4, 18))

        form = tk.Frame(shell, background="#ffffff", highlightthickness=1, highlightbackground="#d8dddf", padx=20, pady=18)
        form.pack(fill="both", expand=True)
        form.columnconfigure(1, weight=1)

        self._field(form, 0, "服务器", self.server)
        self._field(form, 1, "后台密码", self.password, show="●")
        self._field(form, 2, "备注", self.note)
        self._field(form, 3, "最多使用次数", self.max_uses)
        self._field(form, 4, "有效小时数", self.expiry_hours)
        ttk.Label(form, text="填写 0 表示永久有效", style="Form.TLabel").grid(row=5, column=1, sticky="w", pady=(0, 10))

        self.generate_button = ttk.Button(form, text="生成激活码", style="Primary.TButton", command=self.generate)
        self.generate_button.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(4, 16))

        ttk.Label(form, text="生成结果", style="Form.TLabel").grid(row=7, column=0, sticky="w", padx=(0, 14))
        result_row = ttk.Frame(form)
        result_row.grid(row=7, column=1, sticky="ew")
        result_row.columnconfigure(0, weight=1)
        ttk.Entry(result_row, textvariable=self.code, state="readonly", font=("Consolas", 12)).grid(row=0, column=0, sticky="ew")
        ttk.Button(result_row, text="复制", command=self.copy_code).grid(row=0, column=1, padx=(8, 0))
        ttk.Label(form, text="注册链接", style="Form.TLabel").grid(row=8, column=0, sticky="w", padx=(0, 14), pady=(12, 0))
        link_row = ttk.Frame(form)
        link_row.grid(row=8, column=1, sticky="ew", pady=(12, 0))
        link_row.columnconfigure(0, weight=1)
        ttk.Entry(link_row, textvariable=self.registration_link, state="readonly").grid(row=0, column=0, sticky="ew")
        ttk.Button(link_row, text="复制", command=self.copy_registration_link).grid(row=0, column=1, padx=(8, 0))
        ttk.Label(form, textvariable=self.status, style="Form.TLabel", wraplength=480).grid(row=9, column=0, columnspan=2, sticky="w", pady=(12, 0))

    @staticmethod
    def _field(parent: tk.Widget, row: int, label: str, variable: tk.StringVar, show: str = "") -> None:
        ttk.Label(parent, text=label, style="Form.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 14), pady=7)
        ttk.Entry(parent, textvariable=variable, show=show).grid(row=row, column=1, sticky="ew", pady=7)

    def generate(self) -> None:
        if not self.password.get():
            messagebox.showwarning(APP_TITLE, "请输入后台密码。", parent=self)
            return
        try:
            max_uses = int(self.max_uses.get())
            hours = int(self.expiry_hours.get())
            if not 1 <= max_uses <= 10_000 or not 0 <= hours <= 24 * 365 * 10:
                raise ValueError
        except ValueError:
            messagebox.showwarning(APP_TITLE, "使用次数或有效小时数不正确。", parent=self)
            return
        self.generate_button.configure(state="disabled")
        self.status.set("正在通过 HTTPS 连接后台...")
        self.code.set("")
        self.registration_link.set("")
        threading.Thread(target=self._generate_worker, args=(max_uses, hours), daemon=True).start()

    def _generate_worker(self, max_uses: int, hours: int) -> None:
        try:
            login = api_request(
                self.server.get(),
                "/api/v1/auth/admin-login",
                {"password": self.password.get()},
            )
            expires_at = None
            if hours:
                expires_at = int((datetime.now().astimezone() + timedelta(hours=hours)).timestamp() * 1000)
            created = api_request(
                self.server.get(),
                "/api/v1/admin/activation-codes",
                {"note": self.note.get().strip(), "max_uses": max_uses, "expires_at": expires_at},
                str(login.get("token") or ""),
            )
            activation = created.get("activationCode") or {}
            code = str(activation.get("code") or "")
            registration_path = str(activation.get("registrationPath") or "")
            if not code:
                raise ApiError("后台未返回完整激活码")
            if not registration_path:
                raise ApiError("后台未返回注册链接")
            link = urljoin(self.server.get().strip().rstrip("/") + "/", registration_path)
            self.after(0, self._generated, code, link)
        except Exception as exc:
            self.after(0, self._failed, str(exc))

    def _generated(self, code: str, link: str) -> None:
        self.code.set(code)
        self.registration_link.set(link)
        self.password.set("")
        self.status.set("激活码已生成。后台只保存摘要，完整内容不会再次显示。")
        self.generate_button.configure(state="normal")
        self.copy_code()

    def _failed(self, message: str) -> None:
        self.status.set(message or "生成失败")
        self.generate_button.configure(state="normal")
        messagebox.showerror(APP_TITLE, message or "生成失败", parent=self)

    def copy_code(self) -> None:
        value = self.code.get()
        if not value:
            return
        self.clipboard_clear()
        self.clipboard_append(value)
        self.update_idletasks()
        self.status.set("激活码已复制到剪贴板。")

    def copy_registration_link(self) -> None:
        value = self.registration_link.get()
        if not value:
            return
        self.clipboard_clear()
        self.clipboard_append(value)
        self.update_idletasks()
        self.status.set("注册链接已复制到剪贴板。")


if __name__ == "__main__":
    ActivationManager().mainloop()
