import * as THREE from "three";
import { insideClip, lonLatToXZ, sampleElev, xzToLonLat, type TerrainGrid } from "./massTerrain";

export type FloodCell = { x: number; z: number; elev: number };

export type FloodPlan = {
  rainfallMm: number;
  cellM: number;
  minElev: number;
  surfaceElev: number;
  exag: number;
  areaM2: number;
  cells: FloodCell[];
  gridN: number;
  x0: number;
  z0: number;
  elevs: Float32Array;
};

export type FloodJob = {
  token: number;
  started: number;
  durationMs: number;
  plan: FloodPlan;
};

export type GrowthJob = {
  token: number;
  startYear: number;
  endYear: number;
  playYear: number;
  playing: boolean;
  lastTick: number;
  yearPerMs: number;
};

type RiseMesh = THREE.Mesh & { userData: { year?: number; riseEdge?: THREE.Object3D } };

function pointInRing(x: number, z: number, ring: Array<[number, number]>): boolean {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const xi = ring[i][0];
    const zi = ring[i][1];
    const xj = ring[j][0];
    const zj = ring[j][1];
    const hit = zi > z !== zj > z && x < ((xj - xi) * (z - zi)) / (zj - zi + 0.0000001) + xi;
    if (hit) inside = !inside;
  }
  return inside;
}

function volumeBelow(cells: FloodCell[], surface: number, cellArea: number): number {
  let volume = 0;
  for (const cell of cells) {
    if (cell.elev < surface) volume += (surface - cell.elev) * cellArea;
  }
  return volume;
}

export function planFlood(opts: {
  terrain: TerrainGrid | null | undefined;
  origin: [number, number];
  radiusM: number;
  shape: "circle" | "rect";
  rainfallMm: number;
  exag: number;
  boxLl: number[][] | null;
}): FloodPlan | null {
  const terrain = opts.terrain;
  if (!terrain?.grid?.length || opts.rainfallMm <= 0) return null;
  const ring =
    opts.boxLl && opts.boxLl.length >= 3
      ? opts.boxLl.map((pair) => {
          const p = lonLatToXZ(pair[0], pair[1], opts.origin);
          return [p.x, p.z] as [number, number];
        })
      : null;
  const gridN = 72;
  const step = (opts.radiusM * 2) / gridN;
  const cellArea = step * step;
  const x0 = -opts.radiusM;
  const z0 = -opts.radiusM;
  const side = gridN + 1;
  const elevs = new Float32Array(side * side);
  elevs.fill(1e6);
  const cells: FloodCell[] = [];
  for (let i = 0; i < side; i++) {
    for (let j = 0; j < side; j++) {
      const x = x0 + step * j;
      const z = z0 + step * i;
      if (!insideClip(x, z, opts.shape, opts.radiusM)) continue;
      if (ring && !pointInRing(x, z, ring)) continue;
      const { lng, lat } = xzToLonLat(x, z, opts.origin);
      const elev = sampleElev(terrain, lat, lng);
      if (elev == null) continue;
      elevs[i * side + j] = elev;
    }
  }
  for (let i = 0; i < gridN; i++) {
    for (let j = 0; j < gridN; j++) {
      const elev = elevs[i * side + j];
      if (elev > 1e5) continue;
      cells.push({ x: x0 + step * (j + 0.5), z: z0 + step * (i + 0.5), elev });
    }
  }
  if (cells.length === 0) return null;
  let minElev = Infinity;
  let maxElev = -Infinity;
  for (const cell of cells) {
    minElev = Math.min(minElev, cell.elev);
    maxElev = Math.max(maxElev, cell.elev);
  }
  const target = cells.length * cellArea * (opts.rainfallMm / 1000);
  let lo = minElev;
  let hi = maxElev + opts.rainfallMm / 1000 + 1;
  for (let k = 0; k < 28; k++) {
    const mid = (lo + hi) / 2;
    if (volumeBelow(cells, mid, cellArea) < target) lo = mid;
    else hi = mid;
  }
  return {
    rainfallMm: opts.rainfallMm,
    cellM: step,
    minElev,
    surfaceElev: hi,
    exag: opts.exag > 0 ? opts.exag : 1,
    areaM2: cells.length * cellArea,
    cells,
    gridN,
    x0,
    z0,
    elevs,
  };
}

