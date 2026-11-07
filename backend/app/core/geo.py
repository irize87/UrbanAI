from __future__ import annotations

from typing import Any

from app.core.config import load_hardcode
from app.core.districts import sync_bbox as union_sync_bbox


def seocho_bbox() -> dict[str, float]:
    box = load_hardcode()["SEOCHO_BBOX"]
    return {
        "min_lng": float(box["min_lng"]),
        "min_lat": float(box["min_lat"]),
        "max_lng": float(box["max_lng"]),
        "max_lat": float(box["max_lat"]),
    }


def seocho_center() -> dict[str, float]:
    c = load_hardcode()["SEOCHO_CENTER"]
    return {"lat": float(c["lat"]), "lng": float(c["lng"])}


def collection_bbox() -> dict[str, float]:
    """서초+인접 구 수집 bbox."""
    return union_sync_bbox()


def is_within_seocho(lat: float, lng: float) -> bool:
    """중심 좌표가 서초 bbox 안인지 검사."""
    b = seocho_bbox()
    return b["min_lat"] <= lat <= b["max_lat"] and b["min_lng"] <= lng <= b["max_lng"]


def require_seocho(lat: float, lng: float) -> None:
    if not is_within_seocho(lat, lng):
        raise ValueError(
            "서초구 범위 밖의 좌표입니다. 이 앱은 서울 서초구만 지원합니다."
        )


def site_summary(lat: float, lng: float, radius_m: float, shape: str) -> dict[str, Any]:
    require_seocho(lat, lng)
    return {
        "lat": lat,
        "lng": lng,
        "radius_m": radius_m,
        "shape": shape,
        "bbox": seocho_bbox(),
    }
