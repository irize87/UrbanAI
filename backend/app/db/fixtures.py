from __future__ import annotations

"""서초 일부 mock geometry — VWorld 키 없이 UI/렌더 검증용."""

import json

from shapely.geometry import Point, box, LineString

from app.core.config import load_hardcode
from app.db.schema import connect, init_db


def _offset_box(lng: float, lat: float, dx: float, dy: float, w: float, h: float) -> str:
    # 대략적 도→미터 (서울 위도)
    m_lng = 0.000011 * dx
    m_lat = 0.000009 * dy
    g = box(lng + m_lng, lat + m_lat, lng + m_lng + w * 0.000011, lat + m_lat + h * 0.000009)
    return g.wkt


def load_fixtures(*, force: bool = False) -> dict:
    """개발용 샘플. 실데이터(source != fixture)가 있으면 덮어쓰지 않는다."""
    init_db()
    center = load_hardcode()["SEOCHO_CENTER"]
    lng, lat = float(center["lng"]), float(center["lat"])

    with connect() as conn:
        real_n = conn.execute(
            "SELECT COUNT(*) AS c FROM buildings WHERE source != 'fixture'"
        ).fetchone()["c"]
        if real_n > 0 and not force:
            total = conn.execute("SELECT COUNT(*) AS c FROM buildings").fetchone()["c"]
            return {
                "skipped": True,
                "reason": "실데이터가 있어 fixture를 덮어쓰지 않습니다.",
                "buildings": total,
            }

    buildings = [
        ("f1", "주거", "아파트", 1995, 15, 48.0, 12000.0, _offset_box(lng, lat, -120, -80, 80, 60)),
        ("f2", "업무", "오피스", 2012, 20, 70.0, 18000.0, _offset_box(lng, lat, 40, -40, 50, 70)),
        ("f3", "근린생활", "근생", 1988, 4, None, 800.0, _offset_box(lng, lat, -40, 60, 30, 25)),
        ("f4", "판매", "상가", 2005, 6, 22.0, 1500.0, _offset_box(lng, lat, 90, 50, 40, 35)),
        ("f5", "미상", None, None, None, None, None, _offset_box(lng, lat, -180, 20, 25, 20)),
        ("f6", "교육복지", "학교", 1978, 5, 18.0, 9000.0, _offset_box(lng, lat, 140, -120, 90, 70)),
        ("f7", "주거", "다세대", 2001, 5, None, 600.0, _offset_box(lng, lat, -220, -150, 28, 22)),
        ("f8", "업무", "타워", 2019, 30, 110.0, 40000.0, _offset_box(lng, lat, 20, 140, 45, 45)),
    ]

    with connect() as conn:
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
            conn.execute(f"DELETE FROM {table}")

        conn.executemany(
            """
            INSERT INTO buildings(
                ext_id, use_cat, use_raw, year, floors, height_m, gfa_m2, geom_wkt, source, sgg_cd
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'fixture', ?)
            """,
            [(*row, "11650") for row in buildings]
            + [
                (
                    "f9",
                    "업무",
                    "인접오피스",
                    2015,
                    12,
                    40.0,
                    4000.0,
                    _offset_box(lng, lat, 280, -20, 40, 40),
                    "11680",
                )
            ],
        )

        conn.executemany(
            "INSERT INTO sgg_bounds(sgg_cd, name, kind, geom_wkt) VALUES (?, ?, ?, ?)",
            [
                (
                    "11650",
                    "서초구",
                    "primary",
                    box(lng - 0.005, lat - 0.004, lng + 0.0022, lat + 0.004).wkt,
                ),
                (
                    "11680",
                    "강남구",
                    "adjacent",
                    box(lng + 0.0022, lat - 0.004, lng + 0.008, lat + 0.004).wkt,
                ),
                (
                    "11590",
                    "동작구",
                    "adjacent",
                    box(lng - 0.01, lat - 0.002, lng - 0.005, lat + 0.003).wkt,
                ),
                (
                    "11620",
                    "관악구",
                    "adjacent",
                    box(lng - 0.008, lat - 0.008, lng - 0.002, lat - 0.004).wkt,
                ),
            ],
        )

        conn.execute(
            "INSERT INTO zoning(zone_cat, zone_name, geom_wkt, source) VALUES (?, ?, ?, 'fixture')",
            ("주거", "제3종일반주거", box(lng - 0.004, lat - 0.003, lng + 0.001, lat + 0.003).wkt),
        )
        conn.execute(
            "INSERT INTO zoning(zone_cat, zone_name, geom_wkt, source) VALUES (?, ?, ?, 'fixture')",
            ("상업", "일반상업", box(lng + 0.001, lat - 0.002, lng + 0.004, lat + 0.002).wkt),
        )

        conn.execute(
            "INSERT INTO green(kind, name, geom_wkt, source) VALUES (?, ?, ?, 'fixture')",
            ("other", "fixture숲", box(lng - 0.004, lat + 0.001, lng - 0.0005, lat + 0.004).wkt),
        )
        conn.execute(
            "INSERT INTO green(kind, name, geom_wkt, source) VALUES (?, ?, ?, 'fixture')",
            ("park", "fixture공원", box(lng - 0.003, lat + 0.002, lng - 0.001, lat + 0.0035).wkt),
        )
        conn.execute(
            "INSERT INTO green(kind, name, geom_wkt, source) VALUES (?, ?, ?, 'fixture')",
            ("water", "fixture하천", LineString([(lng - 0.004, lat - 0.002), (lng + 0.004, lat - 0.0015)]).buffer(0.00015).wkt),
        )

        conn.execute(
            "INSERT INTO roads(road_class, name, geom_wkt, source) VALUES (?, ?, ?, 'fixture')",
            ("primary", "서초대로", LineString([(lng - 0.005, lat), (lng + 0.005, lat)]).wkt),
        )
        conn.execute(
            "INSERT INTO roads(road_class, name, geom_wkt, source) VALUES (?, ?, ?, 'fixture')",
            ("secondary", "반포대로", LineString([(lng, lat - 0.004), (lng, lat + 0.004)]).wkt),
        )

        street_rows = []
        for i in range(-8, 9):
            t_lng = lng + i * 0.00032
            t_lat = lat + 0.00005 * ((i % 3) - 1)
            street_rows.append(
                (
                    "서초구",
                    "서초대로",
                    "은행나무",
                    "서울특별시 서초구 서초대로",
                    "서울특별시 서초구 서초동",
                    t_lng,
                    t_lat,
                    Point(t_lng, t_lat).wkt,
                    "fixture",
                    "11650",
                )
            )
        conn.executemany(
            """
            INSERT INTO street_trees(
                cgg, rte, trspc, road_nm_addr, lotno_addr,
                lng, lat, geom_wkt, source, sgg_cd
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            street_rows,
        )

        conn.execute(
            "INSERT INTO transit(kind, name, line_name, geom_wkt, source) VALUES (?, ?, ?, ?, 'fixture')",
            ("subway", "서초역", "2호선", Point(lng - 0.001, lat - 0.0005).wkt),
        )
        conn.execute(
            "INSERT INTO transit(kind, name, line_name, geom_wkt, source) VALUES (?, ?, ?, ?, 'fixture')",
            ("bus", "버스정류장", None, Point(lng + 0.0015, lat + 0.001).wkt),
        )

        conn.execute(
            "INSERT INTO parcels(pnu, jimok, geom_wkt, source) VALUES (?, ?, ?, 'fixture')",
            ("1165010100", "대", box(lng - 0.0008, lat - 0.0006, lng - 0.0002, lat - 0.0001).wkt),
        )
        conn.execute(
            "INSERT INTO parcels(pnu, jimok, geom_wkt, source) VALUES (?, ?, ?, 'fixture')",
            ("1165010101", "대", box(lng - 0.0002, lat - 0.0006, lng + 0.0004, lat - 0.0001).wkt),
        )
        conn.execute(
            "INSERT INTO parcels(pnu, jimok, geom_wkt, source) VALUES (?, ?, ?, 'fixture')",
            ("1165010102", "도", box(lng - 0.003, lat - 0.00015, lng + 0.003, lat + 0.00015).wkt),
        )
        conn.execute(
            "INSERT INTO parcels(pnu, jimok, geom_wkt, source) VALUES (?, ?, ?, 'fixture')",
            (
                "1165010103",
                "대",
                box(lng + 0.0015, lat + 0.001, lng + 0.0045, lat + 0.0038).wkt,
            ),
        )

        n_dem = 10
        lats = [lat - 0.007 + 0.014 * i / (n_dem - 1) for i in range(n_dem)]
        lngs = [lng - 0.009 + 0.018 * j / (n_dem - 1) for j in range(n_dem)]
        grid = []
        for i, _la in enumerate(lats):
            row = []
            for j, _lo in enumerate(lngs):
                row.append(18.0 + i * 9.5 + j * 2.2)
            grid.append(row)
        terrain = {
            "lats": lats,
            "lngs": lngs,
            "grid": grid,
            "relief_m": grid[-1][-1] - grid[0][0],
            "step_m": 90,
        }
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
            ("terrain_grid", json.dumps(terrain)),
        )

        conn.execute(
            "INSERT INTO blocks(geom_wkt, source) VALUES (?, 'fixture')",
            (box(lng - 0.002, lat - 0.002, lng + 0.002, lat + 0.001).wkt,),
        )
        conn.execute(
            "INSERT INTO district(kind, name, geom_wkt, source) VALUES (?, ?, ?, 'fixture')",
            ("duz", "지구단위계획(샘플)", box(lng - 0.0015, lat - 0.001, lng + 0.0025, lat + 0.0015).wkt),
        )
        conn.execute(
            "INSERT INTO planned(kind, name, geom_wkt, source) VALUES (?, ?, ?, 'fixture')",
            ("school", "서초초(샘플)", box(lng + 0.002, lat - 0.0025, lng + 0.003, lat - 0.0015).wkt),
        )

        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
            ("data_mode", "fixture"),
        )
        conn.execute(
            "INSERT INTO sync_log(layer, source, feature_count, status, message) VALUES (?, ?, ?, ?, ?)",
            ("all", "fixture", len(buildings), "ok", "개발용 fixture 적재"),
        )
        conn.commit()

    from app.services.layer_cache import invalidate_layer_cache

    invalidate_layer_cache()
    return {"buildings": len(buildings), "center": {"lat": lat, "lng": lng}}
