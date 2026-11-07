from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from shapely.geometry import box

from app.render.terrain import (
    contour_interval,
    lerp_hex,
    marching_squares,
    render_terrain_paths,
    sample_elev,
)


def test_contour_interval_steps():
    assert contour_interval(10) == 10.0
    assert contour_interval(90) == 10.0


def test_sample_elev_midpoint():
    lats = [0.0, 1.0]
    lngs = [0.0, 1.0]
    grid = [[0.0, 10.0], [20.0, 30.0]]
    mid = sample_elev(lats, lngs, grid, 0.5, 0.5)
    assert mid is not None
    assert abs(mid - 15.0) < 1e-6


def test_marching_squares_vertical_ramp():
    xs = [0.0, 1.0]
    ys = [0.0, 1.0]
    zz = [[0.0, 10.0], [0.0, 10.0]]
    segs = marching_squares(xs, ys, zz, 5.0)
    assert len(segs) == 1
    (x0, _y0), (x1, _y1) = segs[0]
    assert abs((x0 + x1) / 2 - 0.5) < 0.05


def test_lerp_hex_ends():
    ramp = ["#ffffff", "#000000"]
    assert lerp_hex(ramp, 0) == "#ffffff"
    assert lerp_hex(ramp, 1) == "#000000"


def test_render_terrain_has_contours():
    lats = [37.476, 37.492]
    lngs = [127.024, 127.040]
    grid = [[20.0, 22.0], [80.0, 78.0]]
    terrain = {"lats": lats, "lngs": lngs, "grid": grid}
    clip = box(127.028, 37.480, 127.036, 37.488)
    paths, relief = render_terrain_paths(
        terrain, clip, 37.484, 127.032, 0.5, building_rows=[]
    )
    assert relief > 3
    cats = {p["cat"] for p in paths}
    assert "hyps" in cats
    assert "contour" in cats
    assert any(p["stroke"] != "none" for p in paths)
