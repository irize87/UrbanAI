from __future__ import annotations

from app.services.city_analysis import parse_analysis_prompt, row_passes_filter, scenario_score


def test_parse_age_and_floors_is_filter():
    spec = parse_analysis_prompt("30년 이상 된 5층 이하 건물만 표시해줘")
    assert spec["action"] == "filter"
    assert spec["age_min"] == 30
    assert spec["floors_max"] == 5


def test_parse_school_and_zone_is_scenario():
    spec = parse_analysis_prompt("학교 반경 500m 안 상업지역 저층만 보여줘")
    assert spec["action"] == "scenario"
    assert spec["school_m"] == 500
    assert spec["zone_cats"] == ["상업"]
    assert spec["floors_max"] == 5


def test_old_filter_skips_missing_year():
    spec = {"age_min": 30, "floors_max": 5, "use_cats": []}
    assert row_passes_filter({"year": 1990, "floors": 4, "height_m": 12}, spec, now_year=2026)
    assert not row_passes_filter({"year": None, "floors": 4, "height_m": 12}, spec, now_year=2026)
    assert not row_passes_filter({"year": 2015, "floors": 4, "height_m": 12}, spec, now_year=2026)


def test_parse_guide_prompts():
    assert parse_analysis_prompt("30년 이상, 5층 이하만 표시해줘")["action"] == "filter"
    assert parse_analysis_prompt("브리핑 해줘")["action"] == "brief"
    subway = parse_analysis_prompt("지하철 반경 500m 지역 중 30년 이상된 저층 건물 표시해줘")
    assert subway["action"] == "scenario"
    assert subway["station_m"] == 500
    assert subway["age_min"] == 30
    assert subway["floors_max"] == 5
    edit = parse_analysis_prompt("해당 건물 높이 10층 높이로 바꿔줘")
    assert edit["action"] == "edit"
    assert edit["edit"]["height_set_m"] == 32.5
    flood = parse_analysis_prompt("강수량 100mm 상황 보여줘")
    assert flood["action"] == "flood"
    assert flood["rainfall_mm"] == 100
    tape = parse_analysis_prompt("1988년부터 서초구의 성장 역사를 보여줘")
    assert tape["action"] == "timelapse"
    assert tape["start_year"] == 1988


def test_brief_skips_far_and_road_width():
    from app.services.city_analysis import _brief_lines

    text = _brief_lines(
        {
            "count": 3,
            "avg_height_m": 12.0,
            "avg_floors": 4.0,
            "uses": {"주거": 2, "미상": 1},
            "old_count": 1,
            "dated_count": 2,
            "old_years": 30,
            "old_ratio": 0.5,
        }
    )
    assert "용적률" not in text
    assert "접도" not in text
    assert text.startswith("# 도시 현황 브리핑")


def test_scenario_score_uses_fixed_rules():
    assert scenario_score(age=40, floors=4, zone="상업", station_m=200) >= 5
    assert scenario_score(age=5, floors=20, zone="녹지", station_m=2000) == 0
