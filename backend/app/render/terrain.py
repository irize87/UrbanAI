from __future__ import annotations

"""DEM 격자 → 등고선·기복 색. 분석 clip 기준 보간."""

import math
from bisect import bisect_right
from typing import Any

from shapely.geometry import LineString, box as shapely_box

from app.core.config import load_hardcode
from app.render.svg_engine import clip_geom, geom_to_svg_path, wkt_to_geom

HYPSO_DEFAULT = [
    "#f7f4ee",
    "#eee6d6",
    "#e0d2b6",
    "#cbb892",
    "#b3946c",
    "#8f6e48",
]
CONTOUR_COLOR = "#5c4030"
BUILDING_FILL = "#f3efe6"

# bit0=SW, bit1=SE, bit2=NE, bit3=NW. edges: 0=bottom 1=right 2=top 3=left
_MS_CASES: dict[int, tuple[tuple[int, int], ...]] = {
    1: ((0, 3),),
    2: ((0, 1),),
    3: ((3, 1),),
    4: ((1, 2),),
    5: ((0, 1), (3, 2)),
    6: ((0, 2),),
    7: ((3, 2),),
    8: ((3, 2),),
    9: ((0, 2),),
    10: ((0, 3), (1, 2)),
    11: ((1, 2),),
    12: ((3, 1),),
    13: ((0, 1),),
    14: ((0, 3),),
}


def contour_interval(_relief_m: float | None = None) -> float:
    """등고선 간격(m). 기복과 무관하게 hardcode 고정값."""
    return float(load_hardcode().get("TERRAIN_CONTOUR_M") or 10)


def lerp_hex(colors: list[str], t: float) -> str:
    if t <= 0:
        return colors[0]
    if t >= 1:
        return colors[-1]
    n = len(colors) - 1
    x = t * n
    i = min(n - 1, int(x))
    f = x - i

    def rgb(h: str) -> tuple[int, int, int]:
        h = h.lstrip("#")
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)

    a, b = rgb(colors[i]), rgb(colors[i + 1])
    r = int(a[0] + (b[0] - a[0]) * f)
    g = int(a[1] + (b[1] - a[1]) * f)
    bl = int(a[2] + (b[2] - a[2]) * f)
    return f"#{r:02x}{g:02x}{bl:02x}"


def sample_elev(
    lats: list[float],
    lngs: list[float],
    grid: list[list[float]],
    lat: float,
    lng: float,
) -> float | None:
    """격자 bilinear 샘플. 범위 밖이면 None."""
    if len(lats) < 2 or len(lngs) < 2:
        return None
    if lat < lats[0] or lat > lats[-1] or lng < lngs[0] or lng > lngs[-1]:
        return None
    i = min(len(lats) - 2, max(0, bisect_right(lats, lat) - 1))
    j = min(len(lngs) - 2, max(0, bisect_right(lngs, lng) - 1))
    lat0, lat1 = lats[i], lats[i + 1]
    lng0, lng1 = lngs[j], lngs[j + 1]
    ty = 0.0 if lat1 == lat0 else (lat - lat0) / (lat1 - lat0)
    tx = 0.0 if lng1 == lng0 else (lng - lng0) / (lng1 - lng0)
    h00 = float(grid[i][j])
    h10 = float(grid[i][j + 1])
    h01 = float(grid[i + 1][j])
    h11 = float(grid[i + 1][j + 1])
    return (
        h00 * (1 - tx) * (1 - ty)
        + h10 * tx * (1 - ty)
        + h01 * (1 - tx) * ty
        + h11 * tx * ty
    )


def _edge_point(
    edge: int,
    xs: list[float],
    ys: list[float],
    i: int,
    j: int,
    h: list[float],
    level: float,
) -> tuple[float, float]:
    def t(a: float, b: float) -> float:
        if a == b:
            return 0.5
        return (level - a) / (b - a)

    if edge == 0:
        u = t(h[0], h[1])
        return xs[j] + u * (xs[j + 1] - xs[j]), ys[i]
    if edge == 1:
        u = t(h[1], h[2])
        return xs[j + 1], ys[i] + u * (ys[i + 1] - ys[i])
    if edge == 2:
        u = t(h[3], h[2])
        return xs[j] + u * (xs[j + 1] - xs[j]), ys[i + 1]
    u = t(h[0], h[3])
    return xs[j], ys[i] + u * (ys[i + 1] - ys[i])


