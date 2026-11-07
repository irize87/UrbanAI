"""VWorld 항공(Satellite) 타일을 분석 clip에 맞춰 모자이크."""

from __future__ import annotations

import math
from io import BytesIO
from typing import Any

import httpx
from PIL import Image, ImageDraw

from app.core.config import get_settings, load_hardcode

_TILE = 256
_XDWORLD = "https://xdworld.vworld.kr/2d/Satellite/service"


def _lonlat_to_pixel(lng: float, lat: float, z: int) -> tuple[float, float]:
    n = 2.0**z
    x = (lng + 180.0) / 360.0 * n * _TILE
    lat = min(85.05112878, max(-85.05112878, lat))
    siny = math.sin(lat * math.pi / 180.0)
    y = (0.5 - math.log((1 + siny) / (1 - siny)) / (4 * math.pi)) * n * _TILE
    return x, y


def _zoom_for_radius(radius_m: float) -> int:
    if radius_m >= 1100:
        return 16
    if radius_m >= 600:
        return 17
    return 18


def _is_raster(content: bytes, content_type: str) -> bool:
    ctype = (content_type or "").lower()
    if "xml" in ctype or "html" in ctype or "json" in ctype:
        return False
    if len(content) < 24:
        return False
    return content[:3] == b"\xff\xd8\xff" or content[:8] == b"\x89PNG\r\n\x1a\n"


def _open_rgb(content: bytes) -> Image.Image | None:
    try:
        tile = Image.open(BytesIO(content)).convert("RGB")
    except OSError:
        return None
    if tile.size[0] < 8 or tile.size[1] < 8:
        return None
    return tile


def satellite_tile_urls(
    z: int,
    tx: int,
    ty: int,
    key: str,
    layer: str = "Satellite",
    wmts_base: str | None = None,
) -> list[str]:
    """WMTS는 z/y/x.jpeg, 공개 XYZ는 z/x/y.jpeg."""
    base = wmts_base or "https://api.vworld.kr/req/wmts/1.0.0"
    urls = [
        f"{base}/{key}/{layer}/{z}/{ty}/{tx}.jpeg",
        f"{base}/{key}/{layer}/{z}/{ty}/{tx}.png",
        f"{_XDWORLD}/{z}/{tx}/{ty}.jpeg",
    ]
    return urls


def _fetch_one_tile(client: httpx.Client, urls: list[str]) -> Image.Image | None:
    for url in urls:
        try:
            resp = client.get(url)
        except httpx.HTTPError:
            continue
        if resp.status_code != 200:
            continue
        if not _is_raster(resp.content, resp.headers.get("content-type", "")):
            continue
        tile = _open_rgb(resp.content)
        if tile is not None:
            return tile
    return None


def fetch_satellite_mosaic(
    min_lng: float,
    min_lat: float,
    max_lng: float,
    max_lat: float,
    zoom: int | None = None,
    radius_m: float = 800,
) -> Image.Image | None:
    settings = get_settings()
    hc = load_hardcode()
    layer = str(hc.get("VWORLD_SATELLITE_LAYER") or "Satellite")
    z = int(zoom or _zoom_for_radius(radius_m))
    z = max(12, min(19, z))
    x0, y1 = _lonlat_to_pixel(min_lng, min_lat, z)
    x1, y0 = _lonlat_to_pixel(max_lng, max_lat, z)
    tx0 = int(math.floor(min(x0, x1) / _TILE))
    tx1 = int(math.floor(max(x0, x1) / _TILE))
    ty0 = int(math.floor(min(y0, y1) / _TILE))
    ty1 = int(math.floor(max(y0, y1) / _TILE))
    n_tiles = 2**z
    tx0 = max(0, tx0)
    ty0 = max(0, ty0)
    tx1 = min(n_tiles - 1, tx1)
    ty1 = min(n_tiles - 1, ty1)
    cols = tx1 - tx0 + 1
    rows = ty1 - ty0 + 1
    if cols <= 0 or rows <= 0 or cols * rows > 120:
        return None
    mosaic = Image.new("RGB", (cols * _TILE, rows * _TILE), (230, 230, 228))
    base = str(hc.get("VWORLD_WMTS_BASE") or "https://api.vworld.kr/req/wmts/1.0.0")
    key = settings.vworld_api_key
    headers = {"Referer": settings.vworld_domain, "User-Agent": "sitedia/1.0"}
    pasted = 0
    try:
        with httpx.Client(timeout=18.0, follow_redirects=True, headers=headers) as client:
            for ty in range(ty0, ty1 + 1):
                for tx in range(tx0, tx1 + 1):
                    urls = satellite_tile_urls(z, tx, ty, key, layer, base)
                    if not settings.has_vworld_key:
                        urls = [u for u in urls if "xdworld.vworld.kr" in u]
                    tile = _fetch_one_tile(client, urls)
                    if tile is None:
                        continue
                    mosaic.paste(tile, ((tx - tx0) * _TILE, (ty - ty0) * _TILE))
                    pasted += 1
    except httpx.HTTPError:
        return None
    if pasted == 0:
        return None
    left = min(x0, x1) - tx0 * _TILE
    upper = min(y0, y1) - ty0 * _TILE
    right = max(x0, x1) - tx0 * _TILE
    lower = max(y0, y1) - ty0 * _TILE
    if right - left < 4 or lower - upper < 4:
        return mosaic
    return mosaic.crop((int(left), int(upper), int(math.ceil(right)), int(math.ceil(lower))))


def mosaic_to_panel_png(
    image: Image.Image,
    clip: Any,
    origin_lng: float,
    origin_lat: float,
    radius_m: float,
    size_px: int = 800,
    shape_kind: str = "circle",
) -> bytes:
    """모자이크를 패널 정사각 PNG로. clip 밖은 투명."""
    img = image.convert("RGBA").resize((size_px, size_px), Image.Resampling.BILINEAR)
    mask = Image.new("L", (size_px, size_px), 0)
    draw = ImageDraw.Draw(mask)
    if shape_kind == "rect":
        draw.rectangle((0, 0, size_px - 1, size_px - 1), fill=255)
    else:
        draw.ellipse((1, 1, size_px - 2, size_px - 2), fill=255)
    img.putalpha(mask)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
