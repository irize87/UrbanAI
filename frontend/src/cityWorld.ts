import * as THREE from "three";
import { mergeGeometries } from "three/examples/jsm/utils/BufferGeometryUtils.js";
import type { CityPoly, CityRing, CityRoad } from "./api";
import type { CityBundle } from "./cityCache";
import { USE_COLORS } from "./palette";
import { polyToShape, ringToShape } from "./massWorld";
import {
  buildRoadRibbon,
  lonLatToXZ,
  mergeStyle,
  roadHalfWidth,
  sampleElev,
  tessellateThenDrape,
  xzToLonLat,
  type TerrainGrid,
} from "./massTerrain";

export type CityLayers = {
  floor: THREE.Group;
  terrain: THREE.Group;
  green: THREE.Group;
  roads: THREE.Group;
  buildings: THREE.Group;
  plate: THREE.Group;
  contextBuildings: THREE.Group;
  contextRoads: THREE.Group;
};

export type CityScene = {
  root: THREE.Group;
  layers: CityLayers;
  origin: [number, number];
  center: THREE.Vector3;
  span: number;
};

type XZ = { x: number; z: number };

function yieldTick(): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, 0);
  });
}

const CONTEXT_FADE_M = 180;

type BoundaryEdge = { ax: number; az: number; bx: number; bz: number };

function distToSegment(px: number, pz: number, edge: BoundaryEdge): number {
  const abx = edge.bx - edge.ax;
  const abz = edge.bz - edge.az;
  const denom = abx * abx + abz * abz;
  let t = denom > 0 ? ((px - edge.ax) * abx + (pz - edge.az) * abz) / denom : 0;
  if (t < 0) t = 0;
  else if (t > 1) t = 1;
  return Math.hypot(edge.ax + abx * t - px, edge.az + abz * t - pz);
}

/** 서초 경계에서의 거리. 띠의 바깥 180m만 0까지 투명해진다. */
function makeContextFade(
  polys: XZ[][][],
  bufferM: number,
): (x: number, z: number) => number {
  const cell = 200;
  const buckets = new Map<string, BoundaryEdge[]>();
  const pushEdge = (edge: BoundaryEdge) => {
    const i0 = Math.floor(Math.min(edge.ax, edge.bx) / cell);
    const i1 = Math.floor(Math.max(edge.ax, edge.bx) / cell);
    const k0 = Math.floor(Math.min(edge.az, edge.bz) / cell);
    const k1 = Math.floor(Math.max(edge.az, edge.bz) / cell);
    for (let i = i0; i <= i1; i++) {
      for (let k = k0; k <= k1; k++) {
        const key = `${i},${k}`;
        const list = buckets.get(key);
        if (list) list.push(edge);
        else buckets.set(key, [edge]);
      }
    }
  };
  for (const poly of polys) {
    const ring = poly[0];
    if (!ring) continue;
    for (let i = 0; i < ring.length; i++) {
      const a = ring[i];
      const b = ring[(i + 1) % ring.length];
      if (a.x === b.x && a.z === b.z) continue;
      pushEdge({ ax: a.x, az: a.z, bx: b.x, bz: b.z });
    }
  }
  const fadeM = Math.min(CONTEXT_FADE_M, Math.max(40, bufferM * 0.3));
  const solidUntil = Math.max(0, bufferM - fadeM);
  const reach = Math.ceil((bufferM + cell) / cell);
  const distanceAt = (x: number, z: number) => {
    const ix = Math.floor(x / cell);
    const iz = Math.floor(z / cell);
    let best = Infinity;
    for (let ring = 0; ring <= reach; ring++) {
      for (let di = -ring; di <= ring; di++) {
        for (let dk = -ring; dk <= ring; dk++) {
          if (Math.max(Math.abs(di), Math.abs(dk)) !== ring) continue;
          const list = buckets.get(`${ix + di},${iz + dk}`);
          if (!list) continue;
          for (const edge of list) {
            const dist = distToSegment(x, z, edge);
            if (dist < best) best = dist;
          }
        }
      }
      if (best <= solidUntil) return best;
    }
    return best;
  };
  return (x: number, z: number) => {
    const dist = distanceAt(x, z);
    if (!Number.isFinite(dist) || dist <= solidUntil) return 1;
    if (dist >= bufferM) return 0;
    const t = (dist - solidUntil) / fadeM;
    const smooth = t * t * (3 - 2 * t);
    return 1 - smooth;
  };
}

