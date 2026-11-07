from __future__ import annotations

from functools import lru_cache
from typing import Any

from shapely.geometry import mapping

from app.core.config import load_hardcode
from app.core.geo import require_seocho
from app.db.schema import connect
from app.render.svg_engine import (
    ShapeKind,
    geom_to_svg_path,
    wrap_svg,
)
from app.render.heat import render_heat_paths
from app.render.terrain import render_slope_paths, render_terrain_paths
from app.services.clip import analysis_clip
from app.services.layer_cache import clipped_buildings, iter_clipped
from app.services.panels_meta import (
    AGE_COLORS,
    age_bucket,
    age_ramp,
    panel_by_key,
)

USE_COLORS = {
    c["key"]: c["color"] for c in (panel_by_key("use") or {}).get("cats") or []
}
ZONE_COLORS = {
    c["key"]: c["color"] for c in (panel_by_key("zoning") or {}).get("cats") or []
}
GREEN_COLORS = {
    c["key"]: c["color"] for c in (panel_by_key("green") or {}).get("cats") or []
}
# 연한 녹지 위에 지정 공원이 겹치도록 (샘플의 두 초록 레이어)
GREEN_DRAW_ORDER = {"water": 0, "sea": 0, "other": 1, "park": 2}
DISTRICT_COLORS = {
    c["key"]: c["color"] for c in (panel_by_key("district") or {}).get("cats") or []
}
PLANNED_COLORS = {
    c["key"]: c["color"] for c in (panel_by_key("planned") or {}).get("cats") or []
}
TRANSIT_COLORS = {
    c["key"]: c["color"] for c in (panel_by_key("transit") or {}).get("cats") or []
}


def _scale(radius_m: float, size_px: int = 800) -> float:
    return size_px / (radius_m * 2)


@lru_cache(maxsize=1)
def _road_tables() -> tuple[dict[str, float], frozenset[str]]:
    hc = load_hardcode()
    widths = {str(k): float(v) for k, v in (hc.get("ROAD_WIDTH_M") or {}).items()}
    skip = frozenset(str(x) for x in (hc.get("ROAD_SKIP") or []))
    return widths, skip


def road_stroke_px(road_class: str | None, scale: float) -> float | None:
    """OSM highway 등급 → SVG stroke(px). 보행 전용 등은 None(생략)."""
    widths, skip = _road_tables()
    key = (road_class or "").strip()
    if not key or key in skip:
        return None
    width_m = widths.get(key)
    if width_m is None:
        return None
    return max(1.15, width_m * scale)


def _building_height(row: dict) -> float | None:
    if row.get("height_m"):
        return float(row["height_m"])
    floors = row.get("floors")
    if floors:
        return float(floors) * 3.25
    return None


GHOST_FILL = "#ececec"


def _darken_hex(hex_color: str, amount: float = 0.32) -> str:
    """채우기 색을 어둡게 해 구역 구분선으로 쓴다."""
    raw = hex_color.lstrip("#")
    if len(raw) == 3:
        raw = "".join(ch * 2 for ch in raw)
    if len(raw) != 6:
        return hex_color
    rgb = [int(raw[i : i + 2], 16) for i in (0, 2, 4)]
    return "#" + "".join(f"{max(0, int(c * (1 - amount))):02x}" for c in rgb)


def _append_road_paths(
    paths: list[dict],
    clip: Any,
    lng: float,
    lat: float,
    scale: float,
    stroke: str,
    cat: str,
) -> None:
    """OSM 도로를 같은 굵기 계층으로 path에 추가. 얇은 길 먼저."""
    roads: list[dict] = []
    for row, geom in iter_clipped("roads", clip):
        sw = road_stroke_px(row.get("road_class"), scale)
        if sw is None:
            continue
        d = geom_to_svg_path(geom, lng, lat, scale, filled=False)
        if not d:
            continue
        roads.append(
            _path(d, "none", cat, stroke=stroke, stroke_width=round(sw, 2))
        )
    roads.sort(key=lambda p: float(p.get("stroke_width") or 0))
    paths.extend(roads)


