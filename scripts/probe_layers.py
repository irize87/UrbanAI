import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.geo import seocho_bbox
from app.sync.monthly import fetch_vworld_wfs


async def main() -> None:
    b = seocho_bbox()
    tile = f"{b['min_lat']},{b['min_lng']},{b['min_lat'] + 0.02},{b['min_lng'] + 0.02}"
    for tn in [
        "lt_c_uq162",
        "lt_c_wkmstrm",
        "lt_c_upisuq161",
        "lt_c_upisuq153",
        "lt_c_upisuq155",
        "lp_pa_cbnd_bubun",
    ]:
        try:
            d = await fetch_vworld_wfs(tn, tile, max_features=3)
            feats = d.get("features") or []
            props = (feats[0].get("properties") if feats else {}) or {}
            print(tn, "matched", d.get("numberMatched"), "keys", list(props.keys())[:10])
        except Exception as exc:
            print(tn, "ERR", exc)


if __name__ == "__main__":
    asyncio.run(main())
