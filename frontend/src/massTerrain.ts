import * as THREE from "three";
import { TessellateModifier } from "three/examples/jsm/modifiers/TessellateModifier.js";

export type TerrainGrid = {
  lats: number[];
  lngs: number[];
  grid: number[][];
  min?: number;
  max?: number;
};

export type MassStyle = {
  base_h_m: number;
  floor: string;
  bg: string;
  road: string;
    green: string;
    water: string;
    water_lift_m: number;
    draw_contours: boolean;
  contour: string;
  contour_m: number;
  terrain_n: number;
  terrain_exag: number;
  explode_gap_ratio: number;
  layer_h_m: number;
  terrain_layer_h_m: number;
  road_lift_m: number;
  green_lift_m: number;
  green_depth_m: number;
  road_width_m: Record<string, number>;
  road_skip: string[];
};

const FALLBACK_WIDTHS: Record<string, number> = {
  motorway: 28,
  motorway_link: 22,
  trunk: 22,
  trunk_link: 18,
  primary: 16,
  primary_link: 14,
  busway: 14,
  secondary: 10,
  secondary_link: 8,
  tertiary: 6.5,
  tertiary_link: 5.5,
  residential: 4,
  unclassified: 4,
  living_street: 3.5,
  service: 2.4,
  pedestrian: 2.4,
};

const FALLBACK_SKIP = [
  "footway",
  "path",
  "steps",
  "cycleway",
  "corridor",
  "elevator",
  "platform",
  "construction",
  "raceway",
  "services",
  "track",
  "proposed",
  "abandoned",
];

export const DEFAULT_MASS_STYLE: MassStyle = {
  base_h_m: 180,
  floor: "#E7E9EC",
  bg: "#ffffff",
  road: "#c4c4c4",
  green: "#c3e6a4",
  water: "#8ecae6",
  water_lift_m: 0.16,
  draw_contours: false,
  contour: "#9e9e9e",
  contour_m: 10,
  terrain_n: 72,
  terrain_exag: 2.0,
  explode_gap_ratio: 0.34,
  layer_h_m: 10,
  terrain_layer_h_m: 28,
  road_lift_m: 0.45,
  green_lift_m: 0.08,
  green_depth_m: 0.14,
  road_width_m: FALLBACK_WIDTHS,
  road_skip: FALLBACK_SKIP,
};

export function mergeStyle(raw: unknown): MassStyle {
  const src = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
  const widths =
    src.road_width_m && typeof src.road_width_m === "object"
      ? (src.road_width_m as Record<string, number>)
      : FALLBACK_WIDTHS;
  const skip = Array.isArray(src.road_skip)
    ? (src.road_skip as string[])
    : FALLBACK_SKIP;
  return {
    base_h_m: Number(src.base_h_m) > 0 ? Number(src.base_h_m) : DEFAULT_MASS_STYLE.base_h_m,
    floor: String(src.floor || DEFAULT_MASS_STYLE.floor),
    bg: String(src.bg || DEFAULT_MASS_STYLE.bg),
    road: String(src.road || DEFAULT_MASS_STYLE.road),
    green: String(src.green || DEFAULT_MASS_STYLE.green),
    water: String(src.water || DEFAULT_MASS_STYLE.water),
    water_lift_m:
      Number(src.water_lift_m) >= 0 ? Number(src.water_lift_m) : DEFAULT_MASS_STYLE.water_lift_m,
    draw_contours: src.draw_contours === true,
    contour: String(src.contour || DEFAULT_MASS_STYLE.contour),
    contour_m: Number(src.contour_m) > 0 ? Number(src.contour_m) : 10,
    terrain_n: Number(src.terrain_n) >= 16 ? Math.min(96, Number(src.terrain_n)) : 72,
    terrain_exag:
      Number(src.terrain_exag) > 0 ? Number(src.terrain_exag) : DEFAULT_MASS_STYLE.terrain_exag,
    explode_gap_ratio:
      Number(src.explode_gap_ratio) > 0
        ? Number(src.explode_gap_ratio)
        : DEFAULT_MASS_STYLE.explode_gap_ratio,
    layer_h_m:
      Number(src.layer_h_m) > 0 ? Number(src.layer_h_m) : DEFAULT_MASS_STYLE.layer_h_m,
    terrain_layer_h_m:
      Number(src.terrain_layer_h_m) > 0
        ? Number(src.terrain_layer_h_m)
        : DEFAULT_MASS_STYLE.terrain_layer_h_m,
    road_lift_m:
      Number(src.road_lift_m) > 0 ? Number(src.road_lift_m) : DEFAULT_MASS_STYLE.road_lift_m,
    green_lift_m:
      Number(src.green_lift_m) >= 0 ? Number(src.green_lift_m) : DEFAULT_MASS_STYLE.green_lift_m,
    green_depth_m:
      Number(src.green_depth_m) > 0 ? Number(src.green_depth_m) : DEFAULT_MASS_STYLE.green_depth_m,
    road_width_m: widths,
    road_skip: skip,
  };
}