def _append_ghost_buildings(
    paths: list[dict],
    clip: Any,
    lng: float,
    lat: float,
    scale: float,
) -> None:
    """건물 폴리곤을 배경용 옅은 회색으로 path 맨 앞에 추가."""
    fill = str(load_hardcode().get("CONTEXT_BUILDING_COLOR") or GHOST_FILL)
    ghosts: list[dict] = []
    for _row, geom in clipped_buildings(clip):
        d = geom_to_svg_path(geom, lng, lat, scale, filled=True)
        if not d:
            continue
        ghosts.append(_path(d, fill, "ghost", stroke="none"))
    paths[0:0] = ghosts


def _path(
    d: str,
    fill: str,
    cat: str,
    opacity: float = 1,
    stroke: str = "none",
    stroke_width: float = 0.5,
    fill_opacity: float | None = None,
    join: str | None = None,
) -> dict:
    out = {
        "d": d,
        "fill": fill,
        "cat": cat,
        "opacity": opacity,
        "stroke": stroke,
        "stroke_width": stroke_width,
    }
    if fill_opacity is not None:
        out["fill_opacity"] = fill_opacity
    if join:
        out["join"] = join
    return out


def render_panel(
    panel: str,
    lat: float,
    lng: float,
    radius_m: float = 800,
    shape: ShapeKind = "circle",
    width_m: float | None = None,
    height_m: float | None = None,
    threshold: float | None = None,
    color: str | None = None,
    include_adjacent: bool = False,
    metric: str | None = None,
) -> dict[str, Any]:
    require_seocho(lat, lng)
    clip = analysis_clip(
        lat,
        lng,
        radius_m,
        shape,
        include_adjacent=include_adjacent,
        width_m=width_m,
        height_m=height_m,
    )
    scale = _scale(radius_m)
    meta = panel_by_key(panel)
    title = (meta or {}).get("ko") or panel
    accent = color or (meta or {}).get("color")

    def _svg(paths: list[dict], note: str | None = None) -> dict[str, Any]:
        svg = wrap_svg(
            paths,
            lat,
            lng,
            radius_m,
            shape,
            title,
            width_m,
            height_m,
            clip_id=f"clip-{panel}",
            message=note if not paths else None,
        )
        out: dict[str, Any] = {
            "panel": panel,
            "svg": svg,
            "feature_count": len(paths),
            "clip": mapping(clip),
        }
        if note:
            out["note"] = note
        return out

    paths: list[dict] = []

    if panel == "aerial":
        from base64 import b64encode

        from app.services.aerial_mosaic import fetch_satellite_mosaic, mosaic_to_panel_png

        minx, miny, maxx, maxy = clip.bounds
        mosaic = fetch_satellite_mosaic(
            minx, miny, maxx, maxy, radius_m=radius_m
        )
        if mosaic is None:
            return _svg([], "항공사진 타일을 받지 못했습니다")
        png = mosaic_to_panel_png(
            mosaic, clip, lng, lat, radius_m, shape_kind=shape
        )
        href = "data:image/png;base64," + b64encode(png).decode("ascii")
        vw = radius_m * scale
        paths.append(
            {
                "kind": "image",
                "href": href,
                "x": -vw,
                "y": -vw,
                "width": vw * 2,
                "height": vw * 2,
                "cat": "aerial",
            }
        )
        out = _svg(paths)
        out["note"] = "촬영 시기 불명 · VWorld 항공영상"
        return out


    if panel == "heat":
        paths = render_heat_paths(
            None,
            clip,
            lat,
            lng,
            scale,
            metric=metric,
        )
        return _svg(paths)

    if panel in {"buildings", "use", "age", "high", "lowrise"}:
        thr = threshold
        if panel == "high":
            thr = thr if thr is not None else 35
        if panel == "lowrise":
            thr = thr if thr is not None else 12
        ramp = age_ramp(color) if panel == "age" else None

        for row, geom in clipped_buildings(clip):
            h = _building_height(row)
            d = geom_to_svg_path(geom, lng, lat, scale, filled=True)
            if not d:
                continue
            if panel == "buildings":
                paths.append(_path(d, accent or "#000000", "main"))
            elif panel == "use":
                cat = row.get("use_cat") or "미상"
                paths.append(_path(d, USE_COLORS.get(cat, GHOST_FILL), cat))
            elif panel == "age":
                cat = age_bucket(row.get("year"))
                if cat == "미상":
                    fill = AGE_COLORS["미상"]
                elif ramp:
                    labels = [
                        "~1979",
                        "1980년대",
                        "1990년대",
                        "2000년대",
                        "2010년대",
                        "2020~",
                    ]
                    fill = ramp[labels.index(cat)] if cat in labels else AGE_COLORS[cat]
                else:
                    fill = AGE_COLORS[cat]
                paths.append(_path(d, fill, cat))
            elif panel == "high":
                if h is None or h < thr:
                    paths.append(_path(d, GHOST_FILL, "other"))
                else:
                    paths.append(_path(d, accent or "#4169e1", "main"))
            elif panel == "lowrise":
                if h is None or h > thr:
                    paths.append(_path(d, GHOST_FILL, "other"))
                else:
                    paths.append(_path(d, accent or "#4ecdc4", "main"))

        paths.sort(key=lambda p: 0 if p.get("cat") in {"미상", "other"} else 1)

    elif panel == "zoning":
        for row, geom in iter_clipped("zoning", clip):
            cat = row.get("zone_cat") or "기타"
            paths.append(
                _path(
                    geom_to_svg_path(geom, lng, lat, scale),
                    ZONE_COLORS.get(cat, "#e0e0e0"),
                    cat,
                    opacity=0.85,
                )
            )
        road_col = str(load_hardcode().get("ZONING_ROAD_COLOR") or "#ffffff")
        _append_road_paths(paths, clip, lng, lat, scale, road_col, "road")

    elif panel == "green":
        _append_ghost_buildings(paths, clip, lng, lat, scale)
        green_items = sorted(
            iter_clipped("green", clip),
            key=lambda it: GREEN_DRAW_ORDER.get(it[0].get("kind") or "park", 1),
        )
        for row, geom in green_items:
            cat = row.get("kind") or "park"
            paths.append(
                _path(
                    geom_to_svg_path(geom, lng, lat, scale),
                    GREEN_COLORS.get(cat, "#7ac943"),
                    cat,
                    opacity=0.9,
                )
            )

    elif panel == "roads":
        _append_road_paths(
            paths, clip, lng, lat, scale, accent or "#38bdf8", "main"
        )

    elif panel == "transit":
        road_col = str(load_hardcode().get("TRANSIT_ROAD_COLOR") or "#c4c4c4")
        _append_road_paths(paths, clip, lng, lat, scale, road_col, "road")
        for row, geom in iter_clipped("transit", clip):
            cat = "subway" if row.get("kind") == "subway" else "bus"
            col = TRANSIT_COLORS.get(cat, "#38bdf8")
            d = geom_to_svg_path(geom, lng, lat, scale, filled=True)
            if not d:
                continue
            paths.append(
                _path(
                    d,
                    col,
                    cat,
                    stroke="#ffffff",
                    stroke_width=1.2,
                )
            )

    elif panel == "parcels":
        for row, geom in iter_clipped("parcels", clip):
            paths.append(
                _path(
                    geom_to_svg_path(geom, lng, lat, scale),
                    "none",
                    "main",
                    stroke=accent or "#d08466",
                    stroke_width=0.8,
                )
            )

    elif panel == "block":
        # 피겨그라운드: 대지 덩어리 + 흰 도로. 필지 경계가 아님.
        fill = accent or "#e8825f"
        land = geom_to_svg_path(clip, lng, lat, scale, filled=True)
        if land:
            paths.append(_path(land, fill, "main", stroke="none"))
        for row, geom in iter_clipped("green", clip):
            if (row.get("kind") or "") not in {"water", "sea"}:
                continue
            d = geom_to_svg_path(geom, lng, lat, scale, filled=True)
            if d:
                paths.append(_path(d, "#ffffff", "void", stroke="none"))
        road_col = str(load_hardcode().get("BLOCK_ROAD_COLOR") or "#ffffff")
        _append_road_paths(paths, clip, lng, lat, scale, road_col, "road")

    elif panel == "district":
        _append_ghost_buildings(paths, clip, lng, lat, scale)
        for row, geom in iter_clipped("district", clip):
            cat = row.get("kind") or "duz"
            fill = DISTRICT_COLORS.get(cat, "#2ab3a6")
            stroke_w = float(load_hardcode().get("DISTRICT_STROKE_PX") or 1.6)
            paths.append(
                _path(
                    geom_to_svg_path(geom, lng, lat, scale, filled=True),
                    fill,
                    cat,
                    stroke=_darken_hex(fill),
                    stroke_width=stroke_w,
                    fill_opacity=0.4,
                )
            )

    elif panel == "planned":
        _append_ghost_buildings(paths, clip, lng, lat, scale)
        for row, geom in iter_clipped("planned", clip):
            cat = row.get("kind") or "edu"
            paths.append(
                _path(
                    geom_to_svg_path(geom, lng, lat, scale),
                    PLANNED_COLORS.get(cat, "#3f6fb7"),
                    cat,
                    opacity=0.7,
                )
            )

    elif panel == "terrain":
        import json

        with connect() as conn:
            row = conn.execute(
                "SELECT value FROM meta WHERE key='terrain_grid'"
            ).fetchone()
        if not row:
            return _svg([], "지형·등고선 (동기화 필요 · Copernicus DEM)")
        terrain = json.loads(row["value"])
        city_relief = float(terrain.get("relief_m") or 0)
        if city_relief < 3:
            out = _svg([], f"지형·등고선 (평지, 기복 {city_relief:.1f}m)")
            out["relief_m"] = city_relief
            return out
        paths, local_relief = render_terrain_paths(
            terrain,
            clip,
            lat,
            lng,
            scale,
            building_rows=None,
        )
        out = _svg(paths)
        out["relief_m"] = local_relief
        return out

    elif panel == "slope":
        import json as _json

        with connect() as conn:
            row = conn.execute(
                "SELECT value FROM meta WHERE key='terrain_grid'"
            ).fetchone()
        if not row:
            return _svg([], "경사도 (동기화 필요 · Copernicus DEM)")
        terrain = _json.loads(row["value"])
        paths, stats = render_slope_paths(terrain, clip, lat, lng, scale, accent)
        if not paths:
            return _svg([], "경사도 (표고 격자 부족)")
        out = _svg(paths)
        out["caption"] = (
            f"{stats['smooth_m']:.0f}m 평활 · 최대 {stats['max_deg']}° · "
            f"평균 {stats['mean_deg']}°"
        )
        return out

    elif panel == "plot_size":
        from app.render.svg_engine import to_metric
        from app.services.panels_meta import PLOT_SIZE_RAMP, plot_size_bucket, tint_ramp

        hc = load_hardcode()
        ramp = tint_ramp(PLOT_SIZE_RAMP, color)
        skip = set(str(x) for x in (hc.get("PLOT_SIZE_JIMOK_SKIP") or list("도천구제철")))
        gray = str(hc.get("PLOT_SIZE_VOID") or "#d6d6d6")
        stroke_w = float(hc.get("PLOT_SIZE_STROKE") or 1.45)
        land = geom_to_svg_path(clip, lng, lat, scale, filled=True)
        if land:
            paths.append(_path(land, gray, "void", stroke="none"))
        for row, geom in iter_clipped("parcels", clip):
            d = geom_to_svg_path(geom, lng, lat, scale, filled=True)
            if not d:
                continue
            jimok = (row.get("jimok") or "").strip()
            token = jimok[-1] if jimok else ""
            if token in skip:
                paths.append(
                    _path(d, gray, "void", stroke="#ffffff", stroke_width=stroke_w, join="miter")
                )
                continue
            area = float(to_metric(geom).area)
            cat, idx = plot_size_bucket(area)
            fill = ramp[min(idx, len(ramp) - 1)]
            paths.append(
                _path(d, fill, cat, stroke="#ffffff", stroke_width=stroke_w, join="miter")
            )
        return _svg(paths)

    elif panel == "sun_hours":
        from base64 import b64encode

        from app.render.sun_hours import compute_sun_hours, hours_grid_to_png
        from app.services.solar import preset_civil_date

        hc = load_hardcode()
        kind = str(hc.get("SUN_HOURS_DATE") or "winter")
        default_h = float(hc.get("SUN_HOURS_DEFAULT_H") or 9.75)
        rows = list(clipped_buildings(clip))
        built = [(geom, _building_height(row) or default_h) for row, geom in rows]
        result = compute_sun_hours(built, lng, lat, radius_m, dt=preset_civil_date(kind))
        vmax = max(float(result["stats"]["day_length_h"]), 1.0)
        png = hours_grid_to_png(result["hours"], result["height"], vmax, size_px=800)
        href = "data:image/png;base64," + b64encode(png).decode("ascii")
        vw = radius_m * scale
        paths.append(
            {
                "kind": "image",
                "href": href,
                "x": -vw,
                "y": -vw,
                "width": vw * 2,
                "height": vw * 2,
                "cat": "sun",
                "pixelated": True,
            }
        )
        out = _svg(paths)
        st = result["stats"]
        season = "동지" if kind == "winter" else kind
        out["caption"] = (
            f"{season} · 낮 {st['day_length_h']}h · 평균 {st['mean_h']}h · "
            f"연속2h {st['consec_2h_pct']}% · 총4h {st['total_4h_pct']}%"
        )
        out["stats"] = st
        return out

    elif panel == "trees":
        from app.render.trees import detect_trees, merge_tree_stems, street_tree_stems, tree_paths
        from app.services.aerial_mosaic import fetch_satellite_mosaic

        fill = accent or "#3d8c4a"
        canopy_fill = "#b5d0a0"
        for cat in (meta or {}).get("cats") or []:
            if cat.get("key") == "canopy" and cat.get("color"):
                canopy_fill = str(cat["color"])
            if cat.get("key") == "tree" and cat.get("color"):
                fill = str(cat["color"])
        green_rows = list(iter_clipped("green", clip))
        water = [g for r, g in green_rows if (r.get("kind") or "") in {"water", "sea"}]
        parks = [g for r, g in green_rows if (r.get("kind") or "") in {"park", "other"}]
        _append_ghost_buildings(paths, clip, lng, lat, scale)
        street = street_tree_stems(clip)
        try:
            minx, miny, maxx, maxy = clip.bounds
            mosaic = fetch_satellite_mosaic(minx, miny, maxx, maxy, radius_m=radius_m)
            canopies, hsv_stems = ([], [])
            if mosaic is not None:
                bgeoms = [g for _r, g in clipped_buildings(clip)]
                canopies, hsv_stems = detect_trees(
                    mosaic, lng, lat, radius_m, bgeoms, water, parks
                )
        except Exception:
            canopies, hsv_stems = [], []
        stems = merge_tree_stems(hsv_stems, street)
        paths.extend(
            tree_paths(canopies, stems, lng, lat, scale, fill, canopy_fill)
        )
        if not any(p.get("cat") in {"canopy", "tree"} for p in paths):
            return _svg([], "수목 — 검출된 수관이 없습니다")
        return _svg(paths)

    else:
        raise ValueError(f"알 수 없는 패널: {panel}")

    return _svg(paths)
