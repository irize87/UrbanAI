from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import asdict, dataclass
from typing import Any

import httpx

from app.core.config import get_settings, load_hardcode

logger = logging.getLogger(__name__)

USE_CATS = (
    "주거",
    "근린생활",
    "업무",
    "판매",
    "숙박",
    "문화집회",
    "교육복지",
    "공업",
    "기타",
    "미상",
)
TYPOLOGIES = ("slab_grid", "tower_grid", "single_mass", "podium_tower")

_USE_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("아파트", "주택", "주거", "단지", "빌라", "오피스텔"), "주거"),
    (("오피스", "업무", "사무실", "타워"), "업무"),
    (("학교", "병원", "복지", "유치원", "대학"), "교육복지"),
    (("상점", "판매", "마트", "시장"), "판매"),
    (("호텔", "숙박", "모텔"), "숙박"),
    (("공장", "창고", "공업"), "공업"),
    (("문화", "체육", "교회", "공연장"), "문화집회"),
    (("근린", "상가"), "근린생활"),
)

_TYPO_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("타워", "초고층", "포인트"), "tower_grid"),
    (("포디엄", "저층부", "주상복합"), "podium_tower"),
    (("한 동", "단일", "통매스", "한덩어리"), "single_mass"),
    (("아파트", "단지", "판상", "슬래브"), "slab_grid"),
)

@dataclass
class SimSpec:
    use_cat: str
    height_m: float
    floors: int | None
    typology: str
    coverage: float
    setback_m: float
    gap_m: float
    reply_ko: str
    count: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ImageMass:
    ring: list[list[float]]
    height_m: float
    use_cat: str
    floors: int | None = None


@dataclass
class SimTurn:
    status: str
    reply_ko: str
    ask_ko: str | None
    spec: SimSpec | None
    partial: dict[str, Any]
    masses: list[ImageMass] | None = None
    session: str = "grid"

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reply_ko": self.reply_ko,
            "ask_ko": self.ask_ko,
            "spec": self.spec.as_dict() if self.spec else None,
            "partial": self.partial,
            "session": self.session,
            "mass_count": len(self.masses or []),
        }


_SYSTEM = """너는 도시 3D 매스 시뮬레이션 파라미터 추출기다.
사용자와 이어지는 대화(history)를 보고 JSON 한 객체로만 답한다. 마크다운 금지.
색상 코드는 넣지 않는다.

용도 use_cat: 주거|근린생활|업무|판매|숙박|문화집회|교육복지|공업|기타|미상
typology: slab_grid(판상 단지)|tower_grid(타워 격자)|single_mass(한 매스)|podium_tower(포디엄+타워)
아파트·주택 단지는 주거+slab_grid.
count는 새로 세울 동 수(정수).

정보가 부족하면 추측해서 채우지 말고 한 가지만 한국어로 직접 묻는다. ask_ko는 네가 쓴 질문 문장이다.
예: 아파트 단지인데 동 수가 없으면
{"status":"ask","ask_ko":"단지는 몇 개 동으로 할까요?","partial":{"use_cat":"주거","typology":"slab_grid"}}

이미지가 있으면 사진의 매스 형태(판상·타워·포디엄, 동 수, 상대 높이)를 읽어 spec을 채운다.
이미지만으로 동 수·높이를 알 수 있으면 묻지 말고 status=ok 로 채운다.

충분하면
{"status":"ok","use_cat":"주거","height_m":30,"floors":null,"count":3,"typology":"slab_grid","coverage":0.35,"setback_m":8,"gap_m":12,"reply_ko":"선택 구역을 30m 주거 3개 동으로 구성합니다."}

이미 나온 숫자는 유지한다. 사용자가 '3개'라고만 답하면 count=3.
height_m가 없고 층수만 있으면 floors만 넣는다.
"""

