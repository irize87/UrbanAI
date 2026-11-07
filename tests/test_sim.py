from __future__ import annotations

from shapely.geometry import shape

from app.core.config import get_settings
from app.db.fixtures import load_fixtures
from app.services.mass3d import build_mass_payload
from app.services.sim_layout import apply_image_preview, apply_layout
from app.services.sim_llm import (
    extract_json_object,
    fallback_spec_from_prompt,
    next_sim_turn,
    normalize_image_masses,
    normalize_spec,
    parse_sim_image,
    _assistant_text,
)


def test_fallback_apartment_30m():
    spec = fallback_spec_from_prompt("선택된 지역을 30m 높이 아파트 단지로 구성해줘")
    assert spec.use_cat == "주거"
    assert spec.height_m == 30
    assert spec.typology == "slab_grid"
    assert spec.floors and spec.floors >= 9


def test_fallback_office_tower():
    spec = fallback_spec_from_prompt("업무 타워 15층으로 바꿔줘")
    assert spec.use_cat == "업무"
    assert spec.typology == "tower_grid"
    assert spec.height_m == 15 * 3.25


def test_normalize_rejects_unknown_use():
    spec = normalize_spec({"use_cat": "우주기지", "height_m": 20, "typology": "nope"}, prompt="학교")
    assert spec.use_cat == "교육복지"
    assert spec.typology == "slab_grid"
    assert 6 <= spec.height_m <= 300
    assert 0.2 <= spec.coverage <= 0.5


def test_extract_json_from_fenced_text():
    raw = '설명\n```json\n{"use_cat":"주거","height_m":24}\n```\n'
    out = extract_json_object(raw)
    assert out["use_cat"] == "주거"
    assert out["height_m"] == 24


def test_mass_payload_includes_building_id():
    load_fixtures()
    out = build_mass_payload(37.4837, 127.0324, 800, "circle")
    feats = out["buildings"]["features"]
    assert feats
    assert all(f["properties"].get("id") is not None for f in feats)


def test_layout_grid_stays_inside_box():
    load_fixtures()
    spec = fallback_spec_from_prompt("30m 높이 아파트 단지")
    lat, lng = 37.4837, 127.0324
    dlat = 60 / 111320
    dlng = 60 / (111320 * 0.79)
    box = [
        [lng - dlng, lat - dlat],
        [lng + dlng, lat - dlat],
        [lng + dlng, lat + dlat],
        [lng - dlng, lat + dlat],
        [lng - dlng, lat - dlat],
    ]
    laid = apply_layout(
        spec=spec,
        lat=lat,
        lng=lng,
        radius_m=800,
        shape_kind="circle",
        include_adjacent=True,
        building_ids=[],
        box_ll=box,
    )
    assert laid["sim_count"] >= 1
    assert laid["replaced_count"] >= 1
    from shapely.geometry import Polygon

    sel = Polygon([(p[0], p[1]) for p in box])
    for feat in laid["buildings"]["features"]:
        geom = shape(feat["geometry"])
        assert feat["properties"]["use_cat"] == "주거"
        assert feat["properties"]["sim"] is True
        assert feat["properties"]["height_m"] == 30
        assert sel.buffer(1e-5).contains(geom.centroid)


def test_layout_irregular_polygon():
    load_fixtures()
    spec = fallback_spec_from_prompt("30m 높이 아파트 단지")
    lat, lng = 37.4837, 127.0324
    dlat = 80 / 111320
    dlng = 80 / (111320 * 0.79)
    poly = [
        [lng - dlng, lat - dlat],
        [lng + dlng * 0.4, lat - dlat * 1.2],
        [lng + dlng, lat],
        [lng + dlng * 0.2, lat + dlat],
        [lng - dlng * 0.6, lat + dlat * 0.3],
    ]
    laid = apply_layout(
        spec=spec,
        lat=lat,
        lng=lng,
        radius_m=800,
        shape_kind="circle",
        include_adjacent=True,
        building_ids=[],
        box_ll=poly,
    )
    assert laid["sim_count"] >= 1
    sel = shape({"type": "Polygon", "coordinates": [poly + [poly[0]]]})
    for feat in laid["buildings"]["features"]:
        geom = shape(feat["geometry"])
        assert feat["properties"]["sim"] is True
        assert sel.buffer(1e-5).contains(geom.centroid)


