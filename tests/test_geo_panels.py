from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from shapely.geometry import Polygon

from app.core.geo import is_within_seocho, require_seocho
from app.core.districts import extract_sgg_cd, is_in_scope
from app.db.fixtures import load_fixtures
from app.render.panel_render import render_panel, road_stroke_px
from app.render.svg_engine import geom_to_svg_path
from app.services.mass3d import build_mass_payload
from app.services.panels_meta import AGE_RAMP, panel_payload, tint_ramp
from app.sync.monthly import (
    _use_from_zone,
    map_osm_green_kind,
    map_planned_kind,
    map_use_cat,
    map_zone_cat,
)


def test_seocho_bbox_accepts_center():
    assert is_within_seocho(37.4837, 127.0324)


def test_seocho_bbox_rejects_gangnam_far():
    assert not is_within_seocho(37.5665, 126.9780)  # 시청 근처


def test_require_seocho_raises():
    try:
        require_seocho(33.0, 126.0)
        assert False, "should raise"
    except ValueError as exc:
        assert "서초구" in str(exc)


def test_render_buildings_svg():
    load_fixtures()
    out = render_panel("buildings", 37.4837, 127.0324, radius_m=800)
    assert out["svg"].startswith("<?xml")
    assert "건물" in out["svg"] or "BUILDINGS" in out["svg"] or "<svg" in out["svg"]
    assert out["feature_count"] >= 1


def test_diagram_frame_fits_in_viewbox():
    from app.render.svg_engine import wrap_svg

    svg = wrap_svg([], 37.4837, 127.0324, 800, "circle", "테두리")
    match = re.search(r'viewBox="([^"]+)"', svg)
    assert match
    x, y, width, height = (float(v) for v in match.group(1).split())
    assert x < -400
    assert y < -400
    assert width > 800
    assert height > 800
    assert 'stroke-width="0.5"' in svg
    r = 400.0
    half_sw = 0.25
    assert x <= -r - half_sw
    assert x + width >= r + half_sw


def test_render_use_panel():
    load_fixtures()
    out = render_panel("use", 37.4837, 127.0324, radius_m=600)
    assert "<path" in out["svg"]
    assert "data-cat=" in out["svg"]


def test_zoning_cats_include_management():
    zoning = next(p for p in panel_payload()["panels"] if p["key"] == "zoning")
    keys = [c["key"] for c in zoning["cats"]]
    assert keys == ["주거", "상업", "공업", "녹지", "관리", "농림", "자연환경", "기타"]
    assert map_zone_cat("자연환경보전지역") == "자연환경"
    assert map_zone_cat("자연녹지지역") == "녹지"
    assert map_zone_cat("관리지역") == "관리"


def test_map_osm_green_kind():
    assert map_osm_green_kind({"leisure": "park"}) == "park"
    assert map_osm_green_kind({"natural": "wood"}) == "other"
    assert map_osm_green_kind({"landuse": "forest"}) == "other"
    assert map_osm_green_kind({"leisure": "garden"}) == "other"
    assert map_osm_green_kind({"natural": "water"}) == "water"
    assert map_osm_green_kind({"leisure": "park", "natural": "wood"}) == "park"
    assert map_osm_green_kind({"highway": "residential"}) is None


def test_green_draws_other_under_park():
    load_fixtures()
    svg = render_panel("green", 37.4837, 127.0324, radius_m=800)["svg"]
    other_i = svg.find('data-cat="other"')
    park_i = svg.find('data-cat="park"')
    assert other_i >= 0 and park_i >= 0
    assert other_i < park_i


def test_planned_cats_are_facilities_not_green():
    planned = next(p for p in panel_payload()["panels"] if p["key"] == "planned")
    keys = [c["key"] for c in planned["cats"]]
    assert keys == ["road", "school", "edu", "market", "parking"]
    labels = {c["key"]: c["label"] for c in planned["cats"]}
    assert labels["road"] == "계획도로"
    assert labels["parking"] == "주차·철도"
    assert "park" not in keys


