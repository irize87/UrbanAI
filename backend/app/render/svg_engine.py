from __future__ import annotations

import math
import re
from typing import Literal

from shapely.geometry import GeometryCollection, Point, box, mapping, shape
from shapely.ops import transform
from shapely import wkt
from pyproj import Transformer

from app.core.config import load_hardcode

ShapeKind = Literal["circle", "rect"]


def _xml_text(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _xml_attr(value: str) -> str:
    return '"' + _xml_text(value).replace('"', "&quot;") + '"'

_TO_M = Transformer.from_crs("EPSG:4326", "EPSG:5186", always_xy=True)
_TO_LL = Transformer.from_crs("EPSG:5186", "EPSG:4326", always_xy=True)


def to_metric(geom):
    return transform(lambda x, y: _TO_M.transform(x, y), geom)


def to_lonlat(geom):
    return transform(lambda x, y: _TO_LL.transform(x, y), geom)


def make_clip(
    lat: float,
    lng: float,
    radius_m: float,
    shape_kind: ShapeKind = "circle",
    width_m: float | None = None,
    height_m: float | None = None,
):
    """분석 영역 clip geometry (EPSG:4326)."""
    cx, cy = _TO_M.transform(lng, lat)
    if shape_kind == "rect":
        w = (width_m or radius_m * 2) / 2
        h = (height_m or radius_m * 2) / 2
        metric = box(cx - w, cy - h, cx + w, cy + h)
    else:
        metric = Point(cx, cy).buffer(radius_m, resolution=64)
    return to_lonlat(metric)


def wkt_to_geom(geom_wkt: str):
    return wkt.loads(geom_wkt)


def clip_geom(geom, clip):
    if geom is None or geom.is_empty:
        return geom
    gb, cb = geom.bounds, clip.bounds
    if gb[2] < cb[0] or gb[0] > cb[2] or gb[3] < cb[1] or gb[1] > cb[3]:
        return GeometryCollection()
    return geom.intersection(clip)


def geom_to_svg_path(
    geom,
    origin_lng: float,
    origin_lat: float,
    scale: float,
    *,
    filled: bool = True,
) -> str:
    """로컬 미터 좌표를 SVG path로 변환. scale = px per meter.

    filled=True 이면 폴리곤은 외곽만 쓰고(가짜 홀이 테두리만 칠하는 현상 방지),
    LineString은 면 채우기에 쓰지 않는다.
    """
    if geom.is_empty:
        return ""

    ox, oy = _TO_M.transform(origin_lng, origin_lat)
    metric = to_metric(geom)

    def xy_to_svg(x: float, y: float) -> tuple[float, float]:
        return (x - ox) * scale, -(y - oy) * scale

    def ring_path(coords) -> str:
        parts: list[str] = []
        for i, (x, y) in enumerate(coords):
            sx, sy = xy_to_svg(x, y)
            cmd = "M" if i == 0 else "L"
            parts.append(f"{cmd}{sx:.2f},{sy:.2f}")
        parts.append("Z")
        return "".join(parts)

    paths: list[str] = []
    if metric.geom_type == "GeometryCollection" or metric.geom_type.startswith("Multi"):
        geoms = list(metric.geoms)
    else:
        geoms = [metric]
    for g in geoms:
        if g.geom_type == "Polygon":
            paths.append(ring_path(g.exterior.coords))
            if not filled:
                for interior in g.interiors:
                    paths.append(ring_path(interior.coords))
        elif g.geom_type == "LineString":
            if filled:
                continue
            parts = []
            for i, (x, y) in enumerate(g.coords):
                sx, sy = xy_to_svg(x, y)
                cmd = "M" if i == 0 else "L"
                parts.append(f"{cmd}{sx:.2f},{sy:.2f}")
            paths.append("".join(parts))
        elif g.geom_type == "Point":
            sx, sy = xy_to_svg(g.x, g.y)
            r = 3.0
            paths.append(
                f"M{sx - r:.2f},{sy:.2f}"
                f"a{r},{r} 0 1,0 {r * 2},0"
                f"a{r},{r} 0 1,0 {-r * 2},0"
            )
    return "".join(paths)


def view_size(radius_m: float, shape_kind: ShapeKind, width_m: float | None, height_m: float | None) -> tuple[float, float]:
    if shape_kind == "rect":
        return float(width_m or radius_m * 2), float(height_m or radius_m * 2)
    return radius_m * 2, radius_m * 2


def wrap_svg(
    paths: list[dict],
    lat: float,
    lng: float,
    radius_m: float,
    shape_kind: ShapeKind,
    title: str,
    width_m: float | None = None,
    height_m: float | None = None,
    size_px: int = 800,
    clip_id: str = "siteClip",
    message: str | None = None,
) -> str:
    vw, vh = view_size(radius_m, shape_kind, width_m, height_m)
    scale = size_px / max(vw, vh)
    half_w = (vw * scale) / 2
    half_h = (vh * scale) / 2
    frame_sw = float(load_hardcode().get("DIAGRAM_FRAME_STROKE") or 0.5)
    pad = max(2.0, frame_sw * 0.5 + 1.0)
    vb_x = -half_w - pad
    vb_y = -half_h - pad
    vb_w = vw * scale + pad * 2
    vb_h = vh * scale + pad * 2
    safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", clip_id) or "siteClip"
    if shape_kind == "rect":
        clip_shape = (
            f'<rect x="{-half_w:.2f}" y="{-half_h:.2f}" '
            f'width="{vw * scale:.2f}" height="{vh * scale:.2f}"/>'
        )
        frame_el = (
            f'<rect x="{-half_w:.2f}" y="{-half_h:.2f}" '
            f'width="{vw * scale:.2f}" height="{vh * scale:.2f}" '
            f'fill="none" stroke="#111" stroke-width="{frame_sw:g}" '
            f'stroke-linejoin="miter" vector-effect="non-scaling-stroke"/>'
        )
    else:
        clip_shape = f'<circle cx="0" cy="0" r="{radius_m * scale:.2f}"/>'
        frame_el = (
            f'<circle cx="0" cy="0" r="{radius_m * scale:.2f}" fill="none" '
            f'stroke="#111" stroke-width="{frame_sw:g}" '
            f'vector-effect="non-scaling-stroke"/>'
        )

    body = []
    for p in paths:
        fill = p.get("fill", "#333")
        stroke = p.get("stroke", "none")
        sw = p.get("stroke_width", 0.5)
        opacity = p.get("opacity", 1)
        fill_op = p.get("fill_opacity")
        cat = p.get("cat")
        cat_attr = f" data-cat={_xml_attr(str(cat))}" if cat else ""
        fill_op_attr = f' fill-opacity="{fill_op}"' if fill_op is not None else ""
        kind = p.get("kind") or "path"
        if kind == "image" and p.get("href"):
            x = float(p.get("x", -half_w))
            y = float(p.get("y", -half_h))
            iw = float(p.get("width", vw * scale))
            ih = float(p.get("height", vh * scale))
            href_attr = _xml_attr(str(p["href"]))
            pix = (
                ' style="image-rendering:pixelated;image-rendering:crisp-edges"'
                if p.get("pixelated")
                else ""
            )
            body.append(
                f'<image href={href_attr} xlink:href={href_attr} x="{x:.2f}" y="{y:.2f}" '
                f'width="{iw:.2f}" height="{ih:.2f}" preserveAspectRatio="none"'
                f"{pix}{cat_attr}/>"
            )
            continue
        if kind == "circle":
            cx = float(p.get("cx", 0))
            cy = float(p.get("cy", 0))
            rr = float(p.get("r", 1))
            body.append(
                f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{rr:.2f}" fill="{fill}" '
                f'stroke="{stroke}" stroke-width="{sw}"{fill_op_attr} '
                f'opacity="{opacity}"{cat_attr}/>'
            )
            continue
        d = p.get("d", "")
        if not d:
            continue
        join = ""
        if stroke and stroke != "none":
            if (p.get("join") or "round") == "miter":
                join = (
                    ' stroke-linecap="butt" stroke-linejoin="miter" stroke-miterlimit="2.4"'
                )
            else:
                join = ' stroke-linecap="round" stroke-linejoin="round"'
        body.append(
            f'<path d="{d}" fill="{fill}" fill-rule="nonzero" stroke="{stroke}" '
            f'stroke-width="{sw}"{join}{fill_op_attr} opacity="{opacity}"{cat_attr}/>'
        )

    if not body and message:
        msg = _xml_text(message)
        body.append(
            f'<text x="0" y="0" text-anchor="middle" font-size="18" fill="#888">{msg}</text>'
        )

    layers = "\n".join(body)
    safe_title = _xml_text(title)
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"
     width="{size_px}" height="{size_px}" overflow="visible"
     viewBox="{vb_x:.2f} {vb_y:.2f} {vb_w:.2f} {vb_h:.2f}">
  <title>{safe_title}</title>
  <defs>
    <clipPath id="{safe_id}">{clip_shape}</clipPath>
  </defs>
  <rect x="{vb_x:.2f}" y="{vb_y:.2f}" width="{vb_w:.2f}" height="{vb_h:.2f}" fill="#ffffff"/>
  <g clip-path="url(#{safe_id})">
    {layers}
  </g>
  {frame_el}
</svg>
'''