const WATER_VERT = `
attribute float elev;
attribute float groundY;
uniform float surfaceElev;
uniform float exag;
uniform float time;
varying float vDepth;
varying vec2 vXZ;
void main() {
  float depth = surfaceElev - elev;
  float wet = step(0.001, depth);
  float ripple = sin(position.x * 0.085 + time * 1.25) * 0.16;
  ripple += sin(position.z * 0.12 - time * 0.9) * 0.12;
  ripple += sin((position.x + position.z) * 0.045 + time * 0.65) * 0.08;
  vec3 moved = position;
  moved.y = groundY + max(depth, 0.0) * exag + ripple * wet + 0.2;
  vDepth = depth;
  vXZ = position.xz;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(moved, 1.0);
}
`;

const WATER_FRAG = `
uniform float time;
varying float vDepth;
varying vec2 vXZ;
void main() {
  if (vDepth <= 0.02) discard;
  float shore = smoothstep(0.02, 0.55, vDepth);
  float deep = clamp(vDepth / 2.2, 0.0, 1.0);
  vec3 shallow = vec3(0.62, 0.84, 0.93);
  vec3 abyss = vec3(0.08, 0.38, 0.72);
  vec3 color = mix(shallow, abyss, deep);
  float sheen = pow(max(sin(vXZ.x * 0.16 + time * 1.1) * 0.5 + 0.5, 0.0), 4.0);
  sheen += pow(max(sin(vXZ.y * 0.21 - time * 0.8) * 0.5 + 0.5, 0.0), 5.0) * 0.6;
  color += sheen * 0.16;
  gl_FragColor = vec4(color, 0.28 + shore * 0.42);
}
`;

function waterMaterial(exag: number): THREE.ShaderMaterial {
  return new THREE.ShaderMaterial({
    transparent: true,
    depthWrite: false,
    depthTest: true,
    side: THREE.DoubleSide,
    polygonOffset: true,
    polygonOffsetFactor: -2,
    polygonOffsetUnits: -2,
    uniforms: {
      surfaceElev: { value: 0 },
      exag: { value: exag },
      time: { value: 0 },
    },
    vertexShader: WATER_VERT,
    fragmentShader: WATER_FRAG,
  });
}