def marching_squares(
    xs: list[float],
    ys: list[float],
    zz: list[list[float | None]],
    level: float,
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """등고 선분 (lng,lat) 쌍 목록."""
    segs: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for i in range(len(ys) - 1):
        for j in range(len(xs) - 1):
            raw = [zz[i][j], zz[i][j + 1], zz[i + 1][j + 1], zz[i + 1][j]]
            if any(v is None or (isinstance(v, float) and math.isnan(v)) for v in raw):
                continue
            h = [float(v) for v in raw]
            idx = 0
            for bit, val in enumerate(h):
                if val >= level:
                    idx |= 1 << bit
            for e0, e1 in _MS_CASES.get(idx, ()):
                p0 = _edge_point(e0, xs, ys, i, j, h, level)
                p1 = _edge_point(e1, xs, ys, i, j, h, level)
                if p0 != p1:
                    segs.append((p0, p1))
    return segs


def _path(
    d: str,
    fill: str,
    cat: str,
    opacity: float = 1,
    stroke: str = "none",
    stroke_width: float = 0.5,
) -> dict[str, Any]:
    return {
        "d": d,
        "fill": fill,
        "cat": cat,
        "opacity": opacity,
        "stroke": stroke,
        "stroke_width": stroke_width,
    }


def render_terrain_paths(
    terrain: dict[str, Any],
    clip: Any,
    origin_lat: float,
    origin_lng: float,
    scale: float,
    building_rows: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], float]:
    """분석 clip 안에서 기복 색 + 등고 + 건물 실루엣."""
    hc = load_hardcode()
    colors = list(hc.get("TERRAIN_HYPSO") or HYPSO_DEFAULT)
    contour_col = str(hc.get("TERRAIN_CONTOUR") or CONTOUR_COLOR)
    bldg_fill = str(hc.get("TERRAIN_BUILDING") or BUILDING_FILL)
    n = int(hc.get("TERRAIN_RENDER_N") or 56)
    n = max(24, min(80, n))

    lats = [float(x) for x in terrain["lats"]]
    lngs = [float(x) for x in terrain["lngs"]]
    grid = terrain["grid"]
    minx, miny, maxx, maxy = clip.bounds
    pad_x = (maxx - minx) * 0.02
    pad_y = (maxy - miny) * 0.02
    xs = [
        minx - pad_x + (maxx - minx + 2 * pad_x) * j / (n - 1) for j in range(n)
    ]
    ys = [
        miny - pad_y + (maxy - miny + 2 * pad_y) * i / (n - 1) for i in range(n)
    ]
    zz: list[list[float | None]] = []
    vals: list[float] = []
    for lat_i in ys:
        row: list[float | None] = []
        for lng_j in xs:
            h = sample_elev(lats, lngs, grid, lat_i, lng_j)
            row.append(h)
            if h is not None:
                vals.append(h)
        zz.append(row)
    if len(vals) < 4:
        return [], 0.0
    vmin, vmax = min(vals), max(vals)
    relief = vmax - vmin
    paths: list[dict[str, Any]] = []

    base = clip_geom(clip, clip)
    if not base.is_empty:
        d0 = geom_to_svg_path(base, origin_lng, origin_lat, scale, filled=True)
        if d0:
            paths.append(_path(d0, colors[0], "hyps", opacity=1))

    span = relief if relief > 1e-6 else 1.0
    for i in range(n - 1):
        for j in range(n - 1):
            cell_h = [zz[i][j], zz[i][j + 1], zz[i + 1][j], zz[i + 1][j + 1]]
            if any(v is None for v in cell_h):
                continue
            h = sum(float(v) for v in cell_h) / 4
            t = (h - vmin) / span
            fill = lerp_hex(colors, t)
            cell = shapely_box(xs[j], ys[i], xs[j + 1], ys[i + 1])
            geom = clip_geom(cell, clip)
            if geom.is_empty:
                continue
            d = geom_to_svg_path(geom, origin_lng, origin_lat, scale, filled=True)
            if d:
                paths.append(_path(d, fill, "hyps", opacity=1))

    if building_rows is None:
        from app.services.layer_cache import clipped_buildings

        for _row, geom in clipped_buildings(clip):
            d = geom_to_svg_path(geom, origin_lng, origin_lat, scale, filled=True)
            if d:
                paths.append(_path(d, bldg_fill, "footprint", opacity=0.82, stroke="none"))
    else:
        for row in building_rows:
            geom = clip_geom(wkt_to_geom(row["geom_wkt"]), clip)
            if geom.is_empty:
                continue
            d = geom_to_svg_path(geom, origin_lng, origin_lat, scale, filled=True)
            if d:
                paths.append(_path(d, bldg_fill, "footprint", opacity=0.82, stroke="none"))

    if relief >= 3:
        interval = contour_interval(relief)
        z0 = math.ceil(vmin / interval) * interval
        levels: list[float] = []
        z = z0
        while z <= vmax + 1e-6:
            if vmin < z < vmax:
                levels.append(z)
            z += interval
        for level in levels:
            thick = (
                1.35
                if abs(level / (interval * 5) - round(level / (interval * 5))) < 1e-6
                else 0.7
            )
            for (x0, y0), (x1, y1) in marching_squares(xs, ys, zz, level):
                line = LineString([(x0, y0), (x1, y1)])
                geom = clip_geom(line, clip)
                if geom.is_empty:
                    continue
                d = geom_to_svg_path(geom, origin_lng, origin_lat, scale, filled=False)
                if d:
                    paths.append(
                        _path(
                            d,
                            "none",
                            "contour",
                            stroke=contour_col,
                            stroke_width=thick,
                        )
                    )

    return paths, relief