export function lonLatToXZ(
  lng: number,
  lat: number,
  origin: [number, number],
): { x: number; z: number } {
  const [olng, olat] = origin;
  const x = (lng - olng) * 111320 * Math.cos((olat * Math.PI) / 180);
  const z = -((lat - olat) * 111320);
  return { x, z };
}

export function xzToLonLat(
  x: number,
  z: number,
  origin: [number, number],
): { lng: number; lat: number } {
  const [olng, olat] = origin;
  const lng = olng + x / (111320 * Math.cos((olat * Math.PI) / 180));
  const lat = olat - z / 111320;
  return { lng, lat };
}

export function insideClip(
  x: number,
  z: number,
  shape: "circle" | "rect",
  radiusM: number,
): boolean {
  if (shape === "circle") return x * x + z * z <= radiusM * radiusM + 0.8;
  return Math.abs(x) <= radiusM + 0.8 && Math.abs(z) <= radiusM + 0.8;
}

function bisectRight(arr: number[], value: number): number {
  let lo = 0;
  let hi = arr.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (value < arr[mid]) hi = mid;
    else lo = mid + 1;
  }
  return lo;
}

export function sampleElev(terrain: TerrainGrid, lat: number, lng: number): number | null {
  const { lats, lngs, grid } = terrain;
  if (lats.length < 2 || lngs.length < 2) return null;
  if (lat < lats[0] || lat > lats[lats.length - 1] || lng < lngs[0] || lng > lngs[lngs.length - 1]) {
    return null;
  }
  const i = Math.min(lats.length - 2, Math.max(0, bisectRight(lats, lat) - 1));
  const j = Math.min(lngs.length - 2, Math.max(0, bisectRight(lngs, lng) - 1));
  const lat0 = lats[i];
  const lat1 = lats[i + 1];
  const lng0 = lngs[j];
  const lng1 = lngs[j + 1];
  const ty = lat1 === lat0 ? 0 : (lat - lat0) / (lat1 - lat0);
  const tx = lng1 === lng0 ? 0 : (lng - lng0) / (lng1 - lng0);
  const row0 = grid[i];
  const row1 = grid[i + 1];
  if (!row0 || !row1) return null;
  const h00 = Number(row0[j]);
  const h10 = Number(row0[j + 1]);
  const h01 = Number(row1[j]);
  const h11 = Number(row1[j + 1]);
  if (![h00, h10, h01, h11].every(Number.isFinite)) return null;
  return (
    h00 * (1 - tx) * (1 - ty) + h10 * tx * (1 - ty) + h01 * (1 - tx) * ty + h11 * tx * ty
  );
}

