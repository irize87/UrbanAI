from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from shapely.geometry import box

from app.db.fixtures import load_fixtures
from app.render.panel_render import render_panel
from app.render.sun_hours import compute_sun_hours
from app.services.panels_meta import panel_payload
from app.services.solar import KST


def test_twenty_panels():
    keys = [p["key"] for p in panel_payload()["panels"]]
    assert keys[0] == "aerial"
    assert keys[-1] == "trees"
    assert "slope" in keys and "plot_size" in keys and "sun_hours" in keys
    assert len(keys) == 20


def test_box_building_casts_shadow():
    geom = box(127.0323, 37.4836, 127.0325, 37.4838)
    dt = datetime(2026, 12, 21, 12, 0, tzinfo=KST)
    result = compute_sun_hours(
        [(geom, 40.0)],
        127.0324,
        37.4837,
        radius_m=200,
        dt=dt,
        n=32,
    )
    hours = result["hours"]
    assert hours.max() > 0
    assert float(hours.mean()) < result["stats"]["day_length_h"]


def test_sun_hours_panel_fixture():
    load_fixtures(force=True)
    out = render_panel("sun_hours", 37.4837, 127.0324, radius_m=400)
    assert "image" in out["svg"] or "<svg" in out["svg"]
    assert "xlink:href" in out["svg"]
    assert out.get("stats")
    assert "동지" in (out.get("caption") or "")


def test_satellite_tile_urls_prefer_jpeg():
    from app.services.aerial_mosaic import satellite_tile_urls

    urls = satellite_tile_urls(17, 111787, 50795, "KEY")
    assert urls[0].endswith(".jpeg")
    assert "/Satellite/17/50795/111787.jpeg" in urls[0]
    assert "xdworld.vworld.kr/2d/Satellite/service/17/111787/50795.jpeg" in urls[-1]
