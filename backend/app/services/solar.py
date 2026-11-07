"""NOAA 태양 위치. 외부 API 없음. 서머타임 미적용."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
RAD = math.pi / 180.0
DEG = 180.0 / math.pi
REFRACT_ZENITH = 90.833


@dataclass(frozen=True)
class SunPosition:
    altitude_deg: float
    azimuth_deg: float
    zenith_deg: float
    declination_deg: float
    equation_of_time_min: float
    true_solar_minutes: float
    solar_noon_minutes: float


def _julian_day(dt: datetime) -> float:
    utc = dt.astimezone(timezone.utc)
    year, month = utc.year, utc.month
    day = (
        utc.day
        + (utc.hour + utc.minute / 60.0 + utc.second / 3600.0 + utc.microsecond / 3.6e9)
        / 24.0
    )
    if month <= 2:
        year -= 1
        month += 12
    century_part = math.floor(year / 100)
    b = 2 - century_part + math.floor(century_part / 4)
    return (
        math.floor(365.25 * (year + 4716))
        + math.floor(30.6001 * (month + 1))
        + day
        + b
        - 1524.5
    )


def _julian_century(jd: float) -> float:
    return (jd - 2451545.0) / 36525.0


def _geom_mean_long(t: float) -> float:
    return (280.46646 + t * (36000.76983 + t * 0.0003032)) % 360.0


def _geom_mean_anom(t: float) -> float:
    return 357.52911 + t * (35999.05029 - 0.0001537 * t)


def _eccentricity(t: float) -> float:
    return 0.016708634 - t * (0.000042037 + 0.0000001267 * t)


def _sun_eq_of_center(t: float) -> float:
    m = _geom_mean_anom(t) * RAD
    return (
        math.sin(m) * (1.914602 - t * (0.004817 + 0.000014 * t))
        + math.sin(2 * m) * (0.019993 - 0.000101 * t)
        + math.sin(3 * m) * 0.000289
    )


def _sun_true_long(t: float) -> float:
    return _geom_mean_long(t) + _sun_eq_of_center(t)


def _omega(t: float) -> float:
    return 125.04 - 1934.136 * t


def _sun_app_long(t: float) -> float:
    return _sun_true_long(t) - 0.00569 - 0.00478 * math.sin(_omega(t) * RAD)


def _mean_obliquity(t: float) -> float:
    seconds = 21.448 - t * (46.8150 + t * (0.00059 - t * 0.001813))
    return 23.0 + (26.0 + (seconds / 60.0)) / 60.0


def _obliquity_corr(t: float) -> float:
    return _mean_obliquity(t) + 0.00256 * math.cos(_omega(t) * RAD)


def _declination(t: float) -> float:
    e = _obliquity_corr(t) * RAD
    lam = _sun_app_long(t) * RAD
    return math.asin(math.sin(e) * math.sin(lam)) * DEG


def _equation_of_time(t: float) -> float:
    epsilon = _obliquity_corr(t) * RAD
    l0 = _geom_mean_long(t) * RAD
    e = _eccentricity(t)
    m = _geom_mean_anom(t) * RAD
    y = math.tan(epsilon / 2.0) ** 2
    sin2l0 = math.sin(2.0 * l0)
    sinm = math.sin(m)
    cos2l0 = math.cos(2.0 * l0)
    sin4l0 = math.sin(4.0 * l0)
    sin2m = math.sin(2.0 * m)
    et = y * sin2l0 - 2.0 * e * sinm + 4.0 * e * y * sinm * cos2l0
    et -= 0.5 * y * y * sin4l0 - 1.25 * e * e * sin2m
    return et * 4.0 * DEG


def _ha_sunrise(lat: float, dec: float) -> float | None:
    arg = math.cos(REFRACT_ZENITH * RAD) / (
        math.cos(lat * RAD) * math.cos(dec * RAD)
    ) - math.tan(lat * RAD) * math.tan(dec * RAD)
    if arg < -1.0 or arg > 1.0:
        return None
    return math.acos(arg) * DEG


def ensure_kst(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=KST)
    return dt.astimezone(KST)


def solar_position(
    lat: float,
    lng: float,
    dt: datetime,
    tz_offset_hours: float = 9.0,
) -> SunPosition:
    """위도·경도·시각 → 겉보기 고도(°)·방위(° 정북 시계방향)."""
    local = ensure_kst(dt) if abs(tz_offset_hours - 9.0) < 1e-6 else dt
    if local.tzinfo is None:
        local = local.replace(tzinfo=timezone(timedelta(hours=tz_offset_hours)))
    jd = _julian_day(local)
    t = _julian_century(jd)
    dec = _declination(t)
    eq = _equation_of_time(t)
    minutes = (
        local.hour * 60.0
        + local.minute
        + local.second / 60.0
        + local.microsecond / 6e7
    )
    true_solar = (minutes + eq + 4.0 * lng - 60.0 * tz_offset_hours) % 1440.0
    ha = true_solar / 4.0 - 180.0
    if ha < -180.0:
        ha += 360.0
    czen = math.sin(lat * RAD) * math.sin(dec * RAD) + math.cos(lat * RAD) * math.cos(
        dec * RAD
    ) * math.cos(ha * RAD)
    czen = min(1.0, max(-1.0, czen))
    zenith = math.acos(czen) * DEG
    az_num = math.sin(lat * RAD) * math.cos(zenith * RAD) - math.sin(dec * RAD)
    az_den = math.cos(lat * RAD) * math.sin(zenith * RAD)
    if abs(az_den) < 1e-12:
        az = 180.0 if lat > 0 else 0.0
    else:
        ac = min(1.0, max(-1.0, az_num / az_den))
        az_temp = math.acos(ac) * DEG
        az = (az_temp + 180.0) % 360.0 if ha > 0 else (540.0 - az_temp) % 360.0
    exo = zenith
    if exo > 85.0:
        altitude = 90.0 - exo
    else:
        te = math.tan((90.0 - exo) * RAD)
        if exo > 85.0:
            corr = 0.0
        elif exo > 5.0:
            corr = (
                58.1 / te
                - 0.07 / (te**3)
                + 0.000086 / (te**5)
            ) / 3600.0
        elif exo > -0.575:
            corr = 1735.0 + exo * (
                -518.2 + exo * (103.4 + exo * (-12.79 + exo * 0.711))
            )
            corr /= 3600.0
        else:
            corr = -20.774 / te / 3600.0
        altitude = 90.0 - exo + corr
    noon = (720.0 - 4.0 * lng - eq + 60.0 * tz_offset_hours) % 1440.0
    return SunPosition(
        altitude_deg=altitude,
        azimuth_deg=az,
        zenith_deg=zenith,
        declination_deg=dec,
        equation_of_time_min=eq,
        true_solar_minutes=true_solar,
        solar_noon_minutes=noon,
    )


def sunrise_sunset_minutes(
    lat: float,
    lng: float,
    dt: datetime,
    tz_offset_hours: float = 9.0,
) -> tuple[float | None, float | None]:
    """일출·일몰의 표준시 분(0–1440). 극야/백야면 None."""
    local = ensure_kst(dt)
    noon = local.replace(hour=12, minute=0, second=0, microsecond=0)
    pos = solar_position(lat, lng, noon, tz_offset_hours)
    ha = _ha_sunrise(lat, pos.declination_deg)
    if ha is None:
        return None, None
    rise = pos.solar_noon_minutes - ha * 4.0
    set_ = pos.solar_noon_minutes + ha * 4.0
    return rise % 1440.0, set_ % 1440.0


def day_length_hours(
    lat: float,
    lng: float,
    dt: datetime,
    tz_offset_hours: float = 9.0,
) -> float:
    rise, set_ = sunrise_sunset_minutes(lat, lng, dt, tz_offset_hours)
    if rise is None or set_ is None:
        pos = solar_position(lat, lng, dt.replace(hour=12, minute=0), tz_offset_hours)
        return 24.0 if pos.altitude_deg > 0 else 0.0
    span = set_ - rise
    if span < 0:
        span += 1440.0
    return span / 60.0


def minutes_to_hhmm(minutes: float) -> str:
    m = int(round(minutes)) % 1440
    return f"{m // 60:02d}:{m % 60:02d}"


def true_solar_window_std(
    lat: float,
    lng: float,
    dt: datetime,
    start_hour: float,
    end_hour: float,
    tz_offset_hours: float = 9.0,
) -> tuple[float, float]:
    """진태양시 창을 표준시 분으로 환산."""
    noon = ensure_kst(dt).replace(hour=12, minute=0, second=0, microsecond=0)
    pos = solar_position(lat, lng, noon, tz_offset_hours)
    offset = pos.solar_noon_minutes - 720.0
    return start_hour * 60.0 + offset, end_hour * 60.0 + offset


def preset_civil_date(kind: str, year: int = 2026) -> datetime:
    """하지·춘분·추분·동지·지금의 KST 정오(시각은 호출측에서 덮어씀)."""
    key = (kind or "solstice").strip().lower()
    if key in {"now", "지금"}:
        return datetime.now(KST)
    table = {
        "summer": (6, 21),
        "solstice": (6, 21),
        "하지": (6, 21),
        "spring": (3, 21),
        "춘분": (3, 21),
        "autumn": (9, 23),
        "fall": (9, 23),
        "추분": (9, 23),
        "winter": (12, 21),
        "동지": (12, 21),
    }
    month, day = table.get(key, (6, 21))
    return datetime(year, month, day, 12, 0, tzinfo=KST)


def sun_samples(
    lat: float,
    lng: float,
    dt: datetime,
    step_min: float = 10.0,
    tz_offset_hours: float = 9.0,
) -> list[SunPosition]:
    """일출~일몰 step_min 간격 태양 위치. 고도>0만."""
    rise, set_ = sunrise_sunset_minutes(lat, lng, dt, tz_offset_hours)
    local = ensure_kst(dt)
    if rise is None or set_ is None:
        return []
    out: list[SunPosition] = []
    t = rise
    end = set_ if set_ >= rise else set_ + 1440.0
    while t <= end + 1e-6:
        minutes = t % 1440.0
        hour = int(minutes // 60)
        minute = int(minutes % 60)
        second = int(round((minutes - hour * 60 - minute) * 60))
        instant = local.replace(
            hour=hour, minute=minute, second=min(second, 59), microsecond=0
        )
        pos = solar_position(lat, lng, instant, tz_offset_hours)
        if pos.altitude_deg > 0.05:
            out.append(pos)
        t += step_min
    return out