export function makeGroundSampler(
  terrain: TerrainGrid | null | undefined,
  origin: [number, number],
  radiusM: number,
  shape: "circle" | "rect",
  displace: boolean,
  exag = 1,
): { yAt: (x: number, z: number) => number; vmin: number; vmax: number } {
  if (!displace || !terrain?.lats?.length || !terrain.lngs?.length || !terrain.grid?.length) {
    return { yAt: () => 0, vmin: 0, vmax: 0 };
  }
  const n = 40;
  let vmin = Infinity;
  let vmax = -Infinity;
  for (let i = 0; i <= n; i++) {
    for (let j = 0; j <= n; j++) {
      const x = -radiusM + (2 * radiusM * j) / n;
      const z = -radiusM + (2 * radiusM * i) / n;
      if (!insideClip(x, z, shape, radiusM)) continue;
      const { lng, lat } = xzToLonLat(x, z, origin);
      const elev = sampleElev(terrain, lat, lng);
      if (elev == null) continue;
      vmin = Math.min(vmin, elev);
      vmax = Math.max(vmax, elev);
    }
  }
  if (!Number.isFinite(vmin)) {
    vmin = Number(terrain.min) || 0;
    vmax = Number(terrain.max) || vmin;
  }
  const scale = exag > 0 ? exag : 1;
  return {
    vmin,
    vmax,
    yAt: (x: number, z: number) => {
      const { lng, lat } = xzToLonLat(x, z, origin);
      const elev = sampleElev(terrain, lat, lng);
      return ((elev ?? vmin) - vmin) * scale;
    },
  };
}

export function drapeGeometry(
  geo: THREE.BufferGeometry,
  yAt: (x: number, z: number) => number,
  lift: number,
): void {
  const pos = geo.attributes.position;
  for (let i = 0; i < pos.count; i++) {
    const x = pos.getX(i);
    const z = pos.getZ(i);
    const thickness = pos.getY(i);
    pos.setY(i, yAt(x, z) + lift + thickness);
  }
  pos.needsUpdate = true;
  geo.computeVertexNormals();
}

/** 큰 삼각형이 언덕을 뚫지 않게 변을 나눈 뒤, 각 꼭짓점을 지형 위에 붙인다. */
export function tessellateThenDrape(
  geo: THREE.BufferGeometry,
  yAt: (x: number, z: number) => number,
  lift: number,
  maxEdgeM = 16,
): THREE.BufferGeometry {
  const tess = new TessellateModifier(maxEdgeM, 8);
  const dense = tess.modify(geo);
  if (dense !== geo) geo.dispose();
  drapeGeometry(dense, yAt, lift);
  return dense;
}

export type LandusePatch = {
  ring: number[][];
  color: string;
};

type PreparedPatch = {
  xz: { x: number; z: number }[];
  minX: number;
  maxX: number;
  minZ: number;
  maxZ: number;
  rgb: [number, number, number];
};

function hexToRgb01(hex: string): [number, number, number] {
  const h = hex.replace("#", "");
  const full = h.length === 3 ? h.split("").map((c) => c + c).join("") : h;
  return [
    parseInt(full.slice(0, 2), 16) / 255,
    parseInt(full.slice(2, 4), 16) / 255,
    parseInt(full.slice(4, 6), 16) / 255,
  ];
}

function pointInRing(px: number, pz: number, ring: { x: number; z: number }[]): boolean {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const xi = ring[i].x;
    const zi = ring[i].z;
    const xj = ring[j].x;
    const zj = ring[j].z;
    const denom = zj - zi;
    const intersect =
      zi > pz !== zj > pz && px < ((xj - xi) * (pz - zi)) / (denom === 0 ? 1e-12 : denom) + xi;
    if (intersect) inside = !inside;
  }
  return inside;
}

export function prepareLandusePatches(
  patches: LandusePatch[],
  origin: [number, number],
): PreparedPatch[] {
  const out: PreparedPatch[] = [];
  for (const patch of patches) {
    if (!patch.ring || patch.ring.length < 3) continue;
    const xz = patch.ring.map((c) => lonLatToXZ(c[0], c[1], origin));
    let minX = Infinity;
    let maxX = -Infinity;
    let minZ = Infinity;
    let maxZ = -Infinity;
    for (const p of xz) {
      minX = Math.min(minX, p.x);
      maxX = Math.max(maxX, p.x);
      minZ = Math.min(minZ, p.z);
      maxZ = Math.max(maxZ, p.z);
    }
    out.push({ xz, minX, maxX, minZ, maxZ, rgb: hexToRgb01(patch.color) });
  }
  return out;
}

