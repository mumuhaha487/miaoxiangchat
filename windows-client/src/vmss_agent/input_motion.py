from __future__ import annotations

import math


def pointer_duration(start: tuple[int, int], end: tuple[int, int]) -> float:
    distance = math.dist(start, end)
    return max(0.24, min(0.9, 0.2 + distance / 1450.0))


def pointer_path(start: tuple[int, int], end: tuple[int, int]) -> list[tuple[int, int]]:
    """Return a smooth, slightly curved path whose final point is exact."""
    if start == end:
        return [end]
    distance = math.dist(start, end)
    steps = max(14, min(72, int(distance / 18) + 12))
    dx, dy = end[0] - start[0], end[1] - start[1]
    normal_x, normal_y = -dy / distance, dx / distance
    direction = -1.0 if (start[0] + start[1] + end[0] + end[1]) % 2 else 1.0
    bend = min(78.0, max(8.0, distance * 0.09)) * direction
    control_1 = (start[0] + dx * 0.28 + normal_x * bend, start[1] + dy * 0.28 + normal_y * bend)
    control_2 = (start[0] + dx * 0.72 - normal_x * bend * 0.35, start[1] + dy * 0.72 - normal_y * bend * 0.35)
    points: list[tuple[int, int]] = []
    for index in range(1, steps + 1):
        raw = index / steps
        t = raw * raw * (3.0 - 2.0 * raw)
        inverse = 1.0 - t
        x = (
            inverse**3 * start[0]
            + 3 * inverse**2 * t * control_1[0]
            + 3 * inverse * t**2 * control_2[0]
            + t**3 * end[0]
        )
        y = (
            inverse**3 * start[1]
            + 3 * inverse**2 * t * control_1[1]
            + 3 * inverse * t**2 * control_2[1]
            + t**3 * end[1]
        )
        point = end if index == steps else (round(x), round(y))
        if not points or point != points[-1]:
            points.append(point)
    return points


def typing_delay(character: str, index: int, total: int) -> float:
    scale = 1.0 if total <= 240 else 0.55 if total <= 1000 else 0.28
    cadence = 0.022 + ((ord(character) + index * 17) % 13) / 1000.0
    if character in "\r\n":
        cadence += 0.065
    elif character in ".,!?;:，。！？；：":
        cadence += 0.035
    elif character.isspace():
        cadence += 0.012
    return cadence * scale
