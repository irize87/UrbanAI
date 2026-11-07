from __future__ import annotations

import re

_DONG_RE = re.compile(r".+[동가리읍면]$")


def format_vworld_address(payload: dict) -> str | None:
    """VWorld getAddress 응답에서 도로명 우선 한 줄 주소를 만든다.

    예: 서울특별시 서초구 남부순환로 2584 (서초동)
    """
    raw = (payload.get("response") or {}).get("result")
    if isinstance(raw, dict):
        results = [raw]
    elif isinstance(raw, list):
        results = raw
    else:
        return None
    if not results:
        return None

    def _type(item: dict) -> str:
        return str(item.get("type") or "").lower()

    chosen = next((item for item in results if _type(item) == "road"), None)
    if chosen is None:
        chosen = next((item for item in results if _type(item) == "parcel"), None)
    if chosen is None:
        chosen = results[0]

    text = str(chosen.get("text") or "").strip()
    if not text:
        return None

    grouped = re.search(r"^(.*?)\s*\((.+)\)\s*$", text)
    if grouped:
        base = grouped.group(1).strip()
        parts = [p.strip() for p in grouped.group(2).split(",") if p.strip()]
        dong = next((p for p in parts if _DONG_RE.match(p)), parts[0] if parts else "")
        return f"{base} ({dong})" if dong else base

    structure = chosen.get("structure") or {}
    dong = ""
    for key in ("level3", "level4A", "level4L"):
        val = str(structure.get(key) or "").strip()
        if _DONG_RE.match(val):
            dong = val
            break
    if dong:
        return f"{text} ({dong})"
    return text