def test_health_exposes_clova_flag():
    settings = get_settings()
    assert settings.has_clova_key is bool(settings.clova_api_key.strip())


def test_ask_for_building_count_then_apply():
    first = next_sim_turn("아파트 단지 구성해줘")
    assert first.status == "ask"
    assert first.spec is None
    assert "몇 개 동" in (first.ask_ko or "")
    second = next_sim_turn(
        "3개",
        history=[
            {"role": "user", "content": "아파트 단지 구성해줘"},
            {"role": "assistant", "content": first.ask_ko or ""},
        ],
        partial=first.partial,
    )
    assert second.status == "ok"
    assert second.spec is not None
    assert second.spec.count == 3
    assert second.spec.use_cat == "주거"
    assert second.spec.typology == "slab_grid"


def test_ask_prefers_llm_wording():
    turn = next_sim_turn(
        "아파트 단지 구성해줘",
        llm_extra={
            "status": "ask",
            "ask_ko": "몇 개 동으로 배치할까요?",
            "use_cat": "주거",
            "typology": "slab_grid",
        },
    )
    assert turn.status == "ask"
    assert turn.ask_ko == "몇 개 동으로 배치할까요?"


def test_parse_sim_image_rejects_bad_mime():
    try:
        parse_sim_image("AAAA", "application/pdf")
    except ValueError as exc:
        assert "형식" in str(exc)
    else:
        raise AssertionError("mime 검사가 통과되면 안 됩니다")


def test_assistant_text_reads_openai_and_fenced_json():
    raw = _assistant_text(
        {"choices": [{"message": {"content": "```json\n{\"use_cat\":\"주거\"}\n```"}}]}
    )
    out = extract_json_object(raw)
    assert out["use_cat"] == "주거"


def test_vision_failed_asks_instead_of_guessing():
    turn = next_sim_turn("", vision_failed=True)
    assert turn.status == "ask"
    assert "이미지" in (turn.ask_ko or "")


def test_apply_stated_height_overrides_plan_legend():
    from app.services.sim_llm import ImageMass, apply_stated_height

    masses = [
        ImageMass(ring=[[0, 0], [1, 0], [1, 1], [0, 0]], height_m=12, use_cat="주거", floors=4),
        ImageMass(ring=[[0, 0], [0.2, 0], [0.2, 0.4], [0, 0]], height_m=9, use_cat="주거", floors=3),
    ]
    stated = apply_stated_height(masses, "이 배치도처럼 단지를 만들고 49층으로 해줘")
    assert stated == round(49 * 3.25, 2)
    assert all(mass.height_m == stated and mass.floors == 49 for mass in masses)
    masses = normalize_image_masses(
        {
            "masses": [
                {
                    "ring": [[0.1, 0.1], [0.4, 0.1], [0.4, 0.35], [0.1, 0.35]],
                    "height_m": 24,
                    "use_cat": "업무",
                },
                {"ring": [[0.0, 0.0]], "height_m": 10},
            ]
        }
    )
    assert len(masses) == 1
    assert masses[0].use_cat == "업무"
    assert masses[0].height_m == 24
    assert masses[0].ring[0] == masses[0].ring[-1]