_IMAGE_MASS_SYSTEM = """너는 단지 배치도·조감도·평면 이미지를 보고 건물 동 외곽만 추론한다.
JSON 한 객체만 답한다. 마크다운 금지.

masses[].ring 은 정규화 좌표 0~1.
x=왼쪽 0 → 오른쪽 1, y=아래(남) 0 → 위(북) 1.
색이 칠해진 아파트·건물 바닥만 동으로 본다.
도로, 수목, 조경, 하천, 주차장 선, 인동거리 치수, 방위표, 범례(㎡ TYPE)는 건물로 넣지 않는다.
ㄱ자·ㄴ자·ㄷ자·중정은 그 꺾인 외곽을 유지한다. 직사각형으로 단순화하지 않는다.
꼭짓점은 동당 4~16점. 곡선은 직선으로 근사. 중정 구멍은 무시하고 외곽만.
배치도에 보이는 동을 빠짐없이 넣는다. 최대 36개.
높이와 층수는 비워 둔다. 사용자가 말한 높이는 서버가 모든 동에 똑같이 넣는다.
용도는 주거.

예:
{"status":"ok","view":"plan","reply_ko":"배치도에서 동 외곽을 올립니다.","masses":[{"ring":[[0.12,0.20],[0.28,0.20],[0.28,0.32],[0.20,0.32],[0.20,0.42],[0.12,0.42]],"use_cat":"주거"}]}

동 외곽을 전혀 못 읽으면
{"status":"ask","ask_ko":"건물 바닥 색이 잘 안 보입니다. 배치도를 더 크게 넣어 주세요.","masses":[]}
"""


def _defaults() -> dict[str, Any]:
    hc = load_hardcode()
    return {
        "coverage": float(hc.get("SIM_COVERAGE", 0.35)),
        "setback_m": float(hc.get("SIM_SETBACK_M", 8)),
        "gap_m": float(hc.get("SIM_GAP_M", 12)),
        "floor_h": float(hc.get("FLOOR_HEIGHT_M", 3.25)),
    }


def normalize_spec(raw: dict[str, Any], *, prompt: str = "") -> SimSpec:
    """LLM/폴백 dict를 허용 범위로 고정한다."""
    d = _defaults()
    use_cat = str(raw.get("use_cat") or "").strip()
    if use_cat not in USE_CATS:
        use_cat = fallback_use_cat(prompt) or "주거"
    typology = str(raw.get("typology") or "").strip()
    if typology not in TYPOLOGIES:
        typology = fallback_typology(prompt) or "slab_grid"
    floor_h = d["floor_h"]
    floors_raw = raw.get("floors")
    floors: int | None = None
    try:
        if floors_raw is not None:
            floors = max(1, int(floors_raw))
    except (TypeError, ValueError):
        floors = None
    height: float | None = None
    try:
        if raw.get("height_m") is not None:
            height = float(raw["height_m"])
    except (TypeError, ValueError):
        height = None
    if height is None or height < 3:
        if floors is not None:
            height = floors * floor_h
        else:
            height = fallback_height_m(prompt) or 30.0
    height = max(6.0, min(300.0, height))
    if floors is None:
        floors = max(1, round(height / floor_h))
    count: int | None = None
    try:
        if raw.get("count") is not None:
            count = max(1, min(80, int(raw["count"])))
    except (TypeError, ValueError):
        count = None
    if count is None:
        count = fallback_count(prompt)
    try:
        coverage = float(raw.get("coverage", d["coverage"]))
    except (TypeError, ValueError):
        coverage = d["coverage"]
    coverage = max(0.2, min(0.5, coverage))
    try:
        setback_m = float(raw.get("setback_m", d["setback_m"]))
    except (TypeError, ValueError):
        setback_m = d["setback_m"]
    setback_m = max(0.0, min(40.0, setback_m))
    try:
        gap_m = float(raw.get("gap_m", d["gap_m"]))
    except (TypeError, ValueError):
        gap_m = d["gap_m"]
    gap_m = max(4.0, min(40.0, gap_m))
    reply = str(raw.get("reply_ko") or "").strip()
    if not reply:
        reply = (
            f"선택 구역을 {height:.0f}m {use_cat} "
            f"({typology})로 다시 구성합니다."
        )
    return SimSpec(
        use_cat=use_cat,
        height_m=round(height, 2),
        floors=floors,
        typology=typology,
        coverage=round(coverage, 3),
        setback_m=round(setback_m, 2),
        gap_m=round(gap_m, 2),
        reply_ko=reply,
        count=count,
    )


def fallback_use_cat(prompt: str) -> str | None:
    text = prompt or ""
    for keys, cat in _USE_HINTS:
        if any(k in text for k in keys):
            return cat
    return None


def fallback_typology(prompt: str) -> str | None:
    text = prompt or ""
    for keys, typ in _TYPO_HINTS:
        if any(k in text for k in keys):
            return typ
    return None


def fallback_height_m(prompt: str) -> float | None:
    text = prompt or ""
    m = re.search(r"(\d+(?:\.\d+)?)\s*m", text, re.I)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d+)\s*층", text)
    if m:
        return int(m.group(1)) * _defaults()["floor_h"]
    return None


