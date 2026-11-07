from __future__ import annotations

"""VWorld WFS → 로컬 SQLite 월간 동기화."""

import logging
from datetime import datetime, timezone
from typing import Any, Callable
import asyncio

import httpx
from shapely.geometry import Polygon, shape
from shapely.ops import unary_union
from shapely import wkt as shapely_wkt

from app.core.config import get_settings, load_hardcode
from app.core.districts import (
    adjacent_sgg_cds,
    all_sgg_cds,
    district_bboxes,
    extract_sgg_cd,
    is_in_scope,
    primary_sgg_cd,
    sgg_name,
)
from app.core.geo import collection_bbox
from app.db.schema import connect, init_db
from app.db.sgg import backfill_sgg
from app.sync.copernicus_dem import download_cdse_dem
from app.sync.street_trees import sync_street_trees

logger = logging.getLogger(__name__)

# 건축법 별표1 대분류(앞 2자리). VWorld usability는 01000 형태.
# 패널에 없는 중분류는 가장 가까운 범주로만 묶고, 코드가 없으면 미상.
_USE_CODE_MAP_DEFAULT: dict[str, str] = {
    "01": "주거",
    "02": "주거",
    "03": "근린생활",
    "04": "근린생활",
    "05": "문화집회",
    "06": "문화집회",
    "07": "판매",
    "08": "기타",
    "09": "교육복지",
    "10": "교육복지",
    "11": "교육복지",
    "12": "교육복지",
    "13": "문화집회",
    "14": "업무",
    "15": "숙박",
    "16": "기타",
    "17": "공업",
    "18": "공업",
    "19": "공업",
    "20": "기타",
    "21": "기타",
    "22": "기타",
    "23": "기타",
    "24": "기타",
    "25": "공업",
    "26": "기타",
    "27": "기타",
    "28": "기타",
    "29": "기타",
}

# 시설명에 유형이 직접 있을 때만. 단지명·용도지역으로 추정하지 않음.
USE_NAME_HINTS: tuple[tuple[str, str], ...] = (
    ("다세대", "주거"),
    ("다가구", "주거"),
    ("아파트", "주거"),
    ("연립", "주거"),
    ("단독주택", "주거"),
    ("공동주택", "주거"),
    ("빌라", "주거"),
    ("주택", "주거"),
    ("오피스텔", "업무"),
    ("오피스", "업무"),
    ("사무실", "업무"),
    ("어린이집", "교육복지"),
    ("유치원", "교육복지"),
    ("초등학교", "교육복지"),
    ("중학교", "교육복지"),
    ("고등학교", "교육복지"),
    ("대학교", "교육복지"),
    ("복지관", "교육복지"),
    ("학교", "교육복지"),
    ("대학", "교육복지"),
    ("병원", "교육복지"),
    ("의원", "교육복지"),
    ("봉은사", "문화집회"),
    ("호텔", "숙박"),
    ("모텔", "숙박"),
    ("여관", "숙박"),
    ("백화점", "판매"),
    ("마트", "판매"),
    ("공장", "공업"),
    ("창고", "공업"),
    ("상가", "근린생활"),
    ("선교회", "문화집회"),
    ("예배당", "문화집회"),
    ("종교시설", "문화집회"),
    ("사찰", "문화집회"),
    ("성전", "문화집회"),
    ("교회", "문화집회"),
    ("성당", "문화집회"),
)
_USE_NAME_HINTS_SORTED = tuple(
    sorted(USE_NAME_HINTS, key=lambda item: len(item[0]), reverse=True)
)

_USE_OFFICIAL_NAMES: tuple[tuple[str, str], ...] = (
    ("교육연구시설", "교육복지"),
    ("노유자시설", "교육복지"),
    ("의료시설", "교육복지"),
    ("수련시설", "교육복지"),
    ("문화및집회시설", "문화집회"),
    ("문화 및 집회시설", "문화집회"),
    ("종교시설", "문화집회"),
    ("운동시설", "문화집회"),
    ("판매시설", "판매"),
    ("업무시설", "업무"),
    ("숙박시설", "숙박"),
    ("근린생활시설", "근린생활"),
    ("창고시설", "공업"),
    ("위험물", "공업"),
    ("발전시설", "공업"),
)

# 자연환경보전을 녹지보다 먼저 본다. "자연녹지"는 녹지 범주다.


