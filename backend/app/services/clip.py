from __future__ import annotations

"""분석 원/사각 clip 을 자치구 영역과 교집합."""

from app.db.sgg import load_sgg_region
from app.render.svg_engine import ShapeKind, make_clip


def analysis_clip(
    lat: float,
    lng: float,
    radius_m: float,
    shape: ShapeKind = "circle",
    *,
    include_adjacent: bool = False,
    width_m: float | None = None,
    height_m: float | None = None,
):
    """대지 clip ∩ (서초 | 서초+인접). 구 경계가 없으면 대지 clip만."""
    clip = make_clip(lat, lng, radius_m, shape, width_m, height_m)
    region = load_sgg_region(include_adjacent)
    if region is None or region.is_empty:
        return clip
    out = clip.intersection(region)
    if out.is_empty:
        return clip
    return out