export function makeLanduseColorAt(
  patches: PreparedPatch[],
  fallbackHex: string,
): (x: number, z: number) => [number, number, number] {
  const fallback = hexToRgb01(fallbackHex);
  return (x: number, z: number) => {
    let rgb = fallback;
    for (const patch of patches) {
      if (x < patch.minX || x > patch.maxX || z < patch.minZ || z > patch.maxZ) continue;
      if (pointInRing(x, z, patch.xz)) rgb = patch.rgb;
    }
    return rgb;
  };
}

export function buildTerrainSurface(
  radiusM: number,
  shape: "circle" | "rect",
  n: number,
  yAt: (x: number, z: number) => number,
  color: string,
  colorAt?: (x: number, z: number) => [number, number, number],
): THREE.Mesh {
  const geo = new THREE.PlaneGeometry(radiusM * 2, radiusM * 2, n, n);
  geo.rotateX(-Math.PI / 2);
  const pos = geo.attributes.position;
  const colors = colorAt ? new Float32Array(pos.count * 3) : null;
  for (let i = 0; i < pos.count; i++) {
    let x = pos.getX(i);
    let z = pos.getZ(i);
    if (shape === "circle") {
      const dist = Math.hypot(x, z);
      if (dist > radiusM && dist > 1e-6) {
        x = (x / dist) * radiusM;
        z = (z / dist) * radiusM;
      }
    }
    pos.setXYZ(i, x, yAt(x, z), z);
    if (colors && colorAt) {
      const [r, g, b] = colorAt(x, z);
      colors[i * 3] = r;
      colors[i * 3 + 1] = g;
      colors[i * 3 + 2] = b;
    }
  }
  pos.needsUpdate = true;
  geo.computeVertexNormals();
  if (colors) {
    geo.setAttribute("color", new THREE.BufferAttribute(colors, 3));
  }
  const mat = new THREE.MeshBasicMaterial({
    color: colors ? "#ffffff" : color,
    vertexColors: Boolean(colors),
    side: THREE.DoubleSide,
    polygonOffset: true,
    polygonOffsetFactor: 4,
    polygonOffsetUnits: 4,
    depthWrite: true,
  });
  const mesh = new THREE.Mesh(geo, mat);
  mesh.renderOrder = 0;
  return mesh;
}

export function buildCakeBase(
  radiusM: number,
  shape: "circle" | "rect",
  baseH: number,
  yAt: (x: number, z: number) => number,
  color: string,
  sideColor = "#cfcfcf",
): THREE.Group {
  const group = new THREE.Group();
  const topMat = new THREE.MeshBasicMaterial({ color, side: THREE.DoubleSide });
  const sideMat = new THREE.MeshLambertMaterial({
    color: sideColor,
    side: THREE.DoubleSide,
  });
  const yBottom = -baseH;

  const bottom =
    shape === "circle"
      ? new THREE.CircleGeometry(radiusM, 96)
      : new THREE.PlaneGeometry(radiusM * 2, radiusM * 2);
  bottom.rotateX(Math.PI / 2);
  const bottomMesh = new THREE.Mesh(bottom, topMat);
  bottomMesh.position.y = yBottom;
  group.add(bottomMesh);

  const rim: { x: number; z: number }[] = [];
  if (shape === "circle") {
    const segs = 96;
    for (let i = 0; i < segs; i++) {
      const a = (i / segs) * Math.PI * 2;
      rim.push({ x: Math.cos(a) * radiusM, z: Math.sin(a) * radiusM });
    }
  } else {
    const s = radiusM;
    rim.push({ x: -s, z: -s }, { x: s, z: -s }, { x: s, z: s }, { x: -s, z: s });
  }

  const positions: number[] = [];
  const normals: number[] = [];
  const rimBottom: THREE.Vector3[] = [];
  for (let i = 0; i < rim.length; i++) {
    const a = rim[i];
    const b = rim[(i + 1) % rim.length];
    const ya = yAt(a.x, a.z);
    const yb = yAt(b.x, b.z);
    positions.push(
      a.x, yBottom, a.z,
      b.x, yBottom, b.z,
      b.x, yb, b.z,
      a.x, yBottom, a.z,
      b.x, yb, b.z,
      a.x, ya, a.z,
    );
    const nx = b.z - a.z;
    const nz = a.x - b.x;
    const len = Math.hypot(nx, nz) || 1;
    const nnx = nx / len;
    const nnz = nz / len;
    for (let k = 0; k < 6; k++) normals.push(nnx, 0, nnz);
    rimBottom.push(new THREE.Vector3(a.x, yBottom, a.z));
  }
  rimBottom.push(rimBottom[0].clone());
  const sideGeo = new THREE.BufferGeometry();
  sideGeo.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  sideGeo.setAttribute("normal", new THREE.Float32BufferAttribute(normals, 3));
  group.add(new THREE.Mesh(sideGeo, sideMat));
  group.add(
    new THREE.Line(
      new THREE.BufferGeometry().setFromPoints(rimBottom),
      new THREE.LineBasicMaterial({ color: "#c0c0c0" }),
    ),
  );
  return group;
}

