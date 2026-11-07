from __future__ import annotations

"""Copernicus Data Space Sentinel Hub Process API → DEM 격자."""

import math
from io import BytesIO
from typing import Any

import httpx
import numpy as np
import tifffile

from app.core.config import get_settings, load_hardcode

DEM_EVALSCRIPT = """
//VERSION=3
function setup() {
  return {
    input: ["DEM"],
    output: { id: "default", bands: 1, sampleType: SampleType.FLOAT32 },
  };
}
function evaluatePixel(sample) {
  return [sample.DEM];
}
"""


def dem_output_size(
    bbox: dict[str, float],
    step_m: float,
    max_side: int = 2500,
) -> tuple[int, int]:
    """bbox와 목표 간격으로 Process API width/height 계산."""
    lat_m = (bbox["max_lat"] - bbox["min_lat"]) * 111320
    mid_lat = (bbox["min_lat"] + bbox["max_lat"]) / 2
    lng_m = (bbox["max_lng"] - bbox["min_lng"]) * 111320 * math.cos(
        math.radians(mid_lat)
    )
    height = max(32, min(max_side, int(round(lat_m / step_m))))
    width = max(32, min(max_side, int(round(lng_m / step_m))))
    return width, height


def _extract_tiff(data: bytes) -> bytes:
    if data[:2] in (b"II", b"MM"):
        return data
    for magic in (b"II*\x00", b"MM\x00*"):
        idx = data.find(magic)
        if idx >= 0:
            return data[idx:]
    raise ValueError("DEM 응답이 TIFF가 아닙니다.")


def dem_tiff_to_grid(
    tiff_bytes: bytes,
    bbox: dict[str, float],
) -> tuple[list[float], list[float], list[list[float]]]:
    """북쪽이 위인 GeoTIFF를 lats 오름차순 격자로 변환."""
    arr = np.asarray(tifffile.imread(BytesIO(_extract_tiff(tiff_bytes))), dtype=np.float32)
    if arr.ndim == 3:
        arr = arr[:, :, 0]
    arr = np.flipud(arr)
    valid = np.isfinite(arr) & (arr >= -500.0) & (arr <= 9000.0)
    if not np.any(valid):
        raise ValueError("유효한 DEM 표고가 없습니다.")
    fill = float(np.median(arr[valid]))
    arr = np.where(valid, arr, fill)
    n_lat, n_lng = int(arr.shape[0]), int(arr.shape[1])
    if n_lat < 2 or n_lng < 2:
        raise ValueError("DEM TIFF 격자가 너무 작습니다.")
    lats = [
        bbox["min_lat"] + (bbox["max_lat"] - bbox["min_lat"]) * i / (n_lat - 1)
        for i in range(n_lat)
    ]
    lngs = [
        bbox["min_lng"] + (bbox["max_lng"] - bbox["min_lng"]) * j / (n_lng - 1)
        for j in range(n_lng)
    ]
    return lats, lngs, arr.astype(float).tolist()


async def fetch_cdse_token(client: httpx.AsyncClient) -> str:
    settings = get_settings()
    hc = load_hardcode()
    token_url = str(
        hc.get("CDSE_TOKEN_URL")
        or "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
    )
    resp = await client.post(
        token_url,
        data={
            "grant_type": "client_credentials",
            "client_id": settings.cdse_client_id,
            "client_secret": settings.cdse_client_secret,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    resp.raise_for_status()
    token = (resp.json() or {}).get("access_token")
    if not token:
        raise RuntimeError("CDSE 토큰 응답에 access_token이 없습니다.")
    return str(token)


async def download_cdse_dem(bbox: dict[str, float]) -> dict[str, Any]:
    """Sentinel Hub Process API로 Copernicus DEM 격자를 받는다."""
    settings = get_settings()
    hc = load_hardcode()
    instance = (settings.cdse_dem_instance or "COPERNICUS_90").strip().upper()
    if instance not in {"COPERNICUS_30", "COPERNICUS_90"}:
        instance = "COPERNICUS_90"
    step_m = float(hc.get("TERRAIN_STEP_M") or 90)
    width, height = dem_output_size(bbox, step_m)
    process_url = str(
        hc.get("CDSE_PROCESS_URL") or "https://sh.dataspace.copernicus.eu/process/v1"
    )
    body = {
        "input": {
            "bounds": {
                "properties": {"crs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84"},
                "bbox": [
                    bbox["min_lng"],
                    bbox["min_lat"],
                    bbox["max_lng"],
                    bbox["max_lat"],
                ],
            },
            "data": [
                {
                    "type": "dem",
                    "dataFilter": {"demInstance": instance},
                    "processing": {
                        "upsampling": "BILINEAR",
                        "downsampling": "BILINEAR",
                    },
                }
            ],
        },
        "output": {
            "width": width,
            "height": height,
            "responses": [
                {"identifier": "default", "format": {"type": "image/tiff"}}
            ],
        },
        "evalscript": DEM_EVALSCRIPT,
    }
    async with httpx.AsyncClient(timeout=180.0) as client:
        token = await fetch_cdse_token(client)
        resp = await client.post(
            process_url,
            json=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "image/tiff",
            },
        )
        if resp.status_code >= 400 and instance == "COPERNICUS_30":
            body["input"]["data"][0]["dataFilter"]["demInstance"] = "COPERNICUS_90"
            resp = await client.post(
                process_url,
                json=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "image/tiff",
                },
            )
            instance = "COPERNICUS_90"
        if resp.status_code >= 400:
            raise RuntimeError(
                f"CDSE Process API HTTP {resp.status_code}: {resp.text[:400]}"
            )
        ctype = (resp.headers.get("content-type") or "").lower()
        if "json" in ctype:
            raise RuntimeError(f"CDSE Process API JSON 오류: {resp.text[:400]}")
        tiff_bytes = resp.content

    lats, lngs, grid = dem_tiff_to_grid(tiff_bytes, bbox)
    flat = [v for row in grid for v in row]
    relief = max(flat) - min(flat) if flat else 0
    return {
        "lats": lats,
        "lngs": lngs,
        "grid": grid,
        "min": min(flat) if flat else 0,
        "max": max(flat) if flat else 0,
        "relief_m": relief,
        "source": "Copernicus DEM © ESA",
        "dataset": f"{instance} (CDSE Sentinel Hub Process API)",
        "step_m": step_m,
        "width": width,
        "height": height,
    }
