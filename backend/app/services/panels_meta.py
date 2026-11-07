from __future__ import annotations

FLOOR_H = 3.25

# 원본 sitedia 갤러리 순서
PANELS: list[dict] = [
    {
        "key": "aerial",
        "label": "AERIAL",
        "ko": "항공사진",
        "color": None,
        "cats": None,
        "ramp": None,
        "threshold": False,
        "thr_default": None,
        "metrics": None,
        "source": "realtime",
        "color_locked": True,
    },
    {
        "key": "buildings",
        "label": "BUILDINGS",
        "ko": "건물",
        "color": "#000000",
        "cats": None,
        "ramp": None,
        "threshold": False,
        "thr_default": None,
        "metrics": None,
        "source": "local",
    },
    {
        "key": "use",
        "label": "BUILDING USE",
        "ko": "건축물 용도",
        "color": None,
        "cats": [
            {"key": "주거", "label": "주거", "color": "#f2d98d"},
            {"key": "근린생활", "label": "근린생활", "color": "#f0a878"},
            {"key": "업무", "label": "업무", "color": "#6f9fd0"},
            {"key": "판매", "label": "판매", "color": "#e0607f"},
            {"key": "숙박", "label": "숙박", "color": "#a97fd0"},
            {"key": "문화집회", "label": "문화집회", "color": "#5fbfb0"},
            {"key": "교육복지", "label": "교육복지", "color": "#86c06a"},
            {"key": "공업", "label": "공업", "color": "#9c8878"},
            {"key": "기타", "label": "기타", "color": "#c4c4c4"},
            {"key": "미상", "label": "미상", "color": "#ececec"},
        ],
        "ramp": None,
        "threshold": False,
        "thr_default": None,
        "metrics": None,
        "source": "local",
    },
    {
        "key": "age",
        "label": "BUILDING AGE",
        "ko": "준공 연대",
        "color": "#b34a35",
        "cats": None,
        "ramp": ["~1979", "1980년대", "1990년대", "2000년대", "2010년대", "2020~"],
        "threshold": False,
        "thr_default": None,
        "metrics": None,
        "source": "local",
    },
    {
        "key": "high",
        "label": "HIGH BUILDINGS",
        "ko": "고층",
        "color": "#4169e1",
        "cats": None,
        "ramp": None,
        "threshold": True,
        "thr_default": 35,
        "metrics": None,
        "source": "local",
    },
    {
        "key": "lowrise",
        "label": "LOW-RISE",
        "ko": "저층",
        "color": "#4ecdc4",
        "cats": None,
        "ramp": None,
        "threshold": True,
        "thr_default": 12,
        "metrics": None,
        "source": "local",
    },
    {
        "key": "zoning",
        "label": "ZONING",
        "ko": "용도지역",
        "color": None,
        "cats": [
            {"key": "주거", "label": "주거", "color": "#f7e7a0"},
            {"key": "상업", "label": "상업", "color": "#f7b2c4"},
            {"key": "공업", "label": "공업", "color": "#c9b3e8"},
            {"key": "녹지", "label": "녹지", "color": "#b9dcae"},
            {"key": "관리", "label": "관리", "color": "#ece4d4"},
            {"key": "농림", "label": "농림", "color": "#d9e3c0"},
            {"key": "자연환경", "label": "자연환경", "color": "#c5ded8"},
            {"key": "기타", "label": "기타", "color": "#e0e0e0"},
        ],
        "ramp": None,
        "threshold": False,
        "thr_default": None,
        "metrics": None,
        "source": "local",
    },
    {
        "key": "green",
        "label": "GREEN SPACES",
        "ko": "공원·녹지·하천",
        "color": "#7ac943",
        "cats": [
            {"key": "park", "label": "공원·녹지(지정)", "color": "#7ac943"},
            {"key": "other", "label": "그 밖의 녹지", "color": "#c3e6a4"},
            {"key": "water", "label": "하천", "color": "#8ecae6"},
            {"key": "sea", "label": "바다", "color": "#5fa8d3"},
        ],
        "ramp": None,
        "threshold": False,
        "thr_default": None,
        "metrics": None,
        "source": "local",
    },
    {
        "key": "district",
        "label": "DISTRICTS & ZONES",
        "ko": "구역·지구",
        "color": "#2ab3a6",
        "cats": [
            {"key": "duz", "label": "지구단위계획구역", "color": "#2ab3a6"},
            {"key": "ind_bnd", "label": "산업단지", "color": "#b0724a"},
            {"key": "lh_done", "label": "사업지구(준공)", "color": "#8a8f98"},
            {"key": "lh_plan", "label": "사업지구(계획·시행)", "color": "#e2571f"},
        ],
        "ramp": None,
        "threshold": False,
        "thr_default": None,
        "metrics": None,
        "source": "local",
    },
    {
        "key": "planned",
        "label": "PLANNED FACILITIES",
        "ko": "도시계획시설",
        "color": None,
        "cats": [
            {"key": "road", "label": "계획도로", "color": "#f59e0b", "off": True},
            {"key": "school", "label": "학교", "color": "#3f6fb7"},
            {"key": "edu", "label": "청사·문화", "color": "#9fb4d8"},
            {"key": "market", "label": "시장", "color": "#e0607f"},
            {"key": "parking", "label": "주차·철도", "color": "#c9b3e8"},
        ],
        "ramp": None,
        "threshold": False,
        "thr_default": None,
        "metrics": None,
        "source": "local",
    },
    {
        "key": "roads",
        "label": "ROADS",
        "ko": "도로",
        "color": "#38bdf8",
        "cats": None,
        "ramp": None,
        "threshold": False,
        "thr_default": None,
        "metrics": None,
        "source": "local",
    },
    {
        "key": "transit",
        "label": "SUBWAY & BUS",
        "ko": "지하철·버스",
        "color": None,
        "cats": [
            {"key": "subway", "label": "지하철", "color": "#e5308c"},
            {"key": "bus", "label": "버스", "color": "#38bdf8"},
        ],
        "ramp": None,
        "threshold": False,
        "thr_default": None,
        "metrics": None,
        "source": "local",
    },
    {
        "key": "block",
        "label": "CITY BLOCK",
        "ko": "가구(街區)",
        "color": "#e8825f",
        "cats": None,
        "ramp": None,
        "threshold": False,
        "thr_default": None,
        "metrics": None,
        "source": "local",
    },
    {
        "key": "heat",
        "label": "DENSITY",
        "ko": "밀도",
        "color": None,
        "cats": None,
        "ramp": None,
        "threshold": False,
        "thr_default": None,
        "metrics": [["area", "건축면적"], ["count", "건축물 수"]],
        "source": "local",
        "color_locked": True,
    },
    {
        "key": "terrain",
        "label": "TERRAIN",
        "ko": "지형·등고선",
        "color": None,
        "cats": None,
        "ramp": None,
        "threshold": False,
        "thr_default": None,
        "metrics": None,
        "source": "local",
        "color_locked": True,
    },
    {
        "key": "slope",
        "label": "SLOPE",
        "ko": "경사도",
        "color": "#9a6238",
        "cats": [
            {"key": "s0", "label": "<5°", "color": "#f3e6d4"},
            {"key": "s1", "label": "5–10°", "color": "#e0c4a0"},
            {"key": "s2", "label": "10–15°", "color": "#c49262"},
            {"key": "s3", "label": "15–20°", "color": "#9a6238"},
            {"key": "s4", "label": "≥20°", "color": "#5c3310"},
        ],
        "ramp": ["<5°", "5–10°", "10–15°", "15–20°", "≥20°"],
        "threshold": False,
        "thr_default": None,
        "metrics": None,
        "source": "local",
    },
    {
        "key": "parcels",
        "label": "CADASTRAL",
        "ko": "지적",
        "color": "#d08466",
        "cats": None,
        "ramp": None,
        "threshold": False,
        "thr_default": None,
        "metrics": None,
        "source": "local",
    },
    {
        "key": "plot_size",
        "label": "PLOT SIZE",
        "ko": "필지 규모",
        "color": "#3d7eb8",
        "cats": [
            {"key": "p0", "label": "<100㎡", "color": "#e8f2fc"},
            {"key": "p1", "label": "100–300㎡", "color": "#b7d4f0"},
            {"key": "p2", "label": "300–1,000㎡", "color": "#7eaddc"},
            {"key": "p3", "label": "1,000–3,000㎡", "color": "#3d7eb8"},
            {"key": "p4", "label": "≥3,000㎡", "color": "#1a4f86"},
        ],
        "ramp": ["<100", "100–300", "300–1,000", "1,000–3,000", "≥3,000㎡"],
        "threshold": False,
        "thr_default": None,
        "metrics": None,
        "source": "local",
    },
    {
        "key": "sun_hours",
        "label": "SUN HOURS",
        "ko": "일조시간",
        "color": None,
        "cats": None,
        "ramp": None,
        "threshold": False,
        "thr_default": None,
        "metrics": None,
        "source": "local",
        "color_locked": True,
    },
    {
        "key": "trees",
        "label": "TREES",
        "ko": "수목",
        "color": "#3d8c4a",
        "cats": [
            {"key": "canopy", "label": "수관(면)", "color": "#b5d0a0"},
            {"key": "tree", "label": "개체", "color": "#3d8c4a"},
        ],
        "ramp": None,
        "threshold": False,
        "thr_default": None,
        "metrics": None,
        "source": "realtime",
    },
]

