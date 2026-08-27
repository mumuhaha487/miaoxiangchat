from __future__ import annotations

import math

from vmss_agent.input_motion import pointer_duration, pointer_path, typing_delay


def test_pointer_path_is_continuous_curved_and_exact():
    start = (20, 40)
    end = (1420, 760)
    points = pointer_path(start, end)

    assert 20 <= len(points) <= 72
    assert points[-1] == end
    assert all(math.dist(left, right) < 100 for left, right in zip([start, *points], points))
    straight_cross_products = {
        (point[0] - start[0]) * (end[1] - start[1]) - (point[1] - start[1]) * (end[0] - start[0])
        for point in points[:-1]
    }
    assert any(value != 0 for value in straight_cross_products)
    assert 0.24 <= pointer_duration(start, end) <= 0.9


def test_typing_cadence_varies_and_pauses_at_punctuation():
    plain = typing_delay("a", 0, 20)
    punctuation = typing_delay("。", 1, 20)
    long_text = typing_delay("a", 0, 2000)

    assert punctuation > plain
    assert 0 < long_text < plain
