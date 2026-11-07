"""WFS 필터/페이징 검증용 임시 스크립트."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import httpx

from app.core.config import get_settings, load_hardcode
from app.core.geo import seocho_bbox


async def main() -> None:
    settings = get_settings()
    hc = load_hardcode()
    bbox = seocho_bbox()
    bbox_str = (
        f"{bbox['min_lat']},{bbox['min_lng']},{bbox['max_lat']},{bbox['max_lng']}"
    )
    url = hc["VWORLD_WFS_URL"]

    filt = (
        "<Filter><PropertyIsEqualTo>"
        "<PropertyName>col_adm_se</PropertyName>"
        "<Literal>11650</Literal>"
        "</PropertyIsEqualTo></Filter>"
    )
    params = {
        "service": "WFS",
        "version": "1.1.0",
        "request": "GetFeature",
        "typename": "lt_c_bldginfo",
        "output": "application/json",
        "srsname": "EPSG:4326",
        "bbox": bbox_str,
        "maxFeatures": "5",
        "key": settings.vworld_api_key,
        "domain": settings.vworld_domain,
        "filter": filt,
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(url, params=params)
        print("filter_status", resp.status_code, resp.headers.get("content-type"))
        print("filter_body_head", resp.text[:200])

        params2 = {
            "service": "WFS",
            "version": "1.1.0",
            "request": "GetFeature",
            "typename": "lt_c_bldginfo",
            "output": "application/json",
            "srsname": "EPSG:4326",
            "bbox": bbox_str,
            "maxFeatures": "3",
            "startIndex": "10",
            "key": settings.vworld_api_key,
            "domain": settings.vworld_domain,
        }
        resp2 = await client.get(url, params=params2)
        data2 = resp2.json()
        feats2 = data2.get("features") or []
        print("startIndex_ufids", [f["properties"].get("ufid") for f in feats2])

        tile = "37.48,127.00,37.49,127.01"
        params3 = dict(params2)
        params3["bbox"] = tile
        params3["maxFeatures"] = "500"
        params3.pop("startIndex", None)
        resp3 = await client.get(url, params=params3)
        data3 = resp3.json()
        feats3 = data3.get("features") or []
        seocho = [
            f
            for f in feats3
            if str(f.get("properties", {}).get("col_adm_se", "")).startswith("11650")
            or str(f.get("properties", {}).get("pnu", "")).startswith("11650")
        ]
        print(
            "tile_matched",
            data3.get("numberMatched"),
            "returned",
            len(feats3),
            "seocho",
            len(seocho),
        )


if __name__ == "__main__":
    asyncio.run(main())