export function ensureFloodMesh(
  group: THREE.Group,
  plan: FloodPlan,
  yAt: (x: number, z: number) => number,
): THREE.Mesh {
  const existing = group.children[0] as THREE.Mesh | undefined;
  if (existing && existing.userData.gridN === plan.gridN) return existing;
  for (const child of [...group.children]) {
    const mesh = child as THREE.Mesh;
    mesh.geometry?.dispose();
    const mat = mesh.material as THREE.Material;
    if (!Array.isArray(mat)) mat.dispose();
    group.remove(child);
  }
  const side = plan.gridN + 1;
  const positions: number[] = [];
  const elevAttr: number[] = [];
  const groundAttr: number[] = [];
  const indexOf: number[] = new Array(side * side).fill(-1);
  for (let i = 0; i < side; i++) {
    for (let j = 0; j < side; j++) {
      const elev = plan.elevs[i * side + j];
      if (elev > 1e5) continue;
      const x = plan.x0 + plan.cellM * j;
      const z = plan.z0 + plan.cellM * i;
      indexOf[i * side + j] = positions.length / 3;
      positions.push(x, 0, z);
      elevAttr.push(elev);
      groundAttr.push(yAt(x, z));
    }
  }
  const indices: number[] = [];
  for (let i = 0; i < plan.gridN; i++) {
    for (let j = 0; j < plan.gridN; j++) {
      const a = indexOf[i * side + j];
      const b = indexOf[i * side + j + 1];
      const c = indexOf[(i + 1) * side + j];
      const d = indexOf[(i + 1) * side + j + 1];
      if (a < 0 || b < 0 || c < 0 || d < 0) continue;
      indices.push(a, c, b, b, c, d);
    }
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  geo.setAttribute("elev", new THREE.Float32BufferAttribute(elevAttr, 1));
  geo.setAttribute("groundY", new THREE.Float32BufferAttribute(groundAttr, 1));
  geo.setIndex(indices);
  const mesh = new THREE.Mesh(geo, waterMaterial(plan.exag));
  mesh.userData.gridN = plan.gridN;
  mesh.renderOrder = 6;
  mesh.frustumCulled = false;
  group.add(mesh);
  return mesh;
}

const FLOOD_RED = "#e53935";

function paintFloodedBuildings(
  group: THREE.Group,
  meshes: THREE.Mesh[],
  surface: number,
): void {
  let red = group.userData.floodMat as THREE.MeshBasicMaterial | undefined;
  let edgeRed = group.userData.floodEdge as THREE.LineBasicMaterial | undefined;
  if (!red) {
    red = new THREE.MeshBasicMaterial({
      color: FLOOD_RED,
      polygonOffset: true,
      polygonOffsetFactor: 1,
      polygonOffsetUnits: 1,
    });
    group.userData.floodMat = red;
  }
  if (!edgeRed) {
    edgeRed = new THREE.LineBasicMaterial({ color: "#b71c1c" });
    group.userData.floodEdge = edgeRed;
  }
  for (const mesh of meshes) {
    const data = mesh.userData as {
      groundElev?: number | null;
      flooded?: boolean;
      baseMat?: THREE.Material | THREE.Material[];
      baseEdgeMat?: THREE.Material | THREE.Material[];
      riseEdge?: THREE.LineSegments;
    };
    const elev = data.groundElev;
    const wet = typeof elev === "number" && elev < surface;
    if (wet && !data.flooded) {
      data.baseMat = mesh.material as THREE.Material | THREE.Material[];
      mesh.material = red;
      if (data.riseEdge) {
        data.baseEdgeMat = data.riseEdge.material as THREE.Material | THREE.Material[];
        data.riseEdge.material = edgeRed;
      }
      data.flooded = true;
    } else if (!wet && data.flooded) {
      if (data.baseMat) mesh.material = data.baseMat;
      if (data.riseEdge && data.baseEdgeMat) data.riseEdge.material = data.baseEdgeMat;
      data.flooded = false;
    }
  }
}

function restoreFloodedBuildings(meshes: THREE.Mesh[]): void {
  for (const mesh of meshes) {
    const data = mesh.userData as {
      flooded?: boolean;
      baseMat?: THREE.Material | THREE.Material[];
      baseEdgeMat?: THREE.Material | THREE.Material[];
      riseEdge?: THREE.LineSegments;
    };
    if (!data.flooded) continue;
    if (data.baseMat) mesh.material = data.baseMat;
    if (data.riseEdge && data.baseEdgeMat) data.riseEdge.material = data.baseEdgeMat;
    data.flooded = false;
  }
}

export function applyFloodFrame(
  group: THREE.Group,
  job: FloodJob | null,
  yAt: (x: number, z: number) => number,
  now: number,
  meshes: THREE.Mesh[] = [],
): void {
  if (!job) {
    group.visible = false;
    restoreFloodedBuildings(meshes);
    return;
  }
  const mesh = ensureFloodMesh(group, job.plan, yAt);
  const t = Math.min(1, Math.max(0, (now - job.started) / job.durationMs));
  const surface = job.plan.minElev + (job.plan.surfaceElev - job.plan.minElev) * t;
  const material = mesh.material as THREE.ShaderMaterial;
  material.uniforms.surfaceElev.value = surface;
  material.uniforms.time.value = now * 0.001;
  paintFloodedBuildings(group, meshes, surface);
  group.visible = true;
}

export function applyGrowthFrame(meshes: THREE.Mesh[], job: GrowthJob | null, now: number): number | null {
  if (!job) {
    for (const mesh of meshes) {
      if (mesh.scale.y !== 1) mesh.scale.y = 1;
    }
    return null;
  }
  if (job.playing) {
    const dt = Math.min(80, Math.max(0, now - job.lastTick));
    job.playYear = Math.min(job.endYear, job.playYear + dt * job.yearPerMs);
    if (job.playYear >= job.endYear) job.playing = false;
  }
  job.lastTick = now;
  const shown = job.playYear;
  for (const mesh of meshes) {
    const data = (mesh as RiseMesh).userData;
    const year = Number(data.year) || 0;
    let scale = 1;
    if (year >= job.startYear) {
      scale = Math.min(1, Math.max(0, shown - (year - 1)));
    }
    mesh.scale.y = scale;
    if (data.riseEdge) data.riseEdge.scale.y = scale;
  }
  return shown;
}

export function growthEndYear(years: number[], startYear: number): number {
  let end = startYear + 1;
  for (const year of years) if (year > end) end = year;
  return end;
}