export function buildThinPlate(
  radiusM: number,
  shape: "circle" | "rect",
  thickness: number,
  color: string,
  sideColor = "#e4e4e4",
): THREE.Group {
  const group = new THREE.Group();
  const h = Math.max(3, thickness);
  const topMat = new THREE.MeshBasicMaterial({ color, side: THREE.DoubleSide });
  const wallMat = new THREE.MeshLambertMaterial({
    color: sideColor,
    side: THREE.DoubleSide,
  });
  if (shape === "circle") {
    const mesh = new THREE.Mesh(new THREE.CylinderGeometry(radiusM, radiusM, h, 96), [
      wallMat,
      topMat,
      topMat,
    ]);
    mesh.position.y = -h / 2;
    group.add(mesh);
  } else {
    const mesh = new THREE.Mesh(
      new THREE.BoxGeometry(radiusM * 2, h, radiusM * 2),
      wallMat,
    );
    mesh.position.y = -h / 2;
    group.add(mesh);
    const cap = new THREE.Mesh(
      new THREE.PlaneGeometry(radiusM * 2, radiusM * 2),
      topMat,
    );
    cap.rotateX(-Math.PI / 2);
    group.add(cap);
  }
  return group;
}

export function buildExplodeGuides(
  radiusM: number,
  shape: "circle" | "rect",
  yBottom: number,
  yTop: number,
): THREE.Group {
  const group = new THREE.Group();
  const mat = new THREE.LineDashedMaterial({
    color: "#9a9a9a",
    dashSize: 22,
    gapSize: 14,
    transparent: true,
    opacity: 0.75,
  });
  const pts: { x: number; z: number }[] = [];
  if (shape === "circle") {
    for (let i = 0; i < 4; i++) {
      const a = Math.PI / 4 + (i * Math.PI) / 2;
      pts.push({ x: Math.cos(a) * radiusM, z: Math.sin(a) * radiusM });
    }
  } else {
    const s = radiusM;
    pts.push({ x: -s, z: -s }, { x: s, z: -s }, { x: s, z: s }, { x: -s, z: s });
  }
  for (const p of pts) {
    const geo = new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(p.x, yBottom, p.z),
      new THREE.Vector3(p.x, yTop, p.z),
    ]);
    const line = new THREE.Line(geo, mat);
    line.computeLineDistances();
    group.add(line);
  }
  return group;
}

