"""항공 RGB HSV·ExG 수목 검출. 이어진 수관은 면, 가장자리·시가지는 개체 원."""

from __future__ import annotations

from typing import Any

import numpy as np
from PIL import Image, ImageFilter
from shapely import linestrings, polygonize
from shapely.geometry import Point, Polygon
from shapely.geometry.base import BaseGeometry

from app.core.config import load_hardcode
from app.db.schema import connect
from app.render.svg_engine import geom_to_svg_path, to_lonlat, to_metric

_MS_PAIRS: dict[int, tuple[tuple[str, str], ...]] = {
    1: (("L", "T"),),
    2: (("T", "R"),),
    3: (("L", "R"),),
    4: (("R", "B"),),
    5: (("L", "T"), ("R", "B")),
    6: (("T", "B"),),
    7: (("L", "B"),),
    8: (("B", "L"),),
    9: (("T", "B"),),
    10: (("T", "R"), ("B", "L")),
    11: (("R", "B"),),
    12: (("R", "L"),),
    13: (("T", "R"),),
    14: (("T", "L"),),
}
_MS_POS = {
    "T": (0.5, 0.0),
    "R": (1.0, 0.5),
    "B": (0.5, 1.0),
    "L": (0.0, 0.5),
}


def _rgb_to_hsv_mask(arr: np.ndarray, hue_lo: float, hue_hi: float, sat_min: float) -> np.ndarray:
    """arr HxWx3 uint8 → 식생 마스크. hue는 0–360."""
    rgb = arr.astype(np.float32) / 255.0
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    df = mx - mn
    h = np.zeros_like(mx)
    mask = df > 1e-6
    r_eq = mask & (mx == r)
    g_eq = mask & (mx == g)
    b_eq = mask & (mx == b)
    h[r_eq] = ((g[r_eq] - b[r_eq]) / df[r_eq]) % 6.0
    h[g_eq] = (b[g_eq] - r[g_eq]) / df[g_eq] + 2.0
    h[b_eq] = (r[b_eq] - g[b_eq]) / df[b_eq] + 4.0
    hue = h * 60.0
    sat = np.zeros_like(mx)
    sat[mx > 1e-6] = df[mx > 1e-6] / mx[mx > 1e-6]
    veg = (hue >= hue_lo) & (hue <= hue_hi) & (sat >= sat_min)
    return veg


def _exg_mask(arr: np.ndarray, min_exg: float) -> np.ndarray:
    """Excess green (2G−R−B). 항공사진 수목에 HSV보다 덜 까다롭다."""
    rgb = arr.astype(np.float32)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    exg = 2.0 * g - r - b
    bright = (r + g + b) / 3.0
    return (exg >= min_exg) & (g > r) & (g > b) & (bright > 18) & (bright < 230)


def _rasterize_mask(
    geoms: list[Any],
    origin_lng: float,
    origin_lat: float,
    radius_m: float,
    w: int,
    h: int,
) -> np.ndarray:
    from PIL import ImageDraw

    mask_img = Image.new("L", (w, h), 0)
    if not geoms:
        return np.zeros((h, w), dtype=bool)
    draw = ImageDraw.Draw(mask_img)
    ox, oy = to_metric(Point(origin_lng, origin_lat)).coords[0]
    half = radius_m

    def to_px(x: float, y: float) -> tuple[float, float]:
        j = (x - (ox - half)) / (2 * half) * w
        i = ((oy + half) - y) / (2 * half) * h
        return j, i

    for geom in geoms:
        if geom is None or geom.is_empty:
            continue
        gm = to_metric(geom)
        parts = list(gm.geoms) if gm.geom_type == "MultiPolygon" else [gm]
        for part in parts:
            if part.is_empty or part.geom_type != "Polygon":
                continue
            coords = [to_px(x, y) for x, y in part.exterior.coords]
            if len(coords) >= 3:
                draw.polygon(coords, fill=255)
            for hole in part.interiors:
                hole_xy = [to_px(x, y) for x, y in hole.coords]
                if len(hole_xy) >= 3:
                    draw.polygon(hole_xy, fill=0)
    return np.array(mask_img) > 0


