/** NOAA 태양 위치. 외부 API 없음. 서머타임 미적용(KST=UTC+9). */

export const KST_OFFSET = 9;

export type SunPosition = {
  altitudeDeg: number;
  azimuthDeg: number;
  zenithDeg: number;
  declinationDeg: number;
  equationOfTimeMin: number;
  trueSolarMinutes: number;
  solarNoonMinutes: number;
};

export type DateKind = "summer" | "spring" | "autumn" | "winter" | "now";

const RAD = Math.PI / 180;
const DEG = 180 / Math.PI;
const REFRACT_ZENITH = 90.833;

function julianDay(dt: Date): number {
  const utc = new Date(dt.getTime());
  let year = utc.getUTCFullYear();
  let month = utc.getUTCMonth() + 1;
  const day =
    utc.getUTCDate() +
    (utc.getUTCHours() + utc.getUTCMinutes() / 60 + utc.getUTCSeconds() / 3600) / 24;
  if (month <= 2) {
    year -= 1;
    month += 12;
  }
  const a = Math.floor(year / 100);
  const b = 2 - a + Math.floor(a / 4);
  return Math.floor(365.25 * (year + 4716)) + Math.floor(30.6001 * (month + 1)) + day + b - 1524.5;
}

function julianCentury(jd: number): number {
  return (jd - 2451545) / 36525;
}

function geomMeanLong(t: number): number {
  return (280.46646 + t * (36000.76983 + t * 0.0003032)) % 360;
}

function geomMeanAnom(t: number): number {
  return 357.52911 + t * (35999.05029 - 0.0001537 * t);
}

function eccentricity(t: number): number {
  return 0.016708634 - t * (0.000042037 + 0.0000001267 * t);
}

function sunEqOfCenter(t: number): number {
  const m = geomMeanAnom(t) * RAD;
  return (
    Math.sin(m) * (1.914602 - t * (0.004817 + 0.000014 * t)) +
    Math.sin(2 * m) * (0.019993 - 0.000101 * t) +
    Math.sin(3 * m) * 0.000289
  );
}

function sunAppLong(t: number): number {
  const trueLong = geomMeanLong(t) + sunEqOfCenter(t);
  const omega = 125.04 - 1934.136 * t;
  return trueLong - 0.00569 - 0.00478 * Math.sin(omega * RAD);
}

function meanObliquity(t: number): number {
  const seconds = 21.448 - t * (46.815 + t * (0.00059 - t * 0.001813));
  return 23 + (26 + seconds / 60) / 60;
}

function obliquityCorr(t: number): number {
  const omega = 125.04 - 1934.136 * t;
  return meanObliquity(t) + 0.00256 * Math.cos(omega * RAD);
}

function declination(t: number): number {
  const e = obliquityCorr(t) * RAD;
  const lam = sunAppLong(t) * RAD;
  return Math.asin(Math.sin(e) * Math.sin(lam)) * DEG;
}

function equationOfTime(t: number): number {
  const epsilon = obliquityCorr(t) * RAD;
  const l0 = geomMeanLong(t) * RAD;
  const e = eccentricity(t);
  const m = geomMeanAnom(t) * RAD;
  const y = Math.tan(epsilon / 2) ** 2;
  const et =
    y * Math.sin(2 * l0) -
    2 * e * Math.sin(m) +
    4 * e * y * Math.sin(m) * Math.cos(2 * l0) -
    0.5 * y * y * Math.sin(4 * l0) -
    1.25 * e * e * Math.sin(2 * m);
  return et * 4 * DEG;
}

function kstParts(dt: Date): { y: number; m: number; d: number; hour: number; min: number; sec: number } {
  const local = new Date(dt.getTime() + KST_OFFSET * 3600 * 1000);
  return {
    y: local.getUTCFullYear(),
    m: local.getUTCMonth() + 1,
    d: local.getUTCDate(),
    hour: local.getUTCHours(),
    min: local.getUTCMinutes(),
    sec: local.getUTCSeconds(),
  };
}

export function solarPosition(
  lat: number,
  lng: number,
  dt: Date,
  tzOffsetHours = KST_OFFSET,
): SunPosition {
  const jd = julianDay(dt);
  const t = julianCentury(jd);
  const dec = declination(t);
  const eq = equationOfTime(t);
  const p = kstParts(dt);
  const minutes = p.hour * 60 + p.min + p.sec / 60;
  let trueSolar = (minutes + eq + 4 * lng - 60 * tzOffsetHours) % 1440;
  if (trueSolar < 0) trueSolar += 1440;
  let ha = trueSolar / 4 - 180;
  if (ha < -180) ha += 360;
  const czen = Math.min(
    1,
    Math.max(
      -1,
      Math.sin(lat * RAD) * Math.sin(dec * RAD) +
        Math.cos(lat * RAD) * Math.cos(dec * RAD) * Math.cos(ha * RAD),
    ),
  );
  const zenith = Math.acos(czen) * DEG;
  const azNum = Math.sin(lat * RAD) * Math.cos(zenith * RAD) - Math.sin(dec * RAD);
  const azDen = Math.cos(lat * RAD) * Math.sin(zenith * RAD);
  let az: number;
  if (Math.abs(azDen) < 1e-12) az = lat > 0 ? 180 : 0;
  else {
    const ac = Math.min(1, Math.max(-1, azNum / azDen));
    const azTemp = Math.acos(ac) * DEG;
    az = ha > 0 ? (azTemp + 180) % 360 : (540 - azTemp) % 360;
  }
  let altitude: number;
  if (zenith > 85) altitude = 90 - zenith;
  else {
    const te = Math.tan((90 - zenith) * RAD);
    let corr: number;
    if (zenith > 5) {
      corr = (58.1 / te - 0.07 / te ** 3 + 0.000086 / te ** 5) / 3600;
    } else if (zenith > -0.575) {
      corr =
        (1735 + zenith * (-518.2 + zenith * (103.4 + zenith * (-12.79 + zenith * 0.711)))) /
        3600;
    } else {
      corr = -20.774 / te / 3600;
    }
    altitude = 90 - zenith + corr;
  }
  let noon = (720 - 4 * lng - eq + 60 * tzOffsetHours) % 1440;
  if (noon < 0) noon += 1440;
  return {
    altitudeDeg: altitude,
    azimuthDeg: az,
    zenithDeg: zenith,
    declinationDeg: dec,
    equationOfTimeMin: eq,
    trueSolarMinutes: trueSolar,
    solarNoonMinutes: noon,
  };
}

