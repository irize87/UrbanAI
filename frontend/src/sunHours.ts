import {
  dayLengthHours,
  sunSamples,
  trueSolarWindowStd,
  type SunPosition,
} from "./solar";

export type HeightBuilding = {
  rings: number[][][][];
  height: number;
};

export type SunHoursResult = {
  n: number;
  cellM: number;
  hours: Float32Array;
  height: Float32Array;
  consec2: Float32Array;
  total4: Float32Array;
  stats: {
    dayLengthH: number;
    meanH: number;
    below1hPct: number;
    consec2hPct: number;
    total4hPct: number;
    std09: number;
    std15: number;
  };
};

const RAMP_HOURS = [0, 1, 2, 3, 4, 5, 6, 8, 10, 12];
const RAMP = [
  "#243868",
  "#2f4d86",
  "#3d6aa8",
  "#5b86c0",
  "#7a9cc4",
  "#c4bc78",
  "#e0b844",
  "#e8c14a",
  "#f0cc4e",
  "#f3d45a",
];

function lerpHex(a: string, b: string, t: number): [number, number, number] {
  const pa = [parseInt(a.slice(1, 3), 16), parseInt(a.slice(3, 5), 16), parseInt(a.slice(5, 7), 16)];
  const pb = [parseInt(b.slice(1, 3), 16), parseInt(b.slice(3, 5), 16), parseInt(b.slice(5, 7), 16)];
  return [
    Math.round(pa[0] + (pb[0] - pa[0]) * t),
    Math.round(pa[1] + (pb[1] - pa[1]) * t),
    Math.round(pa[2] + (pb[2] - pa[2]) * t),
  ];
}

export function hoursToRgb(hours: number, vmax = 12): [number, number, number] {
  const cap = vmax > 0.2 ? vmax : 12;
  const scaled = hours * (12 / cap);
  if (scaled <= RAMP_HOURS[0]) return lerpHex(RAMP[0], RAMP[0], 0);
  if (scaled >= RAMP_HOURS[RAMP_HOURS.length - 1]) {
    const last = RAMP[RAMP.length - 1];
    return lerpHex(last, last, 0);
  }
  for (let i = 0; i < RAMP_HOURS.length - 1; i++) {
    if (scaled >= RAMP_HOURS[i] && scaled <= RAMP_HOURS[i + 1]) {
      const span = RAMP_HOURS[i + 1] - RAMP_HOURS[i];
      const u = span <= 0 ? 0 : (scaled - RAMP_HOURS[i]) / span;
      return lerpHex(RAMP[i], RAMP[i + 1], u);
    }
  }
  return lerpHex(RAMP[0], RAMP[0], 0);
}

function lonLatToM(
  lng: number,
  lat: number,
  olng: number,
  olat: number,
): { x: number; y: number } {
  const x = (lng - olng) * 111320 * Math.cos((olat * Math.PI) / 180);
  const y = (lat - olat) * 111320;
  return { x, y };
}

function pointInRing(x: number, y: number, ring: { x: number; y: number }[]): boolean {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const xi = ring[i].x;
    const yi = ring[i].y;
    const xj = ring[j].x;
    const yj = ring[j].y;
    const hit = yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi + 1e-12) + xi;
    if (hit) inside = !inside;
  }
  return inside;
}

function rasterize(
  buildings: HeightBuilding[],
  lng: number,
  lat: number,
  radiusM: number,
  n: number,
): { height: Float32Array; cell: number } {
  const cell = (radiusM * 2) / n;
  const height = new Float32Array(n * n);
  const half = radiusM;
  for (const b of buildings) {
    if (!b.height || b.height <= 0.2) continue;
    for (const poly of b.rings) {
      if (!poly[0] || poly[0].length < 3) continue;
      const ring = poly[0].map(([plng, plat]) => lonLatToM(plng, plat, lng, lat));
      let minx = Infinity;
      let miny = Infinity;
      let maxx = -Infinity;
      let maxy = -Infinity;
      for (const p of ring) {
        minx = Math.min(minx, p.x);
        maxx = Math.max(maxx, p.x);
        miny = Math.min(miny, p.y);
        maxy = Math.max(maxy, p.y);
      }
      const j0 = Math.max(0, Math.floor((minx + half) / cell) - 1);
      const j1 = Math.min(n, Math.ceil((maxx + half) / cell) + 1);
      const i0 = Math.max(0, Math.floor((half - maxy) / cell) - 1);
      const i1 = Math.min(n, Math.ceil((half - miny) / cell) + 1);
      for (let i = i0; i < i1; i++) {
        const y = half - (i + 0.5) * cell;
        for (let j = j0; j < j1; j++) {
          const x = -half + (j + 0.5) * cell;
          if (pointInRing(x, y, ring)) {
            const idx = i * n + j;
            if (b.height > height[idx]) height[idx] = b.height;
          }
        }
      }
    }
  }
  return { height, cell };
}

