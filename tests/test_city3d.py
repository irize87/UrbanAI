from __future__ import annotations

from app.db.fixtures import load_fixtures
from app.services.city3d import (
    build_city_buildings,
    build_city_context,
    build_city_ground,
    build_city_meta,
    clear_city_cache,
)


def test_city3d_seocho_is_3d_and_outside_is_flat() -> None:
    load_fixtures(force=True)
    clear_city_cache()
    meta = build_city_meta()
    buildings = build_city_buildings()
    ground = build_city_ground()
    context = build_city_context()

    assert meta["context_buffer_m"] == 600
    assert meta["rev"]
    assert buildings["rev"] == meta["rev"]
    assert ground["rev"] == meta["rev"]
    assert context["rev"] == meta["rev"]
    assert len(buildings["items"]) == 7
    assert all(item["h"] > 0 and item["rings"] for item in buildings["items"])
    east = [
        ring
        for ring in context["buildings"]
        if min(point[0] for point in ring) > meta["seocho_bounds"]["max_lng"]
    ]
    assert east
    assert context["plate"]
    assert ground["boundary"]
    assert ground["roads"]
    assert context["roads"]
    assert ground["green"]