class SyncError(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _bbox_tiles(step: float = 0.01) -> list[str]:
    """수집 대상 구 bbox를 작은 타일로 분할 (ymin,xmin,ymax,xmax)."""
    tiles: list[str] = []
    seen: set[str] = set()
    for b in district_bboxes():
        lat = b["min_lat"]
        while lat < b["max_lat"] - 1e-12:
            lng = b["min_lng"]
            lat2 = min(lat + step, b["max_lat"])
            while lng < b["max_lng"] - 1e-12:
                lng2 = min(lng + step, b["max_lng"])
                key = f"{lat:.5f},{lng:.5f},{lat2:.5f},{lng2:.5f}"
                if key not in seen:
                    seen.add(key)
                    tiles.append(f"{lat},{lng},{lat2},{lng2}")
                lng = lng2
            lat = lat2
    return tiles


def use_code_map() -> dict[str, str]:
    """hardcode USE_CODE_MAP + 기본값."""
    out = dict(_USE_CODE_MAP_DEFAULT)
    raw = load_hardcode().get("USE_CODE_MAP") or {}
    if isinstance(raw, dict):
        for key, val in raw.items():
            k = str(key).strip()
            if k.isdigit():
                k = k.zfill(2)
            out[k] = str(val)
    return out


def map_use_cat(usability: str | None) -> str:
    """GIS 주용도코드·시설명 → 패널 범주. 코드·명칭이 없으면 미상."""
    if not usability:
        return "미상"
    raw = str(usability).strip()
    if not raw or raw in {".", "-"}:
        return "미상"
    if len(raw) >= 2 and raw[:2].isdigit():
        mapped = use_code_map().get(raw[:2])
        if mapped:
            return mapped
    compact = raw.replace(" ", "")
    for hint, cat in _USE_OFFICIAL_NAMES:
        if hint.replace(" ", "") in compact:
            return cat
    for hint, cat in _USE_NAME_HINTS_SORTED:
        if hint in raw:
            return cat
    return "미상"


def remap_building_use_cats() -> dict[str, Any]:
    """기존 buildings.use_cat을 현재 매핑으로 다시 채운다. 추정하지 않음."""
    with connect() as conn:
        conn.execute("PRAGMA busy_timeout=120000")
        rows = conn.execute("SELECT id, use_raw FROM buildings").fetchall()
        updates = [(map_use_cat(r["use_raw"]), r["id"]) for r in rows]
        conn.executemany("UPDATE buildings SET use_cat = ? WHERE id = ?", updates)
        conn.commit()
    _log("buildings", "remap_use", len(updates), "ok", "용도 범주 재분류")
    return {"layer": "buildings", "status": "ok", "feature_count": len(updates)}


def _use_from_zone(zone_cat: str, floors: Any) -> str | None:
    if zone_cat == "주거":
        return "주거"
    if zone_cat == "공업":
        return "공업"
    if zone_cat == "상업":
        try:
            fl = int(floors) if floors not in (None, "") else 0
        except (TypeError, ValueError):
            fl = 0
        return "업무" if fl >= 8 else "근린생활"
    return None


def fill_missing_use_from_zoning() -> int:
    """용도 미상 건물을 용도지역(주거·상업·공업)으로 채움."""
    from shapely import STRtree

    from app.render.svg_engine import wkt_to_geom

    with connect() as conn:
        conn.execute("PRAGMA busy_timeout=120000")
        zones = [
            dict(r)
            for r in conn.execute(
                "SELECT zone_cat, geom_wkt FROM zoning "
                "WHERE zone_cat IN ('주거', '상업', '공업')"
            ).fetchall()
        ]
        bldgs = [
            dict(r)
            for r in conn.execute(
                "SELECT id, floors, geom_wkt FROM buildings "
                "WHERE use_cat = '미상' OR use_cat IS NULL"
            ).fetchall()
        ]
    geoms: list[Any] = []
    cats: list[str] = []
    for zone in zones:
        try:
            geom = wkt_to_geom(zone["geom_wkt"])
        except (TypeError, ValueError):
            continue
        if geom is None or geom.is_empty:
            continue
        geoms.append(geom)
        cats.append(str(zone["zone_cat"]))
    if not geoms or not bldgs:
        return 0
    tree = STRtree(geoms)
    updates: list[tuple[str, int]] = []
    for bldg in bldgs:
        try:
            footprint = wkt_to_geom(bldg["geom_wkt"])
        except (TypeError, ValueError):
            continue
        if footprint is None or footprint.is_empty:
            continue
        pt = footprint.centroid
        hits = tree.query(pt, predicate="covers")
        if hits.size == 0:
            hits = tree.query(pt, predicate="intersects")
        if hits.size == 0:
            continue
        mapped = _use_from_zone(cats[int(hits[0])], bldg.get("floors"))
        if mapped:
            updates.append((mapped, int(bldg["id"])))
    if updates:
        with connect() as conn:
            conn.execute("PRAGMA busy_timeout=120000")
            conn.executemany(
                "UPDATE buildings SET use_cat = ? WHERE id = ?", updates
            )
            conn.commit()
        _log("buildings", "zoning_fill", len(updates), "ok", "미상 용도를 용도지역으로 채움")
    return len(updates)


def map_osm_green_kind(
    tags: dict[str, Any] | None,
    mapping: dict[str, Any] | None = None,
) -> str | None:
    """OSM 태그 → park|other|water. 매칭 없으면 None.

    water → park → other 순으로 본다. 지정 공원은 숲 태그보다 park가 이긴다.
    """
    tags = tags or {}
    mapping = mapping if mapping is not None else (load_hardcode().get("OSM_GREEN_KIND") or {})
    for kind in ("water", "park", "other"):
        keys = mapping.get(kind) or {}
        if not isinstance(keys, dict):
            continue
        for key, values in keys.items():
            allowed = {str(v) for v in (values or [])}
            val = tags.get(key)
            if val is not None and str(val) in allowed:
                return kind
    return None


def _overpass_tag_query(
    bbox: dict[str, float],
    key: str,
    values: set[str] | list[str],
) -> str:
    """단일 OSM 키(leisure/landuse/natural 등) Overpass 면 쿼리."""
    south = bbox["min_lat"]
    west = bbox["min_lng"]
    north = bbox["max_lat"]
    east = bbox["max_lng"]
    bb = f"({south},{west},{north},{east})"
    regex = "^(" + "|".join(sorted(str(v) for v in values)) + ")$"
    return (
        "[out:json][timeout:90];\n"
        "(\n"
        f'  way["{key}"~"{regex}"]{bb};\n'
        f'  relation["{key}"~"{regex}"]{bb};\n'
        ");\n"
        "out geom;\n"
    )


def _osm_green_keys(mapping: dict[str, Any]) -> dict[str, set[str]]:
    by_key: dict[str, set[str]] = {}
    for kind_map in mapping.values():
        if not isinstance(kind_map, dict):
            continue
        for key, values in kind_map.items():
            by_key.setdefault(str(key), set()).update(str(v) for v in (values or []))
    return by_key


def _overpass_green_query(bbox: dict[str, float], mapping: dict[str, Any]) -> str:
    """테스트·디버그용 통합 쿼리. 실제 sync는 키별로 나눈다."""
    south = bbox["min_lat"]
    west = bbox["min_lng"]
    north = bbox["max_lat"]
    east = bbox["max_lng"]
    bb = f"({south},{west},{north},{east})"
    lines: list[str] = []
    for key, values in sorted(_osm_green_keys(mapping).items()):
        regex = "^(" + "|".join(sorted(values)) + ")$"
        for osm_type in ("way", "relation"):
            lines.append(f'  {osm_type}["{key}"~"{regex}"]{bb};')
    body = "\n".join(lines)
    return (
        "[out:json][timeout:240];\n"
        "(\n"
        f"{body}\n"
        ");\n"
        "out geom;\n"
    )


async def _fetch_osm_green_elements(
    bbox: dict[str, float],
    mapping: dict[str, Any],
) -> list[dict[str, Any]]:
    """녹지 OSM을 태그 키별로 나눠 받아 504를 피한다."""
    elements: list[dict[str, Any]] = []
    seen: set[tuple[Any, Any]] = set()
    for key, values in sorted(_osm_green_keys(mapping).items()):
        data = await _overpass(_overpass_tag_query(bbox, key, values))
        added = 0
        for el in data.get("elements", []):
            eid = (el.get("type"), el.get("id"))
            if eid in seen:
                continue
            seen.add(eid)
            elements.append(el)
            added += 1
        logger.info("OSM green %s: %s elements", key, added)
    return elements


def map_zone_cat(uname: str | None) -> str:
    name = uname or ""
    if "자연환경" in name:
        return "자연환경"
    if "주거" in name:
        return "주거"
    if "상업" in name:
        return "상업"
    if "공업" in name:
        return "공업"
    if "녹지" in name:
        return "녹지"
    if "관리" in name:
        return "관리"
    if "농림" in name:
        return "농림"
    return "기타"


def parse_year(useapr_day: str | None) -> int | None:
    if not useapr_day:
        return None
    s = str(useapr_day).strip()[:4]
    if s.isdigit():
        year = int(s)
        if 1800 <= year <= 2100:
            return year
    return None


def geom_to_wkt(geom_obj: dict[str, Any] | None) -> str | None:
    if not geom_obj:
        return None
    try:
        g = shape(geom_obj)
        if g.is_empty:
            return None
        if not g.is_valid:
            g = g.buffer(0)
        return g.wkt
    except Exception:
        return None


def is_seocho_building(props: dict[str, Any]) -> bool:
    """하위호환: 수집 대상 4개 구."""
    return is_in_scope(props)


def is_seocho_zoning(props: dict[str, Any]) -> bool:
    return is_in_scope(props)


async def fetch_vworld_wfs(
    typename: str,
    bbox: str,
    max_features: int = 1000,
) -> dict[str, Any]:
    settings = get_settings()
    if not settings.has_vworld_key:
        raise SyncError(
            "VWORLD_API_KEY가 없습니다. https://www.vworld.kr 에서 인증키를 발급하세요."
        )
    hc = load_hardcode()
    wfs_url = hc.get("VWORLD_WFS_URL", "https://api.vworld.kr/req/wfs")
    params = {
        "service": "WFS",
        "version": "1.1.0",
        "request": "GetFeature",
        "typename": typename,
        "output": "application/json",
        "srsname": "EPSG:4326",
        "bbox": bbox,
        "maxFeatures": str(max_features),
        "key": settings.vworld_api_key,
        "domain": settings.vworld_domain,
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.get(wfs_url, params=params)
        if resp.status_code != 200:
            raise SyncError(f"WFS HTTP {resp.status_code}: {resp.text[:200]}")
        ctype = resp.headers.get("content-type", "")
        if "json" not in ctype and not resp.text.lstrip().startswith("{"):
            raise SyncError(f"WFS 비JSON 응답 ({typename}): {resp.text[:200]}")
        return resp.json()


async def fetch_tile_features(
    typename: str,
    bbox: str,
    max_features: int = 1000,
    depth: int = 0,
) -> list[dict[str, Any]]:
    """타일 조회. 잘리면 4분할 재귀."""
    data = await fetch_vworld_wfs(typename, bbox, max_features=max_features)
    matched = int(data.get("numberMatched") or 0)
    feats = list(data.get("features") or [])
    if matched <= len(feats) or depth >= 4:
        if matched > len(feats):
            logger.warning(
                "%s tile %s still truncated matched=%s returned=%s depth=%s",
                typename,
                bbox,
                matched,
                len(feats),
                depth,
            )
        return feats

    ymin, xmin, ymax, xmax = [float(x) for x in bbox.split(",")]
    lat_mid = (ymin + ymax) / 2
    lng_mid = (xmin + xmax) / 2
    sub = [
        f"{ymin},{xmin},{lat_mid},{lng_mid}",
        f"{ymin},{lng_mid},{lat_mid},{xmax}",
        f"{lat_mid},{xmin},{ymax},{lng_mid}",
        f"{lat_mid},{lng_mid},{ymax},{xmax}",
    ]
    out: list[dict[str, Any]] = []
    for tile in sub:
        out.extend(
            await fetch_tile_features(typename, tile, max_features=max_features, depth=depth + 1)
        )
    return out


async def iter_features(
    typename: str,
    max_features: int = 1000,
    tile_step: float = 0.01,
) -> list[dict[str, Any]]:
    all_feats: list[dict[str, Any]] = []
    seen: set[str] = set()
    tiles = _bbox_tiles(tile_step)
    logger.info("%s tiles=%s step=%s", typename, len(tiles), tile_step)
    for i, tile in enumerate(tiles, start=1):
        if i == 1 or i % 20 == 0 or i == len(tiles):
            logger.info("%s tile %s/%s", typename, i, len(tiles))
        feats = await fetch_tile_features(typename, tile, max_features=max_features)
        for f in feats:
            props = f.get("properties") or {}
            key = (
                str(
                    props.get("ufid")
                    or props.get("pk")
                    or props.get("mnum")
                    or props.get("present_sn")
                    or props.get("geoidn")
                    or props.get("bd_mgt_sn")
                    or ""
                )
                + "|"
                + str(props.get("pnu") or props.get("dgm_nm") or "")
                + "|"
                + str((f.get("geometry") or {}).get("type") or "")
                + "|"
                + str(hash(str(f.get("geometry"))[:200]))
            )
            if key in seen:
                continue
            seen.add(key)
            all_feats.append(f)
    return all_feats


def _log(layer: str, source: str, count: int, status: str, message: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO sync_log(layer, source, feature_count, status, message) "
            "VALUES (?, ?, ?, ?, ?)",
            (layer, source, count, status, message[:500]),
        )
        conn.commit()


async def sync_buildings() -> dict[str, Any]:
    feats = await iter_features("lt_c_bldginfo", max_features=1000, tile_step=0.008)
    rows: list[tuple] = []
    for f in feats:
        props = f.get("properties") or {}
        if not is_in_scope(props):
            continue
        sgg_cd = extract_sgg_cd(props) or primary_sgg_cd()
        geom_wkt = geom_to_wkt(f.get("geometry"))
        if not geom_wkt:
            continue
        floors = props.get("grnd_flr")
        try:
            floors_i = int(floors) if floors not in (None, "") else None
        except (TypeError, ValueError):
            floors_i = None
        height = props.get("height")
        try:
            height_f = float(height) if height not in (None, "", 0, "0") else None
        except (TypeError, ValueError):
            height_f = None
        gfa = props.get("totalarea")
        try:
            gfa_f = float(gfa) if gfa not in (None, "") else None
        except (TypeError, ValueError):
            gfa_f = None
        rows.append(
            (
                str(props.get("ufid") or props.get("geoidn") or ""),
                map_use_cat(props.get("usability")),
                str(props.get("usability") or ""),
                parse_year(props.get("useapr_day")),
                floors_i,
                height_f,
                gfa_f,
                geom_wkt,
                "vworld:lt_c_bldginfo",
                sgg_cd,
            )
        )

    with connect() as conn:
        conn.execute("DELETE FROM buildings")
        conn.executemany(
            """
            INSERT INTO buildings(
                ext_id, use_cat, use_raw, year, floors, height_m, gfa_m2, geom_wkt, source, sgg_cd
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
    names = ", ".join(sgg_name(c) for c in [primary_sgg_cd(), *adjacent_sgg_cds()])
    _log("buildings", "vworld:lt_c_bldginfo", len(rows), "ok", f"{names} 건물 적재 완료")
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
            ("data_mode", "synced"),
        )
        conn.commit()
    return {"layer": "buildings", "status": "ok", "feature_count": len(rows)}


async def sync_buildings_road_fill() -> dict[str, Any]:
    """도로명주소 건물로 GIS에 없는 footprint만 보강."""
    from shapely import STRtree

    from app.render.svg_engine import wkt_to_geom
    from app.services.footprint import geom_overlaps_existing

    feats = await iter_features("lt_c_spbd", max_features=1000, tile_step=0.01)
    with connect() as conn:
        existing = {
            r["ext_id"]
            for r in conn.execute(
                "SELECT ext_id FROM buildings WHERE ext_id IS NOT NULL"
            )
        }
        existing_geoms: list[Any] = []
        for r in conn.execute("SELECT geom_wkt FROM buildings"):
            try:
                geom = wkt_to_geom(r["geom_wkt"])
            except (TypeError, ValueError):
                continue
            if geom is None or geom.is_empty:
                continue
            existing_geoms.append(geom)
        tree = STRtree(existing_geoms) if existing_geoms else None
        added = 0
        for f in feats:
            props = f.get("properties") or {}
            if not is_in_scope(props):
                continue
            sgg_cd = extract_sgg_cd(props) or primary_sgg_cd()
            ext = str(props.get("bd_mgt_sn") or props.get("pk") or "")
            if not ext or ext in existing:
                continue
            geom_wkt = geom_to_wkt(f.get("geometry"))
            if not geom_wkt:
                continue
            try:
                new_geom = wkt_to_geom(geom_wkt)
            except (TypeError, ValueError):
                continue
            if tree and geom_overlaps_existing(new_geom, tree, existing_geoms):
                continue
            floors = props.get("gro_flo_co")
            try:
                floors_i = int(floors) if floors not in (None, "") else None
            except (TypeError, ValueError):
                floors_i = None
            conn.execute(
                """
                INSERT INTO buildings(
                    ext_id, use_cat, use_raw, year, floors, height_m, gfa_m2, geom_wkt, source, sgg_cd
                ) VALUES (?, ?, ?, NULL, ?, NULL, NULL, ?, 'vworld:lt_c_spbd', ?)
                """,
                (
                    ext,
                    map_use_cat(str(props.get("buld_nm") or "")),
                    str(props.get("buld_nm") or ""),
                    floors_i,
                    geom_wkt,
                    sgg_cd,
                ),
            )
            existing.add(ext)
            added += 1
        conn.commit()
    _log("buildings_road", "vworld:lt_c_spbd", added, "ok", "신축 보강 추가")
    return {"layer": "buildings_road", "status": "ok", "feature_count": added}


async def sync_zoning() -> dict[str, Any]:
    feats = await iter_features("lt_c_uq111", max_features=1000, tile_step=0.015)
    rows = []
    for f in feats:
        props = f.get("properties") or {}
        geom_wkt = geom_to_wkt(f.get("geometry"))
        if not geom_wkt:
            continue
        uname = props.get("uname") or props.get("dgm_nm")
        rows.append(
            (
                map_zone_cat(uname),
                str(uname or ""),
                geom_wkt,
                "vworld:lt_c_uq111",
                extract_sgg_cd(props),
            )
        )
    with connect() as conn:
        conn.execute("DELETE FROM zoning")
        conn.executemany(
            "INSERT INTO zoning(zone_cat, zone_name, geom_wkt, source, sgg_cd) "
            "VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    backfill_sgg("zoning")
    _log("zoning", "vworld:lt_c_uq111", len(rows), "ok", "용도지역 적재")
    return {"layer": "zoning", "status": "ok", "feature_count": len(rows)}


async def sync_sgg_bounds() -> dict[str, Any]:
    """시군구 경계(서초·강남·동작·관악). 3D 인접 구 클립용."""
    from shapely.geometry import box as shapely_box

    from app.core.districts import district_defs

    rows: list[tuple] = []
    try:
        feats = await iter_features("lt_c_adsigg", max_features=1000, tile_step=0.04)
        for f in feats:
            props = f.get("properties") or {}
            code = extract_sgg_cd(props)
            if not code:
                continue
            geom_wkt = geom_to_wkt(f.get("geometry"))
            if not geom_wkt:
                continue
            kind = "primary" if code == primary_sgg_cd() else "adjacent"
            rows.append((code, sgg_name(code), kind, geom_wkt))
    except Exception as exc:
        logger.warning("lt_c_adsigg 실패: %s", exc)

    if not rows:
        for d in district_defs():
            b = d["bbox"]
            wkt = shapely_box(
                float(b["min_lng"]),
                float(b["min_lat"]),
                float(b["max_lng"]),
                float(b["max_lat"]),
            ).wkt
            rows.append((str(d["code"]), str(d["name"]), str(d["kind"]), wkt))
        src = "bbox_fallback"
        msg = "시군구 경계 bbox 근사"
    else:
        src = "vworld:lt_c_adsigg"
        msg = "시군구 경계 적재"

    with connect() as conn:
        conn.execute("DELETE FROM sgg_bounds")
        conn.executemany(
            "INSERT INTO sgg_bounds(sgg_cd, name, kind, geom_wkt) VALUES (?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    _log("sgg", src, len(rows), "ok", msg)
    return {"layer": "sgg", "status": "ok", "feature_count": len(rows)}


async def sync_green() -> dict[str, Any]:
    """도시자연공원구역(VWorld) + OSM 공원·숲·잔디·정원·하천."""
    rows: list[tuple] = []
    feats = await iter_features("lt_c_uq162", max_features=1000, tile_step=0.02)
    for f in feats:
        props = f.get("properties") or {}
        if str(props.get("std_sggcd") or "") and not is_in_scope(props):
            continue
        geom_wkt = geom_to_wkt(f.get("geometry"))
        if not geom_wkt:
            continue
        rows.append(
            (
                "park",
                str(props.get("uname") or props.get("alias") or "공원·녹지"),
                geom_wkt,
                "vworld:lt_c_uq162",
                extract_sgg_cd(props),
            )
        )

    mapping = load_hardcode().get("OSM_GREEN_KIND") or {}
    for el in await _fetch_osm_green_elements(collection_bbox(), mapping):
        tags = el.get("tags") or {}
        kind = map_osm_green_kind(tags, mapping)
        if not kind:
            continue
        geom_wkt = _osm_polygon_wkt(el)
        if not geom_wkt:
            continue
        rows.append((kind, tags.get("name") or kind, geom_wkt, "osm:overpass", None))

    with connect() as conn:
        conn.execute("DELETE FROM green")
        conn.executemany(
            "INSERT INTO green(kind, name, geom_wkt, source, sgg_cd) VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    backfill_sgg("green")
    _log("green", "vworld+osm", len(rows), "ok", "녹지·하천 적재")
    return {"layer": "green", "status": "ok", "feature_count": len(rows)}


async def sync_district() -> dict[str, Any]:
    feats = await iter_features("lt_c_upisuq161", max_features=1000, tile_step=0.02)
    rows = []
    for f in feats:
        props = f.get("properties") or {}
        geom_wkt = geom_to_wkt(f.get("geometry"))
        if not geom_wkt:
            continue
        rows.append(
            (
                "duz",
                str(props.get("dgm_nm") or "지구단위계획구역"),
                geom_wkt,
                "vworld:lt_c_upisuq161",
                extract_sgg_cd(props),
            )
        )
    with connect() as conn:
        conn.execute("DELETE FROM district")
        conn.executemany(
            "INSERT INTO district(kind, name, geom_wkt, source, sgg_cd) VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    _log("district", "vworld", len(rows), "ok", "지구단위계획 적재")
    return {"layer": "district", "status": "ok", "feature_count": len(rows)}


_PLANNED_LAYER_KIND_DEFAULT: dict[str, str] = {
    "lt_c_upisuq151": "road",
    "lt_c_upisuq152": "parking",
    "lt_c_upisuq154": "market",
    "lt_c_upisuq155": "edu",
}
# uq153 공간시설(공원·녹지·광장)은 녹지 패널. 여기 넣지 않음.
_PLANNED_SCHOOL_HINTS_DEFAULT: tuple[str, ...] = (
    "학교",
    "초교",
    "중교",
    "고교",
    "대학교",
    "유치원",
    "국민학교",
    "여중",
    "여고",
    "공고",
    "대학",
)


def planned_layer_kind(table: str) -> str | None:
    """UPIS 테이블명 → 기본 범주. 매핑 없으면 None(건너뜀)."""
    t = (table or "").lower()
    mapping = dict(_PLANNED_LAYER_KIND_DEFAULT)
    raw = load_hardcode().get("PLANNED_LAYERS") or {}
    if isinstance(raw, dict):
        for key, val in raw.items():
            mapping[str(key).lower()] = str(val)
    for key, kind in mapping.items():
        if key in t:
            return kind
    return None


def _planned_school_hints() -> tuple[str, ...]:
    raw = load_hardcode().get("PLANNED_SCHOOL_HINTS")
    if isinstance(raw, list) and raw:
        return tuple(str(x) for x in raw)
    return _PLANNED_SCHOOL_HINTS_DEFAULT


def map_planned_kind(table: str, name: str, sclas: str = "") -> str | None:
    """도시계획시설 레이어·시설명 → 패널 범주.

    계획도로=uq151, 주차·철도=uq152, 시장=uq154(이름에 시장),
    학교/청사·문화=uq155. 공원·녹지(uq153)는 None.
    """
    base = planned_layer_kind(table)
    if base is None:
        return None
    text = f"{name or ''} {sclas or ''}"
    if base == "edu":
        if any(hint in text for hint in _planned_school_hints()):
            return "school"
        return "edu"
    if base == "market":
        if "시장" not in text:
            return None
        return "market"
    return base


def remap_planned_kinds() -> dict[str, Any]:
    """기존 planned.kind를 레이어·시설명 기준으로 다시 채운다. uq153은 삭제."""
    with connect() as conn:
        conn.execute("PRAGMA busy_timeout=120000")
        deleted = conn.execute(
            "DELETE FROM planned WHERE source LIKE '%upisuq153%'"
        ).rowcount
        rows = conn.execute("SELECT id, name, source FROM planned").fetchall()
        updates: list[tuple[str, int]] = []
        drop_ids: list[int] = []
        for row in rows:
            source = str(row["source"] or "")
            table = source.split(":")[-1] if ":" in source else source
            mapped = map_planned_kind(table, str(row["name"] or ""), "")
            if mapped is None:
                drop_ids.append(int(row["id"]))
            else:
                updates.append((mapped, int(row["id"])))
        if updates:
            conn.executemany("UPDATE planned SET kind = ? WHERE id = ?", updates)
        if drop_ids:
            conn.executemany(
                "DELETE FROM planned WHERE id = ?",
                [(i,) for i in drop_ids],
            )
        conn.commit()
    _log(
        "planned",
        "remap_kind",
        len(updates),
        "ok",
        f"도시계획시설 재분류 deleted_green={deleted} dropped={len(drop_ids)}",
    )
    return {
        "layer": "planned",
        "status": "ok",
        "feature_count": len(updates),
        "deleted_green": deleted,
        "dropped": len(drop_ids),
    }


async def _collect_planned_rows(tables: list[str]) -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = []
    for tn in tables:
        try:
            feats = await iter_features(tn, max_features=1000, tile_step=0.02)
        except Exception as exc:
            logger.warning("planned %s: %s", tn, exc)
            continue
        for f in feats:
            props = f.get("properties") or {}
            geom_wkt = geom_to_wkt(f.get("geometry"))
            if not geom_wkt:
                continue
            mapped = map_planned_kind(
                tn,
                str(props.get("dgm_nm") or ""),
                str(props.get("sclas_cl") or ""),
            )
            if mapped is None:
                continue
            rows.append(
                (
                    mapped,
                    str(props.get("dgm_nm") or tn),
                    geom_wkt,
                    f"vworld:{tn}",
                    extract_sgg_cd(props),
                )
            )
    return rows


async def sync_planned() -> dict[str, Any]:
    tables = list(_PLANNED_LAYER_KIND_DEFAULT.keys())
    raw = load_hardcode().get("PLANNED_LAYERS") or {}
    if isinstance(raw, dict) and raw:
        tables = [str(k) for k in raw.keys()]
    rows = await _collect_planned_rows(tables)
    if not rows:
        _log("planned", "vworld", 0, "skip", "도시계획시설 피처 없음(기존 유지)")
        return {"layer": "planned", "status": "skipped", "feature_count": 0}
    with connect() as conn:
        conn.execute("DELETE FROM planned")
        conn.executemany(
            "INSERT INTO planned(kind, name, geom_wkt, source, sgg_cd) VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    _log("planned", "vworld", len(rows), "ok", "도시계획시설 적재")
    return {"layer": "planned", "status": "ok", "feature_count": len(rows)}


async def sync_blocks() -> dict[str, Any]:
    """가구 근사: 지적 필지를 ~120m 격자로 묶어 dissolve."""
    from collections import defaultdict

    from shapely.ops import unary_union

    with connect() as conn:
        rows_in = conn.execute("SELECT geom_wkt FROM parcels").fetchall()
    if len(rows_in) < 10:
        # fallback OSM
        b = collection_bbox()
        query = f"""
        [out:json][timeout:90];
        (
          way["landuse"~"residential|commercial|retail|industrial|education"]({b['min_lat']},{b['min_lng']},{b['max_lat']},{b['max_lng']});
        );
        out geom;
        """
        try:
            data = await _overpass(query)
            rows = []
            for el in data.get("elements", []):
                geom_wkt = _osm_element_wkt(el)
                if geom_wkt:
                    rows.append((geom_wkt, "osm:landuse"))
            with connect() as conn:
                conn.execute("DELETE FROM blocks")
                conn.executemany(
                    "INSERT INTO blocks(geom_wkt, source) VALUES (?, ?)",
                    rows,
                )
                conn.commit()
            backfill_sgg("blocks")
            _log("block", "osm", len(rows), "ok", "가구(landuse) 적재")
            return {"layer": "block", "status": "ok", "feature_count": len(rows)}
        except Exception as exc:
            return {"layer": "block", "status": "error", "message": str(exc), "feature_count": 0}

    cell = 0.0012  # ~130m
    buckets: dict[tuple[int, int], list] = defaultdict(list)
    for row in rows_in:
        try:
            g = shapely_wkt.loads(row["geom_wkt"])
            if g.is_empty:
                continue
            c = g.centroid
            key = (int(c.x / cell), int(c.y / cell))
            buckets[key].append(g)
        except Exception:
            continue

    out_rows = []
    for geoms in buckets.values():
        try:
            merged = unary_union(geoms)
            if merged.is_empty:
                continue
            if not merged.is_valid:
                merged = merged.buffer(0)
            out_rows.append((merged.wkt, "parcels:grid_dissolve"))
        except Exception:
            continue

    with connect() as conn:
        conn.execute("DELETE FROM blocks")
        conn.executemany(
            "INSERT INTO blocks(geom_wkt, source) VALUES (?, ?)",
            out_rows,
        )
        conn.commit()
    backfill_sgg("blocks")
    _log("block", "parcels", len(out_rows), "ok", "가구(지적 격자 dissolve)")
    return {"layer": "block", "status": "ok", "feature_count": len(out_rows)}


async def sync_terrain() -> dict[str, Any]:
    """등고용 표고 격자. CDSE Sentinel Hub DEM 우선, 없으면 Open-Meteo."""
    import json

    b = collection_bbox()
    settings = get_settings()
    payload: dict[str, Any]
    if settings.has_cdse_key:
        payload = await download_cdse_dem(b)
    else:
        payload = await _sync_terrain_openmeteo(b)

    n_lat = len(payload["lats"])
    n_lng = len(payload["lngs"])
    relief = float(payload.get("relief_m") or 0)
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
            ("terrain_grid", json.dumps(payload)),
        )
        conn.commit()
    _log(
        "terrain",
        "copernicus_dem",
        n_lat * n_lng,
        "ok",
        f"{payload.get('dataset')} {n_lat}x{n_lng} relief={relief:.1f}m",
    )
    return {
        "layer": "terrain",
        "status": "ok",
        "feature_count": n_lat * n_lng,
        "relief_m": relief,
        "source": payload.get("source"),
        "dataset": payload.get("dataset"),
    }


async def _sync_terrain_openmeteo(b: dict[str, float]) -> dict[str, Any]:
    """CDSE 키가 없을 때 Open-Meteo Elevation(GLO-90) 폴백."""
    import math

    hc = load_hardcode()
    step_m = float(hc.get("TERRAIN_STEP_M") or 90)
    max_points = int(hc.get("TERRAIN_MAX_POINTS") or 10000)
    lat_m = (b["max_lat"] - b["min_lat"]) * 111320
    mid_lat = (b["min_lat"] + b["max_lat"]) / 2
    lng_m = (b["max_lng"] - b["min_lng"]) * 111320 * math.cos(math.radians(mid_lat))
    n_lat = max(16, int(lat_m / step_m) + 1)
    n_lng = max(16, int(lng_m / step_m) + 1)
    total = n_lat * n_lng
    if total > max_points:
        scale = math.sqrt(max_points / total)
        n_lat = max(16, int(n_lat * scale))
        n_lng = max(16, int(n_lng * scale))
    lats = [
        b["min_lat"] + (b["max_lat"] - b["min_lat"]) * i / (n_lat - 1)
        for i in range(n_lat)
    ]
    lngs = [
        b["min_lng"] + (b["max_lng"] - b["min_lng"]) * j / (n_lng - 1)
        for j in range(n_lng)
    ]
    points = [(lat, lng) for lat in lats for lng in lngs]
    heights: list[float] = []

    chunk = 100
    async with httpx.AsyncClient(timeout=90.0) as client:
        for i in range(0, len(points), chunk):
            batch = points[i : i + chunk]
            resp = await client.get(
                "https://api.open-meteo.com/v1/elevation",
                params={
                    "latitude": ",".join(f"{lat:.6f}" for lat, _ in batch),
                    "longitude": ",".join(f"{lng:.6f}" for _, lng in batch),
                },
            )
            resp.raise_for_status()
            elev = resp.json().get("elevation") or []
            if len(elev) != len(batch):
                raise SyncError(
                    f"Copernicus DEM 응답 수 불일치: {len(elev)}/{len(batch)}"
                )
            heights.extend(float(x) for x in elev)

    grid: list[list[float]] = []
    idx = 0
    for _ in lats:
        grid.append(heights[idx : idx + n_lng])
        idx += n_lng

    flat = heights
    relief = max(flat) - min(flat) if flat else 0
    return {
        "lats": lats,
        "lngs": lngs,
        "grid": grid,
        "min": min(flat) if flat else 0,
        "max": max(flat) if flat else 0,
        "relief_m": relief,
        "source": "Copernicus DEM © ESA",
        "dataset": "Copernicus DEM GLO-90 (via Open-Meteo, CDSE 키 없음)",
        "step_m": step_m,
    }


async def _overpass(query: str) -> dict[str, Any]:
    endpoints = [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.private.coffee/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
    ]
    last_err: Exception | None = None
    async with httpx.AsyncClient(timeout=180.0) as client:
        for ep in endpoints:
            for attempt in range(2):
                try:
                    resp = await client.post(
                        ep,
                        data={"data": query},
                        headers={"User-Agent": "sitedia-seocho/0.1"},
                    )
                    resp.raise_for_status()
                    return resp.json()
                except Exception as exc:
                    last_err = exc
                    logger.warning("Overpass 실패 %s (%s): %s", ep, attempt + 1, exc)
                    await asyncio.sleep(2 * (attempt + 1))
    raise SyncError(f"Overpass 실패: {last_err}")


def _closed_ring(coords: list[tuple[float, float]]) -> list[tuple[float, float]] | None:
    if len(coords) < 3:
        return None
    if coords[0] != coords[-1]:
        coords = [*coords, coords[0]]
    if len(coords) < 4:
        return None
    return coords


def _ring_polygon(coords: list[tuple[float, float]]):
    """닫힌 링을 유효한 면 geometry로. buffer(0)은 MultiPolygon이 될 수 있다."""
    ring = _closed_ring(coords)
    if not ring:
        return None
    try:
        poly = Polygon(ring)
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.is_empty:
            return None
        if poly.geom_type == "MultiPolygon":
            parts = [g for g in poly.geoms if not g.is_empty]
            return unary_union(parts) if parts else None
        return poly
    except Exception:
        return None


def _osm_polygon_wkt(el: dict[str, Any]) -> str | None:
    """Overpass geom에서 닫힌 면만 WKT. 열린 선은 녹지에 넣지 않는다."""
    if el.get("type") == "way" and el.get("geometry"):
        poly = _ring_polygon([(p["lon"], p["lat"]) for p in el["geometry"]])
        return None if poly is None else poly.wkt
    if el.get("type") == "relation" and el.get("members"):
        outers: list = []
        inners: list = []
        for mem in el["members"]:
            geom = mem.get("geometry")
            if not geom:
                continue
            poly = _ring_polygon([(p["lon"], p["lat"]) for p in geom])
            if poly is None:
                continue
            if mem.get("role") == "inner":
                inners.append(poly)
            else:
                outers.append(poly)
        if not outers:
            return None
        merged = unary_union(outers)
        if inners:
            try:
                merged = merged.difference(unary_union(inners))
            except Exception:
                pass
        if merged.is_empty:
            return None
        return merged.wkt
    return None


def _osm_element_wkt(el: dict[str, Any]) -> str | None:
    """out geom 응답에서 polygon/linestring WKT."""
    if el.get("type") == "way" and el.get("geometry"):
        coords = [(p["lon"], p["lat"]) for p in el["geometry"]]
        if len(coords) < 2:
            return None
        if coords[0] == coords[-1] and len(coords) >= 4:
            return "POLYGON((" + ", ".join(f"{x} {y}" for x, y in coords) + "))"
        return "LINESTRING(" + ", ".join(f"{x} {y}" for x, y in coords) + ")"
    if el.get("type") == "relation" and el.get("members"):
        # outer way만 단순 연결
        for mem in el["members"]:
            if mem.get("role") == "outer" and mem.get("geometry"):
                coords = [(p["lon"], p["lat"]) for p in mem["geometry"]]
                if len(coords) >= 4:
                    if coords[0] != coords[-1]:
                        coords.append(coords[0])
                    return "POLYGON((" + ", ".join(f"{x} {y}" for x, y in coords) + "))"
    return None


async def sync_parcels() -> dict[str, Any]:
    feats = await iter_features("lp_pa_cbnd_bubun", max_features=1000, tile_step=0.008)
    allowed = all_sgg_cds()
    rows = []
    for f in feats:
        props = f.get("properties") or {}
        pnu = str(props.get("pnu") or props.get("PNU") or "") or None
        geom_wkt = geom_to_wkt(f.get("geometry"))
        if not geom_wkt:
            continue
        code = extract_sgg_cd(props)
        prefix = (pnu or "")[:5]
        if prefix.isdigit() and prefix not in allowed:
            continue
        if not code and prefix in allowed:
            code = prefix
        rows.append(
            (
                pnu,
                str(props.get("jibun") or props.get("jimok") or props.get("addr") or ""),
                geom_wkt,
                "vworld:lp_pa_cbnd_bubun",
                code,
            )
        )
    with connect() as conn:
        conn.execute("DELETE FROM parcels")
        conn.executemany(
            "INSERT INTO parcels(pnu, jimok, geom_wkt, source, sgg_cd) VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    _log("parcels", "vworld:lp_pa_cbnd_bubun", len(rows), "ok", "지적 적재")
    backfill_sgg("parcels")
    return {"layer": "parcels", "status": "ok", "feature_count": len(rows)}


async def sync_osm_overpass() -> dict[str, Any]:
    """도로·대중교통을 Overpass로 수집."""
    b = collection_bbox()
    query = f"""
    [out:json][timeout:180];
    (
      way["highway"]({b['min_lat']},{b['min_lng']},{b['max_lat']},{b['max_lng']});
      node["highway"="bus_stop"]({b['min_lat']},{b['min_lng']},{b['max_lat']},{b['max_lng']});
      node["railway"="station"]({b['min_lat']},{b['min_lng']},{b['max_lat']},{b['max_lng']});
      node["station"="subway"]({b['min_lat']},{b['min_lng']},{b['max_lat']},{b['max_lng']});
      node["public_transport"="station"]({b['min_lat']},{b['min_lng']},{b['max_lat']},{b['max_lng']});
    );
    out body;
    >;
    out skel qt;
    """
    endpoints = [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
    ]
    data = None
    last_err: Exception | None = None
    async with httpx.AsyncClient(timeout=120.0) as client:
        for ep in endpoints:
            try:
                resp = await client.post(
                    ep,
                    data={"data": query},
                    headers={"User-Agent": "sitedia-seocho/0.1 (local research)"},
                )
                resp.raise_for_status()
                data = resp.json()
                break
            except Exception as exc:
                last_err = exc
                logger.warning("Overpass 실패 %s: %s", ep, exc)
    if data is None:
        raise SyncError(f"Overpass 실패: {last_err}")

    nodes = {
        el["id"]: (el["lon"], el["lat"])
        for el in data.get("elements", [])
        if el.get("type") == "node" and "lon" in el
    }
    road_rows = []
    transit_rows = []
    for el in data.get("elements", []):
        tags = el.get("tags") or {}
        if el.get("type") == "way" and "highway" in tags:
            coords = [nodes[i] for i in el.get("nodes", []) if i in nodes]
            if len(coords) < 2:
                continue
            line = shapely_wkt.loads(
                "LINESTRING(" + ", ".join(f"{x} {y}" for x, y in coords) + ")"
            )
            road_rows.append(
                (
                    tags.get("highway"),
                    tags.get("name"),
                    line.wkt,
                    "osm:overpass",
                )
            )
        if el.get("type") == "node":
            kind = None
            if tags.get("highway") == "bus_stop":
                kind = "bus"
            elif tags.get("station") == "subway" or tags.get("railway") == "station":
                kind = "subway"
            elif tags.get("public_transport") == "station":
                kind = "subway"
            if not kind:
                continue
            transit_rows.append(
                (
                    kind,
                    tags.get("name"),
                    tags.get("network") or tags.get("line"),
                    f"POINT({el['lon']} {el['lat']})",
                    "osm:overpass",
                )
            )

    with connect() as conn:
        conn.execute("DELETE FROM roads")
        conn.execute("DELETE FROM transit")
        conn.executemany(
            "INSERT INTO roads(road_class, name, geom_wkt, source) VALUES (?, ?, ?, ?)",
            road_rows,
        )
        conn.executemany(
            "INSERT INTO transit(kind, name, line_name, geom_wkt, source) "
            "VALUES (?, ?, ?, ?, ?)",
            transit_rows,
        )
        conn.commit()
    backfill_sgg("roads")
    backfill_sgg("transit")
    _log("roads", "osm", len(road_rows), "ok", "OSM 도로")
    _log("transit", "osm", len(transit_rows), "ok", "OSM 정류장·역")
    return {
        "layer": "osm",
        "status": "ok",
        "feature_count": len(road_rows) + len(transit_rows),
        "roads": len(road_rows),
        "transit": len(transit_rows),
    }


async def sync_layer_stub(layer: str) -> dict[str, Any]:
    handlers: dict[str, Callable] = {
        "sgg": sync_sgg_bounds,
        "buildings": sync_buildings,
        "buildings_road": sync_buildings_road_fill,
        "zoning": sync_zoning,
        "parcels": sync_parcels,
        "green": sync_green,
        "district": sync_district,
        "planned": sync_planned,
        "block": sync_blocks,
        "terrain": sync_terrain,
        "roads": sync_osm_overpass,
        "transit": sync_osm_overpass,
        "street_trees": sync_street_trees,
    }
    settings = get_settings()
    vworld_layers = {
        "buildings",
        "buildings_road",
        "zoning",
        "parcels",
        "green",
        "district",
        "planned",
    }
    if layer in vworld_layers and not settings.has_vworld_key:
        return {
            "layer": layer,
            "status": "skipped",
            "message": "VWorld API 키 없음",
            "feature_count": 0,
        }
    if layer == "street_trees" and not settings.has_seoul_open_key:
        return {
            "layer": layer,
            "status": "skipped",
            "message": "서울열린데이터광장 API 키 없음",
            "feature_count": 0,
        }
    handler = handlers.get(layer)
    if handler is None:
        return {
            "layer": layer,
            "status": "skipped",
            "message": f"미구현 레이어: {layer}",
            "feature_count": 0,
        }
    try:
        return await handler()
    except Exception as exc:
        logger.exception("sync failed %s", layer)
        _log(layer, "error", 0, "error", str(exc))
        return {
            "layer": layer,
            "status": "error",
            "message": str(exc),
            "feature_count": 0,
        }


async def sync_monthly(
    layers: list[str] | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    init_db()
    targets = layers or [
        "sgg",
        "buildings",
        "buildings_road",
        "zoning",
        "green",
        "district",
        "planned",
        "parcels",
        "block",
        "terrain",
        "roads",
        "street_trees",
    ]
    results = []
    did_osm = False
    for layer in targets:
        if on_progress:
            on_progress(f"{layer} 동기화 중")
        if layer in {"roads", "transit"}:
            if did_osm:
                results.append(
                    {
                        "layer": layer,
                        "status": "ok",
                        "message": "OSM 일괄 처리됨",
                        "feature_count": 0,
                    }
                )
                continue
            r = await sync_osm_overpass()
            results.append(r)
            did_osm = True
            continue
        results.append(await sync_layer_stub(layer))
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
            ("last_sync_at", _now()),
        )
        conn.commit()
    return {"results": results, "synced_at": _now()}
