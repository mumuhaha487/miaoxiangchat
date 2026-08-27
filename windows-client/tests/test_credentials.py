from __future__ import annotations

import base64
import os
import sys
from types import SimpleNamespace

import pytest

from vmss_agent.credentials import protect_credential, unprotect_credential


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI is required")
def test_dpapi_round_trip_on_windows() -> None:
    protected = protect_credential("pairing-secret")

    assert protected.startswith("dpapi:")
    assert unprotect_credential(protected) == "pairing-secret"


@pytest.mark.skipif(os.name != "nt", reason="Windows code path is required")
@pytest.mark.parametrize("tuple_result", [False, True])
def test_dpapi_accepts_both_pywin32_return_shapes(monkeypatch: pytest.MonkeyPatch, tuple_result: bool) -> None:
    encrypted = b"encrypted"
    decrypted = b"pairing-secret"

    fake = SimpleNamespace(
        CryptProtectData=lambda *_args: ("description", encrypted) if tuple_result else encrypted,
        CryptUnprotectData=lambda *_args: ("description", decrypted) if tuple_result else decrypted,
    )
    monkeypatch.setitem(sys.modules, "win32crypt", fake)

    protected = protect_credential(decrypted.decode("utf-8"))

    assert protected == "dpapi:" + base64.b64encode(encrypted).decode("ascii")
    assert unprotect_credential(protected) == decrypted.decode("utf-8")
