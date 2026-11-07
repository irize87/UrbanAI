from __future__ import annotations

import asyncio
import hmac
from collections import OrderedDict
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.core.config import ROOT_DIR, carto_voyager_url, get_settings, load_hardcode
from app.core.geo import is_within_seocho, require_seocho, seocho_bbox, seocho_center
from app.core.districts import adjacent_sgg_cds, primary_sgg_cd, sgg_name
from app.db.fixtures import load_fixtures
from app.db.schema import connect, init_db
from app.render.panel_render import render_panel
from app.services.address import format_vworld_address
from app.services.city3d import (
    build_city_buildings,
    build_city_context,
    build_city_ground,
    build_city_meta,
)
from app.services.mass3d import build_mass_payload
from app.services.panels_meta import panel_payload
from app.services.city_analysis import run_analysis
from app.services.sim_layout import (
    apply_existing_edit,
    apply_image_preview,
    apply_layout,
    selection_summary,
)
from app.services.sim_llm import interpret_city_command, interpret_image_masses
from app.sync.monthly import sync_monthly

router = APIRouter(prefix="/api")


def require_admin(x_admin_token: str = Header(default="")) -> None:
    """관리 API. .env 의 ADMIN_TOKEN 과 X-Admin-Token 이 같을 때만 통과한다."""
    expected = get_settings().admin_token.strip()
    provided = x_admin_token.strip()
    if not expected or not provided:
        raise HTTPException(401, "관리 API 인증이 필요합니다.")
    if not hmac.compare_digest(provided.encode(), expected.encode()):
        raise HTTPException(401, "관리 API 인증이 필요합니다.")

_SYNC_JOB: dict = {
    "running": False,
    "message": None,
    "started_at": None,
    "result": None,
}

_TILE_CACHE: OrderedDict[tuple, tuple[bytes, str]] = OrderedDict()
_TILE_MAX = 480
_HTTP: httpx.AsyncClient | None = None


async def _http_client() -> httpx.AsyncClient:
    global _HTTP
    if _HTTP is None or _HTTP.is_closed:
        _HTTP = httpx.AsyncClient(timeout=20.0, follow_redirects=True)
    return _HTTP


def _tile_get(key: tuple) -> tuple[bytes, str] | None:
    hit = _TILE_CACHE.get(key)
    if hit is None:
        return None
    _TILE_CACHE.move_to_end(key)
    return hit


def _tile_put(key: tuple, body: bytes, media: str) -> None:
    _TILE_CACHE[key] = (body, media)
    _TILE_CACHE.move_to_end(key)
    while len(_TILE_CACHE) > _TILE_MAX:
        _TILE_CACHE.popitem(last=False)


@router.get("/data-sources")
def data_sources():
    import json

    path = ROOT_DIR / "json" / "data_sources.json"
    with path.open(encoding="utf-8") as f:
        return json.load(f)


@router.get("/health")
def health():
    settings = get_settings()
    init_db()
    with connect() as conn:
        building_count = conn.execute("SELECT COUNT(*) AS c FROM buildings").fetchone()["c"]
        fixture_count = conn.execute(
            "SELECT COUNT(*) AS c FROM buildings WHERE source = 'fixture'"
        ).fetchone()["c"]
        last_sync = conn.execute(
            "SELECT layer, status, synced_at, message FROM sync_log ORDER BY id DESC LIMIT 5"
        ).fetchall()
        layers = {}
        for table in (
            "buildings",
            "zoning",
            "green",
            "roads",
            "transit",
            "parcels",
            "blocks",
            "district",
            "planned",
            "sgg_bounds",
            "street_trees",
        ):
            try:
                layers[table] = conn.execute(
                    f"SELECT COUNT(*) AS c FROM {table}"
                ).fetchone()["c"]
            except Exception:
                layers[table] = 0
        mode_row = conn.execute("SELECT value FROM meta WHERE key='data_mode'").fetchone()
    using_fixtures = building_count > 0 and fixture_count == building_count
    sgg_counts: dict[str, int] = {}
    with connect() as conn:
        try:
            for row in conn.execute(
                "SELECT sgg_cd, COUNT(*) AS c FROM buildings "
                "GROUP BY sgg_cd"
            ).fetchall():
                code = row["sgg_cd"] or "unknown"
                sgg_counts[code] = row["c"]
        except Exception:
            sgg_counts = {}
    return {
        "ok": True,
        "scope": "seocho",
        "primary_sgg": primary_sgg_cd(),
        "adjacent_sgg": [
            {"code": c, "name": sgg_name(c)} for c in adjacent_sgg_cds()
        ],
        "buildings_by_sgg": sgg_counts,
        "vworld_key_configured": settings.has_vworld_key,
        "carto_key_configured": settings.has_carto_key,
        "cdse_key_configured": settings.has_cdse_key,
        "clova_key_configured": settings.has_clova_key,
        "seoul_open_key_configured": settings.has_seoul_open_key,
        "building_count": building_count,
        "street_tree_count": layers.get("street_trees", 0),
        "using_fixtures": using_fixtures,
        "data_mode": (mode_row["value"] if mode_row else ("fixture" if using_fixtures else "synced")),
        "layers": layers,
        "bbox": seocho_bbox(),
        "center": seocho_center(),
        "recent_sync": [dict(r) for r in last_sync],
        "sync_running": _SYNC_JOB["running"],
        "sync_message": _SYNC_JOB.get("message"),
    }


