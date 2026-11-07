from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from shapely.geometry import box

from app.core.config import get_settings, load_hardcode
from app.db.schema import connect, init_db
from app.render.trees import merge_tree_stems, street_tree_stems
from app.sync.street_trees import (
    normalize_lonlat,
    parse_street_tree_payload,
    row_to_db_tuple,
    street_tree_url,
    sync_street_trees,
)

SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<streetTreeInfo>
<list_total_count>287636</list_total_count>
<RESULT>
<CODE>INFO-000</CODE>
<MESSAGE>정상 처리되었습니다</MESSAGE>
</RESULT>
<row>
<CGG>종로구</CGG>
<RTE>경교장길</RTE>
<TRSPC>벚나무류</TRSPC>
<ROAD_NM_ADDR>서울특별시 종로구 경교장1길 1</ROAD_NM_ADDR>
<LOTNO_ADDR>서울특별시 종로구 교남동 8-2</LOTNO_ADDR>
<LOT>126.9659692</LOT>
<LAT>37.56758229</LAT>
</row>
</streetTreeInfo>
"""


def test_parse_json_payload() -> None:
    payload = {
        "streetTreeInfo": {
            "list_total_count": 3,
            "RESULT": {"CODE": "INFO-000", "MESSAGE": "ok"},
            "row": [
                {
                    "CGG": "서초구",
                    "RTE": "논현로",
                    "TRSPC": "은행나무",
                    "LOT": "127.0454824",
                    "LAT": "37.47657291",
                }
            ],
        }
    }
    total, code, rows = parse_street_tree_payload(payload)
    assert total == 3
    assert code == "INFO-000"
    assert rows[0]["CGG"] == "서초구"


def test_parse_xml_payload() -> None:
    total, code, rows = parse_street_tree_payload(SAMPLE_XML)
    assert total == 287636
    assert code == "INFO-000"
    assert rows[0]["TRSPC"] == "벚나무류"
    assert rows[0]["LOT"] == "126.9659692"


def test_normalize_lonlat_swap_and_reject() -> None:
    swapped = normalize_lonlat("37.48", "127.03")
    assert swapped is not None
    assert abs(swapped[0] - 127.03) < 1e-6
    assert abs(swapped[1] - 37.48) < 1e-6
    assert normalize_lonlat("0", "0") is None
    assert normalize_lonlat("bad", "37.4") is None


def test_row_to_db_tuple_point() -> None:
    item = row_to_db_tuple(
        {
            "CGG": "서초구",
            "RTE": "논현로",
            "TRSPC": "은행나무",
            "ROAD_NM_ADDR": "",
            "LOTNO_ADDR": "양재동",
            "LOT": "127.0454824",
            "LAT": "37.47657291",
        },
        "11650",
    )
    assert item is not None
    assert item[5] == 127.0454824
    assert item[6] == 37.47657291
    assert item[7].startswith("POINT")
    assert item[9] == "11650"


def test_street_tree_url_uses_service_not_hardcoded_key() -> None:
    hc = load_hardcode()
    assert "SEOUL_OPEN_API_KEY" not in hc
    url = street_tree_url("TESTKEY", 1, 5, "서초구")
    assert "streetTreeInfo" in url
    assert "TESTKEY" in url
    assert "%EC%84%9C%EC%B4%88%EA%B5%AC" in url
    blob = json.dumps(hc)
    assert "SEOUL_OPEN_API_KEY" not in hc
    assert "openapi.seoul.go.kr" in blob


def test_sync_skipped_without_key(monkeypatch) -> None:
    class Dummy:
        seoul_open_api_key = ""
        has_seoul_open_key = False

    monkeypatch.setattr("app.sync.street_trees.get_settings", lambda: Dummy())
    result = asyncio.run(sync_street_trees())
    assert result["status"] == "skipped"
    assert result["feature_count"] == 0


def test_street_tree_stems_clip(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "trees.sqlite"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    get_settings.cache_clear()
    try:
        init_db()
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO street_trees(
                    cgg, rte, trspc, road_nm_addr, lotno_addr,
                    lng, lat, geom_wkt, source, sgg_cd
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "서초구",
                    "서초대로",
                    "은행나무",
                    "",
                    "",
                    127.0324,
                    37.4837,
                    "POINT (127.0324 37.4837)",
                    "fixture",
                    "11650",
                ),
            )
            conn.commit()
        inside = street_tree_stems(box(127.03, 37.48, 127.04, 37.49))
        outside = street_tree_stems(box(126.9, 37.4, 126.91, 37.41))
        assert len(inside) == 1
        assert inside[0]["kind"] == "tree"
        assert outside == []
    finally:
        get_settings.cache_clear()


def test_merge_tree_stems_keeps_hsv_and_fills_gap() -> None:
    hsv = [{"lng": 127.0324, "lat": 37.4837, "r_m": 2.0}]
    near = [{"lng": 127.03241, "lat": 37.48371, "r_m": 3.2}]
    far = [{"lng": 127.0400, "lat": 37.4900, "r_m": 3.2}]
    merged_near = merge_tree_stems(hsv, near, min_sep_m=4.0)
    merged_far = merge_tree_stems(hsv, far, min_sep_m=4.0)
    assert len(merged_near) == 1
    assert len(merged_far) == 2
    only_street = merge_tree_stems([], far, min_sep_m=4.0)
    assert len(only_street) == 1
