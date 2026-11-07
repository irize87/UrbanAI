from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.solar import (
    KST,
    day_length_hours,
    minutes_to_hhmm,
    preset_civil_date,
    solar_position,
    sunrise_sunset_minutes,
)


SEOUL = (37.57, 126.98)


def test_summer_noon_altitude_seoul():
    dt = datetime(2026, 6, 21, 12, 34, tzinfo=KST)
    pos = solar_position(*SEOUL, dt)
    assert abs(pos.altitude_deg - 75.9) < 1.2
    assert 140 < pos.azimuth_deg < 220


def test_winter_noon_altitude_seoul():
    dt = datetime(2026, 12, 21, 12, 30, tzinfo=KST)
    pos = solar_position(*SEOUL, dt)
    assert abs(pos.altitude_deg - 29.0) < 1.2


def test_spring_noon_near_theory():
    dt = datetime(2026, 3, 21, 12, 39, tzinfo=KST)
    pos = solar_position(*SEOUL, dt)
    assert abs(pos.altitude_deg - 52.5) < 1.5


def test_summer_sunrise_seoul():
    dt = datetime(2026, 6, 21, 12, 0, tzinfo=KST)
    rise, set_ = sunrise_sunset_minutes(*SEOUL, dt)
    assert rise is not None and set_ is not None
    assert abs(rise - (5 * 60 + 11)) < 12
    assert abs(set_ - (19 * 60 + 57)) < 12
    assert 14.2 < day_length_hours(*SEOUL, dt) < 15.0


def test_preset_and_hhmm():
    d = preset_civil_date("하지", 2026)
    assert d.month == 6 and d.day == 21
    assert minutes_to_hhmm(5 * 60 + 11) == "05:11"