def test_map_planned_kind():
    assert map_planned_kind("lt_c_upisuq151", "대로") == "road"
    assert map_planned_kind("lt_c_upisuq152", "주차장") == "parking"
    assert map_planned_kind("lt_c_upisuq152", "철도") == "parking"
    assert map_planned_kind("lt_c_upisuq153", "녹지") is None
    assert map_planned_kind("lt_c_upisuq153", "근린공원") is None
    assert map_planned_kind("lt_c_upisuq154", "시장") == "market"
    assert map_planned_kind("lt_c_upisuq154", "전기공급설비") is None
    assert map_planned_kind("lt_c_upisuq155", "공공청사") == "edu"
    assert map_planned_kind("lt_c_upisuq155", "서초초등학교") == "school"
    assert map_planned_kind("lt_c_upisuq155", "포이초교") == "school"
    assert map_planned_kind("lt_c_upisuq155", "개포고교") == "school"
    assert map_planned_kind("lt_c_upisuq155", "유치원(유아원)") == "school"


def test_map_use_cat_building_code():
    assert map_use_cat("01000") == "주거"
    assert map_use_cat("02000") == "주거"
    assert map_use_cat("03000") == "근린생활"
    assert map_use_cat("04000") == "근린생활"
    assert map_use_cat("14000") == "업무"
    assert map_use_cat("06000") == "문화집회"
    assert map_use_cat("횃불선교회관") == "문화집회"
    assert map_use_cat("온누리교회") == "문화집회"
    assert map_use_cat("15000") == "숙박"
    assert map_use_cat("17000") == "공업"
    assert map_use_cat("18000") == "공업"
    assert map_use_cat("10000") == "교육복지"
    assert map_use_cat("05000") == "문화집회"
    assert map_use_cat("교육연구시설") == "교육복지"
    assert map_use_cat("대림아파트") == "주거"
    assert map_use_cat("래미안 퍼스티지") == "미상"
    assert map_use_cat("개포자이 프레지던스") == "미상"
    assert map_use_cat("힐스테이트 상도") == "미상"
    assert map_use_cat("서울공업고등학교") == "교육복지"
    assert map_use_cat("") == "미상"
    assert _use_from_zone("주거", 2) == "주거"
    assert _use_from_zone("상업", 3) == "근린생활"
    assert _use_from_zone("상업", 12) == "업무"
    assert _use_from_zone("공업", 1) == "공업"


def test_tint_ramp_keeps_step_count():
    out = tint_ramp(AGE_RAMP, "#3b82f6")
    assert len(out) == len(AGE_RAMP)
    assert all(c.startswith("#") and len(c) == 7 for c in out)


def test_extract_sgg_from_pnu():
    assert extract_sgg_cd({"pnu": "1168010100"}) == "11680"
    assert extract_sgg_cd({"col_adm_se": "11590"}) == "11590"
    assert extract_sgg_cd({"sigg_name": "관악구"}) == "11620"
    assert extract_sgg_cd({"pnu": "1111010100"}) is None
    assert is_in_scope({"pnu": "1165010100"})
    assert extract_sgg_cd({"sggcd": "11620"}) == "11620"
    assert extract_sgg_cd({"PNU": "1159010100"}) == "11590"
    assert extract_sgg_cd({"li_pnu": "116501010010"}) == "11650"
    assert extract_sgg_cd({"col_adm_sect_cd": "11680"}) == "11680"


def test_filled_polygon_skips_holes():
    poly = Polygon(
        [
            (127.03, 37.48),
            (127.031, 37.48),
            (127.031, 37.481),
            (127.03, 37.481),
        ],
        [
            [
                (127.0302, 37.4802),
                (127.0308, 37.4802),
                (127.0308, 37.4808),
                (127.0302, 37.4808),
            ]
        ],
    )
    filled = geom_to_svg_path(poly, 127.0324, 37.4837, 0.5, filled=True)
    open_path = geom_to_svg_path(poly, 127.0324, 37.4837, 0.5, filled=False)
    assert filled.count("Z") == 1
    assert open_path.count("Z") == 2


def test_high_lowrise_ghost_other():
    load_fixtures()
    high = render_panel("high", 37.4837, 127.0324, radius_m=800, threshold=35)
    low = render_panel("lowrise", 37.4837, 127.0324, radius_m=800, threshold=12)
    assert 'data-cat="other"' in high["svg"]
    assert 'data-cat="main"' in high["svg"]
    assert 'fill="#ececec"' in high["svg"]
    assert 'data-cat="other"' in low["svg"]
    assert 'fill="#ececec"' in low["svg"]