@router.get("/panels")
def panels():
    return panel_payload()


@router.get("/basemap")
def basemap():
    settings = get_settings()
    return {
        "provider": "vworld",
        "key_configured": settings.has_vworld_key,
        "tile_proxy": "/api/tiles/base/{z}/{y}/{x}.png",
        "note": "타일은 /api/tiles 프록시만 사용합니다.",
        "fallback_osm": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        "domain": settings.vworld_domain,
        "carto": {
            "key_configured": settings.has_carto_key,
            "tile_proxy": "/api/tiles/carto/{z}/{x}/{y}.png",
            "style": "voyager",
        },
    }


async def _vworld_tile(
    layer: str,
    z: int,
    y: int,
    x: int,
    exts: tuple[str, ...] = ("png",),
) -> Response:
    settings = get_settings()
    if not settings.has_vworld_key:
        raise HTTPException(400, "VWORLD_API_KEY가 필요합니다. 설정 후 다시 시도하세요.")
    if not (0 <= z <= 20):
        raise HTTPException(400, "잘못된 줌 레벨입니다.")
    key = ("vworld", layer, z, y, x, exts)
    cached = _tile_get(key)
    if cached:
        return Response(content=cached[0], media_type=cached[1])
    client = await _http_client()
    last_status = 404
    for ext in exts:
        url = (
            f"https://api.vworld.kr/req/wmts/1.0.0/{settings.vworld_api_key}/"
            f"{layer}/{z}/{y}/{x}.{ext}"
        )
        resp = await client.get(url, headers={"Referer": settings.vworld_domain})
        last_status = resp.status_code
        if resp.status_code != 200:
            continue
        ctype = (resp.headers.get("content-type") or "").lower()
        if "xml" in ctype or "html" in ctype:
            continue
        body = resp.content or b""
        if body[:3] != b"\xff\xd8\xff" and body[:8] != b"\x89PNG\r\n\x1a\n":
            continue
        media = "image/jpeg" if ext in {"jpeg", "jpg"} else "image/png"
        _tile_put(key, body, media)
        return Response(content=body, media_type=media)
    raise HTTPException(last_status, "타일 요청 실패")


@router.get("/tiles/base/{z}/{y}/{x}.png")
async def tiles_base(z: int, y: int, x: int):
    return await _vworld_tile("Base", z, y, x)


@router.get("/tiles/satellite/{z}/{y}/{x}.png")
async def tiles_satellite(z: int, y: int, x: int):
    hc = load_hardcode()
    layer = str(hc.get("VWORLD_SATELLITE_LAYER") or "Satellite")
    return await _vworld_tile(layer, z, y, x, exts=("jpeg", "png"))


@router.get("/tiles/carto/{z}/{x}/{y}.png")
async def tiles_carto(z: int, x: int, y: int):
    if not (0 <= z <= 20):
        raise HTTPException(400, "잘못된 줌 레벨입니다.")
    span = 1 << z
    if not (0 <= x < span and 0 <= y < span):
        raise HTTPException(400, "잘못된 타일 좌표입니다.")

    settings = get_settings()
    if not settings.has_carto_key:
        raise HTTPException(400, "CARTO_API_KEY가 필요합니다. .env에 입력 후 재시작하세요.")
    key = ("carto", z, x, y)
    cached = _tile_get(key)
    if cached:
        return Response(content=cached[0], media_type=cached[1])
    url = carto_voyager_url(z, x, y, settings.carto_api_key.strip())
    client = await _http_client()
    resp = await client.get(url, headers={"User-Agent": "sitedia/1.0"})
    if resp.status_code != 200:
        raise HTTPException(resp.status_code, "Carto 타일 요청 실패")
    media = resp.headers.get("content-type", "image/png")
    if not media.startswith("image/"):
        raise HTTPException(502, "Carto 타일 응답이 이미지가 아닙니다. API 키를 확인하세요.")
    _tile_put(key, resp.content, media)
    return Response(content=resp.content, media_type=media)