AGE_RAMP = ["#7c2d12", "#b34a35", "#d9714e", "#ee9d6f", "#f6c89f", "#fdeeda"]
AGE_LABELS = ["~1979", "1980년대", "1990년대", "2000년대", "2010년대", "2020~"]
HEIGHT_RAMP = ["#dbeafe", "#93c5fd", "#3b82f6", "#1d4ed8", "#172554"]
HEIGHT_LABELS = ["~12m", "12~25m", "25~45m", "45~80m", "80m~"]

AGE_COLORS = {
    "~1979": "#7c2d12",
    "1980년대": "#b34a35",
    "1990년대": "#d9714e",
    "2000년대": "#ee9d6f",
    "2010년대": "#f6c89f",
    "2020~": "#fdeeda",
    "미상": "#ececec",
}

AGE_UNKNOWN_3D = "#dcdcdc"
SOLID_3D = "#d8d8d8"
PLOT_SIZE_RAMP = ["#e8f2fc", "#b7d4f0", "#7eaddc", "#3d7eb8", "#1a4f86"]
SLOPE_RAMP = ["#f3e6d4", "#e0c4a0", "#c49262", "#9a6238", "#5c3310"]


def plot_size_bucket(area_m2: float) -> tuple[str, int]:
    if area_m2 < 100:
        return "p0", 0
    if area_m2 < 300:
        return "p1", 1
    if area_m2 < 1000:
        return "p2", 2
    if area_m2 < 3000:
        return "p3", 3
    return "p4", 4