def test_use_panel_keeps_category_fills():
    load_fixtures()
    out = render_panel("use", 37.4837, 127.0324, radius_m=800)
    assert any(
        color in out["svg"]
        for color in ("#f2d98d", "#6f9fd0", "#f0a878", "#e0607f", "#86c06a")
    )


def test_panel_accepts_include_adjacent():
    load_fixtures()
    off = render_panel("buildings", 37.4837, 127.0324, radius_m=800, include_adjacent=False)
    on = render_panel("buildings", 37.4837, 127.0324, radius_m=800, include_adjacent=True)
    assert "<svg" in off["svg"]
    assert "<svg" in on["svg"]
    assert on["feature_count"] >= off["feature_count"]
    load_fixtures()
    off = build_mass_payload(37.4837, 127.0324, 800, "circle", include_adjacent=False)
    on = build_mass_payload(37.4837, 127.0324, 800, "circle", include_adjacent=True)
    assert off["stats"]["mass_count"] >= 1
    assert on["stats"]["mass_count"] > off["stats"]["mass_count"]
    off_ids = {f["properties"].get("sgg_cd") for f in off["buildings"]["features"]}
    assert "11680" not in off_ids
    on_ids = {f["properties"].get("sgg_cd") for f in on["buildings"]["features"]}
    assert "11680" in on_ids


def test_mass_payload_style_and_terrain_keys():
    load_fixtures()
    out = build_mass_payload(37.4837, 127.0324, 800, "circle", include_adjacent=True)
    style = out["style"]
    assert style["base_h_m"] >= 20
    assert style["floor"] == "#E7E9EC"
    assert style["road"]
    assert "motorway" in style["road_width_m"]
    assert "footway" in style["road_skip"]
    assert "terrain" in out
    assert out["terrain"] is None or (
        out["terrain"].get("lats") and out["terrain"].get("grid")
    )
    assert style["green"] == "#c3e6a4"
    assert style["water"] == "#8ecae6"
    assert len(out["green"]["features"]) <= 1
    assert all(
        (f.get("properties") or {}).get("kind") == "green"
        for f in out["green"]["features"]
    )
    assert "water" in out
    assert all(
        (f.get("properties") or {}).get("kind") == "water"
        for f in out["water"]["features"]
    )


def test_mass_green_unifies_park_and_other():
    """2D 지정 공원·그 밖 녹지가 3D에서는 한 녹지 면으로 들어간다."""
    from shapely.geometry import shape

    load_fixtures()
    out = build_mass_payload(37.4837, 127.0324, 800, "circle")
    feats = out["green"]["features"]
    assert feats
    geom = shape(feats[0]["geometry"])
    assert geom.area > 0
    assert out["style"]["green"] == "#c3e6a4"


def test_mass_water_is_blue_and_separate_from_green():
    """fixture 하천이 3D에서 녹지와 다른 파란 면으로 들어간다."""
    from shapely.geometry import shape

    load_fixtures()
    out = build_mass_payload(37.4837, 127.0324, 800, "circle")
    feats = out["water"]["features"]
    assert feats
    geom = shape(feats[0]["geometry"])
    assert geom.area > 0
    assert out["style"]["water"] == "#8ecae6"
    assert all((f.get("properties") or {}).get("kind") == "water" for f in feats)
    assert all(
        (f.get("properties") or {}).get("kind") != "water"
        for f in out["green"]["features"]
    )


def test_dissolve_water_geoms_keeps_holes():
    from shapely.geometry import Polygon

    from app.services.mass3d import dissolve_water_geoms

    ring = [(0, 0), (4, 0), (4, 4), (0, 4), (0, 0)]
    hole = [(1, 1), (2, 1), (2, 2), (1, 2), (1, 1)]
    donut = Polygon(ring, [hole])
    merged = dissolve_water_geoms([donut])
    assert merged is not None
    assert merged.geom_type in {"Polygon", "MultiPolygon"}
    interiors = 0
    if merged.geom_type == "Polygon":
        interiors = len(merged.interiors)
    else:
        interiors = sum(len(p.interiors) for p in merged.geoms)
    assert interiors >= 1


