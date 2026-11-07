from __future__ import annotations

"""서울열린데이터광장 가로수 위치 → 로컬 SQLite."""

import asyncio
import logging
import defusedxml.ElementTree as ET
from typing import Any
from urllib.parse import quote

import httpx
from shapely.geometry import Point

from app.core.config import get_settings, load_hardcode
from app.core.districts import district_defs
from app.db.schema import connect, init_db
from app.services.layer_cache import invalidate_layer_cache

logger = logging.getLogger(__name__)

OK_CODES = {"INFO-000"}
EMPTY_CODES = {"INFO-200"}


def street_tree_url(
    api_key: str,
    start: int,
    end: int,
    cgg: str,
    *,
    file_type: str | None = None,
) -> str:
    """OpenAPI 요청 URL. 키는 경로에만 넣고 로그에 남기지 않는다."""
    hc = load_hardcode()
    base = str(hc.get("SEOUL_OPEN_API_BASE", "http://openapi.seoul.go.kr:8088")).rstrip(
        "/"
    )
    service = str(hc.get("SEOUL_STREET_TREE_SERVICE", "streetTreeInfo"))
    kind = file_type or str(hc.get("SEOUL_STREET_TREE_TYPE", "json"))
    cgg_q = quote(cgg, safe="")
    return f"{base}/{api_key}/{kind}/{service}/{start}/{end}/{cgg_q}"


