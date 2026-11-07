from __future__ import annotations

"""자치구 경계 적재·조회. 3D 인접 구 클립에 사용."""

from typing import Any

from shapely import wkt as shapely_wkt
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from app.core.districts import primary_sgg_cd
from app.db.schema import connect

SGG_TABLES = (
    "buildings",
    "zoning",
    "green",
    "roads",
    "transit",
    "parcels",
    "blocks",
    "district",
    "planned",
    "street_trees",
)


def ensure_sgg_columns(conn: Any) -> None:
    """기존 DB에 sgg_cd / sgg_bounds 를 추가한다."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sgg_bounds (
            sgg_cd TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            kind TEXT NOT NULL,
            geom_wkt TEXT NOT NULL
        )
        """
    )
    for table in SGG_TABLES:
        cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if "sgg_cd" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN sgg_cd TEXT")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_buildings_sgg ON buildings(sgg_cd)"
    )
    primary = primary_sgg_cd()
    conn.execute(
        "UPDATE buildings SET sgg_cd = ? "
        "WHERE sgg_cd IS NULL AND source != 'fixture'",
        (primary,),
    )


def list_sgg_polygons() -> list[tuple[str, str, BaseGeometry]]:
    """(code, kind, geom) 목록."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT sgg_cd, kind, geom_wkt FROM sgg_bounds"
        ).fetchall()
    out: list[tuple[str, str, BaseGeometry]] = []
    for row in rows:
        try:
            geom = shapely_wkt.loads(row["geom_wkt"])
        except Exception:
            continue
        if geom.is_empty:
            continue
        if not geom.is_valid:
            geom = geom.buffer(0)
        out.append((str(row["sgg_cd"]), str(row["kind"]), geom))
    return out


def load_sgg_region(include_adjacent: bool) -> BaseGeometry | None:
    """체크 해제=서초만, 체크=서초+인접 합집합. 경계 없으면 None."""
    polys = list_sgg_polygons()
    if not polys:
        return None
    if include_adjacent:
        geoms = [g for _code, _kind, g in polys]
    else:
        primary = primary_sgg_cd()
        geoms = [g for code, _kind, g in polys if code == primary]
        if not geoms:
            geoms = [g for _code, kind, g in polys if kind == "primary"]
    if not geoms:
        return None
    merged = unary_union(geoms)
    if merged.is_empty:
        return None
    if not merged.is_valid:
        merged = merged.buffer(0)
    return merged


def assign_sgg_cd(geom: BaseGeometry) -> str | None:
    if geom is None or geom.is_empty:
        return None
    pt = geom.centroid
    for code, _kind, poly in list_sgg_polygons():
        try:
            if poly.contains(pt) or poly.intersects(pt):
                return code
        except Exception:
            continue
    return None


def backfill_sgg(table: str) -> None:
    """sgg_cd가 비어 있는 행을 구 경계 중심으로 채운다."""
    polys = list_sgg_polygons()
    if not polys:
        return
    with connect() as conn:
        cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if "sgg_cd" not in cols:
            return
        rows = conn.execute(
            f"SELECT id, geom_wkt FROM {table} WHERE sgg_cd IS NULL OR sgg_cd = ''"
        ).fetchall()
        for row in rows:
            try:
                geom = shapely_wkt.loads(row["geom_wkt"])
            except Exception:
                continue
            code = None
            pt = geom.centroid if not geom.is_empty else None
            if pt is None:
                continue
            for sgg_cd, _kind, poly in polys:
                try:
                    if poly.contains(pt) or poly.intersects(pt):
                        code = sgg_cd
                        break
                except Exception:
                    continue
            if code:
                conn.execute(
                    f"UPDATE {table} SET sgg_cd = ? WHERE id = ?",
                    (code, row["id"]),
                )
        conn.commit()
