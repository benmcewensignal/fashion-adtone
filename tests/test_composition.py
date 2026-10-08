"""Composition measures v1: each measure on pictures whose answer is known."""
import hashlib

import numpy as np
import pytest
from PIL import Image, ImageFilter

from adtone import composition as C
from adtone import config
from adtone.score import check_frozen


def _img(a):
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


def _plain(rgb=(255, 255, 255), size=(600, 400)):
    return Image.new("RGB", size, rgb)


def _noise(seed=1, size=(400, 600)):
    rng = np.random.default_rng(seed)
    return _img(rng.integers(0, 256, size=(size[0], size[1], 3)))


def test_the_method_is_frozen():
    check_frozen(config.RUBRIC_DIR / "composition-v1.md", config.RUBRIC_DIR / "composition-v1.sha256")


def test_a_plain_picture_is_all_open_space_and_no_clutter():
    m = C.measure(_plain())
    assert m["open_space"] == 1.0 and m["edge_density"] == 0.0 and m["diagonals"] is None
    assert m["symmetry"] is None                       # nothing to mirror
    n = C.measure(_noise())
    assert n["open_space"] < 0.01 and n["edge_density"] > 0.5
    assert n["complexity"] > 5 * m["complexity"]
    assert abs(n["diagonals"] - 0.5) < 0.1             # lines in every direction


def test_colour_and_brightness_give_valdez_and_mehrabians_feelings():
    m = C.measure(_plain((255, 255, 255)))
    assert m["pleasure"] == pytest.approx(69.0) and m["arousal"] == pytest.approx(-31.0) and m["dominance"] == pytest.approx(-76.0)
    r = C.measure(_plain((255, 0, 0)))                 # bright and fully saturated
    assert r["arousal"] == pytest.approx(-31 + 60) and r["pleasure"] == pytest.approx(69 + 22)


def test_a_mirror_image_is_symmetrical_and_noise_is_not():
    rng = np.random.default_rng(3)
    left = rng.integers(0, 256, size=(400, 300, 3)).astype(np.float64)
    left = np.asarray(_img(left).filter(ImageFilter.GaussianBlur(6)), dtype=np.float64)
    sym = np.concatenate([left, left[:, ::-1]], axis=1)
    assert C.measure(_img(sym))["symmetry"] > 0.95
    assert abs(C.measure(_noise(5))["symmetry"]) < 0.2


def test_a_subject_on_a_point_of_thirds_is_found_there():
    a = np.full((600, 900, 3), 235.0)
    cy, cx = 200, 300                                  # the upper left point of thirds
    a[cy - 40:cy + 40, cx - 40:cx + 40] = 20.0
    m = C.measure(_img(a))
    assert abs(m["mass_x"] - 1 / 3) < 0.05 and abs(m["mass_y"] - 1 / 3) < 0.05
    assert m["thirds"] < 0.1 and m["centre_offset"] > 0.35
    assert 0 < m["figure_size"] < 0.2
    b = np.full((600, 900, 3), 235.0)
    b[260:340, 410:490] = 20.0                         # the same subject in the middle
    mid = C.measure(_img(b))
    assert mid["centre_offset"] < 0.05 and mid["thirds"] > 0.45


def test_line_direction():
    y = np.arange(512)[:, None] * np.ones((1, 512))
    flat = 127 + 120 * np.sign(np.sin(y / 6.0))
    horiz = C.measure(_img(np.repeat(flat[:, :, None], 3, axis=2)))
    assert horiz["diagonals"] < 0.05
    xx, yy = np.meshgrid(np.arange(512), np.arange(512))
    d = 127 + 120 * np.sign(np.sin((xx + yy) / 8.0))
    assert C.measure(_img(np.repeat(d[:, :, None], 3, axis=2)))["diagonals"] > 0.9


def test_a_sharp_subject_on_a_soft_ground_has_a_shallow_depth_of_field():
    rng = np.random.default_rng(7)
    ground = np.asarray(_img(rng.integers(0, 256, size=(512, 512, 3))).filter(ImageFilter.GaussianBlur(8)), dtype=np.float64)
    sharp = rng.integers(0, 256, size=(140, 140, 3)).astype(np.float64)
    a = ground.copy()
    a[186:326, 186:326] = sharp
    m = C.measure(_img(a))
    assert m["depth_of_field"] is not None and m["depth_of_field"] > 1.5
    assert m["centre_offset"] < 0.15


def test_measures_are_reported_for_every_key_and_rounded():
    m = C.measure(_noise(9, size=(300, 500)))
    assert set(m) == set(C.KEYS)
    assert all(v is None or v == round(v, 4) for v in m.values())