SLOPE_BINS = (5.0, 10.0, 15.0, 20.0)
SLOPE_DEFAULT = ["#f3e6d4", "#e0c4a0", "#c49262", "#9a6238", "#5c3310"]


def _gaussian_kernel1d(sigma: float) -> list[float]:
    radius = max(1, int(math.ceil(sigma * 3)))
    s2 = 2 * sigma * sigma
    k = [math.exp(-(i * i) / s2) for i in range(-radius, radius + 1)]
    tot = sum(k) or 1.0
    return [v / tot for v in k]


def _blur2d(zz: list[list[float | None]], sigma_cells: float) -> list[list[float | None]]:
    if sigma_cells < 0.35:
        return zz
    kernel = _gaussian_kernel1d(sigma_cells)
    r = len(kernel) // 2
    n = len(zz)
    m = len(zz[0]) if n else 0
    tmp: list[list[float | None]] = [[None] * m for _ in range(n)]
    for i in range(n):
        for j in range(m):
            acc = 0.0
            wsum = 0.0
            for k, kv in enumerate(kernel):
                jj = j + k - r
                if 0 <= jj < m and zz[i][jj] is not None:
                    acc += float(zz[i][jj]) * kv
                    wsum += kv
            tmp[i][j] = acc / wsum if wsum > 0 else None
    out: list[list[float | None]] = [[None] * m for _ in range(n)]
    for i in range(n):
        for j in range(m):
            acc = 0.0
            wsum = 0.0
            for k, kv in enumerate(kernel):
                ii = i + k - r
                if 0 <= ii < n and tmp[ii][j] is not None:
                    acc += float(tmp[ii][j]) * kv
                    wsum += kv
            out[i][j] = acc / wsum if wsum > 0 else None
    return out


def slope_bucket(deg: float) -> str:
    if deg < 5:
        return "s0"
    if deg < 10:
        return "s1"
    if deg < 15:
        return "s2"
    if deg < 20:
        return "s3"
    return "s4"


