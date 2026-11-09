from __future__ import annotations

"""서초구 전역 3D와 경계 밖 바닥 2D 페이로드.

분석 반경 없이 구 경계로 자른다. 브라우저가 레이어별로 받아 캐시한다.
"""

import threading
from typing import Any

from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

from app.core.config import load_hardcode
from app.core.geo import seocho_bbox, seocho_center
from app.db.schema import connect
from app.db.sgg import load_sgg_region
from app.render.svg_engine import to_lonlat, to_metric
from app.services.footprint import building_height_m
from app.services.layer_cache import iter_clipped
from app.services.mass3d import mass_style, terrain_payload

_CACHE: dict[str, dict[str, Any]] = {}
_CACHE_LOCK = threading.Lock()


def clear_city_cache() -> None:
    """동기화 뒤 전역 3D 응답 캐시를 비운다."""
    with _CACHE_LOCK:
        _CACHE.clear()


def context_buffer_m() -> float:
    """서초 경계 밖으로 2D를 깔 폭(m)."""
    hc = load_hardcode()
    try:
        value = float(hc.get("CITY_CONTEXT_BUFFER_M") or 600)
    except (TypeError, ValueError):
        value = 600.0
    return min(1500.0, max(200.0, value))


def city_revision() -> str:
    """건물·도로·녹지 수와 마지막 동기화로 캐시 키를 만든다."""
    with connect() as conn:
        buildings = conn.execute("SELECT COUNT(*) AS c FROM buildings").fetchone()["c"]
        roads = conn.execute("SELECT COUNT(*) AS c FROM roads").fetchone()["c"]
        green = conn.execute("SELECT COUNT(*) AS c FROM green").fetchone()["c"]
        sync = conn.execute(
            "SELECT COALESCE(MAX(id), 0) AS i, COALESCE(MAX(synced_at), '') AS t "
            "FROM sync_log"
        ).fetchone()
        mode = conn.execute("SELECT value FROM meta WHERE key='data_mode'").fetchone()
    mode_v = mode["value"] if mode else ""
    return f"{mode_v}|b{buildings}|r{roads}|g{green}|s{sync['i']}|{sync['t']}|city-lod2"


def seocho_region() -> BaseGeometry:
    """서초 구 경계. 경계 행이 없으면 분석 bbox 사각형."""
    region = load_sgg_region(False)
    if region is not None and not region.is_empty:
        if not region.is_valid:
            region = region.buffer(0)
        return region
    bounds = seocho_bbox()
    return box(bounds["min_lng"], bounds["min_lat"], bounds["max_lng"], bounds["max_lat"])


def context_ring(seocho: BaseGeometry) -> BaseGeometry | None:
    """서초 경계에서 바깥으로 buffer 만큼의 띠. 구 안은 빠진다."""
    metric = to_metric(seocho)
    if metric is None or metric.is_empty:
        return None
    if not metric.is_valid:
        metric = metric.buffer(0)
    outer = metric.buffer(context_buffer_m(), resolution=12)
    ring = outer.difference(metric)
    if ring is None or ring.is_empty:
        return None
    if not ring.is_valid:
        ring = ring.buffer(0)
    lonlat = to_lonlat(ring)
    if lonlat is None or lonlat.is_empty:
        return None
    return lonlat


def _bounds(geom: BaseGeometry) -> dict[str, float]:
    minx, miny, maxx, maxy = geom.bounds
    return {
        "min_lng": float(minx),
        "min_lat": float(miny),
        "max_lng": float(maxx),
        "max_lat": float(maxy),
    }


def _simplify(geom: BaseGeometry, meters: float) -> BaseGeometry:
    if geom is None or geom.is_empty:
        return geom
    tol = meters / 111320.0
    try:
        simple = geom.simplify(tol, preserve_topology=True)
    except Exception:
        return geom
    if simple is None or simple.is_empty:
        return geom
    return simple