@router.get("/ramp")
def ramp(kind: str = Query("age"), color: str | None = Query(None)):
    from app.services.panels_meta import (
        AGE_LABELS,
        HEIGHT_LABELS,
        age_ramp,
        height_ramp,
    )

    if kind == "height":
        return {"kind": kind, "colors": height_ramp(color), "labels": HEIGHT_LABELS}
    return {"kind": "age", "colors": age_ramp(color), "labels": AGE_LABELS}


@router.get("/panel")
def panel(
    panel: str = Query(...),
    lat: float = Query(...),
    lng: float = Query(...),
    r: float = Query(800, ge=200, le=1500),
    shape: str = Query("circle"),
    width_m: float | None = None,
    height_m: float | None = None,
    threshold: float | None = Query(None, alias="thr"),
    color: str | None = Query(None),
    include_adjacent: bool = Query(False),
    metric: str | None = Query(None),
):
    try:
        if shape not in {"circle", "rect"}:
            raise ValueError("shape는 circle 또는 rect 여야 합니다.")
        result = render_panel(
            panel=panel,
            lat=lat,
            lng=lng,
            radius_m=r,
            shape=shape,  # type: ignore[arg-type]
            width_m=width_m,
            height_m=height_m,
            threshold=threshold,
            color=color,
            include_adjacent=include_adjacent,
            metric=metric,
        )
        return result
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/geocode")
async def geocode(q: str = Query(..., min_length=1)):
    import httpx

    settings = get_settings()
    if not settings.has_vworld_key:
        raise HTTPException(
            400,
            "주소 검색에는 VWORLD_API_KEY가 필요합니다. https://www.vworld.kr 에서 발급하세요.",
        )

    async def _search(search_type: str) -> list[dict]:
        params = {
            "service": "search",
            "request": "search",
            "version": "2.0",
            "crs": "EPSG:4326",
            "size": "10",
            "page": "1",
            "query": q,
            "type": search_type,
            "format": "json",
            "errorformat": "json",
            "key": settings.vworld_api_key,
        }
        if search_type == "address":
            params["category"] = "road"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get("https://api.vworld.kr/req/search", params=params)
        data = resp.json()
        items = []
        try:
            raw = data["response"]["result"]["items"]
        except Exception:
            return []
        for it in raw:
            point = it.get("point") or {}
            try:
                lat = float(point["y"])
                lng = float(point["x"])
            except Exception:
                continue
            addr = it.get("address") or {}
            items.append(
                {
                    "title": it.get("title"),
                    "address": addr.get("road") or addr.get("parcel"),
                    "lat": lat,
                    "lng": lng,
                    "in_seocho": is_within_seocho(lat, lng),
                    "type": search_type,
                }
            )
        return items

    items = await _search("place")
    if not items:
        items = await _search("address")
    return {"query": q, "items": items}


@router.get("/reverse-geocode")
async def reverse_geocode(
    lat: float = Query(...),
    lng: float = Query(...),
):
    """좌표를 도로명 주소로 변환한다. 키 없으면 address=null."""
    import httpx

    settings = get_settings()
    if not settings.has_vworld_key:
        return {"lat": lat, "lng": lng, "address": None}

    hc = load_hardcode()
    url = hc.get("VWORLD_ADDRESS_URL", "https://api.vworld.kr/req/address")
    params = {
        "service": "address",
        "request": "getAddress",
        "version": "2.0",
        "crs": "epsg:4326",
        "point": f"{lng},{lat}",
        "format": "json",
        "type": "both",
        "zipcode": "false",
        "simple": "false",
        "key": settings.vworld_api_key,
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            url,
            params=params,
            headers={"Referer": settings.vworld_domain},
        )
    try:
        data = resp.json()
    except Exception:
        return {"lat": lat, "lng": lng, "address": None}
    return {"lat": lat, "lng": lng, "address": format_vworld_address(data)}


