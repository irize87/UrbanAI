"""지면 일조시간. 높이맵 + 재귀 러닝맥스 그림자(수평선 지도 없음)."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

import numpy as np
from shapely.geometry import Point

from app.core.config import load_hardcode
from app.render.svg_engine import to_metric
from app.services.solar import (
    day_length_hours,
    preset_civil_date,
    sun_samples,
    true_solar_window_std,
)

FLOOR_H = 3.25
RAMP_HOURS = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 12.0]
RAMP_DEFAULT = [
    "#243868",
    "#2f4d86",
    "#3d6aa8",
    "#5b86c0",
    "#7a9cc4",
    "#c4bc78",
    "#e0b844",
    "#e8c14a",
    "#f0cc4e",
    "#f3d45a",
]
BUILDING_FILL = "#e8e6e0"


def building_height_m(row: dict[str, Any]) -> float | None:
    if row.get("height_m"):
        try:
            h = float(row["height_m"])
        except (TypeError, ValueError):
            h = 0.0
        if h > 0.2:
            return h
    floors = row.get("floors")
    if floors:
        try:
            n = float(floors)
        except (TypeError, ValueError):
            return None
        if n > 0:
            return n * FLOOR_H
    return None


def _hex_ramp() -> list[str]:
    hc = load_hardcode()
    raw = hc.get("SUN_HOURS_RAMP")
    if isinstance(raw, list) and len(raw) >= 2:
        return [str(x) for x in raw]
    return list(RAMP_DEFAULT)


def lerp_hex(colors: list[str], t: float) -> str:
    t = min(1.0, max(0.0, t))
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


def hours_to_color(hours: float, vmax: float | None = None) -> str:
    cap = vmax if vmax and vmax > 0 else 12.0
    scaled = hours * (12.0 / cap)
    t = min(1.0, max(0.0, hours / cap))
    stops = RAMP_HOURS
    colors = _hex_ramp()
    if len(colors) != len(stops):
        return lerp_hex(colors, t)
    if scaled <= stops[0]:
        return colors[0]
    if scaled >= stops[-1]:
        return colors[-1]
    for i in range(len(stops) - 1):
        if stops[i] <= scaled <= stops[i + 1]:
            span = stops[i + 1] - stops[i]
            u = 0.0 if span <= 0 else (scaled - stops[i]) / span
            return lerp_hex([colors[i], colors[i + 1]], u)
    return colors[-1]


def rasterize_heightmap(
    buildings: list[tuple[Any, float]],
    origin_lng: float,
    origin_lat: float,
    radius_m: float,
    n: int,
) -> tuple[np.ndarray, float]:
    """clip 정사각(2r)을 n×n 높이맵으로. 행 증가 = 남쪽."""
    from PIL import Image, ImageDraw

    n = max(24, min(384, int(n)))
    cell = (radius_m * 2) / n
    grid = np.zeros((n, n), dtype=np.float32)
    ox, oy = to_metric(Point(origin_lng, origin_lat)).coords[0]
    half = radius_m

    def to_px(x: float, y: float) -> tuple[float, float]:
        j = (x - (ox - half)) / cell
        i = ((oy + half) - y) / cell
        return j, i

    for geom, h in buildings:
        if h is None or h <= 0.2 or geom is None or geom.is_empty:
            continue
        geom_m = to_metric(geom)
        parts = list(geom_m.geoms) if geom_m.geom_type == "MultiPolygon" else [geom_m]
        for part in parts:
            if part.is_empty or part.geom_type != "Polygon":
                continue
            coords = [to_px(x, y) for x, y in part.exterior.coords]
            if len(coords) < 3:
                continue
            mask = Image.new("L", (n, n), 0)
            draw = ImageDraw.Draw(mask)
            draw.polygon(coords, fill=255)
            for hole in part.interiors:
                hole_xy = [to_px(x, y) for x, y in hole.coords]
                if len(hole_xy) >= 3:
                    draw.polygon(hole_xy, fill=0)
            hit = np.array(mask) > 0
            if hit.any():
                grid[hit] = np.maximum(grid[hit], float(h))
    return grid, cell


def _shadow_lit(
    height: np.ndarray,
    cell_m: float,
    alt_deg: float,
    az_deg: float,
) -> np.ndarray:
    """지면(높이 0)이 볕이면 True. 태양 반대 방향 러닝맥스."""
    n = height.shape[0]
    lit = np.ones((n, n), dtype=bool)
    if alt_deg <= 0.12:
        lit[:] = False
        return lit
    tan_a = math.tan(math.radians(alt_deg))
    sdi = math.cos(math.radians(az_deg))
    sdj = -math.sin(math.radians(az_deg))
    length = math.hypot(sdi, sdj) or 1.0
    step_i = sdi / length
    step_j = sdj / length
    starts: list[tuple[float, float]] = []
    for k in range(n):
        if sdi >= 0:
            starts.append((-0.5, k + 0.5))
        if sdi <= 0:
            starts.append((n - 0.5, k + 0.5))
        if sdj >= 0:
            starts.append((k + 0.5, -0.5))
        if sdj <= 0:
            starts.append((k + 0.5, n - 0.5))
    seen = np.zeros((n, n), dtype=np.uint8)
    for si, sj in starts:
        ray = 0.0
        i, j = si, sj
        guard = 0
        max_steps = n * 3
        while guard < max_steps:
            guard += 1
            i += step_i
            j += step_j
            ii = int(math.floor(i))
            jj = int(math.floor(j))
            if ii < 0 or jj < 0 or ii >= n or jj >= n:
                if ii < -1 or jj < -1 or ii > n or jj > n:
                    break
                ray = max(0.0, ray - cell_m * tan_a)
                continue
            ray = max(0.0, ray - cell_m * tan_a)
            if seen[ii, jj] == 0:
                lit[ii, jj] = ray <= 0.04
                seen[ii, jj] = 1
            h = float(height[ii, jj])
            if h > ray:
                ray = h
    missing = seen == 0
    if missing.any():
        lit[missing] = True
    roof = height > 0.2
    lit[roof] = False
    return lit


def accumulate_hours(
    height: np.ndarray,
    cell_m: float,
    lat: float,
    lng: float,
    dt: datetime,
    step_min: float = 10.0,
) -> tuple[np.ndarray, list[dict[str, float]]]:
    samples = sun_samples(lat, lng, dt, step_min=step_min)
    hours = np.zeros(height.shape, dtype=np.float32)
    dt_h = step_min / 60.0
    meta: list[dict[str, float]] = []
    for pos in samples:
        lit = _shadow_lit(height, cell_m, pos.altitude_deg, pos.azimuth_deg)
        hours += lit.astype(np.float32) * dt_h
        meta.append(
            {
                "alt": pos.altitude_deg,
                "az": pos.azimuth_deg,
                "tst": pos.true_solar_minutes,
            }
        )
    return hours, meta


def consecutive_and_total(
    height: np.ndarray,
    cell_m: float,
    lat: float,
    lng: float,
    dt: datetime,
    step_min: float = 10.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list]:
    """hours, 진태양시 09–15 최장 연속, 08–16 총합."""
    samples = sun_samples(lat, lng, dt, step_min=step_min)
    dt_h = step_min / 60.0
    n = height.shape[0]
    hours = np.zeros((n, n), dtype=np.float32)
    run = np.zeros((n, n), dtype=np.float32)
    best = np.zeros((n, n), dtype=np.float32)
    total4 = np.zeros((n, n), dtype=np.float32)
    for pos in samples:
        lit = _shadow_lit(height, cell_m, pos.altitude_deg, pos.azimuth_deg)
        hours += lit.astype(np.float32) * dt_h
        tst = pos.true_solar_minutes
        if 9.0 * 60.0 <= tst <= 15.0 * 60.0:
            run = np.where(lit, run + dt_h, 0.0)
            best = np.maximum(best, run)
        else:
            run[:] = 0.0
        if 8.0 * 60.0 <= tst <= 16.0 * 60.0:
            total4 += lit.astype(np.float32) * dt_h
    roof = height > 0.2
    hours[roof] = 0.0
    best[roof] = 0.0
    total4[roof] = 0.0
    return hours, best, total4, samples


def summarize(
    hours: np.ndarray,
    height: np.ndarray,
    consec2: np.ndarray,
    total4: np.ndarray,
    lat: float,
    lng: float,
    dt: datetime,
) -> dict[str, Any]:
    ground = height <= 0.2
    vals = hours[ground]
    if vals.size == 0:
        vals = hours.ravel()
    mean_h = float(vals.mean()) if vals.size else 0.0
    below1 = float((vals < 1.0).mean() * 100.0) if vals.size else 0.0
    share2 = float((consec2[ground] >= 2.0).mean() * 100.0) if vals.size else 0.0
    share4 = float((total4[ground] >= 4.0).mean() * 100.0) if vals.size else 0.0
    length = day_length_hours(lat, lng, dt)
    std2 = true_solar_window_std(lat, lng, dt, 9.0, 15.0)
    return {
        "day_length_h": round(length, 2),
        "mean_h": round(mean_h, 2),
        "below_1h_pct": round(below1, 1),
        "consec_2h_pct": round(share2, 1),
        "total_4h_pct": round(share4, 1),
        "std_09": std2[0],
        "std_15": std2[1],
    }


def compute_sun_hours(
    buildings: list[tuple[Any, float | None]],
    origin_lng: float,
    origin_lat: float,
    radius_m: float,
    dt: datetime | None = None,
    n: int | None = None,
) -> dict[str, Any]:
    hc = load_hardcode()
    grid_n = int(n or hc.get("SUN_HOURS_GRID_N") or 384)
    step = float(hc.get("SUN_HOURS_STEP_MIN") or 10)
    kind = str(hc.get("SUN_HOURS_DATE") or "winter")
    when = dt or preset_civil_date(kind)
    erected = [(g, h) for g, h in buildings if h is not None]
    height, cell = rasterize_heightmap(
        erected, origin_lng, origin_lat, radius_m, grid_n
    )
    hours, consec2, total4, _samples = consecutive_and_total(
        height, cell, origin_lat, origin_lng, when, step_min=step
    )
    stats = summarize(hours, height, consec2, total4, origin_lat, origin_lng, when)
    return {
        "hours": hours,
        "height": height,
        "cell_m": cell,
        "consec2": consec2,
        "total4": total4,
        "stats": stats,
        "date": when.isoformat(),
    }


def hours_grid_to_png(
    hours: np.ndarray,
    height: np.ndarray,
    vmax: float,
    size_px: int = 800,
) -> bytes:
    from io import BytesIO

    from PIL import Image

    n, m = hours.shape
    roof_h = BUILDING_FILL.lstrip("#")
    roof = (int(roof_h[0:2], 16), int(roof_h[2:4], 16), int(roof_h[4:6], 16))
    img = Image.new("RGB", (m, n), roof)
    px = img.load()
    for i in range(n):
        for j in range(m):
            if height[i, j] > 0.2:
                px[j, i] = roof
                continue
            hex_c = hours_to_color(float(hours[i, j]), vmax)
            r = int(hex_c[1:3], 16)
            g = int(hex_c[3:5], 16)
            b = int(hex_c[5:7], 16)
            px[j, i] = (r, g, b)
    out_n = max(n, int(size_px))
    if out_n != n:
        img = img.resize((out_n, out_n), Image.Resampling.NEAREST)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def geom_list_from_rows(rows: list[tuple[dict, Any]]) -> list[tuple[Any, float | None]]:
    out: list[tuple[Any, float | None]] = []
    for row, geom in rows:
        out.append((geom, building_height_m(row)))
    return out