def parse_edit_command(prompt: str) -> dict[str, Any] | None:
    """기존 건물 수정 문장을 높이·용적률·제거로 읽는다. 새 건물 생성은 None."""
    text = re.sub(r"\s+", " ", (prompt or "").strip())
    if not text:
        return None
    create_words = ("세워", "새로", "추가", "만들어", "구성", "넣어", "단지")
    edit_words = (
        "용적률",
        "기존",
        "높여",
        "높이고",
        "높인",
        "낮춰",
        "낮추",
        "제거",
        "없애",
        "삭제",
        "철거",
    )
    if any(word in text for word in create_words) and not any(word in text for word in edit_words):
        return None

    height_add: float | None = None
    height_set: float | None = None
    far_scale: float | None = None
    remove = any(word in text for word in ("제거", "없애", "삭제", "철거")) and not any(
        word in text for word in create_words
    )

    far_match = re.search(r"용적률[^\d%]{0,16}(\d+(?:\.\d+)?)\s*%", text)
    if far_match:
        percent = float(far_match.group(1))
        window = text[max(0, far_match.start() - 4) : far_match.end() + 12]
        down = any(word in window for word in ("낮", "줄", "감"))
        far_scale = 1.0 - percent / 100.0 if down else 1.0 + percent / 100.0
        far_scale = max(0.3, min(3.0, far_scale))

    set_match = re.search(
        r"높이(?:를|을)?\s*(\d+(?:\.\d+)?)\s*(?:m|미터)?\s*(?:로|으로)",
        text,
        re.I,
    )
    if set_match:
        height_set = float(set_match.group(1))
    else:
        add_match = re.search(
            r"높이(?:를|을)?\s*(\d+(?:\.\d+)?)\s*(?:m|미터)\s*(?:더\s*)?(높|올|낮|줄)",
            text,
            re.I,
        )
        if add_match is None:
            add_match = re.search(
                r"(\d+(?:\.\d+)?)\s*(?:m|미터)\s*(?:더\s*)?(높|올|낮|줄)",
                text,
                re.I,
            )
        if add_match:
            delta = float(add_match.group(1))
            if add_match.group(2) in {"낮", "줄"}:
                delta = -delta
            height_add = delta

    if height_set is None and height_add is None and far_scale is None and not remove:
        return None
    return {
        "height_add_m": height_add,
        "height_set_m": height_set,
        "far_scale": far_scale,
        "remove": remove,
    }


def stated_floors(text: str) -> int | None:
    """문장에 적힌 층수. 1~150층만 인정한다."""
    match = re.search(r"(\d+)\s*층", text or "")
    if not match:
        return None
    floors = int(match.group(1))
    if 1 <= floors <= 150:
        return floors
    return None


def prompt_states_height(text: str) -> bool:
    """층수 또는 m 높이가 이미 있으면 True."""
    if stated_floors(text) is not None:
        return True
    return re.search(r"(\d+(?:\.\d+)?)\s*(?:m|미터)", text or "", re.I) is not None


def asks_only_for_height(message: str) -> bool:
    text = message or ""
    return any(token in text for token in ("몇 m", "몇m", "몇 미터", "몇미터", "높이"))


def drop_redundant_height_ask(raw: dict[str, Any], prompt: str) -> dict[str, Any] | None:
    """높이가 이미 있는데 높이만 되물으면 생성으로 진행한다."""
    if not prompt_states_height(prompt):
        return None
    message = str(raw.get("ask_ko") or raw.get("reply_ko") or "")
    if not asks_only_for_height(message):
        return None
    continued = dict(raw)
    continued["status"] = "ok"
    continued["action"] = "create"
    continued.pop("ask_ko", None)
    floors = stated_floors(prompt)
    if floors and continued.get("floors") in (None, ""):
        continued["floors"] = floors
    if continued.get("height_m") in (None, "") and floors:
        continued["height_m"] = None
    reply = str(continued.get("reply_ko") or "").strip()
    if not reply or asks_only_for_height(reply):
        if floors:
            continued["reply_ko"] = f"{floors}층으로 새 건물을 올립니다."
        else:
            continued["reply_ko"] = "말한 높이로 새 건물을 올립니다."
    return continued


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def _as_scale(value: Any) -> float | None:
    """1.2은 배율, 20은 +20%로 읽는다."""
    number = _as_float(value)
    if number is None:
        return None
    if abs(number) > 3:
        number = 1.0 + number / 100.0
    return max(0.3, min(3.0, number))