def _erode(mask: np.ndarray, radius_px: int) -> np.ndarray:
    """이진 마스크를 radius_px만큼 침식해 수관 내부를 남긴다."""
    k = max(1, int(radius_px))
    size = k * 2 + 1
    img = Image.fromarray(mask.astype(np.uint8) * 255, mode="L")
    out = img.filter(ImageFilter.MinFilter(size))
    return np.array(out) > 0


def _pixel_to_lonlat(
    x: float,
    y: float,
    origin_lng: float,
    origin_lat: float,
    radius_m: float,
    w: int,
    h: int,
) -> tuple[float, float]:
    ox, oy = to_metric(Point(origin_lng, origin_lat)).coords[0]
    px = (2 * radius_m) / w
    py = (2 * radius_m) / h
    mx = ox - radius_m + x * px
    my = oy + radius_m - y * py
    pt = to_lonlat(Point(mx, my))
    return pt.x, pt.y


def _shrink_mask(mask: np.ndarray, max_edge: int) -> np.ndarray:
    """면 추출용으로만 줄인다. 지리 범위는 그대로다."""
    h, w = mask.shape
    if max(h, w) <= max_edge:
        return mask
    img = Image.fromarray(mask.astype(np.uint8) * 255, mode="L")
    img.thumbnail((max_edge, max_edge), Image.Resampling.NEAREST)
    return np.array(img) > 127


def _mask_to_polygons(
    mask: np.ndarray,
    origin_lng: float,
    origin_lat: float,
    radius_m: float,
    min_area_m2: float,
    poly_max_edge: int = 100,
) -> list[BaseGeometry]:
    """이어진 True 화소를 수관 면(폴리곤)으로 바꾼다. 격자원이 아니다."""
    if not mask.any():
        return []
    mask = _shrink_mask(mask, poly_max_edge)
    padded = np.pad(mask.astype(np.uint8), 1, mode="constant", constant_values=0)
    hh, ww = padded.shape
    segs: list[list[list[float]]] = []
    for i in range(hh - 1):
        row = padded[i]
        row1 = padded[i + 1]
        for j in range(ww - 1):
            idx = int(row[j] | (row[j + 1] << 1) | (row1[j + 1] << 2) | (row1[j] << 3))
            pairs = _MS_PAIRS.get(idx)
            if not pairs:
                continue
            for a, b in pairs:
                ax, ay = _MS_POS[a]
                bx, by = _MS_POS[b]
                segs.append(
                    [
                        [j + ax - 1.0, i + ay - 1.0],
                        [j + bx - 1.0, i + by - 1.0],
                    ]
                )
    if not segs:
        return []
    coords = np.asarray(segs, dtype=np.float64)
    geoms = polygonize(linestrings(coords))
    if hasattr(geoms, "geom_type"):
        if geoms.geom_type == "GeometryCollection":
            geom_iter = list(geoms.geoms)
        else:
            geom_iter = [geoms]
    else:
        geom_iter = list(geoms)
    h, w = mask.shape
    px_m = (2 * radius_m) / w
    py_m = (2 * radius_m) / h
    cell_m2 = px_m * py_m
    out: list[BaseGeometry] = []
    for geom in geom_iter:
        if geom is None or geom.is_empty or geom.geom_type != "Polygon":
            continue
        if not geom.is_valid:
            geom = geom.buffer(0)
        if geom.is_empty:
            continue
        parts = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
        for part in parts:
            if part.is_empty or part.geom_type != "Polygon":
                continue
            if abs(part.area) * cell_m2 < min_area_m2:
                continue
            cx, cy = part.representative_point().coords[0]
            ii = int(round(cy))
            jj = int(round(cx))
            if not (0 <= ii < h and 0 <= jj < w and mask[ii, jj]):
                continue
            ring_ll: list[tuple[float, float]] = []
            for x, y in part.exterior.coords:
                ring_ll.append(
                    _pixel_to_lonlat(x, y, origin_lng, origin_lat, radius_m, w, h)
                )
            holes: list[list[tuple[float, float]]] = []
            for hole in part.interiors:
                holes.append(
                    [
                        _pixel_to_lonlat(x, y, origin_lng, origin_lat, radius_m, w, h)
                        for x, y in hole.coords
                    ]
                )
            poly = Polygon(ring_ll, holes)
            if not poly.is_valid:
                poly = poly.buffer(0)
            if poly.is_empty:
                continue
            out.append(poly)
    return out