def test_layout_image_masses_inside_box():
    load_fixtures()
    lat, lng = 37.4837, 127.0324
    dlat = 80 / 111320
    dlng = 80 / (111320 * 0.79)
    box = [
        [lng - dlng, lat - dlat],
        [lng + dlng, lat - dlat],
        [lng + dlng, lat + dlat],
        [lng - dlng, lat + dlat],
        [lng - dlng, lat - dlat],
    ]
    masses = normalize_image_masses(
        {
            "masses": [
                {
                    "ring": [[0.2, 0.2], [0.6, 0.2], [0.6, 0.55], [0.2, 0.55]],
                    "height_m": 21,
                    "floors": 6,
                    "use_cat": "주거",
                }
            ]
        }
    )
    laid = apply_image_preview(
        masses=masses,
        lat=lat,
        lng=lng,
        radius_m=800,
        shape_kind="circle",
        include_adjacent=True,
        building_ids=[],
        box_ll=box,
    )
    assert laid["session"] == "image_mass"
    assert laid["sim_count"] == 1
    feat = laid["buildings"]["features"][0]
    assert feat["properties"]["preview"] is True
    assert feat["properties"]["height_m"] == 21
    sel = shape({"type": "Polygon", "coordinates": [box]})
    assert sel.buffer(1e-5).contains(shape(feat["geometry"]).centroid)


def test_drop_height_ask_when_floors_given():
    from app.services.sim_llm import drop_redundant_height_ask, next_sim_turn

    prompt = "ㄱ자 형태로 아파트 단지로 새로 구성해줘. 49층 높이."
    raw = drop_redundant_height_ask(
        {"status": "ask", "ask_ko": "건물의 높이를 몇 m로 설정할까요?"},
        prompt,
    )
    assert raw is not None
    assert raw["floors"] == 49
    turn = next_sim_turn(prompt, llm_extra=raw)
    assert turn.status == "ok"
    assert turn.spec is not None
    assert turn.spec.floors == 49
    assert turn.spec.height_m == round(49 * 3.25, 2)
    from app.services.sim_llm import normalize_model_edit

    scaled = normalize_model_edit(
        {
            "action": "edit",
            "far_scale": 1.2,
            "height_add_m": 10,
            "reply_ko": "높이를 올리고 10m를 더합니다.",
        }
    )
    assert scaled is not None
    assert scaled["far_scale"] == 1.2
    assert scaled["height_add_m"] == 10
    percent = normalize_model_edit({"action": "edit", "far_scale": 20, "remove": False})
    assert percent is not None
    assert percent["far_scale"] == 1.2
    assert normalize_model_edit({"action": "create", "height_m": 30}) is None

    from app.services.sim_llm import parse_edit_command

    edit = parse_edit_command(
        "이 지역 용적률을 20% 높이고, 건물 높이를 10m 높였을 때 스카이라인을 보여줘"
    )
    assert edit is not None
    assert edit["far_scale"] == 1.2
    assert edit["height_add_m"] == 10
    assert edit["remove"] is False


def test_parse_edit_leaves_new_building_prompt():
    from app.services.sim_llm import parse_edit_command

    assert parse_edit_command("30m 주거 3동을 세워줘") is None


def test_apply_existing_edit_uses_override_then_delta():
    from app.db.fixtures import load_fixtures
    from app.services.sim_layout import apply_existing_edit

    load_fixtures(force=True)
    box = [
        [127.02, 37.47],
        [127.05, 37.47],
        [127.05, 37.50],
        [127.02, 37.50],
        [127.02, 37.47],
    ]
    first = apply_existing_edit(
        edit={"height_add_m": 10, "height_set_m": None, "far_scale": 1.2, "remove": False},
        lat=37.4837,
        lng=127.0324,
        radius_m=800,
        shape_kind="circle",
        include_adjacent=False,
        building_ids=[],
        box_ll=box,
    )
    assert first["action"] == "edit"
    assert first["updates"]
    sample = first["updates"][0]
    second = apply_existing_edit(
        edit={"height_add_m": 5, "height_set_m": None, "far_scale": None, "remove": False},
        lat=37.4837,
        lng=127.0324,
        radius_m=800,
        shape_kind="circle",
        include_adjacent=False,
        building_ids=[sample["id"]],
        box_ll=None,
        height_overrides={str(sample["id"]): sample["height_m"]},
    )
    assert second["updates"][0]["height_m"] == round(sample["height_m"] + 5, 2)
