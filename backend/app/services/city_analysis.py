"""기존 건물을 고르고, 집계하고, 용도지역·학교·역으로 후보를 표시한다."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from shapely.strtree import STRtree

from app.core.config import load_hardcode
from app.render.svg_engine import to_lonlat, to_metric
from app.services.clip import analysis_clip
from app.services.footprint import building_approval_year, building_height_m
from app.services.layer_cache import clipped_buildings, iter_clipped
from app.services.sim_layout import apply_existing_edit, polygon_from_box
from app.services.sim_llm import USE_CATS, _vlm_complete, parse_edit_command

_ZONE_WORDS = (
    ("상업지역", "상업"),
    ("공업지역", "공업"),
    ("녹지지역", "녹지"),
    ("관리지역", "관리"),
    ("주거지역", "주거"),
)
_USE_WORDS = (
    ("근린생활", "근린생활"),
    ("오피스텔", "주거"),
    ("아파트", "주거"),
    ("주거", "주거"),
    ("업무", "업무"),
    ("오피스", "업무"),
    ("판매", "판매"),
    ("숙박", "숙박"),
    ("교육", "교육복지"),
    ("학교용도", "교육복지"),
    ("공업", "공업"),
    ("공장", "공업"),
    ("문화", "문화집회"),
)


def _hc_int(key: str, default: int) -> int:
    try:
        return int(load_hardcode().get(key, default))
    except (TypeError, ValueError):
        return default


def _floors_of(row: dict[str, Any]) -> int | None:
    try:
        floors = int(row.get("floors") or 0)
    except (TypeError, ValueError):
        floors = 0
    if floors > 0:
        return floors
    height = building_height_m(row)
    if height is None:
        return None
    return max(1, round(height / 3.25))


def parse_analysis_prompt(prompt: str) -> dict[str, Any]:
    """문장에서 필터·브리핑·시나리오·높이 수정을 읽는다. 애매하면 action=ask."""
    text = (prompt or "").strip()
    spec: dict[str, Any] = {
        "action": "ask",
        "age_min": None,
        "age_max": None,
        "floors_min": None,
        "floors_max": None,
        "height_min_m": None,
        "height_max_m": None,
        "use_cats": [],
        "zone_cats": [],
        "school_m": None,
        "station_m": None,
        "show_only": True,
    }
    age = re.search(r"(\d+)\s*년\s*이상", text)
    if age:
        spec["age_min"] = int(age.group(1))
    age_max = re.search(r"(\d+)\s*년\s*이하", text)
    if age_max:
        spec["age_max"] = int(age_max.group(1))
    floors_max = re.search(r"(\d+)\s*층\s*이하", text)
    if floors_max:
        spec["floors_max"] = int(floors_max.group(1))
    floors_min = re.search(r"(\d+)\s*층\s*이상", text)
    if floors_min:
        spec["floors_min"] = int(floors_min.group(1))
    if "고층" in text and spec["floors_min"] is None and spec["height_min_m"] is None:
        spec["floors_min"] = _hc_int("ANALYSIS_HIGH_FLOOR", 10)
    if "저층" in text and spec["floors_max"] is None:
        spec["floors_max"] = _hc_int("ANALYSIS_LOW_FLOOR", 5)
    height_max = re.search(r"높이\s*(\d+(?:\.\d+)?)\s*m\s*이하", text, re.I)
    if height_max:
        spec["height_max_m"] = float(height_max.group(1))
    height_min = re.search(r"높이\s*(\d+(?:\.\d+)?)\s*m\s*이상", text, re.I)
    if height_min:
        spec["height_min_m"] = float(height_min.group(1))
    uses: list[str] = []
    for word, cat in _USE_WORDS:
        if word in text and cat not in uses and cat in USE_CATS:
            if word in {"주거", "공업"} and f"{word}지역" in text:
                continue
            uses.append(cat)
    spec["use_cats"] = uses
    zones: list[str] = []
    for word, cat in _ZONE_WORDS:
        if word in text and cat not in zones:
            zones.append(cat)
    spec["zone_cats"] = zones
    school = re.search(r"학교(?:\s*반경)?\s*(\d+)\s*m", text)
    if "학교" in text and "학교용도" not in text:
        spec["school_m"] = int(school.group(1)) if school else _hc_int("ANALYSIS_SCHOOL_M", 500)
    station = re.search(r"(?:역세권|지하철)(?:\s*반경)?\s*(\d+)\s*m", text)
    if any(word in text for word in ("역세권", "지하철", "역 근처")):
        spec["station_m"] = int(station.group(1)) if station else _hc_int("ANALYSIS_STATION_M", 500)

    rain = re.search(r"(\d+(?:\.\d+)?)\s*mm", text, re.I)
    if any(word in text for word in ("침수", "강수", "강우", "호우")):
        spec["action"] = "flood"
        spec["rainfall_mm"] = float(rain.group(1)) if rain else None
        return spec
    year_m = re.search(r"(18|19|20)\d{2}", text)
    if any(word in text for word in ("타임랩스", "타임 랩스", "도시 성장", "성장 타임", "성장 역사", "성장사")) or (
        "성장" in text and year_m
    ):
        spec["action"] = "timelapse"
        spec["start_year"] = int(year_m.group(0)) if year_m else None
        return spec
    floor_set = re.search(r"(\d+)\s*층(?:\s*높이)?(?:으로|로)", text)
    if floor_set and any(word in text for word in ("바꿔", "바꾸", "변경", "맞춰")):
        floors = int(floor_set.group(1))
        if 1 <= floors <= 150:
            floor_h = float(load_hardcode().get("FLOOR_HEIGHT_M", 3.25))
            spec["action"] = "edit"
            spec["edit"] = {
                "height_add_m": None,
                "height_set_m": round(floors * floor_h, 2),
                "far_scale": None,
                "remove": False,
            }
            return spec

    spatial = bool(spec["zone_cats"] or spec["school_m"] or spec["station_m"])
    scenario_word = any(word in text for word in ("고밀", "후보지", "우선 검토", "우선검토", "시나리오"))
    brief_word = any(word in text for word in ("브리핑", "현황", "보고서", "요약"))
    has_filter = any(
        spec[key] is not None
        for key in ("age_min", "age_max", "floors_min", "floors_max", "height_min_m", "height_max_m")
    ) or bool(spec["use_cats"])
    edit = parse_edit_command(text)
    if spatial or scenario_word:
        spec["action"] = "scenario"
    elif edit and not has_filter:
        spec["action"] = "edit"
        spec["edit"] = edit
    elif brief_word and not has_filter:
        spec["action"] = "brief"
    elif has_filter:
        spec["action"] = "filter"
    return spec


def row_passes_filter(row: dict[str, Any], spec: dict[str, Any], *, now_year: int) -> bool:
    """연도·층수·높이·용도 조건. 연도가 없으면 노후 조건에서 제외한다."""
    year = building_approval_year(row)
    age = (now_year - year) if year else None
    if spec.get("age_min") is not None and (age is None or age < int(spec["age_min"])):
        return False
    if spec.get("age_max") is not None and (age is None or age > int(spec["age_max"])):
        return False
    floors = _floors_of(row)
    if spec.get("floors_min") is not None and (floors is None or floors < int(spec["floors_min"])):
        return False
    if spec.get("floors_max") is not None and (floors is None or floors > int(spec["floors_max"])):
        return False
    height = building_height_m(row)
    if spec.get("height_min_m") is not None and (height is None or height < float(spec["height_min_m"])):
        return False
    if spec.get("height_max_m") is not None and (height is None or height > float(spec["height_max_m"])):
        return False
    uses = spec.get("use_cats") or []
    if uses and str(row.get("use_cat") or "미상") not in uses:
        return False
    return True


def scenario_score(
    *,
    age: int | None,
    floors: int | None,
    zone: str | None,
    station_m: float | None,
) -> int:
    """노후·저층·상업·역세권 가점. 모델이 점수를 만들지 않는다."""
    old_year = _hc_int("ANALYSIS_OLD_YEARS", 30)
    near_m = _hc_int("ANALYSIS_STATION_M", 500)
    low_floor = _hc_int("ANALYSIS_LOW_FLOOR", 5)
    score = 0
    if age is not None and age >= old_year:
        score += 2
    if floors is not None and floors <= low_floor:
        score += 2
    if zone == "상업":
        score += 2
    if station_m is not None and station_m <= near_m:
        score += 2
    return score


def _colors() -> tuple[str, str]:
    hc = load_hardcode()
    return str(hc.get("ANALYSIS_COLOR_HIGH") or "#c62828"), str(hc.get("ANALYSIS_COLOR_MID") or "#ef6c00")


def _load_view(
    *,
    lat: float,
    lng: float,
    radius_m: float,
    shape_kind: str,
    include_adjacent: bool,
    box_ll: list[list[float]] | None,
) -> tuple[Any, list[tuple[dict[str, Any], Any]], Any | None]:
    clip = analysis_clip(
        lat,
        lng,
        radius_m,
        shape_kind if shape_kind in {"circle", "rect"} else "circle",
        include_adjacent=include_adjacent,
    )
    items = clipped_buildings(clip, require_height=True)
    selection = None
    if box_ll:
        selection = polygon_from_box(box_ll).intersection(clip)
    return clip, items, selection


def _selected_rows(
    items: list[tuple[dict[str, Any], Any]],
    selection: Any | None,
) -> list[tuple[dict[str, Any], Any]]:
    if selection is None or selection.is_empty:
        return items
    return [(row, geom) for row, geom in items if geom.intersects(selection)]


def _stats(rows: list[tuple[dict[str, Any], Any]], *, now_year: int) -> dict[str, Any]:
    old_year = _hc_int("ANALYSIS_OLD_YEARS", 30)
    uses: dict[str, int] = {}
    heights: list[float] = []
    floors: list[int] = []
    old = 0
    dated = 0
    for row, _geom in rows:
        cat = str(row.get("use_cat") or "미상")
        uses[cat] = uses.get(cat, 0) + 1
        height = building_height_m(row)
        if height is not None:
            heights.append(height)
        floor = _floors_of(row)
        if floor is not None:
            floors.append(floor)
        year = building_approval_year(row)
        if year:
            dated += 1
            if now_year - year >= old_year:
                old += 1
    count = len(rows)
    avg_h = round(sum(heights) / len(heights), 1) if heights else 0
    avg_f = round(sum(floors) / len(floors), 1) if floors else 0
    old_ratio = (old / dated) if dated else 0
    return {
        "count": count,
        "avg_height_m": avg_h,
        "avg_floors": avg_f,
        "uses": uses,
        "old_count": old,
        "dated_count": dated,
        "old_years": old_year,
        "old_ratio": round(old_ratio, 3),
    }


def _brief_lines(stats: dict[str, Any]) -> str:
    uses = stats["uses"]
    use_lines = [
        f"- {name} {count}동"
        for name, count in sorted(uses.items(), key=lambda item: -item[1])
    ] or ["- 없음"]
    if stats["dated_count"] == 0:
        dev = "사용승인 연도가 없어 노후 비율은 계산하지 않았습니다."
    elif stats["old_ratio"] >= 0.35 and stats["avg_floors"] <= 8:
        dev = "노후 비율이 높고 평균 층수가 낮아, 정비 검토 후보로 볼 수 있습니다."
    else:
        dev = "노후 비율과 평균 층수만 보면 우선 정비 대상으로는 두드러지지 않습니다."
    body = "\n".join(
        [
            "# 도시 현황 브리핑",
            "",
            "## 규모",
            f"- 건물 {stats['count']}동",
            f"- 평균 높이 {stats['avg_height_m']}m",
            f"- 평균 {stats['avg_floors']}층",
            "",
            "## 용도",
            *use_lines,
            "",
            "## 노후",
            (
                f"- 사용승인 {stats['old_years']}년 이상 {stats['old_count']}동 "
                f"(연도가 있는 {stats['dated_count']}동 기준)"
            ),
            "",
            "## 검토",
            dev,
        ]
    )
    return body


def _drop_unbriefed(text: str) -> str:
    parts = re.split(r"(?<=[.!?。])\s+|\n+", text)
    kept = [part.strip() for part in parts if part.strip() and "용적률" not in part and "접도" not in part]
    return " ".join(kept).strip()


def _index_zones(clip: Any) -> tuple[STRtree | None, list[Any], list[str]]:
    geoms = []
    cats = []
    wide = to_lonlat(to_metric(clip).buffer(40))
    for row, geom in iter_clipped("zoning", wide):
        geoms.append(geom)
        cats.append(str(row.get("zone_cat") or "기타"))
    if not geoms:
        return None, [], []
    return STRtree(geoms), geoms, cats


def _zone_of(geom: Any, tree: STRtree | None, geoms: list[Any], cats: list[str]) -> str | None:
    if tree is None:
        return None
    point = geom.centroid
    for idx in tree.query(point):
        if geoms[int(idx)].covers(point) or geoms[int(idx)].intersects(point):
            return cats[int(idx)]
    return None


def _near_meters(clip: Any, table: str, kind: str) -> list[Any]:
    wide = to_lonlat(to_metric(clip).buffer(1200))
    out = []
    for row, geom in iter_clipped(table, wide):
        if str(row.get("kind") or "") != kind:
            continue
        out.append(to_metric(geom))
    return out


def _min_distance(geom: Any, targets: list[Any]) -> float | None:
    if not targets:
        return None
    point = to_metric(geom.centroid)
    return min(point.distance(target) for target in targets)


def _apply_gates(
    rows: list[tuple[dict[str, Any], Any]],
    spec: dict[str, Any],
    clip: Any,
) -> list[tuple[dict[str, Any], Any, str | None, float | None]]:
    need_zone = bool(spec.get("zone_cats")) or spec.get("action") == "scenario"
    tree, geoms, cats = _index_zones(clip) if need_zone else (None, [], [])
    schools = _near_meters(clip, "planned", "school") if spec.get("school_m") else []
    stations = _near_meters(clip, "transit", "subway") if spec.get("station_m") or spec.get("action") == "scenario" else []
    kept = []
    for row, geom in rows:
        zone = _zone_of(geom, tree, geoms, cats) if need_zone else None
        if spec.get("zone_cats") and zone not in spec["zone_cats"]:
            continue
        if spec.get("school_m") is not None:
            dist = _min_distance(geom, schools)
            if dist is None or dist > float(spec["school_m"]):
                continue
        station_dist = _min_distance(geom, stations) if stations else None
        if spec.get("station_m") is not None and (station_dist is None or station_dist > float(spec["station_m"])):
            continue
        kept.append((row, geom, zone, station_dist))
    return kept


def _hide_ids(items: list[tuple[dict[str, Any], Any]], keep: set[int]) -> list[int]:
    hidden = []
    for row, _geom in items:
        rid = row.get("id")
        if rid is None:
            continue
        if int(rid) not in keep:
            hidden.append(int(rid))
    return hidden


def _filter_reply(spec: dict[str, Any], count: int) -> str:
    bits = []
    if spec.get("age_min") is not None:
        bits.append(f"{spec['age_min']}년 이상")
    if spec.get("floors_max") is not None:
        bits.append(f"{spec['floors_max']}층 이하")
    if spec.get("floors_min") is not None:
        bits.append(f"{spec['floors_min']}층 이상")
    if spec.get("height_min_m") is not None:
        bits.append(f"높이 {spec['height_min_m']}m 이상")
    if spec.get("height_max_m") is not None:
        bits.append(f"높이 {spec['height_max_m']}m 이하")
    if spec.get("use_cats"):
        bits.append(", ".join(spec["use_cats"]))
    if spec.get("zone_cats"):
        bits.append(", ".join(f"{name}지역" for name in spec["zone_cats"]))
    if spec.get("school_m"):
        bits.append(f"학교 {spec['school_m']}m")
    if spec.get("station_m"):
        bits.append(f"역 {spec['station_m']}m")
    label = ", ".join(bits) or "조건"
    scope = "지금 보이는 분석 범위"
    return f"{scope}에서 {label}에 해당하는 {count}동만 표시했습니다. 연도가 없는 건물은 노후 조건에서 빠집니다."


async def _narrate(stats: dict[str, Any], base: str) -> str:
    raw = await _vlm_complete(
        [
            {
                "role": "system",
                "content": (
                    "주어진 집계 숫자만 사용해 도시계획 검토 문장을 두 문장으로 쓴다. "
                    "용적률, 접도 폭, 인구, 사업성은 말하지 않는다. "
                    'JSON만. {"reply_ko":"..."}'
                ),
            },
            {"role": "user", "content": str(stats)},
        ],
        max_tokens=400,
    )
    extra = ""
    if isinstance(raw, dict):
        extra = str(raw.get("reply_ko") or "").strip()
    extra = _drop_unbriefed(extra)
    if not extra:
        return base
    return f"{base}\n\n{extra}"


async def run_analysis(
    *,
    prompt: str,
    action: str,
    lat: float,
    lng: float,
    radius_m: float,
    shape_kind: str,
    include_adjacent: bool,
    box_ll: list[list[float]] | None,
    building_ids: list[int],
) -> dict[str, Any]:
    """필터, 브리핑, 시나리오, 높이 수정 중 하나를 실행한다."""
    now_year = datetime.now().year
    _clip, items, selection = _load_view(
        lat=lat,
        lng=lng,
        radius_m=radius_m,
        shape_kind=shape_kind,
        include_adjacent=include_adjacent,
        box_ll=box_ll,
    )
    scope = _selected_rows(items, selection)
    forced = (action or "auto").strip()
    spec = parse_analysis_prompt(prompt)
    if forced == "brief":
        spec = {**spec, "action": "brief"}
    elif spec["action"] == "ask" and prompt.strip():
        raw = await _vlm_complete(
            [
                {
                    "role": "system",
                    "content": (
                        "도시 분석 요청을 JSON으로만 분류한다. "
                        "action은 filter, brief, scenario, ask 중 하나. "
                        "filter는 age_min, floors_max, floors_min, height_min_m, height_max_m, use_cats. "
                        "scenario는 zone_cats(주거|상업|공업|녹지), school_m, station_m. "
                        "숫자를 지어내지 않는다. 본문에 없는 값은 null."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            max_tokens=500,
        )
        if isinstance(raw, dict) and raw.get("action") in {"filter", "brief", "scenario"}:
            for key in (
                "age_min",
                "age_max",
                "floors_min",
                "floors_max",
                "height_min_m",
                "height_max_m",
                "school_m",
                "station_m",
            ):
                if raw.get(key) is not None:
                    spec[key] = raw.get(key)
            if isinstance(raw.get("use_cats"), list):
                spec["use_cats"] = [c for c in raw["use_cats"] if c in USE_CATS]
            if isinstance(raw.get("zone_cats"), list):
                spec["zone_cats"] = [c for c in raw["zone_cats"] if c in {"주거", "상업", "공업", "녹지", "관리"}]
            spec["action"] = raw["action"]

    if spec["action"] == "flood":
        mm = spec.get("rainfall_mm")
        if not isinstance(mm, (int, float)) or float(mm) <= 0:
            return {
                "status": "ask",
                "action": "ask",
                "reply_ko": "시간당 강수량을 mm로 말해 주세요. 예: 시간당 50mm 침수",
                "hide_ids": None,
                "colors": None,
                "match_count": 0,
                "updates": [],
                "rainfall_mm": None,
                "start_year": None,
            }
        return {
            "status": "ok",
            "action": "flood",
            "reply_ko": (
                f"시간당 {float(mm):g}mm를 분석 범위 면적에 곱해, "
                "지형이 낮은 곳부터 파란 물이 차오릅니다. 선택한 영역이 있으면 그 안으로 한정합니다."
            ),
            "hide_ids": None,
            "colors": None,
            "match_count": 0,
            "updates": [],
            "rainfall_mm": float(mm),
            "start_year": None,
        }
    if spec["action"] == "timelapse":
        year = spec.get("start_year")
        if not isinstance(year, int) or year < 1800:
            return {
                "status": "ask",
                "action": "ask",
                "reply_ko": "타임랩스 시작 연도를 말해 주세요. 예: 1990년 도시 성장 타임랩스",
                "hide_ids": None,
                "colors": None,
                "match_count": 0,
                "updates": [],
                "rainfall_mm": None,
                "start_year": None,
            }
        return {
            "status": "ok",
            "action": "timelapse",
            "reply_ko": (
                f"{year}년 이전에 준공된 건물은 그대로 두고, "
                f"{year}년 이후 준공 순서대로 건물이 올라옵니다. 연도가 없는 건물은 처음부터 보입니다."
            ),
            "hide_ids": None,
            "colors": None,
            "match_count": 0,
            "updates": [],
            "rainfall_mm": None,
            "start_year": int(year),
        }
    if spec["action"] == "ask":
        return {
            "status": "ask",
            "action": "ask",
            "reply_ko": "연도, 층수, 높이, 용도나 현황 브리핑, 용도지역·학교·역 조건으로 물어봐 주세요.",
            "hide_ids": None,
            "colors": None,
            "match_count": 0,
            "updates": [],
        }
    if spec["action"] == "edit":
        try:
            laid = apply_existing_edit(
                edit=spec.get("edit") or parse_edit_command(prompt) or {},
                lat=lat,
                lng=lng,
                radius_m=radius_m,
                shape_kind=shape_kind,
                include_adjacent=include_adjacent,
                building_ids=building_ids,
                box_ll=box_ll,
            )
        except ValueError:
            return {
                "status": "ask",
                "action": "ask",
                "reply_ko": "높이를 바꿀 건물이나 영역을 먼저 지정해 주세요.",
                "hide_ids": None,
                "colors": None,
                "match_count": 0,
                "updates": [],
            }
        return {
            "status": "ok",
            "action": "edit",
            "reply_ko": laid["reply_ko"],
            "hide_ids": laid["hide_ids"] or None,
            "colors": None,
            "match_count": 0,
            "updates": laid["updates"],
        }
    if spec["action"] == "brief":
        stats = _stats(scope, now_year=now_year)
        base = _brief_lines(stats)
        if box_ll is None and not building_ids:
            base = base.replace(
                "# 도시 현황 브리핑\n",
                "# 도시 현황 브리핑\n\n현재 분석 반경 전체를 요약했습니다.\n",
                1,
            )
        text = await _narrate(stats, base)
        return {
            "status": "ok",
            "action": "brief",
            "reply_ko": text,
            "hide_ids": None,
            "colors": None,
            "match_count": stats["count"],
            "updates": [],
        }

    passed = [(row, geom) for row, geom in scope if row_passes_filter(row, spec, now_year=now_year)]
    high, mid = _colors()
    colors: dict[str, str] = {}
    if spec["action"] == "scenario":
        gated = _apply_gates(passed, spec, _clip)
        ranked: list[tuple[int, int]] = []
        explicit = bool(spec.get("zone_cats") or spec.get("school_m") or spec.get("station_m"))
        for row, _geom, zone, station_dist in gated:
            rid = row.get("id")
            if rid is None:
                continue
            year = building_approval_year(row)
            age = (now_year - year) if year else None
            score = scenario_score(
                age=age,
                floors=_floors_of(row),
                zone=zone,
                station_m=station_dist,
            )
            if not explicit and score < 3:
                continue
            ranked.append((int(rid), score if explicit else score))
            colors[str(int(rid))] = high if score >= 5 else mid
        keep = {rid for rid, _score in ranked}
        reply = (
            f"후보 {len(keep)}동을 표시했습니다. "
            "빨간색은 노후·저층·상업·역세권 가점이 높은 곳, 주황색은 그다음입니다. "
            "법정 용적률과 접도 폭은 반영하지 않았습니다."
        )
        if explicit:
            reply = _filter_reply(spec, len(keep)) + " 가점이 높은 동은 빨간색입니다."
    else:
        keep = set()
        for row, _geom in passed:
            rid = row.get("id")
            if rid is not None:
                keep.add(int(rid))
        reply = _filter_reply(spec, len(keep))
    return {
        "status": "ok",
        "action": spec["action"],
        "reply_ko": reply,
        "hide_ids": _hide_ids(items, keep),
        "colors": colors or None,
        "match_count": len(keep),
        "updates": [],
    }
