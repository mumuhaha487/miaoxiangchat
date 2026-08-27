from __future__ import annotations

from ctypes import wintypes

from vmss_agent.cursor_overlay import RemoteCursorOverlay


def test_reference_edge_glow_is_soft_and_transparent_toward_the_desktop():
    image = RemoteCursorOverlay.edge_image(64, 1080, "left")

    assert image.getpixel((0, 540))[3] >= 220
    assert 10 <= image.getpixel((32, 540))[3] < image.getpixel((0, 540))[3]
    assert image.getpixel((63, 540))[3] <= 12


def test_reference_banner_is_horizontally_centered_on_the_controlled_monitor():
    second_monitor = wintypes.RECT(1920, 0, 3840, 1080)
    x, y = RemoteCursorOverlay.banner_position(second_monitor)
    image = RemoteCursorOverlay.banner_image()

    assert x + RemoteCursorOverlay.BANNER_WIDTH // 2 == 2880
    assert y == RemoteCursorOverlay.BANNER_TOP
    assert image.getpixel((RemoteCursorOverlay.BANNER_WIDTH // 2, 20))[3] > 240


def test_reference_pointer_has_black_arrow_and_diffuse_blue_halo():
    image = RemoteCursorOverlay.pointer_image(click=False)
    pixels = list(image.get_flattened_data())

    assert any(red < 30 and green < 30 and blue < 30 and alpha > 220 for red, green, blue, alpha in pixels)
    assert any(blue > 200 and green > 100 and 10 < alpha < 150 for red, green, blue, alpha in pixels)
