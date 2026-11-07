from __future__ import annotations

"""서초(주 분석) + 인접 자치구(강남·동작·관악) 코드/bbox."""

from typing import Any

from app.core.config import load_hardcode

PRIMARY_SGG = "11650"


def district_defs() -> list[dict[str, Any]]:
    return list(load_hardcode()["DISTRICTS"])


def primary_sgg_cd() -> str:
    for row in district_defs():
        if row.get("kind") == "primary":
            return str(row["code"])
    return PRIMARY_SGG


def adjacent_sgg_cds() -> list[str]:
    return [str(row["code"]) for row in district_defs() if row.get("kind") == "adjacent"]


def all_sgg_cds() -> set[str]:
    return {str(row["code"]) for row in district_defs()}


def sgg_name(code: str) -> str:
    for row in district_defs():
        if str(row["code"]) == code:
            return str(row["name"])
    return code


def district_bboxes() -> list[dict[str, float]]:
    out: list[dict[str, float]] = []
    for row in district_defs():
        box = row["bbox"]
        out.append(
            {
                "min_lng": float(box["min_lng"]),
                "min_lat": float(box["min_lat"]),
                "max_lng": float(box["max_lng"]),
                "max_lat": float(box["max_lat"]),
            }
        )
    return out


def sync_bbox() -> dict[str, float]:
    """수집용 합집합 bbox (서초+인접)."""
    boxes = district_bboxes()
    return {
        "min_lng": min(b["min_lng"] for b in boxes),
        "min_lat": min(b["min_lat"] for b in boxes),
        "max_lng": max(b["max_lng"] for b in boxes),
        "max_lat": max(b["max_lat"] for b in boxes),
    }


def extract_sgg_cd(props: dict[str, Any]) -> str | None:
    """VWorld/OSM 속성에서 시군구 코드 추출. 수집 대상이 아니면 None."""
    codes = all_sgg_cds()
    for key in (
        "col_adm_se",
        "sig_cd",
        "std_sggcd",
        "sigungu_cd",
        "sgg_cd",
        "sggcd",
        "sggCd",
        "sigunguCd",
        "gu_cd",
        "bjcd",
        "col_adm_sect_cd",
        "adm_sect_cd",
        "sigungu",
        "sig_cde",
    ):
        raw = str(props.get(key) or "").strip()
        if raw[:5] in codes:
            return raw[:5]
    for key in ("pnu", "bldg_pnu", "PNU", "li_pnu"):
        raw = str(props.get(key) or "").strip()
        if raw[:5] in codes:
            return raw[:5]
    blob = " ".join(
        str(props.get(k) or "") for k in ("sigg_name", "sig_nm", "addr", "sgg_nm")
    )
    for row in district_defs():
        short = str(row["name"]).replace("구", "")
        if short and short in blob:
            return str(row["code"])
    return None


def is_in_scope(props: dict[str, Any]) -> bool:
    return extract_sgg_cd(props) is not None