def render_slope_paths(
    terrain: dict[str, Any],
    clip: Any,
    origin_lat: float,
    origin_lng: float,
    scale: float,
    accent: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    """50m 평활 경사도 색면 + 등고선."""
    from app.services.panels_meta import tint_ramp

    hc = load_hardcode()
    colors = tint_ramp(list(hc.get("SLOPE_RAMP") or SLOPE_DEFAULT), accent)
    contour_col = str(hc.get("TERRAIN_CONTOUR") or CONTOUR_COLOR)
    n = int(hc.get("TERRAIN_RENDER_N") or 56)
    n = max(24, min(80, n))
    smooth_m = float(hc.get("SLOPE_SMOOTH_M") or 52)

    lats = [float(x) for x in terrain["lats"]]
    lngs = [float(x) for x in terrain["lngs"]]
    grid = terrain["grid"]
    minx, miny, maxx, maxy = clip.bounds
    pad_x = (maxx - minx) * 0.02
    pad_y = (maxy - miny) * 0.02
    xs = [minx - pad_x + (maxx - minx + 2 * pad_x) * j / (n - 1) for j in range(n)]
    ys = [miny - pad_y + (maxy - miny + 2 * pad_y) * i / (n - 1) for i in range(n)]
    zz: list[list[float | None]] = []
    for lat_i in ys:
        row: list[float | None] = []
        for lng_j in xs:
            row.append(sample_elev(lats, lngs, grid, lat_i, lng_j))
        zz.append(row)

    mid_lat = (ys[0] + ys[-1]) / 2
    cell_x = abs(xs[1] - xs[0]) * 111320 * math.cos(mid_lat * math.pi / 180)
    cell_y = abs(ys[1] - ys[0]) * 111320
    cell_m = max(1.0, (cell_x + cell_y) / 2)
    sigma = smooth_m / cell_m
    zz_s = _blur2d(zz, sigma)

    slopes: list[list[float | None]] = [[None] * (n - 1) for _ in range(n - 1)]
    vals: list[float] = []
    for i in range(n - 1):
        for j in range(n - 1):
            corners = [zz_s[i][j], zz_s[i][j + 1], zz_s[i + 1][j], zz_s[i + 1][j + 1]]
            if any(v is None for v in corners):
                continue
            z00, z10, z01, z11 = (float(v) for v in corners)
            dzdx = ((z10 + z11) - (z00 + z01)) / (2 * max(cell_x, 1e-3))
            dzdy = ((z01 + z11) - (z00 + z10)) / (2 * max(cell_y, 1e-3))
            deg = math.degrees(math.atan(math.hypot(dzdx, dzdy)))
            slopes[i][j] = deg
            vals.append(deg)

    paths: list[dict[str, Any]] = []
    for i in range(n - 1):
        for j in range(n - 1):
            deg = slopes[i][j]
            if deg is None:
                continue
            cat = slope_bucket(deg)
            idx = {"s0": 0, "s1": 1, "s2": 2, "s3": 3, "s4": 4}[cat]
            fill = colors[min(idx, len(colors) - 1)]
            cell = shapely_box(xs[j], ys[i], xs[j + 1], ys[i + 1])
            geom = clip_geom(cell, clip)
            if geom.is_empty:
                continue
            d = geom_to_svg_path(geom, origin_lng, origin_lat, scale, filled=True)
            if d:
                paths.append(_path(d, fill, cat, opacity=1))

    if vals:
        interval = contour_interval()
        zvals = [float(v) for row in zz_s for v in row if v is not None]
        if zvals and max(zvals) - min(zvals) >= 3:
            z0 = math.ceil(min(zvals) / interval) * interval
            z = z0
            while z <= max(zvals) + 1e-6:
                if min(zvals) < z < max(zvals):
                    for (x0, y0), (x1, y1) in marching_squares(xs, ys, zz_s, z):
                        line = LineString([(x0, y0), (x1, y1)])
                        geom = clip_geom(line, clip)
                        if geom.is_empty:
                            continue
                        d = geom_to_svg_path(
                            geom, origin_lng, origin_lat, scale, filled=False
                        )
                        if d:
                            paths.append(
                                _path(d, "none", "contour", stroke=contour_col, stroke_width=0.7)
                            )
                z += interval
        stats = {
            "max_deg": round(max(vals), 1),
            "mean_deg": round(sum(vals) / len(vals), 1),
            "smooth_m": smooth_m,
        }
    else:
        stats = {"max_deg": 0.0, "mean_deg": 0.0, "smooth_m": smooth_m}
    return paths, stats

