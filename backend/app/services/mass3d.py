from __future__ import annotations

import json
import math
from collections import Counter
from typing import Any, Callable

from shapely.geometry import Polygon, mapping
from shapely.ops import unary_union

from app.core.config import load_hardcode
from app.db.schema import connect
from app.render.svg_engine import clip_geom
from app.services.clip import analysis_clip
from app.services.footprint import building_height_m
from app.services.layer_cache import clipped_buildings, iter_clipped


def _features(
    table: str,
    clip,
    prop_fn: Callable[[dict[str, Any]], dict[str, Any] | None],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row, geom in iter_clipped(table, clip):
        props = prop_fn(row)
        if props is None:
            continue
        out.append(
            {
                "type": "Feature",
                "properties": props,
                "geometry": mapping(geom),
            }
        )
    return out


def _polygonal(geom: Any) -> Any | None:
    """면만 남긴다. 선·점은 녹지 메쉬에 쓰지 않는다."""
    if geom is None or geom.is_empty:
        return None
    if geom.geom_type in {"Polygon", "MultiPolygon"}:
        return geom
    if geom.geom_type == "GeometryCollection":
        parts = [
            g
            for g in geom.geoms
            if g.geom_type in {"Polygon", "MultiPolygon"} and not g.is_empty
        ]
        if not parts:
            return None
        merged = unary_union(parts)
        return None if merged is None or merged.is_empty else merged
    return None


def _fill_rings(geom: Any) -> Any | None:
    """내부 구멍(운동장·공터 등)도 녹지로 채운다. 3D에서 회색 구멍이 남지 않게."""
    geom = _polygonal(geom)
    if geom is None:
        return None
    if geom.geom_type == "Polygon":
        filled = Polygon(geom.exterior)
        return None if filled.is_empty else filled
    parts = [Polygon(poly.exterior) for poly in geom.geoms if not poly.is_empty]
    if not parts:
        return None
    return _polygonal(unary_union(parts))


def dissolve_green_geoms(geoms: list[Any]) -> Any | None:
    """겹치는 공원·숲을 한 면으로 합친다. 구멍은 메워 3D 회색 공터를 없앤다."""
    cleaned: list[Any] = []
    for geom in geoms:
        if geom is None or geom.is_empty:
            continue
        piece = geom if geom.is_valid else geom.buffer(0)
        piece = _fill_rings(piece)
        if piece is None or piece.is_empty:
            continue
        cleaned.append(piece)
    if not cleaned:
        return None
    merged = _fill_rings(unary_union(cleaned))
    if merged is None or merged.is_empty:
        return None
    # ~4m 버퍼로 맞닿은 면 사이 틈을 메운다.
    pad = 4.0 / 111320.0
    welded = merged.buffer(pad).buffer(-pad)
    welded = _fill_rings(welded if welded.is_valid else welded.buffer(0))
    if welded is None or welded.is_empty:
        welded = merged
    # OSM 점 과다로 ExtrudeGeometry가 실패하지 않게 약 2m 단순화
    simple = welded.simplify(2.0 / 111320.0, preserve_topology=True)
    simple = _fill_rings(simple)
    return welded if simple is None or simple.is_empty else simple


def subtract_road_buffers(green: Any, buffers: list[Any]) -> Any | None:
    """녹지에서 도로 버퍼를 빼 통로를 연다. 구멍은 메우지 않는다."""
    if green is None or green.is_empty:
        return None
    cleaned = [buf for buf in buffers if buf is not None and not buf.is_empty]
    if not cleaned:
        return _polygonal(green)
    cut = unary_union(cleaned)
    if cut is None or cut.is_empty:
        return _polygonal(green)
    out = green.difference(cut)
    if out is None or out.is_empty:
        return None
    if not out.is_valid:
        out = out.buffer(0)
    return _polygonal(out)


def _cut_roads_from_green(green: Any, clip: Any) -> Any:
    """녹지 면이 도로를 덮지 않게 도로 폭만큼 걷어낸다."""
    hc = load_hardcode()
    widths = hc.get("ROAD_WIDTH_M") or {}
    if not isinstance(widths, dict):
        widths = {}
    skip = {str(x) for x in (hc.get("ROAD_SKIP") or [])}
    extra_m = float(hc.get("MASS_GREEN_ROAD_CLEAR_M") or 2.5)
    try:
        lat0 = float(clip.centroid.y)
    except Exception:
        lat0 = 37.48
    cos_lat = max(0.2, math.cos(math.radians(lat0)))
    m_per_deg = (111320.0 + 111320.0 * cos_lat) / 2.0
    deg = 1.0 / m_per_deg
    bufs: list[Any] = []
    for row, geom in iter_clipped("roads", clip):
        cls = str(row.get("road_class") or "")
        if cls in skip:
            continue
        width_m = float(widths.get(cls) or 0)
        if width_m <= 0:
            continue
        tube_r = max(1.3, (width_m / 2.0) * 1.45)
        buf = geom.buffer((tube_r + extra_m) * deg)
        if buf is not None and not buf.is_empty:
            bufs.append(buf)
    cut = subtract_road_buffers(green, bufs)
    return green if cut is None or cut.is_empty else cut


def _line_as_water_poly(geom: Any, buffer_m: float = 8.0) -> Any | None:
    """선으로만 들어온 하천을 면으로 만든다. OSM riverbank는 이미 면이라 건너뛴다."""
    if geom is None or geom.is_empty:
        return None
    if geom.geom_type in {"LineString", "MultiLineString"}:
        poly = geom.buffer(buffer_m / 111320.0)
        return None if poly is None or poly.is_empty else poly
    if geom.geom_type == "GeometryCollection":
        parts = [
            g
            for g in geom.geoms
            if g.geom_type in {"LineString", "MultiLineString"} and not g.is_empty
        ]
        if not parts:
            return None
        merged = unary_union(parts).buffer(buffer_m / 111320.0)
        return None if merged is None or merged.is_empty else merged
    return None


def dissolve_water_geoms(geoms: list[Any]) -> Any | None:
    """하천·바다 면을 합친다. 섬(구멍)은 유지하고 도로로 자르지 않는다."""
    cleaned: list[Any] = []
    for geom in geoms:
        if geom is None or geom.is_empty:
            continue
        piece = geom if geom.is_valid else geom.buffer(0)
        poly = _polygonal(piece) or _line_as_water_poly(piece)
        if poly is None or poly.is_empty:
            continue
        cleaned.append(poly)
    if not cleaned:
        return None
    merged = unary_union(cleaned)
    if merged is None or merged.is_empty:
        return None
    if not merged.is_valid:
        merged = merged.buffer(0)
    pad = 4.0 / 111320.0
    welded = merged.buffer(pad).buffer(-pad)
    if welded is None or welded.is_empty:
        welded = merged
    elif not welded.is_valid:
        welded = welded.buffer(0)
    simple = welded.simplify(2.0 / 111320.0, preserve_topology=True)
    out = welded if simple is None or simple.is_empty else simple
    return _polygonal(out)


def _merged_water_geom(clip: Any) -> Any | None:
    """clip 안 water·sea를 한 면으로 합친다."""
    water_kinds = {"water", "sea"}
    geoms = []
    for row, geom in iter_clipped("green", clip):
        kind = str(row.get("kind") or "")
        if kind not in water_kinds:
            continue
        geoms.append(geom)
    merged = dissolve_water_geoms(geoms)
    if merged is None:
        return None
    merged = clip_geom(merged, clip)
    if merged.is_empty:
        return None
    return _polygonal(merged)


def _water_features(clip: Any, merged: Any | None = None) -> list[dict[str, Any]]:
    """3D용 하천·바다. 녹지와 다른 파란 면으로 올린다. 도로는 다리처럼 위에 둔다."""
    if merged is None:
        merged = _merged_water_geom(clip)
    if merged is None or merged.is_empty:
        return []
    return [
        {
            "type": "Feature",
            "properties": {"kind": "water"},
            "geometry": mapping(merged),
        }
    ]


def _green_features(clip: Any, water: Any | None = None) -> list[dict[str, Any]]:
    """3D용 녹지. 지정 공원과 그 밖 녹지를 한 색으로 합친다. 하천은 별도 파란 레이어."""
    hc = load_hardcode()
    zone_cats = hc.get("MASS_ZONE_GREEN_CATS") or ["녹지", "농림", "자연환경"]
    if not isinstance(zone_cats, list):
        zone_cats = ["녹지", "농림", "자연환경"]
    land_kinds = {"park", "other"}
    geoms = []
    for row, geom in iter_clipped("green", clip):
        kind = str(row.get("kind") or "park")
        if kind not in land_kinds:
            continue
        geoms.append(geom)
    zone_set = {str(c) for c in zone_cats}
    for row, geom in iter_clipped("zoning", clip):
        if str(row.get("zone_cat") or "") not in zone_set:
            continue
        geoms.append(geom)
    merged = dissolve_green_geoms(geoms)
    if merged is None:
        return []
    merged = clip_geom(merged, clip)
    if merged.is_empty:
        return []
    merged = _cut_roads_from_green(merged, clip)
    if merged is None or merged.is_empty:
        return []
    if water is not None and not water.is_empty:
        cut = subtract_road_buffers(merged, [water])
        if cut is not None and not cut.is_empty:
            merged = cut
    return [
        {
            "type": "Feature",
            "properties": {"kind": "green"},
            "geometry": mapping(merged),
        }
    ]


def mass_style() -> dict[str, Any]:
    """3D 바닥판 두께·흰 지면·회색 도로·등고 간격. hardcode에서 읽음."""
    hc = load_hardcode()
    widths = hc.get("ROAD_WIDTH_M") or {}
    skip = hc.get("ROAD_SKIP") or []
    return {
        "base_h_m": float(hc.get("MASS_BASE_H_M") or 180),
        "floor": str(hc.get("MASS_FLOOR_COLOR") or "#E7E9EC"),
        "bg": str(hc.get("MASS_BG_COLOR") or "#ffffff"),
        "road": str(hc.get("MASS_ROAD_COLOR") or "#c4c4c4"),
        "green": str(hc.get("MASS_GREEN_COLOR") or "#c3e6a4"),
        "water": str(hc.get("MASS_WATER_COLOR") or "#8ecae6"),
        "water_lift_m": float(hc.get("MASS_WATER_LIFT_M") or 0.16),
        "draw_contours": bool(hc.get("MASS_DRAW_CONTOURS")),
        "contour": str(hc.get("MASS_CONTOUR_COLOR") or "#9e9e9e"),
        "contour_m": float(hc.get("TERRAIN_CONTOUR_M") or 10),
        "terrain_n": int(hc.get("MASS_TERRAIN_N") or 72),
        "terrain_exag": float(hc.get("MASS_TERRAIN_EXAG") or 2.0),
        "explode_gap_ratio": float(hc.get("MASS_EXPLODE_GAP_RATIO") or 0.52),
        "layer_h_m": float(hc.get("MASS_LAYER_H_M") or 10),
        "terrain_layer_h_m": float(hc.get("MASS_TERRAIN_LAYER_H_M") or 28),
        "road_lift_m": float(hc.get("MASS_ROAD_LIFT_M") or 0.45),
        "green_lift_m": float(hc.get("MASS_GREEN_LIFT_M") or 0.08),
        "green_depth_m": float(hc.get("MASS_GREEN_DEPTH_M") or 0.14),
        "road_width_m": widths if isinstance(widths, dict) else {},
        "road_skip": list(skip) if isinstance(skip, list) else [],
    }


def terrain_payload() -> dict[str, Any] | None:
    """meta.terrain_grid DEM을 3D 페이로드로 전달. 없으면 None."""
    with connect() as conn:
        row = conn.execute(
            "SELECT value FROM meta WHERE key='terrain_grid'"
        ).fetchone()
    if not row or not row["value"]:
        return None
    try:
        data = json.loads(row["value"])
    except (TypeError, json.JSONDecodeError):
        return None
    lats = data.get("lats")
    lngs = data.get("lngs")
    grid = data.get("grid")
    if not lats or not lngs or not grid:
        return None
    return {
        "lats": lats,
        "lngs": lngs,
        "grid": grid,
        "min": data.get("min"),
        "max": data.get("max"),
        "relief_m": data.get("relief_m"),
    }


def build_mass_payload(
    lat: float,
    lng: float,
    radius_m: float,
    shape: str,
    *,
    include_adjacent: bool = False,
) -> dict[str, Any]:
    clip = analysis_clip(
        lat,
        lng,
        radius_m,
        shape if shape in {"circle", "rect"} else "circle",
        include_adjacent=include_adjacent,
    )

    buildings: list[dict[str, Any]] = []
    for row, geom in clipped_buildings(clip, require_height=True):
        height = building_height_m(row)
        if height is None:
            continue
        buildings.append(
            {
                "type": "Feature",
                "properties": {
                    "id": row.get("id"),
                    "use_cat": row.get("use_cat") or "미상",
                    "year": row.get("year"),
                    "height_m": float(height),
                    "floors": row.get("floors"),
                    "sgg_cd": row.get("sgg_cd"),
                },
                "geometry": mapping(geom),
            }
        )
    roads = _features(
        "roads",
        clip,
        lambda row: {
            "road_class": row.get("road_class"),
            "name": row.get("name"),
            "sgg_cd": row.get("sgg_cd"),
        },
    )
    water_geom = _merged_water_geom(clip)
    green = _green_features(clip, water=water_geom)
    water = _water_features(clip, merged=water_geom)
    by_sgg = Counter(str(f["properties"].get("sgg_cd") or "") for f in buildings)
    return {
        "buildings": {"type": "FeatureCollection", "features": buildings},
        "roads": {"type": "FeatureCollection", "features": roads},
        "green": {"type": "FeatureCollection", "features": green},
        "water": {"type": "FeatureCollection", "features": water},
        "clip": mapping(clip),
        "terrain": terrain_payload(),
        "style": mass_style(),
        "floor_h": 3.25,
        "include_adjacent": include_adjacent,
        "stats": {
            "mass_count": len(buildings),
            "include_adjacent": include_adjacent,
            "by_sgg": dict(by_sgg),
            "note": "높이·층수 없는 건물은 제외. 인접 자치구 체크 해제는 서초 구 영역만.",
        },
        "type": "FeatureCollection",
        "features": buildings,
    }
