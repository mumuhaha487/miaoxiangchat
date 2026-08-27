from __future__ import annotations

import base64
import ctypes
import os


class CredentialError(RuntimeError):
    pass


def protect_credential(value: str) -> str:
    if os.name != "nt":
        if os.getenv("VMSS_AGENT_ALLOW_TEST_PLAINTEXT") == "1":
            return "test:" + base64.b64encode(value.encode("utf-8")).decode("ascii")
        raise CredentialError("DPAPI 仅在 Windows 上可用")
    try:
        import win32crypt

        protected = win32crypt.CryptProtectData(
            value.encode("utf-8"),
            "MiaoxiangZhiDi Computer Agent",
            None,
            None,
            None,
            0,
        )
        encrypted = protected[1] if isinstance(protected, tuple) else protected
        if not isinstance(encrypted, (bytes, bytearray)):
            raise TypeError("CryptProtectData did not return encrypted bytes")
    except Exception as exc:
        raise CredentialError(f"DPAPI 加密失败: {exc}") from exc
    return "dpapi:" + base64.b64encode(encrypted).decode("ascii")


def unprotect_credential(value: str) -> str:
    raw = str(value or "")
    if raw.startswith("test:") and os.getenv("VMSS_AGENT_ALLOW_TEST_PLAINTEXT") == "1":
        return base64.b64decode(raw[5:]).decode("utf-8")
    if os.name != "nt" or not raw.startswith("dpapi:"):
        raise CredentialError("设备凭据格式无效")
    try:
        import win32crypt

        encrypted = base64.b64decode(raw[6:], validate=True)
        unprotected = win32crypt.CryptUnprotectData(encrypted, None, None, None, 0)
        decrypted = unprotected[1] if isinstance(unprotected, tuple) else unprotected
        if not isinstance(decrypted, (bytes, bytearray)):
            raise TypeError("CryptUnprotectData did not return decrypted bytes")
        return decrypted.decode("utf-8")
    except Exception as exc:
        raise CredentialError(f"DPAPI 解密失败: {exc}") from exc


def is_interactive_desktop() -> bool:
    if os.name != "nt":
        return True
    user32 = ctypes.windll.user32
    desktop = user32.OpenInputDesktop(0, False, 0x0100)
    if not desktop:
        return False
    try:
        return bool(user32.SwitchDesktop(desktop))
    finally:
        user32.CloseDesktop(desktop)
