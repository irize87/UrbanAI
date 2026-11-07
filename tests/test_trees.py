from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from shapely.geometry import Point, box

from app.render.trees import detect_trees, _four_dir_radii, _mask_to_polygons


def _green_image(w: int = 180, h: int = 180) -> Image.Image:
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    arr[:] = (160, 130, 110)
    return Image.fromarray(arr, mode="RGB")


def test_forest_block_is_face_not_grid() -> None:
    img = _green_image()
    arr = np.array(img)
    arr[40:140, 40:140] = (20, 150, 30)
    img = Image.fromarray(arr, mode="RGB")
    canopies, stems = detect_trees(img, 127.0324, 37.4837, 300, [], [])
    assert canopies, "이어진 숲은 수관 면이 있어야 한다"
    assert all(c.get("geom") is not None for c in canopies)
    interior_lng, interior_lat = 127.0324, 37.4837
    near_center = 0
    for s in stems:
        dlat = (s["lat"] - interior_lat) * 111_000
        dlng = (s["lng"] - interior_lng) * 88_000
        if dlat * dlat + dlng * dlng < 40 * 40:
            near_center += 1
    assert near_center <= 8, f"숲 한가운데 격자 점이 많다: {near_center}"


def test_sparse_trees_are_circles() -> None:
    img = _green_image(160, 160)
    arr = np.array(img)
    for y, x in ((30, 30), (30, 80), (90, 40), (120, 110)):
        arr[y : y + 2, x : x + 2] = (20, 160, 25)
    img = Image.fromarray(arr, mode="RGB")
    canopies, stems = detect_trees(img, 127.0324, 37.4837, 300, [], [])
    assert len(stems) >= 3
    for s in stems:
        assert 1.0 <= s["r_m"] <= 4.2
    assert len(canopies) == 0


def test_four_dir_radius_clamped() -> None:
    mask = np.zeros((40, 40), dtype=bool)
    mask[18:23, 10:30] = True
    rows = np.array([20])
    cols = np.array([20])
    r = _four_dir_radii(mask, rows, cols, px_m=1.0, max_m=6.0)
    assert r.shape == (1,)
    assert 1.0 <= r[0] <= 6.0


def test_mask_polygons_solid_square() -> None:
    mask = np.zeros((80, 80), dtype=bool)
    mask[20:60, 20:60] = True
    polys = _mask_to_polygons(mask, 127.03, 37.48, 200, min_area_m2=10)
    assert len(polys) >= 1
    area = sum(p.area for p in polys)
    assert area > 0


def test_building_and_water_masks_drop_false_veg() -> None:
    """녹색 옥상·녹조 수면은 발자국·수면 폴리곤으로 지운다."""
    arr = np.zeros((160, 160, 3), dtype=np.uint8)
    arr[:] = (20, 150, 30)
    img = Image.fromarray(arr, mode="RGB")
    lng, lat, radius_m = 127.0324, 37.4837, 300
    center = Point(lng, lat)
    building = box(lng - 0.0012, lat - 0.0010, lng + 0.0012, lat + 0.0010)
    water = box(lng - 0.0024, lat - 0.0022, lng - 0.0014, lat - 0.0012)
    water_pt = water.centroid

    raw_can, _raw_stems = detect_trees(img, lng, lat, radius_m, [], [])
    masked_can, masked_stems = detect_trees(
        img, lng, lat, radius_m, [building], [water]
    )
    assert any(
        (c.get("geom") is not None) and c["geom"].intersects(center)
        for c in raw_can
    ), "마스킹 전에는 중심이 수관 면이어야 한다"
    assert all(
        c.get("geom") is None or not c["geom"].contains(center)
        for c in masked_can
    )
    assert all(
        c.get("geom") is None or not c["geom"].contains(water_pt)
        for c in masked_can
    )
    assert sum(1 for s in masked_stems if building.contains(Point(s["lng"], s["lat"]))) == 0
    assert sum(1 for s in masked_stems if water.contains(Point(s["lng"], s["lat"]))) == 0