def test_dissolve_green_geoms_merges_overlap():
    from shapely.geometry import box

    from app.services.mass3d import dissolve_green_geoms

    merged = dissolve_green_geoms([box(0, 0, 2, 2), box(1, 1, 3, 3)])
    assert merged is not None
    assert abs(merged.area - 7.0) < 1e-4


def test_subtract_road_buffers_cuts_corridor():
    from shapely.geometry import LineString, box

    from app.services.mass3d import subtract_road_buffers

    park = box(0, 0, 4, 4)
    road = LineString([(0, 2), (4, 2)]).buffer(0.3)
    out = subtract_road_buffers(park, [road])
    assert out is not None
    assert out.area < park.area - 0.5


def test_dissolve_green_geoms_fills_holes():
    from shapely.geometry import Polygon

    from app.services.mass3d import dissolve_green_geoms

    donut = Polygon(
        [(0, 0), (3, 0), (3, 3), (0, 3)],
        [[(1, 1), (2, 1), (2, 2), (1, 2)]],
    )
    merged = dissolve_green_geoms([donut])
    assert merged is not None
    assert merged.area > 8.5
    if merged.geom_type == "Polygon":
        assert len(merged.interiors) == 0


def test_zoning_overlays_white_roads():
    load_fixtures()
    out = render_panel("zoning", 37.4837, 127.0324, radius_m=800)
    assert 'data-cat="road"' in out["svg"]
    assert 'stroke="#ffffff"' in out["svg"]


def test_transit_draws_gray_roads_under_stops():
    load_fixtures()
    out = render_panel("transit", 37.4837, 127.0324, radius_m=800)
    svg = out["svg"]
    assert 'data-cat="road"' in svg
    assert 'stroke="#c4c4c4"' in svg
    road_i = svg.find('data-cat="road"')
    stop_i = min(
        i for i in (svg.find('data-cat="subway"'), svg.find('data-cat="bus"')) if i >= 0
    )
    assert road_i < stop_i


def test_context_panels_draw_ghost_buildings_underneath():
    load_fixtures()
    overlays = {
        "green": ("park", "water"),
        "district": ("duz",),
        "planned": ("school",),
    }
    for panel, cats in overlays.items():
        svg = render_panel(panel, 37.4837, 127.0324, radius_m=800)["svg"]
        assert 'data-cat="ghost"' in svg
        assert 'fill="#ececec"' in svg
        ghost_i = svg.find('data-cat="ghost"')
        overlay_i = min(i for i in (svg.find(f'data-cat="{c}"') for c in cats) if i >= 0)
        assert ghost_i < overlay_i, panel


def test_district_draws_zone_boundaries():
    load_fixtures()
    svg = render_panel("district", 37.4837, 127.0324, radius_m=800)["svg"]
    assert 'data-cat="duz"' in svg
    assert 'fill-opacity="0.4"' in svg
    assert re.search(r'stroke="#[0-9a-fA-F]{6}"', svg)


def test_block_is_figure_ground_with_white_roads():
    load_fixtures()
    svg = render_panel("block", 37.4837, 127.0324, radius_m=800)["svg"]
    assert 'data-cat="main"' in svg
    assert 'data-cat="road"' in svg
    assert 'stroke="#ffffff"' in svg
    assert svg.find('data-cat="main"') < svg.find('data-cat="road"')


def test_road_stroke_hierarchy():
    assert road_stroke_px("footway", 0.5) is None
    assert road_stroke_px("motorway", 0.5) > road_stroke_px("residential", 0.5) * 4
    load_fixtures()
    out = render_panel("roads", 37.4837, 127.0324, radius_m=800)
    assert 'fill="none"' in out["svg"]
    assert 'stroke-linecap="round"' in out["svg"]
    widths = [float(m) for m in re.findall(r'stroke-width="([0-9.]+)"', out["svg"])]
    assert widths
    assert max(widths) > min(widths) * 1.4


