"""Tests on synthetic lighter images: a rounded-rectangle body with a cap notch (so it is not symmetric)."""
import math

import cv2
import numpy as np
import pytest

from lightergrab_cv import Recipe, learn, inspect, PlcLink, run_folder

W, H = 640, 480


def lighter(cx, cy, angle, broken=False, size=1.0, back=False):
    img = np.full((H, W, 3), 40, np.uint8)
    # body 160 x 60 with a 40 x 20 cap on one end -> asymmetric outline
    body = np.array([[-80, -30], [80, -30], [80, 30], [-80, 30]], float) * size
    cap = np.array([[80, -15], [110, -15], [110, 15], [80, 15]], float) * size
    # button on one long side only: lying back-side-up mirrors it to the other side
    sgn = 1 if back else -1
    button = np.array([[20, 30 * sgn], [55, 30 * sgn], [55, 44 * sgn], [20, 44 * sgn]], float) * size
    a = math.radians(angle)
    rot = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
    for poly in (body, cap, button):
        pts = (poly @ rot.T + [cx, cy]).astype(np.int32)
        cv2.fillPoly(img, [pts], (210, 210, 210))
    if broken:  # bite a 30 px chunk out of the long edge
        bite = (np.array([[-20, 18], [15, 18], [15, 40], [-20, 40]], float) * size @ rot.T + [cx, cy]).astype(np.int32)
        cv2.fillPoly(img, [bite], (40, 40, 40))
    return img


@pytest.fixture(scope="module")
def recipe():
    # grab point on the body, 40 px left of centre, in the learning image (angle 0, centre 320,240)
    return learn(lighter(320, 240, 0), (280, 240), Recipe())


def grab_expected(cx, cy, angle, recipe):
    # the true grab point: 40 px from body centre toward the non-cap end, rotated
    a = math.radians(angle)
    return cx - 40 * math.cos(a), cy - 40 * math.sin(a)


@pytest.mark.parametrize("angle", [0, 37, 90, 145, -120, 179])
def test_finds_any_angle_and_grab_point(recipe, angle):
    cx, cy = 330, 250
    res = inspect(lighter(cx, cy, angle), recipe)
    assert len(res) == 1 and res[0].ok, res
    d_ang = (res[0].angle - angle + 180) % 360 - 180
    assert abs(d_ang) < 3
    ex, ey = grab_expected(cx, cy, angle, recipe)
    # learned centre is the moment centre (body + cap), so grab is offset consistently; allow a few px
    assert math.hypot(res[0].x - ex, res[0].y - ey) < 15


def test_grab_point_moves_with_pose(recipe):
    a = inspect(lighter(300, 240, 0), recipe)[0]
    b = inspect(lighter(300, 240, 90), recipe)[0]
    # rotating 90 deg about the centre swaps the offset from x to y
    assert abs(a.y - 240) < 5 and abs(b.x - 300) < 8


def test_broken_outline_is_ng(recipe):
    res = inspect(lighter(320, 240, 20, broken=True), recipe)
    assert len(res) == 1 and not res[0].ok
    assert "defect" in res[0].reason or "similarity" in res[0].reason


def test_wrong_size_part_ignored(recipe):
    assert inspect(lighter(320, 240, 0, size=1.6), recipe) == []


def test_empty_belt_finds_nothing(recipe):
    assert inspect(np.full((H, W, 3), 40, np.uint8), recipe) == []


def test_recipe_roundtrip(recipe, tmp_path):
    p = tmp_path / "r.json"
    recipe.save(p)
    r2 = Recipe.load(p)
    assert r2.grab_point == recipe.grab_point and len(r2.contour) == len(recipe.contour)


def test_plc_registers_dry_run(recipe):
    plc = PlcLink(None)
    res = inspect(lighter(330, 250, 37), recipe)[0]
    regs = plc.send(res)
    assert len(regs) == 6 and regs[0] == 1 and regs[4] == 1 and regs[5] == 1
    assert regs[1] == round(res.x_mm * 10)
    neg = plc._s16(-12.3)
    assert neg == (-123) & 0xFFFF
    assert plc.send(None)[0] == 0


def test_run_folder_logs_and_saves_ng(recipe, tmp_path):
    imgs = tmp_path / "imgs"
    imgs.mkdir()
    cv2.imwrite(str(imgs / "a_ok.png"), lighter(320, 240, 10))
    cv2.imwrite(str(imgs / "b_ng.png"), lighter(320, 240, 50, broken=True))
    counts = run_folder(recipe, imgs, tmp_path / "out", PlcLink(None))
    assert counts == {"frames": 2, "ok": 1, "ng": 1, "none": 0}
    assert (tmp_path / "out" / "ng" / "b_ng.png").exists()
    assert len((tmp_path / "out" / "results.csv").read_text().strip().splitlines()) == 3


@pytest.mark.parametrize("angle", [0, 63, -150])
def test_front_and_back_side(recipe, angle):
    f = inspect(lighter(330, 250, angle), recipe)
    b = inspect(lighter(330, 250, angle, back=True), recipe)
    assert len(f) == 1 and f[0].side == "front" and f[0].ok, f
    assert len(b) == 1 and b[0].side == "back" and b[0].ok, b
    d_ang = (b[0].angle - angle + 180) % 360 - 180
    assert abs(d_ang) < 3
    assert PlcLink(None).send(b[0])[4] == 2


def test_calibration_to_mm(recipe):
    import dataclasses
    r = dataclasses.replace(recipe, mm_per_px=0.2, origin_px=(100.0, 50.0))
    res = inspect(lighter(330, 250, 30), r)[0]
    assert abs(res.x_mm - (res.x - 100) * 0.2) < 1e-6 and abs(res.y_mm - (res.y - 50) * 0.2) < 1e-6