def _coords(seq: Any, min_len: int) -> list[list[float]]:
    out: list[list[float]] = []
    for coord in seq:
        lng = round(float(coord[0]), 5)
        lat = round(float(coord[1]), 5)
        if out and out[-1][0] == lng and out[-1][1] == lat:
            continue
        out.append([lng, lat])
    if len(out) >= 2 and out[0] == out[-1]:
        out.pop()
    if len(out) < min_len:
        return []
    return out


def _iter_polygons(geom: BaseGeometry):
    if geom is None or geom.is_empty:
        return
    kind = geom.geom_type
    if kind == "Polygon":
        yield geom
    elif kind == "MultiPolygon":
        yield from geom.geoms
    elif kind == "GeometryCollection":
        for part in geom.geoms:
            yield from _iter_polygons(part)


def _iter_lines(geom: BaseGeometry):
    if geom is None or geom.is_empty:
        return
    kind = geom.geom_type
    if kind == "LineString":
        yield geom
    elif kind == "MultiLineString":
        yield from geom.geoms
    elif kind == "Polygon":
        yield geom.exterior
    elif kind == "MultiPolygon":
        for part in geom.geoms:
            yield part.exterior
    elif kind == "GeometryCollection":
        for part in geom.geoms:
            yield from _iter_lines(part)


def _area_m2(geom: BaseGeometry) -> float:
    try:
        metric = to_metric(geom)
    except Exception:
        return 0.0
    if metric is None or metric.is_empty:
        return 0.0
    return float(metric.area)


def _length_m(geom: BaseGeometry) -> float:
    try:
        metric = to_metric(geom)
    except Exception:
        return 0.0
    if metric is None or metric.is_empty:
        return 0.0
    return float(metric.length)


def _polys(geom: BaseGeometry) -> list[list[list[list[float]]]]:
    """폴리곤을 [외곽, 구멍...] 링 목록으로. 좌표는 소수 6자리."""
    out: list[list[list[list[float]]]] = []
    for poly in _iter_polygons(geom):
        exterior = _coords(poly.exterior.coords, 3)
        if not exterior:
            continue
        rings = [exterior]
        for hole in poly.interiors:
            ring = _coords(hole.coords, 3)
            if ring:
                rings.append(ring)
        out.append(rings)
    return out


def _city_surface_polys(
    table: str,
    clip: BaseGeometry,
    *,
    accept,
    min_area: float,
    simplify_m: float,
) -> list[list[list[list[float]]]]:
    """구 전체를 한 면으로 합치지 않고, 면마다 단순화해서 보낸다.

    전역 녹지·하천을 unary_union·도로 버퍼로 깎으면 요청이 수십 분 멈추는 경우가 있다.
    """
    polys: list[list[list[list[float]]]] = []
    for row, geom in iter_clipped(table, clip):
        if not accept(row):
            continue
        if _area_m2(geom) < min_area:
            continue
        polys.extend(_polys(_simplify(geom, simplify_m)))
    return polys


def _road_items(clip: BaseGeometry, simplify_m: float) -> list[dict[str, Any]]:
    hc = load_hardcode()
    skip = {str(item) for item in (hc.get("ROAD_SKIP") or [])}
    widths = hc.get("ROAD_WIDTH_M") or {}
    if not isinstance(widths, dict):
        widths = {}
    out: list[dict[str, Any]] = []
    for row, geom in iter_clipped("roads", clip):
        road_class = str(row.get("road_class") or "")
        if road_class in skip:
            continue
        try:
            width_m = float(widths.get(road_class) or 0)
        except (TypeError, ValueError):
            width_m = 0.0
        if width_m <= 0:
            continue
        simple = _simplify(geom, simplify_m)
        lines: list[list[list[float]]] = []
        for line in _iter_lines(simple):
            if _length_m(line) < 6:
                continue
            coords = _coords(line.coords, 2)
            if len(coords) >= 2:
                lines.append(coords)
        if lines:
            out.append({"c": road_class, "lines": lines})
    return out


def _building_rings(geom: BaseGeometry, min_area: float) -> list[list[list[float]]]:
    rings: list[list[list[float]]] = []
    for poly in _iter_polygons(geom):
        if _area_m2(poly) < min_area:
            continue
        ring = _coords(poly.exterior.coords, 3)
        if ring:
            rings.append(ring)
    return rings


