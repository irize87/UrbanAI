from __future__ import annotations

"""건물 연면적·동수 KDE → 등치 밴드 히트맵."""

from typing import Any

import numpy as np
from shapely.geometry import LineString, MultiLineString, box as shapely_box
from shapely.ops import polygonize, unary_union

from app.core.config import load_hardcode
from app.render.svg_engine import (
    clip_geom,
    geom_to_svg_path,
    to_lonlat,
    to_metric,
)
from app.render.terrain import marching_squares
from app.services.footprint import clip_and_dedupe_buildings

RAMP_DEFAULT = [
    "#f8d7b0",
    "#f3a97a",
    "#ea6e62",
    "#d63d72",
    "#b21d7a",
    "#7a1578",
]
FOOTPRINT_FILL = "#efeae0"


def normalize_heat_metric(metric: str | None) -> str:
    raw = (metric or "area").strip().lower()
    if raw in {"count", "n", "동수"}:
        return "count"
    return "area"


def _area_weight(row: dict[str, Any], geom_m: Any) -> float:
    try:
        gfa = float(row.get("gfa_m2") or 0)
    except (TypeError, ValueError):
        gfa = 0.0
    if gfa > 0:
        return gfa
    area = float(geom_m.area)
    return area if area > 0 else 1.0


def _kde(
    xs: np.ndarray,
    ys: np.ndarray,
    points: list[tuple[float, float, float]],
    sigma: float,
) -> np.ndarray:
    n = int(xs.size)
    zz = np.zeros((n, n), dtype=np.float64)
    dx = float(xs[1] - xs[0]) if n > 1 else 1.0
    dy = float(ys[1] - ys[0]) if n > 1 else 1.0
    inv = 1.0 / (2.0 * sigma * sigma)
    rad = 3.0 * sigma
    jw = max(1, int(np.ceil(rad / abs(dx))))
    iw = max(1, int(np.ceil(rad / abs(dy))))
    x0 = float(xs[0])
    y0 = float(ys[0])
    for x, y, weight in points:
        if weight <= 0:
            continue
        jc = int(round((x - x0) / dx))
        ic = int(round((y - y0) / dy))
        j0 = max(0, jc - jw)
        j1 = min(n, jc + jw + 1)
        i0 = max(0, ic - iw)
        i1 = min(n, ic + iw + 1)
        if j0 >= j1 or i0 >= i1:
            continue
        sub_x = xs[j0:j1]
        sub_y = ys[i0:i1]
        gx, gy = np.meshgrid(sub_x, sub_y)
        dist2 = (gx - x) ** 2 + (gy - y) ** 2
        zz[i0:i1, j0:j1] += weight * np.exp(-dist2 * inv)
    return zz


def _sample_zz(
    xs: np.ndarray,
    ys: np.ndarray,
    zz: np.ndarray,
    x: float,
    y: float,
) -> float:
    if x < xs[0] or x > xs[-1] or y < ys[0] or y > ys[-1]:
        return 0.0
    j = int(np.searchsorted(xs, x) - 1)
    i = int(np.searchsorted(ys, y) - 1)
    j = max(0, min(len(xs) - 2, j))
    i = max(0, min(len(ys) - 2, i))
    tx = 0.0 if xs[j + 1] == xs[j] else (x - xs[j]) / (xs[j + 1] - xs[j])
    ty = 0.0 if ys[i + 1] == ys[i] else (y - ys[i]) / (ys[i + 1] - ys[i])
    return float(
        zz[i, j] * (1 - tx) * (1 - ty)
        + zz[i, j + 1] * tx * (1 - ty)
        + zz[i + 1, j] * (1 - tx) * ty
        + zz[i + 1, j + 1] * tx * ty
    )


def _region_above(
    xs: np.ndarray,
    ys: np.ndarray,
    zz: np.ndarray,
    level: float,
    clip_m: Any,
) -> Any | None:
    segs = marching_squares(xs.tolist(), ys.tolist(), zz.tolist(), level)
    lines: list[Any] = [LineString([a, b]) for a, b in segs if a != b]
    if clip_m.geom_type == "Polygon":
        lines.append(LineString(clip_m.exterior.coords))
    elif clip_m.geom_type == "MultiPolygon":
        for part in clip_m.geoms:
            lines.append(LineString(part.exterior.coords))
    if not lines:
        return None
    try:
        merged = unary_union(MultiLineString(lines))
        cands = list(polygonize(merged))
    except (ValueError, TypeError):
        cands = []
    kept = []
    for poly in cands:
        if poly.is_empty or poly.area <= 0:
            continue
        c = poly.representative_point()
        if _sample_zz(xs, ys, zz, float(c.x), float(c.y)) >= level * 0.98:
            kept.append(poly)
    if not kept:
        return _region_above_cells(xs, ys, zz, level, clip_m)
    geom = unary_union(kept).intersection(clip_m)
    return None if geom.is_empty else geom