def normalize_model_edit(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    """모델 JSON의 기존 건물 수정을 허용 범위로 고정한다."""
    if not isinstance(raw, dict):
        return None
    action = str(raw.get("action") or "").strip()
    height_add = _as_float(raw.get("height_add_m"))
    height_set = _as_float(raw.get("height_set_m"))
    far_scale = _as_scale(raw.get("far_scale"))
    remove = bool(raw.get("remove"))
    has_edit = any(value is not None for value in (height_add, height_set, far_scale)) or remove
    if action not in {"edit", ""} and not has_edit:
        return None
    if action == "create":
        return None
    if not has_edit:
        return None
    if height_add is not None:
        height_add = max(-150.0, min(150.0, height_add))
    if height_set is not None:
        height_set = max(3.0, min(400.0, height_set))
    return {
        "height_add_m": height_add,
        "height_set_m": height_set,
        "far_scale": far_scale,
        "remove": remove,
        "reply_ko": str(raw.get("reply_ko") or "").strip(),
    }


_CITY_SYSTEM = """너는 서초 3D 시뮬레이션 명령 해석기다.
사용자 문장과 대화를 보고 JSON 한 객체만 답한다. 마크다운 금지.

할 수 있는 일만 고른다.
- action=edit: 선택한 기존 건물을 수정. 바닥 모양은 유지한다.
  height_add_m: 높이 가감(m). 10m 높이면 10, 5m 낮추면 -5.
  height_set_m: 높이를 그 값으로 고정. 가감과 같이 쓰지 않는다.
  far_scale: 용적률. 20% 높이면 1.2, 10% 낮추면 0.9. 바닥은 고정하고 높이에 곱한다.
  remove: true면 선택 건물을 숨긴다.
  한 문장에 용적률과 높이 가감이 있으면 둘 다 넣는다. 적용 순서는 far_scale 다음 height_add_m.
- action=create: 새 건물을 세울 때만. use_cat, height_m, floors, count, typology, coverage, setback_m, gap_m.
  typology: slab_grid|tower_grid|single_mass|podium_tower.
  use_cat: 주거|근린생활|업무|판매|숙박|문화집회|교육복지|공업|기타|미상.

숫자가 없으면 추측하지 말고 status=ask, ask_ko에 한 가지만 묻는다.
층수(49층)나 m가 이미 있으면 높이를 다시 묻지 말고 status=ok 로 진행한다. 층수만 있으면 floors에 넣고 height_m는 비워도 된다. 층고 3.25m는 서버가 곱한다.
ㄱ자·ㄴ자·ㄷ자·중정 같은 형태 이름 때문에 높이를 묻지 않는다.
동 수가 없어도 층수나 높이가 있으면 되묻지 말고 count는 비운 채 ok로 둔다.
일조, 그림자, 건폐율, 건물 이름으로 특정 동만 지목하면 status=refuse.
reply_ko는 무엇을 할지 한국어 한 문장.

예:
{"status":"ok","action":"edit","far_scale":1.2,"height_add_m":10,"height_set_m":null,"remove":false,"reply_ko":"선택 건물 높이를 1.2배로 올린 뒤 10m를 더합니다."}
{"status":"ok","action":"create","use_cat":"주거","height_m":30,"floors":null,"count":3,"typology":"slab_grid","coverage":0.35,"setback_m":8,"gap_m":12,"reply_ko":"선택 구역에 30m 주거 3개 동을 새로 올립니다."}
{"status":"ask","ask_ko":"높이를 몇 m 올릴까요?","reply_ko":"높이를 몇 m 올릴까요?"}
{"status":"refuse","reply_ko":"일조, 건폐율, 건물 이름 지정은 아직 적용하지 않습니다."}
"""


async def interpret_city_command(
    prompt: str,
    summary: dict[str, Any],
    *,
    history: list[dict[str, str]] | None = None,
    partial: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """문장을 모델이 edit 또는 create로 읽는다. 실패하면 문장 규칙으로 넘긴다."""
    text_prompt = (prompt or "").strip()
    user_text = (
        f"지시: {text_prompt}\n"
        f"선택 요약: 동수={summary.get('building_count', 0)}, "
        f"면적약={summary.get('area_m2', 0):.0f}m2, "
        f"평균높이={summary.get('mean_height_m')}, "
        f"용도비율={summary.get('use_mix') or {}}"
    )
    messages: list[dict[str, Any]] = [{"role": "system", "content": _CITY_SYSTEM}]
    for item in history or []:
        role = item.get("role") if item.get("role") in {"user", "assistant"} else "user"
        content = str(item.get("content") or "").strip()
        if content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_text})
    raw = await _vlm_complete(messages, max_tokens=1024)
    if isinstance(raw, dict):
        status = str(raw.get("status") or "").strip()
        if status in {"ask", "refuse"}:
            continued = drop_redundant_height_ask(raw, text_prompt) if status == "ask" else None
            if continued is None:
                rule = parse_edit_command(text_prompt) if status == "ask" else None
                if rule:
                    return {"kind": "edit", "edit": rule, "reply_ko": ""}
                message = str(raw.get("ask_ko") or raw.get("reply_ko") or "").strip()
                if not message:
                    message = "어떤 수정인지 한 가지만 더 알려 주세요."
                return {"kind": "ask", "reply_ko": message, "ask_ko": message}
            raw = continued
            status = "ok"
        edit = normalize_model_edit(raw)
        if edit:
            return {"kind": "edit", "edit": edit, "reply_ko": edit.get("reply_ko") or ""}
        if str(raw.get("action") or "") == "create" or raw.get("use_cat") or raw.get("height_m"):
            turn = next_sim_turn(text_prompt, history=history, partial=partial, llm_extra=raw)
            return {"kind": "create", "turn": turn}
    edit = parse_edit_command(text_prompt)
    if edit:
        return {"kind": "edit", "edit": edit, "reply_ko": ""}
    turn = next_sim_turn(text_prompt, history=history, partial=partial, llm_extra=None)
    return {"kind": "create", "turn": turn}


