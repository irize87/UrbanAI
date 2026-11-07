from __future__ import annotations

import math
from typing import Any

from shapely.affinity import rotate
from shapely.geometry import Polygon, box, mapping
from shapely.ops import unary_union

from app.core.config import load_hardcode
from app.render.svg_engine import to_lonlat, to_metric
from app.services.clip import analysis_clip
from app.services.footprint import building_height_m
from app.services.layer_cache import clipped_buildings
from app.services.sim_llm import ImageMass, SimSpec, USE_CATS

_MIN_CELL_KEEP = 0.35


def _hc() -> dict[str, Any]:
    return load_hardcode()


def _as_polygon(geom: Any) -> Polygon | None:
    if geom is None or geom.is_empty:
        return None
    g = geom
    if g.geom_type == "MultiPolygon":
        g = max(g.geoms, key=lambda x: x.area)
    if g.geom_type == "GeometryCollection":
        polys = [x for x in g.geoms if x.geom_type in {"Polygon", "MultiPolygon"} and not x.is_empty]
        if not polys:
            return None
        g = unary_union(polys)
        if g.geom_type == "MultiPolygon":
            g = max(g.geoms, key=lambda x: x.area)
    if g.geom_type != "Polygon" or g.is_empty:
        return None
    return g


def polygon_from_box(box_ll: list[list[float]]) -> Polygon:
    coords = [(float(p[0]), float(p[1])) for p in box_ll if len(p) >= 2]
    if len(coords) == 2:
        (x0, y0), (x1, y1) = coords
        return box(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
    if len(coords) < 3:
        raise ValueError("선택 상자에 좌표가 부족합니다")
    if coords[0] != coords[-1]:
        coords.append(coords[0])
    poly = Polygon(coords)
    if not poly.is_valid:
        poly = poly.buffer(0)
    if poly.is_empty:
        raise ValueError("선택 상자가 비어 있습니다")
    return poly


def resolve_selection(
    *,
    lat: float,
    lng: float,
    radius_m: float,
    shape_kind: str,
    include_adjacent: bool,
    building_ids: list[int],
    box_ll: list[list[float]] | None,
) -> tuple[Any, list[tuple[dict[str, Any], Any]]]:
    """선택 폴리곤과 clip 안 건물 목록을 돌려준다."""
    clip = analysis_clip(
        lat,
        lng,
        radius_m,
        shape_kind if shape_kind in {"circle", "rect"} else "circle",
        include_adjacent=include_adjacent,
    )
    items = clipped_buildings(clip, require_height=True)
    parts: list[Any] = []
    id_set = {int(i) for i in building_ids if i is not None and int(i) > 0}
    if id_set:
        for row, geom in items:
            rid = row.get("id")
            if rid is not None and int(rid) in id_set:
                parts.append(geom)
    if box_ll:
        parts.append(polygon_from_box(box_ll))
    if not parts:
        raise ValueError("선택한 건물 또는 영역이 없습니다")
    sel = unary_union(parts)
    if sel.is_empty:
        raise ValueError("선택 영역이 비어 있습니다")
    sel = sel.intersection(clip)
    if sel.is_empty:
        raise ValueError("선택이 분석 범위 밖에 있습니다")
    return sel, items


def hide_ids_in_selection(items: list[tuple[dict[str, Any], Any]], selection: Any) -> list[int]:
    """centroid 또는 footprint가 선택과 겹치는 원본 건물 id."""
    out: list[int] = []
    for row, geom in items:
        rid = row.get("id")
        if rid is None:
            continue
        try:
            hit = False
            if not geom.is_empty and geom.intersects(selection):
                hit = True
            else:
                c = geom.centroid
                if not c.is_empty and selection.contains(c):
                    hit = True
            if hit:
                out.append(int(rid))
        except Exception:
            continue
    return out


def _inset(metric_sel: Any, setback_m: float) -> Any:
    inset = metric_sel.buffer(-max(0.0, setback_m))
    if inset.is_empty:
        inset = metric_sel.buffer(-max(0.0, setback_m * 0.4))
    if inset.is_empty:
        return metric_sel
    return inset


def _axis_align(geom: Any) -> tuple[Any, float, tuple[float, float]]:
    rect = geom.minimum_rotated_rectangle
    coords = list(rect.exterior.coords)
    dx = coords[1][0] - coords[0][0]
    dy = coords[1][1] - coords[0][1]
    angle = math.degrees(math.atan2(dy, dx))
    cx, cy = rect.centroid.x, rect.centroid.y
    aligned = rotate(geom, -angle, origin=(cx, cy))
    return aligned, angle, (cx, cy)


def _grid_cells(
    inset: Any,
    cell_w: float,
    cell_h: float,
    gap: float,
    coverage: float,
    min_area: float,
    limit: int | None = None,
) -> list[Any]:
    aligned, angle, origin = _axis_align(inset)
    minx, miny, maxx, maxy = aligned.bounds
    step_x = cell_w + gap
    step_y = cell_h + gap
    if step_x < 1 or step_y < 1:
        return []
    target = float("inf") if limit else inset.area * coverage
    placed: list[Any] = []
    area_sum = 0.0
    y = miny + gap * 0.5
    while y + cell_h <= maxy + 1e-6 and area_sum < target:
        if limit is not None and len(placed) >= limit:
            break
        x = minx + gap * 0.5
        while x + cell_w <= maxx + 1e-6 and area_sum < target:
            if limit is not None and len(placed) >= limit:
                break
            cell = box(x, y, x + cell_w, y + cell_h)
            cut = cell.intersection(aligned)
            if not cut.is_empty and cut.area >= max(min_area, cell.area * _MIN_CELL_KEEP):
                world = rotate(cut, angle, origin=origin)
                poly = _as_polygon(world)
                if poly is not None:
                    placed.append(poly)
                    area_sum += poly.area
            x += step_x
        y += step_y
    return placed


def _single_mass(inset: Any) -> list[Any]:
    poly = _as_polygon(inset)
    return [poly] if poly is not None else []


def layout_polygons(selection_ll: Any, spec: SimSpec) -> list[tuple[Any, float]]:
    """(lon/lat polygon, height_m) 목록."""
    hc = _hc()
    metric = to_metric(selection_ll)
    inset = _inset(metric, spec.setback_m)
    min_area = float(hc.get("SIM_MIN_FOOTPRINT_M2", 80))
    slab_w = float(hc.get("SIM_SLAB_WIDTH_M", 14))
    slab_l = float(hc.get("SIM_SLAB_LENGTH_M", 40))
    tower = float(hc.get("SIM_TOWER_WIDTH_M", 22))
    height = spec.height_m
    limit = spec.count
    out: list[tuple[Any, float]] = []

    if spec.typology == "single_mass":
        cells = _single_mass(inset)
        out = [(c, height) for c in cells]
    elif spec.typology == "tower_grid":
        cells = _grid_cells(inset, tower, tower, spec.gap_m, spec.coverage, min_area, limit)
        out = [(c, height) for c in cells]
    elif spec.typology == "podium_tower":
        podium_h = max(8.0, height * 0.25)
        for p in _single_mass(inset):
            out.append((p, podium_h))
        towers = _grid_cells(
            inset, tower, tower, spec.gap_m, min(0.28, spec.coverage), min_area, limit
        )
        out.extend((c, height) for c in towers)
    else:
        long_first = slab_l >= slab_w
        cw, ch = (slab_w, slab_l) if long_first else (slab_l, slab_w)
        cells = _grid_cells(inset, cw, ch, spec.gap_m, spec.coverage, min_area, limit)
        out = [(c, height) for c in cells]

    if not out:
        for p in _single_mass(inset):
            out.append((p, height))
    return [(to_lonlat(g), h) for g, h in out]


def buildings_feature_collection(
    placed: list[tuple[Any, float]],
    spec: SimSpec,
) -> dict[str, Any]:
    use_cat = spec.use_cat if spec.use_cat in USE_CATS else "미상"
    feats: list[dict[str, Any]] = []
    for i, (geom, height) in enumerate(placed, start=1):
        poly = _as_polygon(geom)
        if poly is None:
            continue
        feats.append(
            {
                "type": "Feature",
                "properties": {
                    "id": -i,
                    "use_cat": use_cat,
                    "year": None,
                    "height_m": float(height),
                    "floors": spec.floors,
                    "sgg_cd": None,
                    "sim": True,
                },
                "geometry": mapping(poly),
            }
        )
    return {"type": "FeatureCollection", "features": feats}


def layout_from_image_masses(
    selection_ll: Any,
    masses: list[ImageMass],
) -> list[tuple[Any, float, str, int | None]]:
    """정규화 0~1 ring을 선택 영역 bbox에 맞춰 (lonlat, height, use_cat, floors)."""
    hc = _hc()
    setback = float(hc.get("SIM_IMAGE_MASS_SETBACK_M", 2))
    min_area = float(hc.get("SIM_MIN_FOOTPRINT_M2", 80)) * 0.25
    metric = to_metric(selection_ll)
    inset = _inset(metric, setback) if setback > 0 else metric
    minx, miny, maxx, maxy = inset.bounds
    width = max(maxx - minx, 1.0)
    height = max(maxy - miny, 1.0)
    out: list[tuple[Any, float, str, int | None]] = []
    for mass in masses:
        coords = [(minx + p[0] * width, miny + p[1] * height) for p in mass.ring]
        if len(coords) < 4:
            continue
        poly = Polygon(coords)
        if not poly.is_valid:
            poly = poly.buffer(0)
        poly = _as_polygon(poly.intersection(inset))
        if poly is None or poly.area < min_area:
            continue
        out.append((to_lonlat(poly), mass.height_m, mass.use_cat, mass.floors))
    return out


def buildings_from_image_masses(
    placed: list[tuple[Any, float, str, int | None]],
) -> dict[str, Any]:
    feats: list[dict[str, Any]] = []
    for i, (geom, height, use_cat, floors) in enumerate(placed, start=1):
        poly = _as_polygon(geom)
        if poly is None:
            continue
        cat = use_cat if use_cat in USE_CATS else "미상"
        feats.append(
            {
                "type": "Feature",
                "properties": {
                    "id": -i,
                    "use_cat": cat,
                    "year": None,
                    "height_m": float(height),
                    "floors": floors,
                    "sgg_cd": None,
                    "sim": True,
                    "preview": True,
                },
                "geometry": mapping(poly),
            }
        )
    return {"type": "FeatureCollection", "features": feats}


def apply_image_preview(
    *,
    masses: list[ImageMass],
    lat: float,
    lng: float,
    radius_m: float,
    shape_kind: str,
    include_adjacent: bool,
    building_ids: list[int],
    box_ll: list[list[float]] | None,
) -> dict[str, Any]:
    """이미지에서 읽은 매스를 선택 영역에 올려 세션 오버레이를 만든다."""
    selection, items = resolve_selection(
        lat=lat,
        lng=lng,
        radius_m=radius_m,
        shape_kind=shape_kind,
        include_adjacent=include_adjacent,
        building_ids=building_ids,
        box_ll=box_ll,
    )
    hide_ids = hide_ids_in_selection(items, selection)
    placed = layout_from_image_masses(selection, masses)
    buildings = buildings_from_image_masses(placed)
    metric_area = to_metric(selection).area
    return {
        "hide_ids": hide_ids,
        "buildings": buildings,
        "area_m2": round(float(metric_area), 1),
        "replaced_count": len(hide_ids),
        "sim_count": len(buildings["features"]),
        "session": "image_mass",
    }


def apply_existing_edit(
    *,
    edit: dict[str, Any],
    lat: float,
    lng: float,
    radius_m: float,
    shape_kind: str,
    include_adjacent: bool,
    building_ids: list[int],
    box_ll: list[list[float]] | None,
    height_overrides: dict[str, float] | None = None,
) -> dict[str, Any]:
    """선택 건물의 바닥은 두고 높이만 바꾸거나, 그 건물을 숨긴다."""
    _selection, items = resolve_selection(
        lat=lat,
        lng=lng,
        radius_m=radius_m,
        shape_kind=shape_kind,
        include_adjacent=include_adjacent,
        building_ids=building_ids,
        box_ll=box_ll,
    )
    target_ids = hide_ids_in_selection(items, _selection)
    id_set = set(target_ids)
    overrides = height_overrides or {}
    if edit.get("remove"):
        reply = f"선택 {len(target_ids)}동을 시뮬레이션에서 숨겼습니다."
        return {
            "action": "edit",
            "hide_ids": target_ids,
            "updates": [],
            "buildings": {"type": "FeatureCollection", "features": []},
            "replaced_count": len(target_ids),
            "sim_count": 0,
            "reply_ko": reply,
        }

    updates: list[dict[str, Any]] = []
    for row, _geom in items:
        rid = row.get("id")
        if rid is None or int(rid) not in id_set:
            continue
        key = str(int(rid))
        raw_base = overrides.get(key)
        if raw_base is None:
            base = building_height_m(row)
        else:
            try:
                base = float(raw_base)
            except (TypeError, ValueError):
                base = building_height_m(row)
        if base is None:
            continue
        height = float(base)
        if edit.get("height_set_m") is not None:
            height = float(edit["height_set_m"])
        else:
            scale = edit.get("far_scale")
            if scale is not None:
                height *= float(scale)
            add = edit.get("height_add_m")
            if add is not None:
                height += float(add)
        height = max(3.0, min(400.0, round(height, 2)))
        updates.append({"id": int(rid), "height_m": height})

    bits: list[str] = []
    scale = edit.get("far_scale")
    if scale is not None:
        percent = round(abs(float(scale) - 1.0) * 100)
        direction = "낮춰" if float(scale) < 1 else "높여"
        bits.append(f"용적률 {percent}% {direction} 높이를 {float(scale):.2f}배")
    add = edit.get("height_add_m")
    if add is not None:
        bits.append(f"높이를 {float(add):+.0f}m")
    if edit.get("height_set_m") is not None:
        bits.append(f"높이를 {float(edit['height_set_m']):.0f}m로")
    change = ", ".join(bits) if bits else "높이를"
    reply = (
        f"선택 {len(updates)}동의 바닥은 그대로 두고 {change} 반영했습니다. "
        "기본 모드에서 각도를 바꿔 볼 수 있습니다."
    )
    return {
        "action": "edit",
        "hide_ids": [],
        "updates": updates,
        "buildings": {"type": "FeatureCollection", "features": []},
        "replaced_count": 0,
        "sim_count": 0,
        "reply_ko": reply,
    }


def apply_layout(
    *,
    spec: SimSpec,
    lat: float,
    lng: float,
    radius_m: float,
    shape_kind: str,
    include_adjacent: bool,
    building_ids: list[int],
    box_ll: list[list[float]] | None,
) -> dict[str, Any]:
    selection, items = resolve_selection(
        lat=lat,
        lng=lng,
        radius_m=radius_m,
        shape_kind=shape_kind,
        include_adjacent=include_adjacent,
        building_ids=building_ids,
        box_ll=box_ll,
    )
    hide_ids = hide_ids_in_selection(items, selection)
    placed = layout_polygons(selection, spec)
    buildings = buildings_feature_collection(placed, spec)
    use_mix: dict[str, int] = {}
    for row, _geom in items:
        rid = row.get("id")
        if rid is None or int(rid) not in set(hide_ids):
            continue
        cat = str(row.get("use_cat") or "미상")
        use_mix[cat] = use_mix.get(cat, 0) + 1
    metric_area = to_metric(selection).area
    return {
        "hide_ids": hide_ids,
        "buildings": buildings,
        "area_m2": round(float(metric_area), 1),
        "replaced_count": len(hide_ids),
        "use_mix": use_mix,
        "sim_count": len(buildings["features"]),
    }


def selection_summary(
    *,
    lat: float,
    lng: float,
    radius_m: float,
    shape_kind: str,
    include_adjacent: bool,
    building_ids: list[int],
    box_ll: list[list[float]] | None,
) -> dict[str, Any]:
    selection, items = resolve_selection(
        lat=lat,
        lng=lng,
        radius_m=radius_m,
        shape_kind=shape_kind,
        include_adjacent=include_adjacent,
        building_ids=building_ids,
        box_ll=box_ll,
    )
    hide_ids = hide_ids_in_selection(items, selection)
    mix: dict[str, int] = {}
    heights: list[float] = []
    for row, geom in items:
        rid = row.get("id")
        if rid is None or int(rid) not in set(hide_ids):
            continue
        cat = str(row.get("use_cat") or "미상")
        mix[cat] = mix.get(cat, 0) + 1
        h = building_height_m(row)
        if h is not None:
            heights.append(float(h))
    return {
        "building_count": len(hide_ids),
        "area_m2": round(float(to_metric(selection).area), 1),
        "use_mix": mix,
        "mean_height_m": round(sum(heights) / len(heights), 1) if heights else None,
    }