@router.get("/city3d/meta")
def city3d_meta():
    """전역 3D 캐시 버전·범위·스타일."""
    return build_city_meta()


@router.get("/city3d/buildings")
def city3d_buildings():
    """서초구 건물 매스. 높이 없는 동은 제외."""
    return build_city_buildings()


@router.get("/city3d/ground")
def city3d_ground():
    """서초구 경계·지형·도로·녹지·하천."""
    return build_city_ground()


@router.get("/city3d/context")
def city3d_context():
    """서초 경계 밖 띠의 건물 풋프린트와 도로."""
    return build_city_context()


@router.get("/mass3d")
def mass3d(
    lat: float = Query(...),
    lng: float = Query(...),
    r: float = Query(800, ge=200, le=1500),
    shape: str = Query("circle"),
    include_adjacent: bool = Query(False),
):
    try:
        require_seocho(lat, lng)
        return build_mass_payload(
            lat,
            lng,
            r,
            shape,
            include_adjacent=include_adjacent,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


class SimApplyIn(BaseModel):
    prompt: str = ""
    lat: float
    lng: float
    r: float = 800
    shape: str = "circle"
    include_adjacent: bool = False
    building_ids: list[int] = Field(default_factory=list)
    box: list[list[float]] | None = None
    history: list[dict[str, str]] = Field(default_factory=list)
    partial: dict | None = None
    image_b64: str | None = None
    image_mime: str | None = None
    session: str = "auto"
    height_overrides: dict[str, float] = Field(default_factory=dict)


@router.post("/sim/apply")
async def sim_apply(body: SimApplyIn):
    settings = get_settings()
    if not settings.has_clova_key:
        raise HTTPException(
            400,
            "CLOVA_API_KEY가 없습니다. .env에 키를 넣은 뒤 백엔드를 재시작하세요.",
        )
    if not (body.prompt or "").strip() and not (body.image_b64 or "").strip():
        raise HTTPException(400, "명령 또는 이미지를 보내 주세요.")
    try:
        require_seocho(body.lat, body.lng)
        summary = selection_summary(
            lat=body.lat,
            lng=body.lng,
            radius_m=body.r,
            shape_kind=body.shape,
            include_adjacent=body.include_adjacent,
            building_ids=body.building_ids,
            box_ll=body.box,
        )
        want_image = (body.session or "auto") in {"auto", "image_mass"} and bool(
            (body.image_b64 or "").strip()
        )
        if not want_image:
            command = await interpret_city_command(
                body.prompt,
                summary,
                history=body.history,
                partial=body.partial,
            )
            if command["kind"] == "ask":
                return {
                    "status": "ask",
                    "reply_ko": command["reply_ko"],
                    "ask_ko": command["ask_ko"],
                    "spec": None,
                    "partial": body.partial,
                    "session": "edit",
                    "action": "edit",
                    "hide_ids": [],
                    "updates": [],
                    "buildings": {"type": "FeatureCollection", "features": []},
                    "area_m2": summary.get("area_m2"),
                    "replaced_count": 0,
                    "sim_count": 0,
                }
            if command["kind"] == "edit":
                laid = apply_existing_edit(
                    edit=command["edit"],
                    lat=body.lat,
                    lng=body.lng,
                    radius_m=body.r,
                    shape_kind=body.shape,
                    include_adjacent=body.include_adjacent,
                    building_ids=body.building_ids,
                    box_ll=body.box,
                    height_overrides=body.height_overrides,
                )
                reply = command.get("reply_ko") or laid["reply_ko"]
                return {
                    "status": "ok",
                    "reply_ko": reply,
                    "ask_ko": None,
                    "spec": None,
                    "partial": None,
                    "session": "edit",
                    "action": "edit",
                    "hide_ids": laid["hide_ids"],
                    "updates": laid["updates"],
                    "buildings": laid["buildings"],
                    "area_m2": summary.get("area_m2"),
                    "replaced_count": laid["replaced_count"],
                    "sim_count": 0,
                }
            turn = command["turn"]
            if turn.status != "ok" or turn.spec is None:
                return {
                    **turn.as_dict(),
                    "hide_ids": [],
                    "buildings": {"type": "FeatureCollection", "features": []},
                    "area_m2": summary.get("area_m2"),
                    "replaced_count": 0,
                    "sim_count": 0,
                }
            laid = apply_layout(
                spec=turn.spec,
                lat=body.lat,
                lng=body.lng,
                radius_m=body.r,
                shape_kind=body.shape,
                include_adjacent=body.include_adjacent,
                building_ids=body.building_ids,
                box_ll=body.box,
            )
            return {
                **turn.as_dict(),
                "hide_ids": laid["hide_ids"],
                "buildings": laid["buildings"],
                "area_m2": laid["area_m2"],
                "replaced_count": laid["replaced_count"],
                "sim_count": laid["sim_count"],
                "session": laid.get("session") or turn.session,
                "action": "create",
                "updates": [],
            }
        turn = await interpret_image_masses(
            body.prompt,
            summary,
            history=body.history,
            image_b64=body.image_b64,
            image_mime=body.image_mime,
        )
        if turn.status != "ok" or not turn.masses:
            return {
                **turn.as_dict(),
                "hide_ids": [],
                "buildings": {"type": "FeatureCollection", "features": []},
                "area_m2": summary.get("area_m2"),
                "replaced_count": 0,
                "sim_count": 0,
            }
        laid = apply_image_preview(
            masses=turn.masses,
            lat=body.lat,
            lng=body.lng,
            radius_m=body.r,
            shape_kind=body.shape,
            include_adjacent=body.include_adjacent,
            building_ids=body.building_ids,
            box_ll=body.box,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        **turn.as_dict(),
        "hide_ids": laid["hide_ids"],
        "buildings": laid["buildings"],
        "area_m2": laid["area_m2"],
        "replaced_count": laid["replaced_count"],
        "sim_count": laid["sim_count"],
        "session": laid.get("session") or turn.session,
        "action": "create",
        "updates": [],
    }


class AnalysisIn(BaseModel):
    prompt: str = ""
    action: str = "auto"
    lat: float
    lng: float
    r: float = 800
    shape: str = "circle"
    include_adjacent: bool = False
    building_ids: list[int] = Field(default_factory=list)
    box: list[list[float]] | None = None


@router.post("/analysis/ask")
async def analysis_ask(body: AnalysisIn):
    try:
        require_seocho(body.lat, body.lng)
        return await run_analysis(
            prompt=body.prompt,
            action=body.action,
            lat=body.lat,
            lng=body.lng,
            radius_m=body.r,
            shape_kind=body.shape,
            include_adjacent=body.include_adjacent,
            box_ll=body.box,
            building_ids=body.building_ids,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/admin/fixtures")
def admin_fixtures(
    force: bool = Query(False),
    _: None = Depends(require_admin),
):
    result = load_fixtures(force=force)
    if result.get("skipped"):
        return {"ok": False, **result}
    return {"ok": True, **result}


async def _run_sync(selected: list[str] | None) -> None:
    _SYNC_JOB["running"] = True
    _SYNC_JOB["message"] = "동기화 시작"
    _SYNC_JOB["started_at"] = datetime.now(timezone.utc).isoformat()
    try:
        result = await sync_monthly(
            selected,
            on_progress=lambda msg: _SYNC_JOB.__setitem__("message", msg),
        )
        _SYNC_JOB["result"] = result
        _SYNC_JOB["message"] = "동기화 완료"
        from app.services.layer_cache import invalidate_layer_cache

        invalidate_layer_cache()
    except Exception as exc:
        _SYNC_JOB["message"] = f"동기화 실패: {exc}"
        _SYNC_JOB["result"] = {"ok": False, "error": str(exc)}
    finally:
        _SYNC_JOB["running"] = False


@router.post("/admin/sync")
async def admin_sync(
    layers: str | None = Query(None),
    _: None = Depends(require_admin),
):
    """layers=green,district,planned,parcels,block,terrain 형태로 부분 sync 가능."""
    if _SYNC_JOB["running"]:
        return {"ok": True, "started": False, "message": "이미 동기화 중"}
    selected = [x.strip() for x in layers.split(",")] if layers else None
    asyncio.create_task(_run_sync(selected))
    return {"ok": True, "started": True, "message": "실데이터 동기화를 시작했습니다."}


@router.get("/admin/sync/status")
def sync_status(_: None = Depends(require_admin)):
    init_db()
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM sync_log ORDER BY id DESC LIMIT 30"
        ).fetchall()
    return {
        "running": _SYNC_JOB["running"],
        "message": _SYNC_JOB["message"],
        "started_at": _SYNC_JOB["started_at"],
        "result": _SYNC_JOB["result"],
        "logs": [dict(r) for r in rows],
    }