def fallback_count(prompt: str) -> int | None:
    text = (prompt or "").strip()
    for pat in (r"(\d+)\s*개\s*동", r"(\d+)\s*동", r"(\d+)\s*개"):
        m = re.search(pat, text)
        if m:
            n = int(m.group(1))
            if 1 <= n <= 80:
                return n
    if re.fullmatch(r"\d+", text):
        n = int(text)
        if 1 <= n <= 80:
            return n
    return None


def wants_building_count(text: str, typology: str | None) -> bool:
    if typology == "single_mass":
        return False
    blob = text or ""
    if any(k in blob for k in ("단지", "아파트", "여러 동", "타워")):
        return True
    return typology in {"slab_grid", "tower_grid", "podium_tower"}


def merge_partial(base: dict[str, Any] | None, extra: dict[str, Any] | None, prompt: str) -> dict[str, Any]:
    out: dict[str, Any] = dict(base or {})
    for key, val in (extra or {}).items():
        if val is not None and val != "":
            out[key] = val
    if not out.get("use_cat"):
        cat = fallback_use_cat(prompt)
        if cat:
            out["use_cat"] = cat
    if not out.get("typology"):
        typ = fallback_typology(prompt)
        if typ:
            out["typology"] = typ
    if out.get("count") is None:
        n = fallback_count(prompt)
        if n:
            out["count"] = n
    if out.get("height_m") is None and out.get("floors") is None:
        h = fallback_height_m(prompt)
        if h:
            out["height_m"] = h
    return out


def missing_question(partial: dict[str, Any], transcript: str) -> str | None:
    if prompt_states_height(transcript):
        return None
    typ = str(partial.get("typology") or fallback_typology(transcript) or "")
    if wants_building_count(transcript, typ or None) and not partial.get("count"):
        return "단지는 몇 개 동으로 할까요?"
    return None


_ALLOWED_IMAGE_MIME = frozenset({"image/jpeg", "image/png", "image/webp", "image/bmp"})
_MAX_IMAGE_B64 = 3_500_000


def parse_sim_image(image_b64: str | None, image_mime: str | None) -> tuple[str, str] | None:
    """첨부 이미지를 (순수 base64, mime)으로 검증한다. 없으면 None."""
    raw = (image_b64 or "").strip()
    if not raw:
        return None
    mime = (image_mime or "image/jpeg").split(";")[0].strip().lower()
    if raw.startswith("data:"):
        header, _, data = raw.partition(",")
        raw = data.strip()
        if "image/" in header:
            mime = header.split(";")[0].replace("data:", "").strip().lower()
    if mime not in _ALLOWED_IMAGE_MIME:
        raise ValueError("지원하지 않는 이미지 형식입니다")
    if len(raw) > _MAX_IMAGE_B64:
        raise ValueError("이미지가 너무 큽니다")
    try:
        base64.b64decode(raw, validate=False)
    except Exception as exc:
        raise ValueError("이미지를 읽지 못했습니다") from exc
    return raw, mime