def _hex_to_rgb(hex_color: str) -> tuple[float, float, float]:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    return int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0, int(h[4:6], 16) / 255.0


def _rgb_to_hsl(r: float, g: float, b: float) -> tuple[float, float, float]:
    mx, mn = max(r, g, b), min(r, g, b)
    l = (mx + mn) / 2
    if mx == mn:
        return 0.0, 0.0, l
    d = mx - mn
    s = d / (2 - mx - mn) if l > 0.5 else d / (mx + mn)
    if mx == r:
        h = ((g - b) / d + (6 if g < b else 0)) / 6
    elif mx == g:
        h = ((b - r) / d + 2) / 6
    else:
        h = ((r - g) / d + 4) / 6
    return h, s, l


def _hue_to_rgb(p: float, q: float, t: float) -> float:
    if t < 0:
        t += 1
    if t > 1:
        t -= 1
    if t < 1 / 6:
        return p + (q - p) * 6 * t
    if t < 1 / 2:
        return q
    if t < 2 / 3:
        return p + (q - p) * (2 / 3 - t) * 6
    return p


def _hsl_to_hex(h: float, s: float, l: float) -> str:
    if s == 0:
        r = g = b = l
    else:
        q = l * (1 + s) if l < 0.5 else l + s - l * s
        p = 2 * l - q
        r = _hue_to_rgb(p, q, h + 1 / 3)
        g = _hue_to_rgb(p, q, h)
        b = _hue_to_rgb(p, q, h - 1 / 3)
    return f"#{int(round(r * 255)):02x}{int(round(g * 255)):02x}{int(round(b * 255)):02x}"


def tint_ramp(template: list[str], base: str | None) -> list[str]:
    """강조색의 색상만 바꾸고, 명암 단계는 템플릿을 유지한다."""
    if not base:
        return list(template)
    try:
        bh, bs, _ = _rgb_to_hsl(*_hex_to_rgb(base))
    except (ValueError, IndexError):
        return list(template)
    out: list[str] = []
    for step in template:
        _, _, tl = _rgb_to_hsl(*_hex_to_rgb(step))
        out.append(_hsl_to_hex(bh, bs, tl))
    return out


def age_ramp(base: str | None = None) -> list[str]:
    return tint_ramp(AGE_RAMP, base)


def height_ramp(base: str | None = None) -> list[str]:
    return tint_ramp(HEIGHT_RAMP, base)


def age_bucket(year: int | None) -> str:
    if year is None or year <= 0:
        return "미상"
    if year < 1980:
        return "~1979"
    if year < 1990:
        return "1980년대"
    if year < 2000:
        return "1990년대"
    if year < 2010:
        return "2000년대"
    if year < 2020:
        return "2010년대"
    return "2020~"


def panel_by_key(key: str) -> dict | None:
    return next((p for p in PANELS if p["key"] == key), None)


def panel_payload() -> dict:
    legend_keys = [
        ("zoning", "용도지역"),
        ("use", "건축물 용도"),
        ("age", "준공 연대"),
        ("green", "공원·녹지·하천"),
        ("planned", "도시계획시설"),
        ("district", "구역·지구"),
        ("transit", "지하철·버스"),
    ]
    legends = []
    for key, title in legend_keys:
        if key == "age":
            legends.append(
                {
                    "title": title,
                    "items": [{"name": k, "color": v} for k, v in AGE_COLORS.items()],
                }
            )
            continue
        panel = panel_by_key(key)
        if not panel or not panel.get("cats"):
            continue
        legends.append(
            {
                "title": title,
                "items": [
                    {"name": c["label"], "color": c["color"]}
                    for c in panel["cats"]
                    if not c.get("off")
                ],
            }
        )
    return {
        "panels": PANELS,
        "floor_h": FLOOR_H,
        "scope": "seocho",
        "age_ramp": AGE_RAMP,
        "height_ramp": HEIGHT_RAMP,
        "legends": legends,
    }