function haSunrise(lat: number, dec: number): number | null {
  const arg =
    Math.cos(REFRACT_ZENITH * RAD) / (Math.cos(lat * RAD) * Math.cos(dec * RAD)) -
    Math.tan(lat * RAD) * Math.tan(dec * RAD);
  if (arg < -1 || arg > 1) return null;
  return Math.acos(arg) * DEG;
}

export function dateAtKst(
  year: number,
  month: number,
  day: number,
  hour: number,
  minute: number,
): Date {
  return new Date(Date.UTC(year, month - 1, day, hour - KST_OFFSET, minute, 0));
}

export function presetDate(kind: DateKind, now = new Date()): Date {
  const y = now.getFullYear();
  if (kind === "now") return now;
  const md: Record<Exclude<DateKind, "now">, [number, number]> = {
    summer: [6, 21],
    spring: [3, 21],
    autumn: [9, 23],
    winter: [12, 21],
  };
  const [m, d] = md[kind];
  const p = kstParts(now);
  return dateAtKst(y, m, d, p.hour, p.min);
}

export function sunriseSunsetMinutes(
  lat: number,
  lng: number,
  dt: Date,
): { rise: number | null; set: number | null } {
  const noon = dateAtKst(kstParts(dt).y, kstParts(dt).m, kstParts(dt).d, 12, 0);
  const pos = solarPosition(lat, lng, noon);
  const ha = haSunrise(lat, pos.declinationDeg);
  if (ha == null) return { rise: null, set: null };
  return {
    rise: ((pos.solarNoonMinutes - ha * 4) + 1440) % 1440,
    set: ((pos.solarNoonMinutes + ha * 4) + 1440) % 1440,
  };
}

export function dayLengthHours(lat: number, lng: number, dt: Date): number {
  const { rise, set } = sunriseSunsetMinutes(lat, lng, dt);
  if (rise == null || set == null) {
    const noon = solarPosition(lat, lng, dt);
    return noon.altitudeDeg > 0 ? 24 : 0;
  }
  let span = set - rise;
  if (span < 0) span += 1440;
  return span / 60;
}

export function minutesToHhmm(minutes: number): string {
  const m = Math.round(((minutes % 1440) + 1440) % 1440);
  const hh = Math.floor(m / 60)
    .toString()
    .padStart(2, "0");
  const mm = (m % 60).toString().padStart(2, "0");
  return `${hh}:${mm}`;
}

export function trueSolarWindowStd(
  lat: number,
  lng: number,
  dt: Date,
  startHour: number,
  endHour: number,
): { start: number; end: number } {
  const p = kstParts(dt);
  const noon = dateAtKst(p.y, p.m, p.d, 12, 0);
  const pos = solarPosition(lat, lng, noon);
  const offset = pos.solarNoonMinutes - 720;
  return { start: startHour * 60 + offset, end: endHour * 60 + offset };
}

export function applyClock(dt: Date, minutes: number): Date {
  const p = kstParts(dt);
  const m = ((Math.round(minutes) % 1440) + 1440) % 1440;
  return dateAtKst(p.y, p.m, p.d, Math.floor(m / 60), m % 60);
}

export function sunSamples(
  lat: number,
  lng: number,
  dt: Date,
  stepMin = 10,
): SunPosition[] {
  const { rise, set } = sunriseSunsetMinutes(lat, lng, dt);
  if (rise == null || set == null) return [];
  const out: SunPosition[] = [];
  let t = rise;
  const end = set >= rise ? set : set + 1440;
  while (t <= end + 1e-6) {
    const instant = applyClock(dt, t);
    const pos = solarPosition(lat, lng, instant);
    if (pos.altitudeDeg > 0.05) out.push(pos);
    t += stepMin;
  }
  return out;
}

export function dateLabel(kind: DateKind, dt: Date): string {
  const p = kstParts(dt);
  if (kind === "now") return `${p.m}/${p.d}`;
  if (kind === "summer") return "6/21";
  if (kind === "spring") return "3/21";
  if (kind === "autumn") return "9/23";
  return "12/21";
}
