from __future__ import annotations

import pytest
from PIL import Image

from vmss_agent.adb_target import ADBError, ADBTarget
from vmss_agent.observation import Observation, ObservationGuard


def fake_target() -> tuple[ADBTarget, list[list[str]]]:
    target = ADBTarget.__new__(ADBTarget)
    target.guard = ObservationGuard()
    target._known_serials = {"127.0.0.1:16384"}
    calls: list[list[str]] = []

    def device(_serial, arguments, **_kwargs):
        calls.append(arguments)
        return ""

    target._device = device
    return target, calls


def observation() -> Observation:
    return Observation("adb:127.0.0.1:16384", "adb", Image.new("RGB", (1080, 1920)))


def test_adb_tap_uses_discovered_serial_and_fixed_arguments():
    target, calls = fake_target()
    current = target.guard.replace(observation())
    result = target.tap(current.observation_id, 500, 250)
    assert result["success"] is True
    assert calls == [["shell", "input", "tap", "540", "480"]]


def test_adb_text_rejects_shell_metacharacters_before_subprocess():
    target, calls = fake_target()
    current = target.guard.replace(observation())
    with pytest.raises(ValueError, match="ASCII"):
        target.type_ascii(current.observation_id, "hello; rm -rf /")
    assert calls == []


def test_adb_rejects_unknown_serial_even_if_well_formed():
    target, _calls = fake_target()
    with pytest.raises(ADBError, match="未授权"):
        target._serial("adb:192.168.0.20:5555")
