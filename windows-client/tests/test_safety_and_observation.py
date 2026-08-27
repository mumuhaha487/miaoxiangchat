from __future__ import annotations

import hashlib

import pytest
from PIL import Image

from vmss_agent.observation import Observation, ObservationGuard, normalize_box
from vmss_agent.safety import EmergencyStop, action_hash, assess_action, canonical_action


def test_normalized_boxes_and_observation_guard_are_single_use():
    assert normalize_box((10, 20, 90, 80), 100, 100) == [100, 200, 900, 800]
    observation = Observation("desktop", "windows", Image.new("RGB", (100, 80)))
    guard = ObservationGuard()
    guard.replace(observation)
    assert guard.consume(observation.observation_id) is observation
    with pytest.raises(ValueError, match="失效"):
        guard.consume(observation.observation_id)


def test_action_hash_is_canonical_and_approval_is_bound_to_exact_arguments():
    first = action_hash("click", {"y": 200, "x": 100})
    second = action_hash("click", {"x": 100, "y": 200})
    changed = action_hash("click", {"x": 101, "y": 200})
    assert first == second
    assert first != changed
    assert first == hashlib.sha256(canonical_action("click", {"x": 100, "y": 200}).encode()).hexdigest()
    decision = assess_action("请发送这条消息", "click", {"observationId": "a" * 32, "x": 1, "y": 2})
    assert decision.requires_remote_approval is True
    assert decision.action_hash == action_hash("click", {"observationId": "a" * 32, "x": 1, "y": 2})


def test_emergency_stop_cannot_be_cleared_implicitly_while_paused():
    stop = EmergencyStop()
    stop.stop()
    with pytest.raises(InterruptedError, match="暂停"):
        stop.prepare_task()
    stop.set_paused(False)
    stop.prepare_task()
    stop.check()