def _four_dir_radii(
    mask: np.ndarray,
    rows: np.ndarray,
    cols: np.ndarray,
    px_m: float,
    max_m: float,
) -> np.ndarray:
    """각 점에서 동서남북으로 식생이 이어지는 길이(m)."""
    h, w = mask.shape
    n = int(rows.size)
    max_s = max(1, int(np.ceil(max_m / max(px_m, 1e-6))))
    runs = np.zeros((n, 4), dtype=np.float64)
    dirs = ((-1, 0), (1, 0), (0, -1), (0, 1))
    for d, (di, dj) in enumerate(dirs):
        alive = np.ones(n, dtype=bool)
        ii = rows.astype(np.int32, copy=True)
        jj = cols.astype(np.int32, copy=True)
        for step in range(1, max_s + 1):
            ii = ii + di
            jj = jj + dj
            inb = (ii >= 0) & (ii < h) & (jj >= 0) & (jj < w)
            still = np.zeros(n, dtype=bool)
            chk = alive & inb
            if chk.any():
                still[chk] = mask[ii[chk], jj[chk]]
            alive &= still
            if not alive.any():
                break
            runs[alive, d] = step * px_m
    return np.median(runs, axis=1)


def _individual_stems(
    veg: np.ndarray,
    interior: np.ndarray,
    origin_lng: float,
    origin_lat: float,
    radius_m: float,
    rmin: float,
    rmax: float,
    cap: int,
) -> list[dict[str, Any]]:
    """수관 내부는 빼고, 가장자리·시가지 식생만 개체로. 격자 샘플이 아니다."""
    stem = veg & ~interior
    if not stem.any():
        return []
    h, w = veg.shape
    px_m = (2 * radius_m) / w
    stem_u8 = stem.astype(np.uint8) * 255
    dens = (
        np.array(
            Image.fromarray(stem_u8, mode="L").filter(ImageFilter.BoxBlur(1)),
            dtype=np.float32,
        )
        / 255.0
    )
    peak = (
        np.array(
            Image.fromarray(np.clip(dens * 255.0, 0, 255).astype(np.uint8), mode="L").filter(
                ImageFilter.MaxFilter(5)
            ),
            dtype=np.float32,
        )
        / 255.0
    )
    cands = stem & (dens + 1e-4 >= peak) & (dens > 0.12)
    rows, cols = np.nonzero(cands)
    if rows.size == 0:
        rows, cols = np.nonzero(stem)
    if rows.size > 5000:
        pick = np.linspace(0, int(rows.size) - 1, 5000).astype(int)
        rows, cols = rows[pick], cols[pick]
    radii = np.clip(_four_dir_radii(veg, rows, cols, px_m, rmax), rmin, rmax)
    order = np.argsort(-radii)
    cell = max(2.0, 2.5 / px_m)
    buckets: dict[tuple[int, int], list[tuple[int, int, float]]] = {}
    chosen: list[tuple[int, int, float]] = []
    for k in order:
        i = int(rows[k])
        j = int(cols[k])
        r_m = float(radii[k])
        sep_px = max(2.2, 1.3 * r_m) / px_m
        gi = int(i / cell)
        gj = int(j / cell)
        too_close = False
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for yi, xj, _rr in buckets.get((gi + di, gj + dj), ()):
                    if (i - yi) ** 2 + (j - xj) ** 2 < sep_px * sep_px:
                        too_close = True
                        break
                if too_close:
                    break
            if too_close:
                break
        if too_close:
            continue
        buckets.setdefault((gi, gj), []).append((i, j, r_m))
        chosen.append((i, j, r_m))
        if len(chosen) >= cap:
            break
    out: list[dict[str, Any]] = []
    for i, j, r_m in chosen:
        lng, lat = _pixel_to_lonlat(
            j + 0.5, i + 0.5, origin_lng, origin_lat, radius_m, w, h
        )
        out.append({"lng": lng, "lat": lat, "r_m": r_m, "kind": "tree"})
    return out