// bit0=SW, bit1=SE, bit2=NE, bit3=NW. edges: 0=bottom 1=right 2=top 3=left
const MS_CASES: Record<number, [number, number][]> = {
  1: [[0, 3]],
  2: [[0, 1]],
  3: [[3, 1]],
  4: [[1, 2]],
  5: [
    [0, 1],
    [3, 2],
  ],
  6: [[0, 2]],
  7: [[3, 2]],
  8: [[3, 2]],
  9: [[0, 2]],
  10: [
    [0, 3],
    [1, 2],
  ],
  11: [[1, 2]],
  12: [[3, 1]],
  13: [[0, 1]],
  14: [[0, 3]],
};

function edgePoint(
  edge: number,
  xs: number[],
  zs: number[],
  i: number,
  j: number,
  h: number[],
  level: number,
): [number, number] {
  const t = (a: number, b: number) => (a === b ? 0.5 : (level - a) / (b - a));
  if (edge === 0) {
    const u = t(h[0], h[1]);
    return [xs[j] + u * (xs[j + 1] - xs[j]), zs[i]];
  }
  if (edge === 1) {
    const u = t(h[1], h[2]);
    return [xs[j + 1], zs[i] + u * (zs[i + 1] - zs[i])];
  }
  if (edge === 2) {
    const u = t(h[3], h[2]);
    return [xs[j] + u * (xs[j + 1] - xs[j]), zs[i + 1]];
  }
  const u = t(h[0], h[3]);
  return [xs[j], zs[i] + u * (zs[i + 1] - zs[i])];
}

function marchingSquares(
  xs: number[],
  zs: number[],
  zz: (number | null)[][],
  level: number,
): [[number, number], [number, number]][] {
  const segs: [[number, number], [number, number]][] = [];
  for (let i = 0; i < zs.length - 1; i++) {
    for (let j = 0; j < xs.length - 1; j++) {
      const raw = [zz[i][j], zz[i][j + 1], zz[i + 1][j + 1], zz[i + 1][j]];
      if (raw.some((v) => v == null || !Number.isFinite(v))) continue;
      const h = raw as number[];
      let idx = 0;
      for (let bit = 0; bit < 4; bit++) {
        if (h[bit] >= level) idx |= 1 << bit;
      }
      for (const [e0, e1] of MS_CASES[idx] || []) {
        const p0 = edgePoint(e0, xs, zs, i, j, h, level);
        const p1 = edgePoint(e1, xs, zs, i, j, h, level);
        if (p0[0] !== p1[0] || p0[1] !== p1[1]) segs.push([p0, p1]);
      }
    }
  }
  return segs;
}

export function buildContourLines(
  terrain: TerrainGrid,
  origin: [number, number],
  radiusM: number,
  shape: "circle" | "rect",
  interval: number,
  yAt: (x: number, z: number) => number,
  color: string,
  n: number,
  colorAt?: (x: number, z: number) => [number, number, number],
): THREE.LineSegments | null {
  const xs: number[] = [];
  const zs: number[] = [];
  for (let i = 0; i <= n; i++) {
    xs.push(-radiusM + (2 * radiusM * i) / n);
    zs.push(-radiusM + (2 * radiusM * i) / n);
  }
  const zz: (number | null)[][] = [];
  let vmin = Infinity;
  let vmax = -Infinity;
  for (let i = 0; i <= n; i++) {
    const z = zs[i];
    const row: (number | null)[] = [];
    for (let j = 0; j <= n; j++) {
      const x = xs[j];
      if (!insideClip(x, z, shape, radiusM)) {
        row.push(null);
        continue;
      }
      const { lng, lat } = xzToLonLat(x, z, origin);
      const elev = sampleElev(terrain, lat, lng);
      row.push(elev);
      if (elev != null) {
        vmin = Math.min(vmin, elev);
        vmax = Math.max(vmax, elev);
      }
    }
    zz.push(row);
  }
  if (!Number.isFinite(vmin) || vmax - vmin < 1) return null;

  const pts: number[] = [];
  const cols: number[] = [];
  const start = Math.ceil(vmin / interval) * interval;
  const shade = 0.58;
  for (let level = start; level <= vmax + 1e-6; level += interval) {
    for (const [[x0, z0], [x1, z1]] of marchingSquares(xs, zs, zz, level)) {
      if (!insideClip(x0, z0, shape, radiusM) && !insideClip(x1, z1, shape, radiusM)) {
        continue;
      }
      pts.push(x0, yAt(x0, z0) + 0.55, z0, x1, yAt(x1, z1) + 0.55, z1);
      if (colorAt) {
        const a = colorAt(x0, z0);
        const b = colorAt(x1, z1);
        cols.push(a[0] * shade, a[1] * shade, a[2] * shade, b[0] * shade, b[1] * shade, b[2] * shade);
      }
    }
  }
  if (pts.length < 6) return null;
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
  if (cols.length) {
    geo.setAttribute("color", new THREE.Float32BufferAttribute(cols, 3));
    return new THREE.LineSegments(
      geo,
      new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.92 }),
    );
  }
  return new THREE.LineSegments(
    geo,
    new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.95 }),
  );
}