def _text(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        raw = row.get(key)
        if raw is None:
            continue
        text = str(raw).strip()
        if text:
            return text
    return ""


def normalize_lonlat(lot: Any, lat: Any) -> tuple[float, float] | None:
    """LOT=경도, LAT=위도. 좌우가 바뀐 값만 교정한다."""
    try:
        lng = float(lot)
        lat_v = float(lat)
    except (TypeError, ValueError):
        return None
    if 30.0 < lng < 45.0 and 120.0 < lat_v < 135.0:
        lng, lat_v = lat_v, lng
    if not (126.0 < lng < 128.0 and 37.0 < lat_v < 38.5):
        return None
    return lng, lat_v


def _rows_from_xml(root: ET.Element) -> tuple[int, str, list[dict[str, Any]]]:
    total_el = root.find("list_total_count")
    total = int(total_el.text) if total_el is not None and total_el.text else 0
    code_el = root.find("RESULT/CODE")
    code = (code_el.text or "").strip() if code_el is not None else ""
    rows: list[dict[str, Any]] = []
    for node in root.findall("row"):
        item = {child.tag: (child.text or "") for child in list(node)}
        if item:
            rows.append(item)
    return total, code, rows


def parse_street_tree_payload(payload: Any) -> tuple[int, str, list[dict[str, Any]]]:
    """JSON dict 또는 XML 문자열 → (총건수, 결과코드, row 목록)."""
    if isinstance(payload, str):
        text = payload.strip()
        if text.startswith("<"):
            return _rows_from_xml(ET.fromstring(text))
        raise ValueError("가로수 응답이 JSON/XML이 아닙니다")
    if not isinstance(payload, dict):
        raise ValueError("가로수 응답 형식이 아닙니다")
    block = payload.get("streetTreeInfo")
    if isinstance(block, dict):
        data = block
    else:
        data = payload
    result = data.get("RESULT") or {}
    code = str(result.get("CODE") or "").strip()
    try:
        total = int(data.get("list_total_count") or 0)
    except (TypeError, ValueError):
        total = 0
    raw_rows = data.get("row") or []
    if isinstance(raw_rows, dict):
        rows = [raw_rows]
    elif isinstance(raw_rows, list):
        rows = [r for r in raw_rows if isinstance(r, dict)]
    else:
        rows = []
    return total, code, rows


def row_to_db_tuple(
    row: dict[str, Any],
    sgg_cd: str | None,
) -> tuple | None:
    """OpenAPI row → street_trees INSERT 튜플."""
    coords = normalize_lonlat(row.get("LOT"), row.get("LAT"))
    if coords is None:
        return None
    lng, lat = coords
    cgg = _text(row, "CGG")
    rte = _text(row, "RTE")
    trspc = _text(row, "TRSPC")
    road = _text(row, "ROAD_NM_ADDR")
    lotno = _text(row, "LOTNO_ADDR")
    return (
        cgg or None,
        rte,
        trspc,
        road,
        lotno,
        lng,
        lat,
        Point(lng, lat).wkt,
        "seoul:streetTreeInfo",
        sgg_cd,
    )


def _log(layer: str, source: str, count: int, status: str, message: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO sync_log(layer, source, feature_count, status, message) "
            "VALUES (?, ?, ?, ?, ?)",
            (layer, source, count, status, message[:500]),
        )
        conn.commit()


def _cgg_targets() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for row in district_defs():
        name = str(row.get("name") or "").strip()
        code = str(row.get("code") or "").strip()
        if name and code:
            out.append((name, code))
    return out


async def fetch_cgg_rows(
    client: httpx.AsyncClient,
    api_key: str,
    cgg: str,
    page: int,
) -> list[dict[str, Any]]:
    """자치구 가로수를 페이지 단위로 모두 받는다."""
    start = 1
    collected: list[dict[str, Any]] = []
    total: int | None = None
    while True:
        end = start + page - 1
        url = street_tree_url(api_key, start, end, cgg)
        resp = await client.get(url)
        if resp.status_code != 200:
            raise RuntimeError(f"가로수 HTTP {resp.status_code} ({cgg})")
        text = resp.text.lstrip()
        if text.startswith("<"):
            total_n, code, batch = parse_street_tree_payload(text)
        else:
            try:
                total_n, code, batch = parse_street_tree_payload(resp.json())
            except ValueError as exc:
                raise RuntimeError(f"가로수 비JSON 응답 ({cgg}): {text[:160]}") from exc
        if code in EMPTY_CODES:
            break
        if code not in OK_CODES:
            raise RuntimeError(f"가로수 API {code} ({cgg})")
        collected.extend(batch)
        if total is None:
            total = total_n
        if not batch or (total is not None and len(collected) >= total) or len(batch) < page:
            break
        start = end + 1
        await asyncio.sleep(0.05)
    return collected


async def sync_street_trees() -> dict[str, Any]:
    """서초+인접 구 가로수를 받아 street_trees를 통째로 교체한다."""
    settings = get_settings()
    if not settings.has_seoul_open_key:
        msg = "SEOUL_OPEN_API_KEY 없음"
        return {
            "layer": "street_trees",
            "status": "skipped",
            "message": msg,
            "feature_count": 0,
        }

    init_db()

    hc = load_hardcode()
    page = int(hc.get("SEOUL_STREET_TREE_PAGE", 1000))
    page = max(1, min(page, 1000))
    targets = _cgg_targets()
    if not targets:
        raise RuntimeError("DISTRICTS가 비어 있습니다")

    api_key = settings.seoul_open_api_key.strip()
    tuples: list[tuple] = []
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        parts = await asyncio.gather(
            *[fetch_cgg_rows(client, api_key, name, page) for name, _code in targets]
        )

    seen: set[tuple[float, float, str]] = set()
    name_to_cd = {name: code for name, code in targets}
    for rows in parts:
        for row in rows:
            cgg = _text(row, "CGG")
            sgg_cd = name_to_cd.get(cgg)
            item = row_to_db_tuple(row, sgg_cd)
            if item is None:
                continue
            key = (round(item[5], 7), round(item[6], 7), item[2])
            if key in seen:
                continue
            seen.add(key)
            tuples.append(item)

    with connect() as conn:
        conn.execute("DELETE FROM street_trees")
        conn.executemany(
            """
            INSERT INTO street_trees(
                cgg, rte, trspc, road_nm_addr, lotno_addr,
                lng, lat, geom_wkt, source, sgg_cd
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            tuples,
        )
        conn.commit()
    invalidate_layer_cache()
    districts = ",".join(name for name, _code in targets)
    msg = f"{districts} 가로수 적재"
    _log("street_trees", "seoul:streetTreeInfo", len(tuples), "ok", msg)
    return {
        "layer": "street_trees",
        "status": "ok",
        "message": msg,
        "feature_count": len(tuples),
    }