def detect_trees(
    image: Image.Image,
    origin_lng: float,
    origin_lat: float,
    radius_m: float,
    building_geoms: list[Any],
    water_geoms: list[Any],
    park_geoms: list[Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """수관 면 + 개체 원. 12m 창 밀도 0.7 이상은 면, 나머지는 개체."""
    del park_geoms
    hc = load_hardcode()
    hue_lo = float(hc.get("TREE_HSV_HUE_LO") or 50)
    hue_hi = float(hc.get("TREE_HSV_HUE_HI") or 170)
    sat_min = float(hc.get("TREE_HSV_SAT_MIN") or 0.18)
    exg_min = float(hc.get("TREE_EXG_MIN") or 14)
    win_m = float(hc.get("TREE_WINDOW_M") or 12)
    dens = float(hc.get("TREE_CANOPY_DENSITY") or 0.7)
    rmin = float(hc.get("TREE_RADIUS_MIN_M") or 1.0)
    rmax = float(hc.get("TREE_RADIUS_MAX_M") or 4.2)
    max_edge = int(hc.get("TREE_MAX_EDGE") or 420)
    stem_cap = int(hc.get("TREE_STEM_MAX") or 2400)
    min_area = float(hc.get("TREE_CANOPY_MIN_M2") or 40)
    edge_m = float(hc.get("TREE_EDGE_M") or 3.0)
    img = image.convert("RGB")
    if max(img.size) > max_edge:
        img.thumbnail((max_edge, max_edge), Image.Resampling.BILINEAR)
    rgb = np.array(img)
    h, w, _ = rgb.shape
    veg = _rgb_to_hsv_mask(rgb, hue_lo, hue_hi, sat_min) | _exg_mask(rgb, exg_min)
    # 녹색 옥상·녹조 수면은 스펙트럼으로 안 갈린다. 건물 발자국·수면 폴리곤으로 지운다.
    bmask = _rasterize_mask(building_geoms, origin_lng, origin_lat, radius_m, w, h)
    wmask = _rasterize_mask(water_geoms, origin_lng, origin_lat, radius_m, w, h)
    veg = veg & ~bmask & ~wmask
    px_m = (radius_m * 2) / w
    rad = max(1, int(round(win_m / px_m / 2)))
    veg_u8 = veg.astype(np.uint8) * 255
    blur = Image.fromarray(veg_u8, mode="L").filter(ImageFilter.BoxBlur(rad))
    dens_arr = np.array(blur, dtype=np.float32) / 255.0
    canopy = veg & (dens_arr >= dens)
    erode_px = max(1, int(round(edge_m / max(px_m, 1e-6))))
    interior = _erode(canopy, erode_px) if canopy.any() else canopy
    polys = _mask_to_polygons(canopy, origin_lng, origin_lat, radius_m, min_area)
    canopies = [{"kind": "canopy", "geom": g} for g in polys]
    stems = _individual_stems(
        veg, interior, origin_lng, origin_lat, radius_m, rmin, rmax, stem_cap
    )
    return canopies, stems


def street_tree_stems(clip: Any) -> list[dict[str, Any]]:
    """클립 안 가로수 점. 서울 OpenAPI를 월간 적재한 로컬 좌표."""
    hc = load_hardcode()
    r_m = float(hc.get("TREE_STREET_R_M", 2.2))
    cap = int(hc.get("TREE_STREET_MAX", 8000))
    minx, miny, maxx, maxy = clip.bounds
    try:
        with connect() as conn:
            rows = conn.execute(
                """
                SELECT lng, lat FROM street_trees
                WHERE lng BETWEEN ? AND ? AND lat BETWEEN ? AND ?
                """,
                (minx, maxx, miny, maxy),
            ).fetchall()
    except Exception:
        return []
    items: list[dict[str, Any]] = []
    for row in rows:
        lng = float(row["lng"])
        lat = float(row["lat"])
        pt = Point(lng, lat)
        if clip.disjoint(pt):
            continue
        items.append({"lng": lng, "lat": lat, "r_m": r_m, "kind": "tree"})
    if len(items) > cap:
        idx = np.linspace(0, len(items) - 1, cap).astype(int)
        items = [items[int(i)] for i in idx]
    return items


def merge_tree_stems(
    hsv_stems: list[dict[str, Any]],
    street_stems: list[dict[str, Any]],
    min_sep_m: float | None = None,
) -> list[dict[str, Any]]:
    """위성 개체를 기준으로 두고, 비는 자리에만 가로수를 보탠다."""
    hc = load_hardcode()
    sep = float(min_sep_m if min_sep_m is not None else hc.get("TREE_MERGE_SEP_M", 4.0))
    out = [dict(item, kind="tree") for item in hsv_stems]
    if not street_stems:
        return out
    if not out:
        return [dict(item, kind="tree") for item in street_stems]
    xy_list = [to_metric(Point(item["lng"], item["lat"])).coords[0] for item in out]
    base_xy = np.array(xy_list, dtype=np.float64)
    sep2 = sep * sep
    for item in street_stems:
        xy = np.array(
            to_metric(Point(item["lng"], item["lat"])).coords[0],
            dtype=np.float64,
        )
        dist2 = float(np.min(np.sum((base_xy - xy) ** 2, axis=1)))
        if dist2 < sep2:
            continue
        out.append(dict(item, kind="tree"))
        base_xy = np.vstack([base_xy, xy])
    return out


def tree_paths(
    canopies: list[dict[str, Any]],
    stems: list[dict[str, Any]],
    origin_lng: float,
    origin_lat: float,
    scale: float,
    fill: str,
    canopy_fill: str | None = None,
) -> list[dict[str, Any]]:
    paths: list[dict[str, Any]] = []
    ox, oy = to_metric(Point(origin_lng, origin_lat)).coords[0]
    wash = canopy_fill or "#b5d0a0"

    for item in canopies:
        geom = item.get("geom")
        if geom is None or getattr(geom, "is_empty", True):
            continue
        d = geom_to_svg_path(geom, origin_lng, origin_lat, scale, filled=False)
        if not d:
            continue
        paths.append(
            {
                "kind": "path",
                "d": d,
                "fill": wash,
                "cat": "canopy",
                "opacity": 0.72,
                "stroke": "none",
                "stroke_width": 0,
            }
        )

    for item in stems:
        pt = to_metric(Point(item["lng"], item["lat"]))
        r_px = float(item["r_m"]) * scale
        if r_px < 0.35:
            continue
        paths.append(
            {
                "kind": "circle",
                "cx": (pt.x - ox) * scale,
                "cy": -(pt.y - oy) * scale,
                "r": r_px,
                "fill": fill,
                "cat": "tree",
                "opacity": 0.9,
                "stroke": "none",
                "stroke_width": 0,
            }
        )
    return paths