function paintContextFade(
  geom: THREE.BufferGeometry,
  fadeAt: (x: number, z: number) => number,
): void {
  const pos = geom.getAttribute("position");
  const colors = new Float32Array(pos.count * 4);
  for (let i = 0; i < pos.count; i++) {
    const fade = fadeAt(pos.getX(i), pos.getZ(i));
    const offset = i * 4;
    colors[offset] = 1;
    colors[offset + 1] = 1;
    colors[offset + 2] = 1;
    colors[offset + 3] = fade;
  }
  geom.setAttribute("color", new THREE.BufferAttribute(colors, 4));
}

function makeContextMaterial(color: string, offset: number): THREE.MeshBasicMaterial {
  return new THREE.MeshBasicMaterial({
    color,
    vertexColors: true,
    transparent: true,
    opacity: 1,
    depthWrite: false,
    side: THREE.DoubleSide,
    polygonOffset: true,
    polygonOffsetFactor: offset,
    polygonOffsetUnits: offset,
  });
}

function pointInRing(px: number, pz: number, ring: XZ[]): boolean {
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

function polyContains(x: number, z: number, rings: XZ[][]): boolean {
  if (!rings[0] || !pointInRing(x, z, rings[0])) return false;
  for (let i = 1; i < rings.length; i++) {
    if (pointInRing(x, z, rings[i])) return false;
  }
  return true;
}

function toXzPolys(polys: CityPoly[], origin: [number, number]): XZ[][][] {
  return polys
    .map((poly) => poly.map((ring) => ring.map((coord) => lonLatToXZ(coord[0], coord[1], origin))))
    .filter((poly) => poly[0] && poly[0].length >= 3);
}

function boundsOf(polys: XZ[][][]): { minX: number; maxX: number; minZ: number; maxZ: number } | null {
  let minX = Infinity;
  let maxX = -Infinity;
  let minZ = Infinity;
  let maxZ = -Infinity;
  for (const poly of polys) {
    for (const point of poly[0]) {
      minX = Math.min(minX, point.x);
      maxX = Math.max(maxX, point.x);
      minZ = Math.min(minZ, point.z);
      maxZ = Math.max(maxZ, point.z);
    }
  }
  if (!Number.isFinite(minX)) return null;
  return { minX, maxX, minZ, maxZ };
}

function terrainFloor(terrain: TerrainGrid | null | undefined): number {
  if (!terrain) return 0;
  const declared = Number(terrain.min);
  if (Number.isFinite(declared)) return declared;
  let vmin = Infinity;
  for (const row of terrain.grid || []) {
    for (const value of row) {
      if (Number.isFinite(value)) vmin = Math.min(vmin, value);
    }
  }
  return Number.isFinite(vmin) ? vmin : 0;
}

function makeSampler(
  terrain: TerrainGrid | null | undefined,
  origin: [number, number],
  exag: number,
): (x: number, z: number) => number {
  const vmin = terrainFloor(terrain);
  const scale = exag > 0 ? exag : 1;
  if (!terrain?.lats?.length || !terrain.lngs?.length || !terrain.grid?.length) {
    return () => 0;
  }
  return (x: number, z: number) => {
    const { lng, lat } = xzToLonLat(x, z, origin);
    const elev = sampleElev(terrain, lat, lng);
    return ((elev ?? vmin) - vmin) * scale;
  };
}

function takeMerged(geoms: THREE.BufferGeometry[]): THREE.BufferGeometry | null {
  const ready = geoms.filter((geom) => (geom.getAttribute("position")?.count || 0) > 0);
  if (!ready.length) return null;
  if (ready.length === 1) return ready[0];
  const normalized = ready.map((geom) => {
    const copy = geom.index ? geom.toNonIndexed() : geom;
    if (copy !== geom) geom.dispose();
    return copy;
  });
  try {
    const merged = mergeGeometries(normalized, false);
    if (!merged) return normalized[0];
    for (const geom of normalized) geom.dispose();
    return merged;
  } catch {
    return normalized[0];
  }
}

function meshFrom(geoms: THREE.BufferGeometry[], material: THREE.Material, order: number): THREE.Mesh | null {
  const merged = takeMerged(geoms);
  if (!merged) return null;
  const mesh = new THREE.Mesh(merged, material);
  mesh.renderOrder = order;
  return mesh;
}

function useColor(name: string): string {
  return USE_COLORS[name] || USE_COLORS["미상"];
}

function extrudeRing(
  ring: CityRing,
  origin: [number, number],
  height: number,
  yAt: (x: number, z: number) => number,
): { lid: THREE.BufferGeometry; wall: THREE.BufferGeometry } | null {
  if (ring.length < 3) return null;
  const shape = ringToShape(ring, origin);
  const lid2d = new THREE.ShapeGeometry(shape);
  const src = lid2d.getAttribute("position");
  const index = lid2d.getIndex();
  if (!src || !index || src.count < 3) {
    lid2d.dispose();
    return null;
  }
  let sumX = 0;
  let sumZ = 0;
  for (const coord of ring) {
    const point = lonLatToXZ(coord[0], coord[1], origin);
    sumX += point.x;
    sumZ += point.z;
  }
  const baseY = yAt(sumX / ring.length, sumZ / ring.length);
  const topY = baseY + Math.max(1, height);
  const lidPos = new Float32Array(src.count * 3);
  for (let i = 0; i < src.count; i++) {
    lidPos[i * 3] = src.getX(i);
    lidPos[i * 3 + 1] = topY;
    lidPos[i * 3 + 2] = -src.getY(i);
  }
  const lidIndex: number[] = [];
  for (let i = 0; i < index.count; i += 3) {
    lidIndex.push(index.getX(i), index.getX(i + 1), index.getX(i + 2));
  }
  const lid = new THREE.BufferGeometry();
  lid.setAttribute("position", new THREE.BufferAttribute(lidPos, 3));
  lid.setIndex(lidIndex);

  const closed =
    ring.length > 1 &&
    ring[0][0] === ring[ring.length - 1][0] &&
    ring[0][1] === ring[ring.length - 1][1];
  const loop = closed ? ring.slice(0, -1) : ring;
  const wallPos: number[] = [];
  for (let i = 0; i < loop.length; i++) {
    const a = lonLatToXZ(loop[i][0], loop[i][1], origin);
    const next = loop[(i + 1) % loop.length];
    const b = lonLatToXZ(next[0], next[1], origin);
    if ((a.x - b.x) * (a.x - b.x) + (a.z - b.z) * (a.z - b.z) < 1e-8) continue;
    wallPos.push(
      a.x, baseY, a.z, b.x, baseY, b.z, b.x, topY, b.z,
      a.x, baseY, a.z, b.x, topY, b.z, a.x, topY, a.z,
    );
  }
  const wall = new THREE.BufferGeometry();
  wall.setAttribute("position", new THREE.Float32BufferAttribute(wallPos, 3));
  lid2d.dispose();
  return { lid, wall };
}

function flatRing(ring: CityRing, origin: [number, number], y: number): THREE.BufferGeometry | null {
  if (ring.length < 3) return null;
  const geom = new THREE.ShapeGeometry(ringToShape(ring, origin));
  geom.rotateX(-Math.PI / 2);
  geom.translate(0, y, 0);
  return geom;
}

function buildTerrainMesh(
  polys: CityPoly[],
  origin: [number, number],
  yAt: (x: number, z: number) => number,
  color: string,
): THREE.Mesh | null {
  const xz = toXzPolys(polys, origin);
  const box = boundsOf(xz);
  if (!box) return null;
  const spanX = Math.max(1, box.maxX - box.minX);
  const spanZ = Math.max(1, box.maxZ - box.minZ);
  const span = Math.max(spanX, spanZ, 80);
  const step = Math.max(40, Math.min(80, span / 80));
  const cols = Math.max(2, Math.ceil(spanX / step));
  const rows = Math.max(2, Math.ceil(spanZ / step));
  const positions: number[] = [];
  const contains = (x: number, z: number) => xz.some((poly) => polyContains(x, z, poly));
  for (let row = 0; row < rows; row++) {
    for (let col = 0; col < cols; col++) {
      const x0 = box.minX + (spanX * col) / cols;
      const x1 = box.minX + (spanX * (col + 1)) / cols;
      const z0 = box.minZ + (spanZ * row) / rows;
      const z1 = box.minZ + (spanZ * (row + 1)) / rows;
      if (!contains((x0 + x1) / 2, (z0 + z1) / 2)) continue;
      positions.push(
        x0, yAt(x0, z0), z0,
        x1, yAt(x1, z0), z0,
        x1, yAt(x1, z1), z1,
        x0, yAt(x0, z0), z0,
        x1, yAt(x1, z1), z1,
        x0, yAt(x0, z1), z1,
      );
    }
  }
  if (positions.length < 9) return null;
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  geo.computeVertexNormals();
  const mesh = new THREE.Mesh(
    geo,
    new THREE.MeshBasicMaterial({
      color,
      side: THREE.DoubleSide,
      polygonOffset: true,
      polygonOffsetFactor: 4,
      polygonOffsetUnits: 4,
    }),
  );
  mesh.renderOrder = 0;
  return mesh;
}

function buildCake(
  polys: CityPoly[],
  origin: [number, number],
  yAt: (x: number, z: number) => number,
  baseH: number,
  floor: string,
): THREE.Group {
  const group = new THREE.Group();
  const topMat = new THREE.MeshBasicMaterial({ color: floor, side: THREE.DoubleSide });
  const sideMat = new THREE.MeshLambertMaterial({ color: "#cfcfcf", side: THREE.DoubleSide });
  for (const poly of polys) {
    const shape = polyToShape(poly, origin);
    if (!shape || poly[0].length < 3) continue;
    const bottom = new THREE.ShapeGeometry(shape);
    bottom.rotateX(-Math.PI / 2);
    const cap = new THREE.Mesh(bottom, topMat);
    cap.position.y = -baseH;
    group.add(cap);
    const rim = poly[0].map((coord) => lonLatToXZ(coord[0], coord[1], origin));
    const positions: number[] = [];
    const normals: number[] = [];
    for (let i = 0; i < rim.length; i++) {
      const a = rim[i];
      const b = rim[(i + 1) % rim.length];
      const ya = yAt(a.x, a.z);
      const yb = yAt(b.x, b.z);
      positions.push(
        a.x, -baseH, a.z,
        b.x, -baseH, b.z,
        b.x, yb, b.z,
        a.x, -baseH, a.z,
        b.x, yb, b.z,
        a.x, ya, a.z,
      );
      const nx = b.z - a.z;
      const nz = a.x - b.x;
      const len = Math.hypot(nx, nz) || 1;
      for (let k = 0; k < 6; k++) normals.push(nx / len, 0, nz / len);
    }
    const side = new THREE.BufferGeometry();
    side.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
    side.setAttribute("normal", new THREE.Float32BufferAttribute(normals, 3));
    group.add(new THREE.Mesh(side, sideMat));
  }
  return group;
}

function addDrapedPolys(
  group: THREE.Group,
  polys: CityPoly[],
  origin: [number, number],
  yAt: (x: number, z: number) => number,
  color: string,
  lift: number,
  order: number,
): void {
  const material = new THREE.MeshBasicMaterial({
    color,
    side: THREE.DoubleSide,
    polygonOffset: true,
    polygonOffsetFactor: order === 2 ? -4 : -2,
    polygonOffsetUnits: order === 2 ? -4 : -2,
  });
  for (const poly of polys) {
    const shape = polyToShape(poly, origin);
    if (!shape) continue;
    try {
      const raw = new THREE.ShapeGeometry(shape);
      raw.rotateX(-Math.PI / 2);
      const geom = tessellateThenDrape(raw, yAt, lift, 70);
      const mesh = new THREE.Mesh(geom, material);
      mesh.renderOrder = order;
      group.add(mesh);
    } catch {
      /* 깨진 면은 건너뛴다 */
    }
  }
}

function addRoads(
  group: THREE.Group,
  roads: CityRoad[],
  origin: [number, number],
  yAt: (x: number, z: number) => number,
  lift: number,
  color: string,
  style: ReturnType<typeof mergeStyle>,
  fadeAt?: (x: number, z: number) => number,
): void {
  const material = fadeAt
    ? makeContextMaterial(color, -8)
    : new THREE.MeshBasicMaterial({
        color,
        side: THREE.DoubleSide,
        polygonOffset: true,
        polygonOffsetFactor: -8,
        polygonOffsetUnits: -8,
        depthWrite: true,
      });
  const geoms: THREE.BufferGeometry[] = [];
  for (const road of roads) {
    const half = roadHalfWidth(road.c, style);
    if (half == null) continue;
    for (const line of road.lines) {
      const pts = line.map((coord) => {
        const { x, z } = lonLatToXZ(coord[0], coord[1], origin);
        return new THREE.Vector3(x, yAt(x, z) + lift, z);
      });
      const ribbon = buildRoadRibbon(pts, half, color, material);
      const mesh = ribbon as THREE.Mesh | null;
      if (!mesh?.geometry) continue;
      if (fadeAt) paintContextFade(mesh.geometry, fadeAt);
      geoms.push(mesh.geometry);
    }
  }
  const mesh = meshFrom(geoms, material, 3);
  if (mesh) group.add(mesh);
}

function addPlate(group: THREE.Group, polys: CityPoly[], origin: [number, number]): void {
  const material = new THREE.MeshBasicMaterial({
    color: "#ffffff",
    side: THREE.DoubleSide,
    polygonOffset: true,
    polygonOffsetFactor: 6,
    polygonOffsetUnits: 6,
  });
  const geoms: THREE.BufferGeometry[] = [];
  for (const poly of polys) {
    const shape = polyToShape(poly, origin);
    if (!shape) continue;
    const geom = new THREE.ShapeGeometry(shape);
    geom.rotateX(-Math.PI / 2);
    geom.translate(0, 0.02, 0);
    geoms.push(geom);
  }
  const mesh = meshFrom(geoms, material, 0);
  if (mesh) group.add(mesh);
}

async function addExtrusions(
  group: THREE.Group,
  items: { ring: CityRing; height: number; color: string }[],
  origin: [number, number],
  yAt: (x: number, z: number) => number,
  onProgress: (done: number, total: number) => void,
  signal: AbortSignal,
): Promise<void> {
  const lids = new Map<string, THREE.BufferGeometry[]>();
  const walls = new Map<string, THREE.BufferGeometry[]>();
  let stamp = performance.now();
  const flush = (color: string, lidGeoms: THREE.BufferGeometry[], wallGeoms: THREE.BufferGeometry[]) => {
    if (lidGeoms.length) {
      const lidMat = new THREE.MeshBasicMaterial({
        color,
        polygonOffset: true,
        polygonOffsetFactor: 1,
        polygonOffsetUnits: 1,
      });
      const lidMesh = meshFrom(lidGeoms, lidMat, 4);
      if (lidMesh) group.add(lidMesh);
    }
    if (wallGeoms.length) {
      const wallMat = new THREE.MeshLambertMaterial({
        color,
        flatShading: true,
        side: THREE.DoubleSide,
      });
      const wallMesh = meshFrom(wallGeoms, wallMat, 4);
      if (wallMesh) group.add(wallMesh);
    }
  };
  for (let index = 0; index < items.length; index++) {
    if (signal.aborted) return;
    const item = items[index];
    try {
      const geom = extrudeRing(item.ring, origin, item.height, yAt);
      if (geom) {
        const lidList = lids.get(item.color) || [];
        lidList.push(geom.lid);
        lids.set(item.color, lidList);
        const wallList = walls.get(item.color) || [];
        wallList.push(geom.wall);
        walls.set(item.color, wallList);
        if (lidList.length >= 400) {
          flush(item.color, lidList, wallList);
          lids.set(item.color, []);
          walls.set(item.color, []);
        }
      }
    } catch {
      /* 압출 실패 건물은 건너뛴다 */
    }
    if (performance.now() - stamp > 80) {
      onProgress(index, items.length);
      await yieldTick();
      stamp = performance.now();
    }
  }
  for (const [color, lidGeoms] of lids) flush(color, lidGeoms, walls.get(color) || []);
  onProgress(items.length, items.length);
}

async function addFootprints(
  group: THREE.Group,
  rings: CityRing[],
  origin: [number, number],
  signal: AbortSignal,
  fadeAt: (x: number, z: number) => number,
): Promise<void> {
  const material = makeContextMaterial("#c8c8c8", -1);
  const geoms: THREE.BufferGeometry[] = [];
  let stamp = performance.now();
  for (let index = 0; index < rings.length; index++) {
    if (signal.aborted) return;
    const geom = flatRing(rings[index], origin, 0.35);
    if (geom) {
      paintContextFade(geom, fadeAt);
      geoms.push(geom);
    }
    if (geoms.length >= 500 || performance.now() - stamp > 48) {
      const mesh = meshFrom(geoms.splice(0, geoms.length), material, 1);
      if (mesh) group.add(mesh);
      await yieldTick();
      stamp = performance.now();
    }
  }
  const mesh = meshFrom(geoms, material, 1);
  if (mesh) group.add(mesh);
}

export function disposeCity(root: THREE.Object3D): void {
  const materials = new Set<THREE.Material>();
  root.traverse((node) => {
    const mesh = node as THREE.Mesh;
    mesh.geometry?.dispose();
    const material = mesh.material as THREE.Material | THREE.Material[] | undefined;
    if (Array.isArray(material)) material.forEach((item) => materials.add(item));
    else if (material) materials.add(material);
  });
  materials.forEach((material) => material.dispose());
}

/** 캐시된 서초·주변 데이터로 입체 구와 바닥 2D를 만든다. 그림자는 넣지 않는다. */
export async function buildCityScene(
  bundle: CityBundle,
  onProgress: (message: string, ratio: number) => void,
  signal: AbortSignal,
  onShell?: (scene: CityScene) => void,
): Promise<CityScene> {
  const origin: [number, number] = [bundle.meta.origin.lng, bundle.meta.origin.lat];
  const style = mergeStyle(bundle.meta.style);
  const yAt = makeSampler(bundle.ground.terrain, origin, style.terrain_exag);
  const root = new THREE.Group();
  const layers: CityLayers = {
    floor: new THREE.Group(),
    terrain: new THREE.Group(),
    green: new THREE.Group(),
    roads: new THREE.Group(),
    buildings: new THREE.Group(),
    plate: new THREE.Group(),
    contextBuildings: new THREE.Group(),
    contextRoads: new THREE.Group(),
  };
  Object.values(layers).forEach((group) => root.add(group));

  onProgress("서초 바닥을 만드는 중…", 0.3);
  layers.floor.add(buildCake(bundle.ground.boundary, origin, yAt, style.base_h_m, style.floor));
  const terrain = buildTerrainMesh(bundle.ground.boundary, origin, yAt, style.floor);
  if (terrain) layers.terrain.add(terrain);

  addDrapedPolys(layers.green, bundle.ground.green, origin, yAt, style.green, style.green_lift_m, 1);
  addDrapedPolys(layers.green, bundle.ground.water, origin, yAt, style.water, style.water_lift_m, 2);
  addRoads(layers.roads, bundle.ground.roads, origin, yAt, style.road_lift_m, style.road, style);

  const box = boundsOf(toXzPolys(bundle.ground.boundary, origin));
  const center = new THREE.Vector3(0, 0, 0);
  let span = 800;
  if (box) {
    center.set((box.minX + box.maxX) / 2, 0, (box.minZ + box.maxZ) / 2);
    span = Math.max(box.maxX - box.minX, box.maxZ - box.minZ, 200);
  }
  const scene: CityScene = { root, layers, origin, center, span };
  onShell?.(scene);

  const masses = bundle.buildings.items.flatMap((item) =>
    item.rings.map((ring) => ({
      ring,
      height: Number(item.h) || 10,
      color: useColor(String(item.u || "미상")),
    })),
  );
  await addExtrusions(
    layers.buildings,
    masses,
    origin,
    yAt,
    (done, total) =>
      onProgress(
        `서초 건물 ${done.toLocaleString("ko-KR")}/${total.toLocaleString("ko-KR")}`,
        0.32 + 0.56 * (total ? done / total : 1),
      ),
    signal,
  );
  if (signal.aborted) throw new DOMException("aborted", "AbortError");

  onProgress("경계 밖 바닥을 만드는 중…", 0.9);
  const fadeAt = makeContextFade(
    toXzPolys(bundle.ground.boundary, origin),
    Number(bundle.meta.context_buffer_m) || 600,
  );
  addPlate(layers.plate, bundle.context.plate, origin);
  await addFootprints(layers.contextBuildings, bundle.context.buildings, origin, signal, fadeAt);
  addRoads(
    layers.contextRoads,
    bundle.context.roads,
    origin,
    () => 0,
    0.55,
    "#b4b4b4",
    style,
    fadeAt,
  );
  return scene;
}