function shadowLit(
  height: Float32Array,
  n: number,
  cellM: number,
  altDeg: number,
  azDeg: number,
): Uint8Array {
  const lit = new Uint8Array(n * n);
  lit.fill(1);
  if (altDeg <= 0.12) {
    lit.fill(0);
    return lit;
  }
  const tanA = Math.tan((altDeg * Math.PI) / 180);
  const sdi = Math.cos((azDeg * Math.PI) / 180);
  const sdj = -Math.sin((azDeg * Math.PI) / 180);
  const length = Math.hypot(sdi, sdj) || 1;
  const stepI = sdi / length;
  const stepJ = sdj / length;
  const starts: [number, number][] = [];
  for (let k = 0; k < n; k++) {
    if (sdi >= 0) starts.push([-0.5, k + 0.5]);
    if (sdi <= 0) starts.push([n - 0.5, k + 0.5]);
    if (sdj >= 0) starts.push([k + 0.5, -0.5]);
    if (sdj <= 0) starts.push([k + 0.5, n - 0.5]);
  }
  const seen = new Uint8Array(n * n);
  for (const [si, sj] of starts) {
    let ray = 0;
    let i = si;
    let j = sj;
    for (let g = 0; g < n * 3; g++) {
      i += stepI;
      j += stepJ;
      const ii = Math.floor(i);
      const jj = Math.floor(j);
      if (ii < 0 || jj < 0 || ii >= n || jj >= n) {
        if (ii < -1 || jj < -1 || ii > n || jj > n) break;
        ray = Math.max(0, ray - cellM * tanA);
        continue;
      }
      ray = Math.max(0, ray - cellM * tanA);
      const idx = ii * n + jj;
      if (!seen[idx]) {
        lit[idx] = ray <= 0.04 ? 1 : 0;
        seen[idx] = 1;
      }
      const h = height[idx];
      if (h > ray) ray = h;
    }
  }
  for (let k = 0; k < n * n; k++) {
    if (height[k] > 0.2) lit[k] = 0;
  }
  return lit;
}

export function computeSunHoursGrid(input: {
  buildings: HeightBuilding[];
  lat: number;
  lng: number;
  radiusM: number;
  date: Date | string;
  n?: number;
  stepMin?: number;
}): SunHoursResult {
  const n = Math.max(24, Math.min(96, input.n ?? 64));
  const stepMin = input.stepMin ?? 10;
  const dt = input.date instanceof Date ? input.date : new Date(input.date);
  const { height, cell } = rasterize(input.buildings, input.lng, input.lat, input.radiusM, n);
  const samples: SunPosition[] = sunSamples(input.lat, input.lng, dt, stepMin);
  const dtH = stepMin / 60;
  const hours = new Float32Array(n * n);
  const run = new Float32Array(n * n);
  const best = new Float32Array(n * n);
  const total4 = new Float32Array(n * n);
  for (const pos of samples) {
    const lit = shadowLit(height, n, cell, pos.altitudeDeg, pos.azimuthDeg);
    const tst = pos.trueSolarMinutes;
    const in2 = tst >= 9 * 60 && tst <= 15 * 60;
    const in4 = tst >= 8 * 60 && tst <= 16 * 60;
    for (let k = 0; k < n * n; k++) {
      if (lit[k]) hours[k] += dtH;
      if (in2) {
        if (lit[k]) run[k] += dtH;
        else run[k] = 0;
        if (run[k] > best[k]) best[k] = run[k];
      } else {
        run[k] = 0;
      }
      if (in4 && lit[k]) total4[k] += dtH;
    }
  }
  let sum = 0;
  let ground = 0;
  let below1 = 0;
  let c2 = 0;
  let t4 = 0;
  for (let k = 0; k < n * n; k++) {
    if (height[k] > 0.2) {
      hours[k] = 0;
      best[k] = 0;
      total4[k] = 0;
      continue;
    }
    ground += 1;
    sum += hours[k];
    if (hours[k] < 1) below1 += 1;
    if (best[k] >= 2) c2 += 1;
    if (total4[k] >= 4) t4 += 1;
  }
  const win = trueSolarWindowStd(input.lat, input.lng, dt, 9, 15);
  return {
    n,
    cellM: cell,
    hours,
    height,
    consec2: best,
    total4,
    stats: {
      dayLengthH: Math.round(dayLengthHours(input.lat, input.lng, dt) * 100) / 100,
      meanH: ground ? Math.round((sum / ground) * 100) / 100 : 0,
      below1hPct: ground ? Math.round((below1 / ground) * 1000) / 10 : 0,
      consec2hPct: ground ? Math.round((c2 / ground) * 1000) / 10 : 0,
      total4hPct: ground ? Math.round((t4 / ground) * 1000) / 10 : 0,
      std09: win.start,
      std15: win.end,
    },
  };
}