export function roadHalfWidth(roadClass: unknown, style: MassStyle): number | null {
  const key = String(roadClass || "").trim();
  if (!key || style.road_skip.includes(key)) return null;
  const widthM = Number(style.road_width_m[key]);
  if (!Number.isFinite(widthM) || widthM <= 0) return null;
  return Math.max(1.3, (widthM / 2) * 1.45);
}

export function buildRoadRibbon(
  pts: THREE.Vector3[],
  halfWidth: number,
  color: string,
  material?: THREE.Material,
): THREE.Object3D | null {
  if (pts.length < 2) return null;
  const cleaned: THREE.Vector3[] = [pts[0]];
  for (let i = 1; i < pts.length; i++) {
    if (pts[i].distanceTo(cleaned[cleaned.length - 1]) > 0.35) cleaned.push(pts[i]);
  }
  if (cleaned.length < 2) return null;
  const n = cleaned.length;
  const positions: number[] = [];
  for (let i = 0; i < n; i++) {
    const curr = cleaned[i];
    let dx: number;
    let dz: number;
    if (i === 0) {
      dx = cleaned[1].x - curr.x;
      dz = cleaned[1].z - curr.z;
    } else if (i === n - 1) {
      dx = curr.x - cleaned[n - 2].x;
      dz = curr.z - cleaned[n - 2].z;
    } else {
      dx = cleaned[i + 1].x - cleaned[i - 1].x;
      dz = cleaned[i + 1].z - cleaned[i - 1].z;
    }
    const len = Math.hypot(dx, dz) || 1;
    const px = (-dz / len) * halfWidth;
    const pz = (dx / len) * halfWidth;
    positions.push(curr.x + px, curr.y, curr.z + pz, curr.x - px, curr.y, curr.z - pz);
  }
  const indices: number[] = [];
  for (let i = 0; i < n - 1; i++) {
    const a = i * 2;
    indices.push(a, a + 1, a + 2, a + 1, a + 3, a + 2);
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  geo.setIndex(indices);
  geo.computeVertexNormals();
  const mat =
    material ??
    new THREE.MeshBasicMaterial({ color, side: THREE.DoubleSide, depthWrite: true });
  return new THREE.Mesh(geo, mat);
}

export function buildSitePin(y: number): THREE.Group {
  const group = new THREE.Group();
  const red = new THREE.MeshStandardMaterial({ color: "#d61f26", roughness: 0.45 });
  const stem = new THREE.Mesh(new THREE.CylinderGeometry(0.9, 0.9, 26, 10), red);
  stem.position.y = y + 13;
  const head = new THREE.Mesh(new THREE.SphereGeometry(5.2, 16, 12), red);
  head.position.y = y + 28;
  group.add(stem, head);
  return group;
}

export function ringCentroidXZ(
  ring: number[][],
  origin: [number, number],
): { x: number; z: number } {
  let sx = 0;
  let sz = 0;
  let n = 0;
  for (const c of ring) {
    const p = lonLatToXZ(c[0], c[1], origin);
    sx += p.x;
    sz += p.z;
    n += 1;
  }
  if (!n) return { x: 0, z: 0 };
  return { x: sx / n, z: sz / n };
}