def next_sim_turn(
    prompt: str,
    *,
    history: list[dict[str, str]] | None = None,
    partial: dict[str, Any] | None = None,
    llm_extra: dict[str, Any] | None = None,
    vision_failed: bool = False,
) -> SimTurn:
    """대화·부분 파라미터를 합쳐 ask 또는 완성 spec을 만든다."""
    hist = history or []
    extra = dict(llm_extra or {})
    status_hint = str(extra.pop("status", "") or "").strip()
    ask_from_llm = str(extra.pop("ask_ko", "") or "").strip() or None
    if vision_failed and not extra and not ask_from_llm:
        msg = "첨부한 이미지를 읽지 못했습니다. 원하는 형태를 글로 적어 주세요."
        return SimTurn(status="ask", reply_ko=msg, ask_ko=msg, spec=None, partial=dict(partial or {}))
    transcript = "\n".join(str(m.get("content") or "") for m in hist)
    if prompt:
        transcript = f"{transcript}\n{prompt}".strip()
    merged = merge_partial(partial, extra, prompt)
    rule_ask = missing_question(merged, transcript)
    ask: str | None = None
    if status_hint == "ask" and ask_from_llm:
        ask = ask_from_llm
    elif rule_ask:
        ask = ask_from_llm or rule_ask
    elif status_hint == "ask":
        ask = ask_from_llm or rule_ask
    if ask:
        return SimTurn(status="ask", reply_ko=ask, ask_ko=ask, spec=None, partial=merged)
    spec = normalize_spec(merged, prompt=transcript)
    if spec.count:
        spec.reply_ko = (
            str(extra.get("reply_ko") or "").strip()
            or f"선택 구역을 {spec.height_m:.0f}m {spec.use_cat} {spec.count}개 동으로 구성합니다."
        )
    elif extra.get("reply_ko"):
        spec.reply_ko = str(extra["reply_ko"]).strip()
    return SimTurn(
        status="ok",
        reply_ko=spec.reply_ko,
        ask_ko=None,
        spec=spec,
        partial=merged,
    )


def fallback_spec_from_prompt(prompt: str) -> SimSpec:
    """키워드만으로 SimSpec을 만든다. 테스트·LLM 실패 폴백."""
    return normalize_spec(
        {
            "use_cat": fallback_use_cat(prompt) or "주거",
            "height_m": fallback_height_m(prompt),
            "typology": fallback_typology(prompt) or "slab_grid",
            "reply_ko": "",
        },
        prompt=prompt,
    )