def test_dedupe_overlapping_buildings_keeps_newer_year():
    from shapely.geometry import box

    from app.services.footprint import dedupe_overlapping_buildings

    office = {
        "use_cat": "업무",
        "source": "vworld:lt_c_bldginfo",
        "use_raw": "14000",
        "year": 2019,
        "height_m": 40,
        "floors": 12,
    }
    house = {
        "use_cat": "주거",
        "source": "vworld:lt_c_spbd",
        "use_raw": "래미안",
        "year": 1995,
        "height_m": None,
        "floors": 20,
    }
    out = dedupe_overlapping_buildings(
        [(office, box(0, 0, 1, 1)), (house, box(0.1, 0.1, 1.1, 1.1))]
    )
    assert len(out) == 1
    assert out[0][0]["use_cat"] == "업무"


def test_dedupe_drops_old_centroid_inside_newer():
    from shapely.geometry import box

    from app.services.footprint import dedupe_overlapping_buildings

    old = {
        "use_cat": "주거",
        "source": "vworld:lt_c_bldginfo",
        "use_raw": "01000",
        "year": 1992,
        "height_m": 9,
    }
    new = {
        "use_cat": "미상",
        "source": "vworld:lt_c_bldginfo",
        "use_raw": "",
        "year": 2021,
        "height_m": 48,
    }
    # 예전 집 중심은 새 건물 안이고, 겹친 면적은 작은 쪽의 40% 미만이다.
    out = dedupe_overlapping_buildings(
        [(old, box(0, 0, 10, 2)), (new, box(4.6, 0.7, 8, 5))]
    )
    assert len(out) == 1
    assert out[0][0]["year"] == 2021


def test_dedupe_prefers_newer_over_named_old():
    from shapely.geometry import box

    from app.services.footprint import dedupe_overlapping_buildings

    empty_gis = {
        "use_cat": "주거",
        "source": "vworld:lt_c_bldginfo",
        "use_raw": "",
        "year": 2001,
        "height_m": 16,
        "floors": 5,
    }
    named = {
        "use_cat": "문화집회",
        "source": "vworld:lt_c_spbd",
        "use_raw": "횃불선교회관",
        "year": 2016,
        "height_m": None,
        "floors": 5,
    }
    out = dedupe_overlapping_buildings(
        [(empty_gis, box(0, 0, 1, 1)), (named, box(0.05, 0.05, 1.05, 1.05))]
    )
    assert len(out) == 1
    assert out[0][0]["use_cat"] == "문화집회"


def test_dedupe_keeps_side_by_side_buildings():
    from shapely.geometry import box

    from app.services.footprint import dedupe_overlapping_buildings

    a = {
        "use_cat": "주거",
        "source": "vworld:lt_c_bldginfo",
        "use_raw": "01000",
        "height_m": 12,
    }
    b = {
        "use_cat": "업무",
        "source": "vworld:lt_c_bldginfo",
        "use_raw": "14000",
        "height_m": 40,
    }
    out = dedupe_overlapping_buildings(
        [(a, box(0, 0, 1, 1)), (b, box(1.2, 0, 2.2, 1))]
    )
    assert len(out) == 2


def test_heat_metrics_are_area_and_count():
    heat = next(p for p in panel_payload()["panels"] if p["key"] == "heat")
    assert heat["metrics"] == [["area", "건축면적"], ["count", "건축물 수"]]


def test_heat_draws_isobands_and_footprints():
    from app.render.heat import normalize_heat_metric

    assert normalize_heat_metric("gfa") == "area"
    assert normalize_heat_metric("count") == "count"
    load_fixtures()
    area = render_panel("heat", 37.4837, 127.0324, radius_m=800, metric="area")
    count = render_panel("heat", 37.4837, 127.0324, radius_m=800, metric="count")
    assert 'data-cat="footprint"' in area["svg"]
    assert 'data-cat="heat"' in area["svg"]
    assert 'data-cat="heat"' in count["svg"]
    assert area["svg"] != count["svg"]


def test_clip_geom_skips_disjoint_bbox():
    from shapely.geometry import box

    from app.render.svg_engine import clip_geom

    far = box(0, 0, 1, 1)
    clip = box(127, 37, 128, 38)
    assert clip_geom(far, clip).is_empty
    near = box(127.1, 37.1, 127.2, 37.2)
    assert not clip_geom(near, clip).is_empty
