from __future__ import annotations

"""겹치는 건물 footprint를 하나(용도 1개)로 줄인다."""

from typing import Any

from shapely import STRtree

from app.core.config import load_hardcode
from app.db.schema import connect
from app.render.svg_engine import clip_geom, wkt_to_geom

BldgItem = tuple[dict[str, Any], Any]


def footprint_overlap_ratio() -> float:
    hc = load_hardcode()
    try:
        val = float(hc.get("MASS_FOOTPRINT_OVERLAP") or 0.4)
    except (TypeError, ValueError):
        val = 0.4
    return min(0.9, max(0.2, val))


def building_approval_year(row: dict[str, Any]) -> int:
    """사용승인 연도. 없거나 범위 밖이면 0."""
    try:
        year = int(row.get("year") or 0)
    except (TypeError, ValueError):
        return 0
    if 1800 <= year <= 2100:
        return year
    return 0


def building_keep_score(row: dict[str, Any], geom: Any) -> tuple:
    """남길 건물 점수. 사용승인 연도, 그다음 높이, 그다음 면적."""
    height = building_height_m(row) or 0.0
    area = float(geom.area) if geom is not None and not geom.is_empty else 0.0
    return (building_approval_year(row), height, area)


def building_height_m(row: dict[str, Any]) -> float | None:
    try:
        h = float(row.get("height_m") or 0)
    except (TypeError, ValueError):
        h = 0.0
    if h > 0:
        return h
    try:
        floors = float(row.get("floors") or 0)
    except (TypeError, ValueError):
        return None
    if floors > 0:
        return floors * 3.25
    return None


def _point_inside(geom: Any) -> Any:
    """건물 면 안의 중심. 오목한 면은 내부 점을 쓴다."""
    point = geom.centroid
    try:
        if geom.covers(point):
            return point
    except Exception:
        return point
    return geom.representative_point()


def _older_inside_newer(years: list[int], geoms: list[Any], i: int, j: int) -> int | None:
    """둘 다 연도가 있고, 예전 건물 중심이 더 새 건물 안에 있으면 예전 쪽 인덱스."""
    year_i = years[i]
    year_j = years[j]
    if year_i <= 0 or year_j <= 0 or year_i == year_j:
        return None
    try:
        if year_i > year_j and geoms[i].covers(_point_inside(geoms[j])):
            return j
        if year_j > year_i and geoms[j].covers(_point_inside(geoms[i])):
            return i
    except Exception:
        return None
    return None


def dedupe_overlapping_buildings(
    items: list[BldgItem],
    overlap_ratio: float | None = None,
) -> list[BldgItem]:
    """겹친 건물은 더 최근 승인 연도, 그다음 높이·면적인 하나만 남긴다.

    예전 건물 중심이 더 새 건물 안에 있으면 겹침 비율이 낮아도 예전 건물을 뺀다.
    DB 행은 지우지 않고, 이 결과를 그릴 때만 적용한다.
    """
    if len(items) <= 1:
        return items
    thresh = footprint_overlap_ratio() if overlap_ratio is None else overlap_ratio
    geoms = [geom for _, geom in items]
    areas = [max(float(geom.area), 1e-18) for geom in geoms]
    years = [building_approval_year(row) for row, _geom in items]
    scores = [building_keep_score(row, geom) for row, geom in items]
    order = sorted(range(len(items)), key=lambda idx: scores[idx], reverse=True)
    tree = STRtree(geoms)
    alive = [True] * len(items)
    for i in order:
        if not alive[i]:
            continue
        hits = tree.query(geoms[i], predicate="intersects")
        for raw_j in hits:
            j = int(raw_j)
            if j == i or not alive[j]:
                continue
            inter = geoms[i].intersection(geoms[j])
            if inter.is_empty:
                continue
            ratio = float(inter.area) / min(areas[i], areas[j])
            contained = _older_inside_newer(years, geoms, i, j)
            if contained is not None:
                alive[contained] = False
                if contained == i:
                    break
                continue
            if ratio >= thresh:
                alive[j] = False
    return [items[i] for i in range(len(items)) if alive[i]]


def clip_and_dedupe_buildings(
    rows: list[dict[str, Any]],
    clip: Any,
    *,
    require_height: bool = False,
    overlap_ratio: float | None = None,
) -> list[BldgItem]:
    """clip 안 건물을 모은 뒤 겹치는 footprint를 1개로 줄인다."""
    items: list[BldgItem] = []
    for row in rows:
        try:
            geom = clip_geom(wkt_to_geom(row["geom_wkt"]), clip)
        except (TypeError, ValueError):
            continue
        if geom is None or geom.is_empty:
            continue
        if require_height and building_height_m(row) is None:
            continue
        items.append((row, geom))
    return dedupe_overlapping_buildings(items, overlap_ratio)


def geom_overlaps_existing(
    geom: Any,
    tree: STRtree,
    existing: list[Any],
    overlap_ratio: float | None = None,
) -> bool:
    """신규 footprint가 기존 건물과 충분히 겹치면 True."""
    if geom is None or geom.is_empty or not existing:
        return False
    thresh = footprint_overlap_ratio() if overlap_ratio is None else overlap_ratio
    area = max(float(geom.area), 1e-18)
    for raw_i in tree.query(geom, predicate="intersects"):
        other = existing[int(raw_i)]
        inter = geom.intersection(other)
        if inter.is_empty:
            continue
        if float(inter.area) / min(area, max(float(other.area), 1e-18)) >= thresh:
            return True
    return False


def purge_overlapping_spbd() -> int:
    """GIS 건물과 겹치는 도로명(spbd) 보강분을 삭제한다."""
    with connect() as conn:
        conn.execute("PRAGMA busy_timeout=120000")
        infos = [
            dict(r)
            for r in conn.execute(
                "SELECT id, geom_wkt FROM buildings WHERE source LIKE '%bldginfo%'"
            ).fetchall()
        ]
        fills = [
            dict(r)
            for r in conn.execute(
                "SELECT id, geom_wkt FROM buildings WHERE source LIKE '%spbd%'"
            ).fetchall()
        ]
    info_geoms: list[Any] = []
    for row in infos:
        try:
            geom = wkt_to_geom(row["geom_wkt"])
        except (TypeError, ValueError):
            continue
        if geom is None or geom.is_empty:
            continue
        info_geoms.append(geom)
    if not info_geoms or not fills:
        return 0
    tree = STRtree(info_geoms)
    drop_ids: list[int] = []
    thresh = footprint_overlap_ratio()
    for row in fills:
        try:
            geom = wkt_to_geom(row["geom_wkt"])
        except (TypeError, ValueError):
            continue
        if geom_overlaps_existing(geom, tree, info_geoms, thresh):
            drop_ids.append(int(row["id"]))
    if drop_ids:
        with connect() as conn:
            conn.execute("PRAGMA busy_timeout=120000")
            conn.executemany(
                "DELETE FROM buildings WHERE id = ?",
                [(i,) for i in drop_ids],
            )
            conn.commit()
    return len(drop_ids)
