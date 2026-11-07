from __future__ import annotations

"""레이어 WKT를 한 번만 파싱하고, clip bbox 밖은 intersection 하지 않는다."""

import threading
from typing import Any, Iterator

from app.core.config import get_settings
from app.db.schema import connect
from app.render.svg_engine import clip_geom, wkt_to_geom

_lock = threading.Lock()
_db_key: str | None = None
_layers: dict[str, list[tuple[dict[str, Any], Any, tuple[float, float, float, float]]]] = {}
_clip_buildings: dict[tuple[Any, ...], list[Any]] = {}


def invalidate_layer_cache() -> None:
    """동기화·fixture 후 메모리 레이어를 버린다."""
    global _db_key
    with _lock:
        _layers.clear()
        _clip_buildings.clear()
        _db_key = None
    try:
        from app.services.city3d import clear_city_cache

        clear_city_cache()
    except Exception:
        pass


def bounds_overlap(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> bool:
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def _ensure_db() -> None:
    global _db_key
    path = str(get_settings().db_path)
    if _db_key != path:
        _layers.clear()
        _clip_buildings.clear()
        _db_key = path


def load_layer(
    table: str,
) -> list[tuple[dict[str, Any], Any, tuple[float, float, float, float]]]:
    _ensure_db()
    with _lock:
        hit = _layers.get(table)
        if hit is not None:
            return hit
    with connect() as conn:
        rows = [dict(x) for x in conn.execute(f"SELECT * FROM {table}").fetchall()]
    items: list[tuple[dict[str, Any], Any, tuple[float, float, float, float]]] = []
    for row in rows:
        raw = row.get("geom_wkt")
        if not raw:
            continue
        try:
            geom = wkt_to_geom(str(raw))
        except (TypeError, ValueError):
            continue
        if geom is None or geom.is_empty:
            continue
        items.append((row, geom, geom.bounds))
    with _lock:
        _layers[table] = items
        return items


def iter_clipped(table: str, clip: Any) -> Iterator[tuple[dict[str, Any], Any]]:
    """clip과 bbox가 겹치는 피처만 잘라 반환한다."""
    clip_bounds = clip.bounds
    for row, geom, bounds in load_layer(table):
        if not bounds_overlap(bounds, clip_bounds):
            continue
        out = clip_geom(geom, clip)
        if out is None or out.is_empty:
            continue
        yield row, out


def clipped_buildings(clip: Any, *, require_height: bool = False) -> list[Any]:
    """분석 clip 안 건물을 Dedup까지 해서 재사용한다."""
    from app.services.footprint import building_height_m, dedupe_overlapping_buildings

    b = clip.bounds
    key = (
        round(b[0], 6),
        round(b[1], 6),
        round(b[2], 6),
        round(b[3], 6),
        round(float(clip.area), 12),
        require_height,
    )
    with _lock:
        hit = _clip_buildings.get(key)
        if hit is not None:
            return hit
    items = []
    for row, geom in iter_clipped("buildings", clip):
        if require_height and building_height_m(row) is None:
            continue
        items.append((row, geom))
    out = dedupe_overlapping_buildings(items)
    with _lock:
        if len(_clip_buildings) > 24:
            _clip_buildings.clear()
        _clip_buildings[key] = out
        return out
