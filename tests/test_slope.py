from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from shapely.geometry import box

from app.db.fixtures import load_fixtures
from app.render.panel_render import render_panel
from app.render.terrain import render_slope_paths, slope_bucket
from app.services.panels_meta import PLOT_SIZE_RAMP, plot_size_bucket


def test_slope_bucket_edges():
    assert slope_bucket(0) == "s0"
    assert slope_bucket(5) == "s1"
    assert slope_bucket(19.9) == "s3"
    assert slope_bucket(20) == "s4"


def test_plot_size_bucket():
    assert plot_size_bucket(80) == ("p0", 0)
    assert plot_size_bucket(250) == ("p1", 1)
    assert plot_size_bucket(800) == ("p2", 2)
    assert plot_size_bucket(2000) == ("p3", 3)
    assert plot_size_bucket(4000) == ("p4", 4)
    assert len(PLOT_SIZE_RAMP) == 5


def test_render_slope_and_plot_size_from_fixture():
    load_fixtures(force=True)
    slope = render_panel("slope", 37.4837, 127.0324, radius_m=800)
    assert "<path" in slope["svg"] or "경사" in slope["svg"]
    assert slope["feature_count"] >= 1
    plot = render_panel("plot_size", 37.4837, 127.0324, radius_m=800)
    assert "<path" in plot["svg"]
    assert plot["feature_count"] >= 1
    assert 'stroke="#ffffff"' in plot["svg"]
    assert "stroke-miterlimit" in plot["svg"]


def test_slope_paths_on_synthetic_dem():
    lats = [37.476, 37.492]
    lngs = [127.024, 127.040]
    grid = [[10.0, 12.0], [80.0, 82.0]]
    terrain = {"lats": lats, "lngs": lngs, "grid": grid}
    clip = box(127.028, 37.480, 127.036, 37.488)
    paths, stats = render_slope_paths(terrain, clip, 37.484, 127.032, 0.5)
    assert stats["max_deg"] > 0
    assert any(p.get("cat", "").startswith("s") for p in paths)