def _cached(kind: str, builder) -> dict[str, Any]:
    rev = city_revision()
    key = f"{rev}:{kind}"
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit is not None:
            return hit
    payload = builder(rev)
    with _CACHE_LOCK:
        current = _CACHE.get(key)
        if current is not None:
            return current
        prefix = f"{rev}:"
        for stale in [name for name in _CACHE if not name.startswith(prefix)]:
            _CACHE.pop(stale, None)
        _CACHE[key] = payload
    return payload


def build_city_meta() -> dict[str, Any]:
    """구 경계 범위, 스타일, 캐시 버전. 도형은 다른 엔드포인트에 둔다."""
    seocho = seocho_region()
    ring = context_ring(seocho)
    center = seocho.centroid
    outer = ring if ring is not None else seocho
    origin_lng = float(center.x) if center is not None else seocho_center()["lng"]
    origin_lat = float(center.y) if center is not None else seocho_center()["lat"]
    return {
        "rev": city_revision(),
        "origin": {"lng": origin_lng, "lat": origin_lat},
        "seocho_bounds": _bounds(seocho),
        "bounds": _bounds(outer),
        "context_buffer_m": context_buffer_m(),
        "style": mass_style(),
    }


def build_city_buildings() -> dict[str, Any]:
    """서초 안 건물. 높이 없는 동은 빼고 용도·높이·외곽 링만 보낸다."""

    def _build(rev: str) -> dict[str, Any]:
        seocho = seocho_region()
        items: list[dict[str, Any]] = []
        for row, geom in iter_clipped("buildings", seocho):
            height = building_height_m(row)
            if height is None or height <= 0:
                continue
            rings = _building_rings(_simplify(geom, 2.5), 12.0)
            if not rings:
                continue
            items.append(
                {
                    "u": str(row.get("use_cat") or "미상"),
                    "h": round(float(height), 1),
                    "rings": rings,
                }
            )
        return {"rev": rev, "items": items}

    return _cached("buildings", _build)


def build_city_ground() -> dict[str, Any]:
    """서초 경계, 지형, 도로, 녹지, 하천."""

    def _build(rev: str) -> dict[str, Any]:
        seocho = seocho_region()
        hc = load_hardcode()
        zone_cats = {str(item) for item in (hc.get("MASS_ZONE_GREEN_CATS") or [])}
        return {
            "rev": rev,
            "boundary": _polys(_simplify(seocho, 4.0)),
            "roads": _road_items(seocho, 3.5),
            "green": _city_surface_polys(
                "green",
                seocho,
                accept=lambda row: str(row.get("kind") or "park") in {"park", "other"},
                min_area=40.0,
                simplify_m=8.0,
            )
            + _city_surface_polys(
                "zoning",
                seocho,
                accept=lambda row: str(row.get("zone_cat") or "") in zone_cats,
                min_area=80.0,
                simplify_m=12.0,
            ),
            "water": _city_surface_polys(
                "green",
                seocho,
                accept=lambda row: str(row.get("kind") or "") in {"water", "sea"},
                min_area=40.0,
                simplify_m=8.0,
            ),
            "terrain": terrain_payload(),
        }

    return _cached("ground", _build)


def build_city_context() -> dict[str, Any]:
    """경계 밖 띠의 건물 풋프린트와 도로. 높이는 보내지 않는다."""

    def _build(rev: str) -> dict[str, Any]:
        seocho = seocho_region()
        ring = context_ring(seocho)
        if ring is None:
            return {"rev": rev, "plate": [], "buildings": [], "roads": []}
        footprints: list[list[list[float]]] = []
        for _row, geom in iter_clipped("buildings", ring):
            footprints.extend(_building_rings(_simplify(geom, 5.0), 40.0))
        return {
            "rev": rev,
            "plate": _polys(_simplify(ring, 6.0)),
            "buildings": footprints,
            "roads": _road_items(ring, 6.0),
        }

    return _cached("context", _build)