def _region_above_cells(
    xs: np.ndarray,
    ys: np.ndarray,
    zz: np.ndarray,
    level: float,
    clip_m: Any,
) -> Any | None:
    cells: list[Any] = []
    for i in range(len(ys) - 1):
        for j in range(len(xs) - 1):
            val = (
                zz[i, j] + zz[i, j + 1] + zz[i + 1, j] + zz[i + 1, j + 1]
            ) / 4.0
            if val < level:
                continue
            cells.append(
                shapely_box(
                    float(xs[j]),
                    float(ys[i]),
                    float(xs[j + 1]),
                    float(ys[i + 1]),
                )
            )
    if not cells:
        return None
    geom = unary_union(cells)
    step = float(min(abs(xs[1] - xs[0]), abs(ys[1] - ys[0])))
    geom = geom.buffer(step * 0.7).buffer(-step * 0.55)
    geom = geom.intersection(clip_m)
    return None if geom.is_empty else geom


def _path(
    d: str,
    fill: str,
    cat: str,
    opacity: float = 1,
    fill_opacity: float | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "d": d,
        "fill": fill,
        "cat": cat,
        "opacity": opacity,
        "stroke": "none",
        "stroke_width": 0.5,
    }
    if fill_opacity is not None:
        out["fill_opacity"] = fill_opacity
    return out


def render_heat_paths(
    building_rows: list[dict[str, Any]] | None,
    clip: Any,
    origin_lat: float,
    origin_lng: float,
    scale: float,
    metric: str | None = None,
) -> list[dict[str, Any]]:
    """분석 clip 안 건물 KDE를 등치 밴드로 그린다. 발자국은 아래에."""
    hc = load_hardcode()
    sigma = float(hc.get("DENSITY_KDE_M") or 100)
    n = int(hc.get("DENSITY_GRID_N") or 64)
    n = max(36, min(80, n))
    colors = list(hc.get("DENSITY_RAMP") or RAMP_DEFAULT)
    footprint = str(hc.get("DENSITY_FOOTPRINT_COLOR") or FOOTPRINT_FILL)
    kind = normalize_heat_metric(metric)

    if building_rows is None:
        from app.services.layer_cache import clipped_buildings

        clipped = clipped_buildings(clip)
    else:
        clipped = clip_and_dedupe_buildings(building_rows, clip)
    clip_m = to_metric(clip)
    points: list[tuple[float, float, float]] = []
    footprints: list[Any] = []
    for row, geom in clipped:
        if geom.is_empty:
            continue
        footprints.append(geom)
        geom_m = to_metric(geom)
        c = geom_m.centroid
        weight = 1.0 if kind == "count" else _area_weight(row, geom_m)
        points.append((float(c.x), float(c.y), weight))

    paths: list[dict[str, Any]] = []
    base = clip_geom(clip, clip)
    if not base.is_empty:
        d0 = geom_to_svg_path(base, origin_lng, origin_lat, scale, filled=True)
        if d0:
            paths.append(_path(d0, "#faf6f0", "heat-base"))

    for geom in footprints:
        d = geom_to_svg_path(geom, origin_lng, origin_lat, scale, filled=True)
        if d:
            paths.append(_path(d, footprint, "footprint", opacity=0.9))

    if len(points) < 2:
        return paths

    minx, miny, maxx, maxy = clip_m.bounds
    pad = sigma * 0.4
    xs = np.linspace(minx - pad, maxx + pad, n)
    ys = np.linspace(miny - pad, maxy + pad, n)
    zz = _kde(xs, ys, points, sigma)
    vmax = float(zz.max())
    if vmax <= 0:
        return paths

    fractions = (0.12, 0.26, 0.40, 0.55, 0.70, 0.86)
    levels = [vmax * f for f in fractions]
    n_col = len(colors)
    for i, level in enumerate(levels):
        region = _region_above(xs, ys, zz, level, clip_m)
        if region is None:
            continue
        geom = to_lonlat(region)
        geom = clip_geom(geom, clip)
        if geom.is_empty:
            continue
        d = geom_to_svg_path(geom, origin_lng, origin_lat, scale, filled=True)
        if not d:
            continue
        fill = colors[min(i, n_col - 1)]
        paths.append(_path(d, fill, "heat", fill_opacity=0.52))
    return paths