def extract_json_object(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("JSON 객체가 없습니다")
    out = json.loads(raw[start : end + 1])
    if not isinstance(out, dict):
        raise ValueError("JSON 객체가 아닙니다")
    return out


def _chat_url(base: str) -> str:
    return f"{base.rstrip('/')}/chat/completions"


def _assistant_text(data: dict[str, Any]) -> str:
    """OpenAI 호환·CLOVA 네이티브 응답에서 assistant 텍스트를 꺼낸다."""
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        msg = choices[0].get("message") or {}
        content = msg.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [str(p.get("text") or "") for p in content if isinstance(p, dict)]
            return "".join(parts)
    result = data.get("result")
    if isinstance(result, dict):
        msg = result.get("message") or {}
        content = msg.get("content")
        if isinstance(content, str):
            return content
    return ""


def _user_content_with_image(text: str, b64: str, mime: str) -> list[dict[str, Any]]:
    data_url = f"data:{mime};base64,{b64}"
    return [
        {"type": "text", "text": text},
        {"type": "image_url", "image_url": {"url": data_url}},
    ]


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _close_xy(pts: list[list[float]]) -> list[list[float]]:
    if not pts:
        return pts
    if pts[0][0] != pts[-1][0] or pts[0][1] != pts[-1][1]:
        return [*pts, [pts[0][0], pts[0][1]]]
    return pts


def _resample_ring(pts: list[list[float]], max_n: int) -> list[list[float]]:
    body = pts[:-1] if len(pts) > 1 and pts[0] == pts[-1] else pts
    if len(body) <= max_n:
        return _close_xy(body)
    step = len(body) / max_n
    sampled = [body[min(len(body) - 1, int(i * step))] for i in range(max_n)]
    return _close_xy(sampled)


def normalize_image_masses(raw: dict[str, Any] | None) -> list[ImageMass]:
    """VLM masses 배열을 0~1 링·허용 높이·용도로 고정한다."""
    hc = load_hardcode()
    max_n = int(hc.get("SIM_IMAGE_MASS_MAX", 20))
    max_ring = int(hc.get("SIM_IMAGE_MASS_MAX_RING", 12))
    floor_h = float(hc.get("FLOOR_HEIGHT_M", 3.25))
    rows = raw.get("masses") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        return []
    out: list[ImageMass] = []
    for row in rows:
        if not isinstance(row, dict) or len(out) >= max_n:
            continue
        ring_in = row.get("ring") or row.get("polygon") or []
        pts: list[list[float]] = []
        if isinstance(ring_in, list):
            for p in ring_in:
                if not isinstance(p, (list, tuple)) or len(p) < 2:
                    continue
                try:
                    pts.append([_clamp01(float(p[0])), _clamp01(float(p[1]))])
                except (TypeError, ValueError):
                    continue
        if len(pts) < 3:
            continue
        pts = _resample_ring(pts, max_ring)
        area = 0.0
        body = pts[:-1]
        for i, q in enumerate(body):
            r = body[(i + 1) % len(body)]
            area += q[0] * r[1] - r[0] * q[1]
        if abs(area) * 0.5 < 1e-4:
            continue
        floors: int | None = None
        try:
            if row.get("floors") is not None:
                floors = max(1, int(row["floors"]))
        except (TypeError, ValueError):
            floors = None
        height: float | None = None
        try:
            if row.get("height_m") is not None:
                height = float(row["height_m"])
        except (TypeError, ValueError):
            height = None
        if height is None or height < 3:
            height = (floors or 8) * floor_h
        height = max(6.0, min(300.0, height))
        if floors is None:
            floors = max(1, round(height / floor_h))
        use_cat = str(row.get("use_cat") or "주거").strip()
        if use_cat not in USE_CATS:
            use_cat = "주거"
        out.append(ImageMass(ring=pts, height_m=height, use_cat=use_cat, floors=floors))
    return out


def apply_stated_height(masses: list[ImageMass], prompt: str) -> float | None:
    """사용자가 말한 층수·m를 모든 동 높이에 덮어쓴다."""
    floors = stated_floors(prompt)
    floor_h = _defaults()["floor_h"]
    stated: float | None = None
    if floors is not None:
        stated = floors * floor_h
    elif re.search(r"(\d+(?:\.\d+)?)\s*(?:m|미터)", prompt or "", re.I):
        stated = fallback_height_m(prompt)
    if stated is None:
        return None
    stated = max(6.0, min(300.0, round(stated, 2)))
    derived = max(1, round(stated / floor_h))
    for mass in masses:
        mass.height_m = stated
        mass.floors = derived
    return stated


async def _vlm_complete(
    messages: list[dict[str, Any]],
    *,
    max_tokens: int = 1024,
) -> dict[str, Any] | None:
    settings = get_settings()
    payload = {
        "model": settings.clova_model,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": max_tokens,
    }
    if settings.clova_model.lower().startswith("qwen"):
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    headers = {
        "Authorization": f"Bearer {settings.clova_api_key.strip()}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=120.0, verify=settings.clova_ssl_verify) as client:
            resp = await client.post(_chat_url(settings.clova_base_url), json=payload, headers=headers)
        if resp.status_code >= 400:
            logger.warning("VLM HTTP %s: %s", resp.status_code, resp.text[:400])
            resp.raise_for_status()
        return extract_json_object(_assistant_text(resp.json()))
    except Exception as exc:
        logger.warning("VLM 호출 실패: %s", exc)
        return None


async def interpret_image_masses(
    prompt: str,
    summary: dict[str, Any],
    *,
    history: list[dict[str, str]] | None = None,
    image_b64: str | None = None,
    image_mime: str | None = None,
) -> SimTurn:
    """조감도·평면 이미지에서 매스 폴리곤을 추론한다."""
    settings = get_settings()
    if not settings.has_clova_key:
        raise ValueError("CLOVA_API_KEY가 없습니다. .env에 키를 넣은 뒤 백엔드를 재시작하세요.")
    parsed = parse_sim_image(image_b64, image_mime)
    empty = SimTurn(
        status="ask",
        reply_ko="",
        ask_ko=None,
        spec=None,
        partial={},
        masses=[],
        session="image_mass",
    )
    if not parsed:
        empty.reply_ko = "조감도나 평면 도면 이미지를 첨부해 주세요."
        empty.ask_ko = empty.reply_ko
        return empty
    text_prompt = prompt.strip() or "이 도면의 건축물 형태로 올려줘"
    user_text = (
        f"지시: {text_prompt}\n"
        f"선택 대지 면적약={summary.get('area_m2', 0):.0f}m2, "
        f"기존 동수={summary.get('building_count', 0)}\n"
        "첨부 이미지는 단지 배치도다. 색칠된 건물 동 외곽만 masses로 옮긴다. "
        "도로·나무·범례는 제외한다. 높이는 비운다."
    )
    messages: list[dict[str, Any]] = [{"role": "system", "content": _IMAGE_MASS_SYSTEM}]
    for item in history or []:
        role = item.get("role") if item.get("role") in {"user", "assistant"} else "user"
        content = str(item.get("content") or "").strip()
        if content:
            messages.append({"role": role, "content": content})
    b64, mime = parsed
    messages.append({"role": "user", "content": _user_content_with_image(user_text, b64, mime)})
    raw = await _vlm_complete(messages, max_tokens=4096)
    if raw is None:
        empty.reply_ko = "첨부한 도면을 읽지 못했습니다. 더 선명한 평면이나 조감도를 넣어 주세요."
        empty.ask_ko = empty.reply_ko
        return empty
    masses = normalize_image_masses(raw)
    ask = str(raw.get("ask_ko") or "").strip()
    if not masses:
        empty.reply_ko = ask or "동 외곽을 도면에서 읽지 못했습니다. 건물 바닥이 구분되는 배치도를 넣어 주세요."
        empty.ask_ko = empty.reply_ko
        return empty
    stated = apply_stated_height(masses, text_prompt)
    if stated is not None:
        floors = stated_floors(text_prompt)
        height_label = f"{floors}층({stated:.0f}m)" if floors else f"{stated:.0f}m"
        reply = f"배치도에서 {len(masses)}개 동 외곽을 읽고, 높이는 {height_label}로 맞춥니다."
    else:
        reply = str(raw.get("reply_ko") or "").strip() or (
            f"도면에서 {len(masses)}개 동 외곽을 올립니다. 높이를 말하면 그 값으로 맞춥니다."
        )
    return SimTurn(
        status="ok",
        reply_ko=reply,
        ask_ko=None,
        spec=None,
        partial={},
        masses=masses,
        session="image_mass",
    )


async def interpret_prompt(
    prompt: str,
    summary: dict[str, Any],
    *,
    history: list[dict[str, str]] | None = None,
    partial: dict[str, Any] | None = None,
    image_b64: str | None = None,
    image_mime: str | None = None,
) -> SimTurn:
    """게이트웨이 VLM으로 SimTurn을 받는다. 실패 시 키워드 폴백."""
    settings = get_settings()
    if not settings.has_clova_key:
        raise ValueError("CLOVA_API_KEY가 없습니다. .env에 키를 넣은 뒤 백엔드를 재시작하세요.")
    parsed = parse_sim_image(image_b64, image_mime)
    text_prompt = prompt.strip() or ("이 이미지처럼 구성해줘" if parsed else "")
    user_text = (
        f"지시: {text_prompt}\n"
        f"선택 요약: 동수={summary.get('building_count', 0)}, "
        f"면적약={summary.get('area_m2', 0):.0f}m2, "
        f"용도비율={summary.get('use_mix') or {}}\n"
        f"지금까지 모은 값: {json.dumps(partial or {}, ensure_ascii=False)}"
    )
    if parsed:
        user_text += (
            "\n첨부 이미지를 보고 건물 형태·대략 동 수·상대 높이를 spec에 반영한다. "
            "JSON 한 객체만 출력한다."
        )
    messages: list[dict[str, Any]] = [{"role": "system", "content": _SYSTEM}]
    for item in history or []:
        role = item.get("role") if item.get("role") in {"user", "assistant"} else "user"
        content = str(item.get("content") or "").strip()
        if content:
            messages.append({"role": role, "content": content})
    if parsed:
        b64, mime = parsed
        messages.append({"role": "user", "content": _user_content_with_image(user_text, b64, mime)})
    else:
        messages.append({"role": "user", "content": user_text})
    llm_extra = await _vlm_complete(messages, max_tokens=1024)
    return next_sim_turn(
        text_prompt,
        history=history,
        partial=partial,
        llm_extra=llm_extra,
        vision_failed=bool(parsed and llm_extra is None),
    )
