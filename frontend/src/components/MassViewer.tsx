import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { GLTFExporter } from "three/examples/jsm/exporters/GLTFExporter.js";
import { applySim, askAnalysis, type AnalysisResult, type SimChatTurn } from "../api";
import { applyFloodFrame, applyGrowthFrame, planFlood, type FloodJob, type GrowthJob } from "../floodGrow";
import { StarLoader } from "./StarLoader";
import icon360 from "../assets/icon-360.png";
import {
  AGE_LABELS,
  AGE_LIMITS,
  AGE_RAMP,
  AGE_UNKNOWN_3D,
  HEIGHT_LABELS,
  HEIGHT_LIMITS,
  HEIGHT_RAMP,
  SOLID_3D,
  USE_COLORS,
  tintRamp,
} from "../palette";
import {
  buildCakeBase,
  buildExplodeGuides,
  buildSitePin,
  lonLatToXZ,
  xzToLonLat,
  mergeStyle,
  DEFAULT_MASS_STYLE,
} from "../massTerrain";
import {
  applyOrthoFrustum,
  assembleCompleteMass,
  buildMassWorld,
  MASS_ORTHO_DEFAULT_ZOOM,
  ORBIT_WHEEL_ZOOM_SCALE,
  type Feature,
  type MassData,
} from "../massWorld";

export type { Feature, MassData };

type ColorMode = "use" | "age" | "height" | "solid";
type ViewMode = "complete" | "explode";

type Props = {
  data: MassData | null;
  lat: number;
  lng: number;
  radiusM: number;
  shape: "circle" | "rect";
  includeAdjacent: boolean;
  adjacentBusy?: boolean;
  onIncludeAdjacentChange: (value: boolean) => void;
  mode?: "view" | "sim";
};

type PalRow = { key: string; label: string; color: string; edit: boolean };

const ANALYSIS_GUIDE: { title: string; body: string; note?: string; prompt: string }[] = [
  {
    title: "자연어 도시 탐색 AI",
    body: "조건(연도·층수·높이·용도)을 말하면 해당 건축물을 표시합니다.",
    prompt: "30년 이상, 5층 이하만 표시해줘",
  },
  {
    title: "AI 도시 브리핑",
    body: "선택한 영역의 건물 수, 평균 높이, 용도 비율, 노후 건축물 현황을 요약 보고합니다.",
    prompt: "브리핑 해줘",
  },
  {
    title: "도시 개발 검토 AI",
    body: "용도지역, 학교 인근, 역세권, 고밀 개발 적합 지역 등 개발 시나리오에 적합한 공간을 표시합니다.",
    prompt: "지하철 반경 500m 지역 중 30년 이상된 저층 건물 표시해줘",
  },
  {
    title: "AI 도시 개발 시뮬레이션",
    body: "오른쪽 하단 마커로 선택한 영역의 건물 높이를 수정 요청하면 시뮬레이션한 도시뷰를 제공합니다.",
    prompt: "해당 건물 높이 10층 높이로 바꿔줘",
  },
  {
    title: "AI 도시 침수 시뮬레이션",
    body: "시간당 강수량을 말하면, 지형(DEM) 기준으로 저지대부터 반투명 파란 물이 차오릅니다.",
    prompt: "강수량 100mm 상황 보여줘",
  },
  {
    title: "도시 성장 타임랩스",
    body: "시작 연도를 말하면, 그 연도부터 준공 순서대로 건축물이 올라옵니다.",
    prompt: "1988년부터 서초구의 성장 역사를 보여줘",
  },
];

async function copyPromptText(text: string) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const area = document.createElement("textarea");
    area.value = text;
    document.body.appendChild(area);
    area.select();
    document.execCommand("copy");
    area.remove();
  }
}

function ringToShape(ring: number[][], origin: [number, number]): THREE.Shape {
  const [olng, olat] = origin;
  const shape = new THREE.Shape();
  const closed =
    ring.length > 1 &&
    ring[0][0] === ring[ring.length - 1][0] &&
    ring[0][1] === ring[ring.length - 1][1];
  const pts = closed ? ring.slice(0, -1) : ring;
  pts.forEach((c, i) => {
    const x = (c[0] - olng) * 111320 * Math.cos((olat * Math.PI) / 180);
    const y = (c[1] - olat) * 111320;
    if (i === 0) shape.moveTo(x, y);
    else shape.lineTo(x, y);
  });
  return shape;
}

function polyToShape(poly: number[][][], origin: [number, number]): THREE.Shape | null {
  if (!poly[0] || poly[0].length < 3) return null;
  const shape = ringToShape(poly[0], origin);
  for (let i = 1; i < poly.length; i++) {
    const hole = poly[i];
    if (!hole || hole.length < 3) continue;
    shape.holes.push(ringToShape(hole, origin));
  }
  return shape;
}

function polysOf(f: Feature): number[][][][] {
  if (f.geometry.type === "Polygon") return [f.geometry.coordinates as number[][][]];
  return f.geometry.coordinates as number[][][][];
}

const SELECT_ACCENT = 0xff6b6b;
const DRAG_PX = 8;
const POLY_CLOSE_PX = 32;
const POLY_CLOSE_M = 28;
const SELECT_LIFT_M = 28;
const SIM_IMAGE_MAX_EDGE = 1200;
const SIM_PIN_SITE_M = 240;

function statedSiteWidthM(prompt: string): number | null {
  const patterns = [
    /(?:가로|폭|너비|단지\s*폭)\s*(?:약\s*)?(\d+(?:\.\d+)?)\s*m/i,
    /(\d+(?:\.\d+)?)\s*m\s*(?:폭|가로|너비)/i,
  ];
  for (const pattern of patterns) {
    const matched = prompt.match(pattern);
    if (!matched) continue;
    const value = Number(matched[1]);
    if (value >= 30 && value <= 1500) return value;
  }
  return null;
}

function previewAspect(preview: string): Promise<number> {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => {
      const width = img.naturalWidth || 1;
      const height = img.naturalHeight || 1;
      resolve(width / height);
    };
    img.onerror = () => resolve(1);
    img.src = preview;
  });
}

function boxAroundPin(
  pin: { lng: number; lat: number },
  origin: [number, number],
  widthM: number,
  depthM: number,
): number[][] {
  const center = lonLatToXZ(pin.lng, pin.lat, origin);
  const halfW = widthM / 2;
  const halfD = depthM / 2;
  const corners: [number, number][] = [
    [center.x - halfW, center.z - halfD],
    [center.x + halfW, center.z - halfD],
    [center.x + halfW, center.z + halfD],
    [center.x - halfW, center.z + halfD],
  ];
  const ring = corners.map(([x, z]) => {
    const ll = xzToLonLat(x, z, origin);
    return [ll.lng, ll.lat];
  });
  ring.push([ring[0][0], ring[0][1]]);
  return ring;
}

function appendSimBuildings(prev: Feature[], added: Feature[]): Feature[] {
  if (added.length === 0) return prev;
  let minId = 0;
  for (const feat of prev) {
    const id = Number(feat.properties.id);
    if (Number.isFinite(id) && id < minId) minId = id;
  }
  return [
    ...prev,
    ...added.map((feat, i) => ({
      ...feat,
      properties: { ...feat.properties, id: minId - 1 - i },
    })),
  ];
}

function mergeHideIds(prev: number[], added: number[]): number[] {
  const next = new Set(prev);
  for (const id of added) next.add(id);
  return [...next];
}

function colorsFromApi(raw: Record<string, string> | null | undefined): Record<number, string> {
  const next: Record<number, string> = {};
  if (!raw) return next;
  for (const [key, color] of Object.entries(raw)) {
    const id = Number(key);
    if (Number.isFinite(id) && color) next[id] = color;
  }
  return next;
}

function renderBrief(markdown: string) {
    const nodes: ReactNode[] = [];
  let items: string[] = [];
  const flush = () => {
    if (items.length === 0) return;
    nodes.push(
      <ul key={`ul-${nodes.length}`}>
        {items.map((item) => (
          <li key={`${nodes.length}-${item}`}>{item}</li>
        ))}
      </ul>,
    );
    items = [];
  };
  markdown.split("\n").forEach((line) => {
    if (line.startsWith("# ")) {
      flush();
      nodes.push(
        <h2 id="brief-title" key={`h-${nodes.length}`}>
          {line.slice(2)}
        </h2>,
      );
      return;
    }
    if (line.startsWith("## ")) {
      flush();
      nodes.push(<h3 key={`h3-${nodes.length}`}>{line.slice(3)}</h3>);
      return;
    }
    if (line.startsWith("- ")) {
      items.push(line.slice(2));
      return;
    }
    if (!line.trim()) {
      flush();
      return;
    }
    flush();
    nodes.push(<p key={`p-${nodes.length}`}>{line}</p>);
  });
  flush();
  return nodes;
}

function downloadBrief(markdown: string) {
  const blob = new Blob([markdown], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "도시현황브리핑.md";
  link.click();
  URL.revokeObjectURL(url);
}

async function copyBrief(markdown: string) {
  const fenced = `\`\`\`\n${markdown}\n\`\`\``;
  try {
    await navigator.clipboard.writeText(fenced);
  } catch {
    const area = document.createElement("textarea");
    area.value = fenced;
    document.body.appendChild(area);
    area.select();
    document.execCommand("copy");
    area.remove();
  }
}

type SimImage = { name: string; mime: string; b64: string; preview: string };

async function loadSimImage(file: File): Promise<SimImage> {
  const url = URL.createObjectURL(file);
  try {
    const img = await new Promise<HTMLImageElement>((ok, ng) => {
      const el = new Image();
      el.onload = () => ok(el);
      el.onerror = () => ng(new Error("이미지를 열지 못했습니다"));
      el.src = url;
    });
    const scale = Math.min(1, SIM_IMAGE_MAX_EDGE / Math.max(img.width, img.height, 1));
    const w = Math.max(1, Math.round(img.width * scale));
    const h = Math.max(1, Math.round(img.height * scale));
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const gfx = canvas.getContext("2d");
    if (!gfx) throw new Error("이미지를 읽지 못했습니다");
    gfx.drawImage(img, 0, 0, w, h);
    const preview = canvas.toDataURL("image/jpeg", 0.82);
    const b64 = preview.split(",")[1] || "";
    if (!b64) throw new Error("이미지를 읽지 못했습니다");
    return { name: file.name, mime: "image/jpeg", b64, preview };
  } finally {
    URL.revokeObjectURL(url);
  }
}

type PickCtx = {
  camera: THREE.OrthographicCamera;
  renderer: THREE.WebGLRenderer;
  controls: OrbitControls;
  buildingMeshes: THREE.Mesh[];
  origin: [number, number];
  highlightGrp: THREE.Group;
  boxLine: THREE.Line;
  hoverLine: THREE.Line;
  selectFill: THREE.Mesh;
  vertexGrp: THREE.Group;
  raycaster: THREE.Raycaster;
  ground: THREE.Plane;
  yAt: (x: number, z: number) => number;
  centerPinGrp: THREE.Group;
};

type SavedView = {
  px: number;
  py: number;
  pz: number;
  tx: number;
  ty: number;
  tz: number;
  ux: number;
  uy: number;
  uz: number;
  zoom: number;
};

function ndcFromPointer(ev: PointerEvent, el: HTMLElement): THREE.Vector2 {
  const rect = el.getBoundingClientRect();
  return new THREE.Vector2(
    ((ev.clientX - rect.left) / Math.max(1, rect.width)) * 2 - 1,
    -((ev.clientY - rect.top) / Math.max(1, rect.height)) * 2 + 1,
  );
}

function hitGround(
  raycaster: THREE.Raycaster,
  camera: THREE.Camera,
  mouse: THREE.Vector2,
  plane: THREE.Plane,
): THREE.Vector3 | null {
  raycaster.setFromCamera(mouse, camera);
  const out = new THREE.Vector3();
  return raycaster.ray.intersectPlane(plane, out) ? out : null;
}

function refreshHighlights(ctx: PickCtx, ids: Set<number>): void {
  const leftover = [...ctx.highlightGrp.children];
  const mats = new Set<THREE.Material>();
  for (const ch of leftover) {
    ctx.highlightGrp.remove(ch);
    const line = ch as THREE.LineSegments;
    line.geometry?.dispose();
    const mat = line.material;
    if (Array.isArray(mat)) mat.forEach((m) => mats.add(m));
    else if (mat) mats.add(mat);
  }
  mats.forEach((m) => m.dispose());
  if (ids.size === 0) return;
  const mat = new THREE.LineBasicMaterial({ color: SELECT_ACCENT });
  for (const mesh of ctx.buildingMeshes) {
    if (!ids.has(Number(mesh.userData.id))) continue;
    const edges = new THREE.LineSegments(new THREE.EdgesGeometry(mesh.geometry, 24), mat);
    edges.position.copy(mesh.position);
    edges.renderOrder = 9;
    ctx.highlightGrp.add(edges);
  }
}

function captureView(camera: THREE.OrthographicCamera, controls: OrbitControls): SavedView {
  return {
    px: camera.position.x,
    py: camera.position.y,
    pz: camera.position.z,
    tx: controls.target.x,
    ty: controls.target.y,
    tz: controls.target.z,
    ux: camera.up.x,
    uy: camera.up.y,
    uz: camera.up.z,
    zoom: camera.zoom,
  };
}

function applyTopDown(camera: THREE.OrthographicCamera, controls: OrbitControls): void {
  const t = controls.target;
  const dist = Math.max(120, camera.position.distanceTo(t));
  camera.up.set(0, 1, 0);
  camera.position.set(t.x, t.y + dist, t.z + dist * 0.0002);
  camera.zoom = SELECT_TOPDOWN_ZOOM;
  controls.minPolarAngle = 0;
  controls.maxPolarAngle = 0.002;
  camera.updateProjectionMatrix();
  controls.update();
}

function restoreOrbitView(
  camera: THREE.OrthographicCamera,
  controls: OrbitControls,
  saved: SavedView,
): void {
  camera.up.set(saved.ux, saved.uy, saved.uz);
  camera.position.set(saved.px, saved.py, saved.pz);
  controls.target.set(saved.tx, saved.ty, saved.tz);
  camera.zoom = saved.zoom;
  controls.minPolarAngle = 0;
  controls.maxPolarAngle = Math.PI / 2;
  camera.updateProjectionMatrix();
  controls.update();
}

function spinOrbitClockwise(
  camera: THREE.OrthographicCamera,
  controls: OrbitControls,
  deltaTheta: number,
  offset: THREE.Vector3,
  spherical: THREE.Spherical,
): void {
  offset.copy(camera.position).sub(controls.target);
  spherical.setFromVector3(offset);
  spherical.theta += deltaTheta;
  offset.setFromSpherical(spherical);
  camera.position.copy(controls.target).add(offset);
  camera.lookAt(controls.target);
}

function selectLiftY(pts: THREE.Vector3[], yAt: (x: number, z: number) => number): number {
  let y = 0;
  for (const p of pts) y = Math.max(y, yAt(p.x, p.z), 0);
  return y + SELECT_LIFT_M;
}

function liftedPts(pts: THREE.Vector3[], y: number): THREE.Vector3[] {
  return pts.map((p) => new THREE.Vector3(p.x, y, p.z));
}

function setLinePoints(line: THREE.Line, pts: THREE.Vector3[]): void {
  const geometry = new THREE.BufferGeometry();
  if (pts.length > 0) geometry.setFromPoints(pts);
  line.geometry.dispose();
  line.geometry = geometry;
  line.visible = pts.length >= 2;
}

function setBoxCorners(
  line: THREE.Line,
  a: THREE.Vector3,
  b: THREE.Vector3,
  yAt: (x: number, z: number) => number,
): void {
  const minX = Math.min(a.x, b.x);
  const maxX = Math.max(a.x, b.x);
  const minZ = Math.min(a.z, b.z);
  const maxZ = Math.max(a.z, b.z);
  const corners = [
    new THREE.Vector3(minX, 0, minZ),
    new THREE.Vector3(maxX, 0, minZ),
    new THREE.Vector3(maxX, 0, maxZ),
    new THREE.Vector3(minX, 0, maxZ),
  ];
  const y = selectLiftY(corners, yAt);
  setLinePoints(line, liftedPts([...corners, corners[0]], y));
}

function applySelectFill(mesh: THREE.Mesh, pts: THREE.Vector3[], y: number): void {
  mesh.geometry.dispose();
  if (pts.length < 3) {
    mesh.geometry = new THREE.BufferGeometry();
    mesh.visible = false;
    return;
  }
  const shape = new THREE.Shape();
  pts.forEach((p, i) => {
    if (i === 0) shape.moveTo(p.x, -p.z);
    else shape.lineTo(p.x, -p.z);
  });
  const geom = new THREE.ShapeGeometry(shape);
  geom.rotateX(-Math.PI / 2);
  geom.translate(0, y, 0);
  mesh.geometry = geom;
  mesh.visible = true;
}

function clearVertexGrp(grp: THREE.Group): void {
  const leftover = [...grp.children];
  for (const ch of leftover) {
    grp.remove(ch);
    const mesh = ch as THREE.Mesh;
    mesh.geometry?.dispose();
    const mat = mesh.material;
    if (Array.isArray(mat)) mat.forEach((m) => m.dispose());
    else if (mat) mat.dispose();
  }
}

function syncVertexMarkers(
  grp: THREE.Group,
  pts: THREE.Vector3[],
  y: number,
  closeable: boolean,
): void {
  clearVertexGrp(grp);
  pts.forEach((p, i) => {
    const first = i === 0;
    const radius = first && closeable ? 28 : first ? 16 : 9;
    const mesh = new THREE.Mesh(
      new THREE.SphereGeometry(radius, 14, 10),
      new THREE.MeshBasicMaterial({
        color: first ? 0xffffff : SELECT_ACCENT,
        depthTest: false,
        depthWrite: false,
        transparent: true,
        opacity: first && closeable ? 1 : 0.95,
      }),
    );
    mesh.position.set(p.x, y, p.z);
    mesh.renderOrder = 22;
    mesh.frustumCulled = false;
    mesh.userData.isStart = first;
    grp.add(mesh);
  });
}

function hideSelectOverlay(ctx: PickCtx): void {
  setLinePoints(ctx.boxLine, []);
  setLinePoints(ctx.hoverLine, []);
  ctx.selectFill.visible = false;
  clearVertexGrp(ctx.vertexGrp);
}

function drawWorldPolygon(
  ctx: PickCtx,
  pts: THREE.Vector3[],
  closed: boolean,
  hover: THREE.Vector3 | null,
): void {
  if (pts.length === 0) {
    hideSelectOverlay(ctx);
    return;
  }
  const y = selectLiftY(hover ? [...pts, hover] : pts, ctx.yAt);
  const outline = closed && pts.length >= 3 ? [...pts, pts[0]] : pts;
  setLinePoints(ctx.boxLine, liftedPts(outline, y));
  if (!closed && hover && pts.length >= 1) {
    setLinePoints(ctx.hoverLine, liftedPts([pts[pts.length - 1], hover], y));
  } else {
    setLinePoints(ctx.hoverLine, []);
  }
  applySelectFill(ctx.selectFill, closed || pts.length >= 3 ? pts : [], y);
  if (!closed && hover && pts.length >= 2) {
    applySelectFill(ctx.selectFill, [...pts, hover], y);
  }
  syncVertexMarkers(ctx.vertexGrp, pts, y, !closed && pts.length >= 3);
}

function showCenterPin(ctx: PickCtx, x: number, z: number): void {
  const group = ctx.centerPinGrp;
  group.clear();
  const y = ctx.yAt(x, z);
  const pin = buildSitePin(y);
  pin.scale.setScalar(4);
  pin.position.set(x, 0, z);
  const ring = new THREE.Mesh(
    new THREE.TorusGeometry(36, 2.2, 8, 48),
    new THREE.MeshBasicMaterial({ color: "#d61f26", depthTest: false }),
  );
  ring.rotation.x = Math.PI / 2;
  ring.position.set(x, y + 1.2, z);
  ring.renderOrder = 24;
  group.add(pin, ring);
}

function ringToWorldPts(ring: number[][], origin: [number, number]): THREE.Vector3[] {
  const closed =
    ring.length > 1 &&
    ring[0][0] === ring[ring.length - 1][0] &&
    ring[0][1] === ring[ring.length - 1][1];
  const src = closed ? ring.slice(0, -1) : ring;
  return src.map((c) => {
    const p = lonLatToXZ(c[0], c[1], origin);
    return new THREE.Vector3(p.x, 0, p.z);
  });
}

function worldPtsToRing(pts: THREE.Vector3[], origin: [number, number]): number[][] {
  const ring = pts.map((p) => {
    const ll = xzToLonLat(p.x, p.z, origin);
    return [ll.lng, ll.lat];
  });
  if (ring.length > 0) ring.push([ring[0][0], ring[0][1]]);
  return ring;
}

function drawSelectBox(ctx: PickCtx, ring: number[][] | null): void {
  if (!ring || ring.length < 3) {
    hideSelectOverlay(ctx);
    return;
  }
  drawWorldPolygon(ctx, ringToWorldPts(ring, ctx.origin), true, null);
}

function screenDistTo(
  camera: THREE.Camera,
  el: HTMLElement,
  world: THREE.Vector3,
  clientX: number,
  clientY: number,
): number {
  const v = world.clone().project(camera);
  const rect = el.getBoundingClientRect();
  const sx = rect.left + (v.x * 0.5 + 0.5) * rect.width;
  const sy = rect.top + (-v.y * 0.5 + 0.5) * rect.height;
  return Math.hypot(clientX - sx, clientY - sy);
}

function pointInPolyXZ(x: number, z: number, poly: THREE.Vector3[]): boolean {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const xi = poly[i].x;
    const zi = poly[i].z;
    const xj = poly[j].x;
    const zj = poly[j].z;
    const hit = zi > z !== zj > z && x < ((xj - xi) * (z - zi)) / (zj - zi + 1e-12) + xi;
    if (hit) inside = !inside;
  }
  return inside;
}

function orient2d(ax: number, az: number, bx: number, bz: number, cx: number, cz: number): number {
  return (bx - ax) * (cz - az) - (bz - az) * (cx - ax);
}

function segmentsCross(
  a: THREE.Vector3,
  b: THREE.Vector3,
  c: THREE.Vector3,
  d: THREE.Vector3,
): boolean {
  const o1 = orient2d(a.x, a.z, b.x, b.z, c.x, c.z);
  const o2 = orient2d(a.x, a.z, b.x, b.z, d.x, d.z);
  const o3 = orient2d(c.x, c.z, d.x, d.z, a.x, a.z);
  const o4 = orient2d(c.x, c.z, d.x, d.z, b.x, b.z);
  return o1 * o2 < 0 && o3 * o4 < 0;
}

function meshHitsPolygon(
  d: { minX: number; maxX: number; minZ: number; maxZ: number },
  poly: THREE.Vector3[],
): boolean {
  let minX = Infinity;
  let maxX = -Infinity;
  let minZ = Infinity;
  let maxZ = -Infinity;
  for (const p of poly) {
    minX = Math.min(minX, p.x);
    maxX = Math.max(maxX, p.x);
    minZ = Math.min(minZ, p.z);
    maxZ = Math.max(maxZ, p.z);
  }
  if (d.maxX < minX || d.minX > maxX || d.maxZ < minZ || d.minZ > maxZ) return false;
  const corners = [
    [d.minX, d.minZ],
    [d.maxX, d.minZ],
    [d.maxX, d.maxZ],
    [d.minX, d.maxZ],
  ];
  const cx = (d.minX + d.maxX) / 2;
  const cz = (d.minZ + d.maxZ) / 2;
  if (pointInPolyXZ(cx, cz, poly)) return true;
  for (const [x, z] of corners) {
    if (pointInPolyXZ(x, z, poly)) return true;
  }
  for (const p of poly) {
    if (p.x >= d.minX && p.x <= d.maxX && p.z >= d.minZ && p.z <= d.maxZ) return true;
  }
  const boxEdges: [THREE.Vector3, THREE.Vector3][] = [
    [new THREE.Vector3(d.minX, 0, d.minZ), new THREE.Vector3(d.maxX, 0, d.minZ)],
    [new THREE.Vector3(d.maxX, 0, d.minZ), new THREE.Vector3(d.maxX, 0, d.maxZ)],
    [new THREE.Vector3(d.maxX, 0, d.maxZ), new THREE.Vector3(d.minX, 0, d.maxZ)],
    [new THREE.Vector3(d.minX, 0, d.maxZ), new THREE.Vector3(d.minX, 0, d.minZ)],
  ];
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    for (const [a, b] of boxEdges) {
      if (segmentsCross(a, b, poly[j], poly[i])) return true;
    }
  }
  return false;
}

function meshesInBox(meshes: THREE.Mesh[], a: THREE.Vector3, b: THREE.Vector3): number[] {
  const minX = Math.min(a.x, b.x);
  const maxX = Math.max(a.x, b.x);
  const minZ = Math.min(a.z, b.z);
  const maxZ = Math.max(a.z, b.z);
  const ids: number[] = [];
  for (const mesh of meshes) {
    const d = mesh.userData as { id?: number; minX: number; maxX: number; minZ: number; maxZ: number };
    if (d.maxX < minX || d.minX > maxX || d.maxZ < minZ || d.minZ > maxZ) continue;
    const id = Number(d.id);
    if (Number.isFinite(id)) ids.push(id);
  }
  return ids;
}

function meshesInPolygon(meshes: THREE.Mesh[], poly: THREE.Vector3[]): number[] {
  const ids: number[] = [];
  for (const mesh of meshes) {
    const d = mesh.userData as { id?: number; minX: number; maxX: number; minZ: number; maxZ: number };
    if (!meshHitsPolygon(d, poly)) continue;
    const id = Number(d.id);
    if (Number.isFinite(id)) ids.push(id);
  }
  return ids;
}

function ageIndex(year: unknown): number | null {
  const y = typeof year === "number" ? year : Number(year);
  if (!y || y < 1000) return null;
  const i = AGE_LIMITS.findIndex((lim) => y < lim);
  return i < 0 ? AGE_LIMITS.length - 1 : i;
}

function heightIndex(h: number): number {
  const i = HEIGHT_LIMITS.findIndex((lim) => h <= lim);
  return i < 0 ? HEIGHT_LIMITS.length - 1 : i;
}

/** 360° 한 바퀴 주기(초). 양수 theta = 위에서 본 모델 시계방향. */
const MASS_SPIN_PERIOD_S = 24;
const MASS_SPIN_RAD_PER_S = (Math.PI * 2) / MASS_SPIN_PERIOD_S;
const DEFAULT_ORTHO_ZOOM = MASS_ORTHO_DEFAULT_ZOOM;
const SELECT_TOPDOWN_ZOOM_OUT_NOTCHES = 8;
const MASS_LOAD_LINES = [
  "지형을 다듬는 중....",
  "도로를 그리는 중...",
  "건축물을 그리는 중....",
];
const SELECT_TOPDOWN_ZOOM =
  DEFAULT_ORTHO_ZOOM * Math.pow(ORBIT_WHEEL_ZOOM_SCALE, SELECT_TOPDOWN_ZOOM_OUT_NOTCHES);

export function MassViewer({
  data,
  lat,
  lng,
  radiusM,
  shape,
  includeAdjacent,
  adjacentBusy,
  onIncludeAdjacentChange,
  mode = "view",
}: Props) {
  const simTab = mode === "sim";
  const mountRef = useRef<HTMLDivElement | null>(null);
  const sceneRef = useRef<THREE.Scene | null>(null);
  const pickCtxRef = useRef<PickCtx | null>(null);
  const selectModeRef = useRef(false);
  const floodRef = useRef<FloodJob | null>(null);
  const growthRef = useRef<GrowthJob | null>(null);
  const floodGrpRef = useRef<THREE.Group | null>(null);
  const selectedIdsRef = useRef<Set<number>>(new Set());
  const selectBoxRef = useRef<number[][] | null>(null);
  const draftPtsRef = useRef<THREE.Vector3[]>([]);
  const savedViewRef = useRef<SavedView | null>(null);
  const [colorMode, setColorMode] = useState<ColorMode>("use");
  const [exaggerationPct, setExaggerationPct] = useState(100);
  const [showFloor, setShowFloor] = useState(true);
  const [showRoads, setShowRoads] = useState(true);
  const [showGreen, setShowGreen] = useState(true);
  const [showTerrain, setShowTerrain] = useState(true);
  const [showSite, setShowSite] = useState(true);
  const [ghost, setGhost] = useState(true);
  const [ghostOpacity, setGhostOpacity] = useState(20);
  const [viewMode, setViewMode] = useState<ViewMode>("complete");
  const [explodeGapPct, setExplodeGapPct] = useState(
    Math.round(DEFAULT_MASS_STYLE.explode_gap_ratio * 100),
  );
  const [useColors, setUseColors] = useState<Record<string, string>>({ ...USE_COLORS });
  const [solidColor, setSolidColor] = useState(SOLID_3D);
  const [ageBase, setAgeBase] = useState("#b34a35");
  const [heightBase, setHeightBase] = useState("#3b82f6");
  const [ageRamp, setAgeRamp] = useState<string[]>([...AGE_RAMP]);
  const [heightRamp, setHeightRamp] = useState<string[]>([...HEIGHT_RAMP]);
  const [off, setOff] = useState<Record<ColorMode, string[]>>({
    use: [],
    age: [],
    height: [],
    solid: [],
  });
  const [selectMode, setSelectMode] = useState(false);
  const [spinning, setSpinning] = useState(false);
  const spinningRef = useRef(false);
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  const [selectBox, setSelectBox] = useState<number[][] | null>(null);
  const [draftCount, setDraftCount] = useState(0);
  const [hideIds, setHideIds] = useState<number[]>([]);
  const [colorById, setColorById] = useState<Record<number, string>>({});
  const [simBuildings, setSimBuildings] = useState<Feature[]>([]);
  const [simPrompt, setSimPrompt] = useState("");
  const [simBusy, setSimBusy] = useState(false);
  const [simError, setSimError] = useState("");
  const [simHistory, setSimHistory] = useState<SimChatTurn[]>([]);
  const [simPartial, setSimPartial] = useState<Record<string, unknown> | null>(null);
  const [simChat, setSimChat] = useState<SimChatTurn[]>([]);
  const [briefMd, setBriefMd] = useState<string | null>(null);
  const [briefOpen, setBriefOpen] = useState(false);
  const [briefCopied, setBriefCopied] = useState(false);
  const [guideOpen, setGuideOpen] = useState(false);
  const [copiedPrompt, setCopiedPrompt] = useState<string | null>(null);
  const [growthHud, setGrowthHud] = useState<{
    start: number;
    end: number;
    year: number;
    playing: boolean;
  } | null>(null);
  const growthHudSync = useRef<(year: number, playing: boolean) => void>(() => {});
  growthHudSync.current = (year, playing) => {
    setGrowthHud((prev) => {
      if (!prev) return prev;
      const shown = Math.min(prev.end, Math.max(prev.start, Math.round(year)));
      if (prev.year === shown && prev.playing === playing) return prev;
      return { ...prev, year: shown, playing };
    });
  };
  const [simAskHint, setSimAskHint] = useState("");
  const [simImage, setSimImage] = useState<SimImage | null>(null);
  const [simSession, setSimSession] = useState<"grid" | "image_mass">("grid");
  const [heightById, setHeightById] = useState<Record<number, number>>({});
  const [sceneReady, setSceneReady] = useState(false);
  const [loadLine, setLoadLine] = useState(0);
  const [pickKind, setPickKind] = useState<"area" | "pin">("area");
  const [pinNotice, setPinNotice] = useState(false);
  const [centerPin, setCenterPin] = useState<{ lng: number; lat: number } | null>(null);
  const pickKindRef = useRef(pickKind);
  const centerPinRef = useRef(centerPin);
  pickKindRef.current = pickKind;
  centerPinRef.current = centerPin;
  const loading3d = Boolean(adjacentBusy) || data == null || !sceneReady;
  const imageInputRef = useRef<HTMLInputElement | null>(null);

  const buildings = useMemo(
    () => data?.buildings?.features || data?.features || [],
    [data],
  );
  const roads = data?.roads?.features || [];
  const green = data?.green?.features || [];
  const water = data?.water?.features || [];
  const terrain = data?.terrain ?? null;
  const style = useMemo(() => mergeStyle(data?.style), [data]);
  const exaggeration = exaggerationPct / 100;
  const explodeGapRatio = explodeGapPct / 100;

  const rows: PalRow[] = useMemo(() => {
    if (colorMode === "solid") {
      return [{ key: "one", label: "단색", color: solidColor, edit: true }];
    }
    if (colorMode === "use") {
      return Object.keys(useColors).map((k) => ({
        key: k,
        label: k,
        color: useColors[k],
        edit: true,
      }));
    }
    if (colorMode === "age") {
      return [
        ...AGE_LABELS.map((lb, i) => ({
          key: String(i),
          label: lb,
          color: ageRamp[i],
          edit: false,
        })),
        { key: "미상", label: "미상", color: AGE_UNKNOWN_3D, edit: true },
      ];
    }
    return HEIGHT_LABELS.map((lb, i) => ({
      key: String(i),
      label: lb,
      color: heightRamp[i],
      edit: false,
    }));
  }, [colorMode, useColors, solidColor, ageRamp, heightRamp]);

  const offSet = useMemo(() => new Set(off[colorMode]), [off, colorMode]);

  useEffect(() => {
    spinningRef.current = spinning;
  }, [spinning]);

  const catOf = (f: Feature): { key: string; color: string } => {
    if (colorMode === "solid") return { key: "one", color: solidColor };
    if (colorMode === "use") {
      const raw = String(f.properties.use_cat || "미상");
      const k = useColors[raw] ? raw : "미상";
      return { key: k, color: useColors[k] || AGE_UNKNOWN_3D };
    }
    if (colorMode === "age") {
      const idx = ageIndex(f.properties.year);
      if (idx == null) return { key: "미상", color: AGE_UNKNOWN_3D };
      return { key: String(idx), color: ageRamp[idx] };
    }
    const h = Number(f.properties.height_m) || 0;
    const idx = heightIndex(h);
    return { key: String(idx), color: heightRamp[idx] };
  };

  useEffect(() => {
    if (!loading3d) return;
    setLoadLine(0);
    const timer = window.setInterval(() => {
      setLoadLine((index) => (index + 1) % MASS_LOAD_LINES.length);
    }, 900);
    return () => window.clearInterval(timer);
  }, [loading3d]);

  useEffect(() => {
    const el = mountRef.current;
    if (!el) return;

    const w = el.clientWidth || 640;
    const h = el.clientHeight || 460;
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(style.bg);
    sceneRef.current = scene;
    const explode = viewMode === "explode";
    const gap = explode ? Math.max(80, radiusM * explodeGapRatio) : 0;
    const stackH = gap * 4;
    const viewSize = explode ? radiusM * 5.6 : radiusM * 2.35;
    const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, -8000, 16000);
    applyOrthoFrustum(camera, w, h, viewSize);
    camera.zoom = DEFAULT_ORTHO_ZOOM;
    camera.updateProjectionMatrix();
    if (explode) {
      camera.position.set(
        radiusM * 2.4,
        stackH * 0.92 + radiusM * 1.1,
        radiusM * 2.4,
      );
    } else {
      const dist = radiusM * 2.2;
      camera.position.set(dist * 0.95, dist * 0.72, dist * 0.95);
    }
    const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
    renderer.setSize(w, h);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.toneMapping = THREE.NoToneMapping;
    el.innerHTML = "";
    el.appendChild(renderer.domElement);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.minPolarAngle = 0;
    controls.maxPolarAngle = Math.PI / 2;
    controls.minZoom = 0.35;
    controls.maxZoom = 12;
    controls.target.set(0, explode ? stackH * 0.48 : 0, 0);
    scene.add(new THREE.AmbientLight(0xffffff, 0.58));
    scene.add(new THREE.HemisphereLight(0xffffff, 0xcdd0d4, 0.18));
    const key = new THREE.DirectionalLight(0xffffff, 0.55);
    key.position.set(radiusM * 0.85, radiusM * 1.15, radiusM * 0.32);
    scene.add(key);
    const fill = new THREE.DirectionalLight(0xffffff, 0.12);
    fill.position.set(-radiusM * 0.55, radiusM * 0.35, -radiusM * 0.7);
    scene.add(fill);

    const origin: [number, number] = [lng, lat];
    const root = new THREE.Group();
    scene.add(root);

    const world = buildMassWorld({
      data,
      lat,
      lng,
      radiusM,
      shape,
      showFloor,
      showRoads,
      showGreen,
      showTerrain,
      showSite,
      exaggeration,
      colorOf: catOf,
      offSet: colorMode === "solid" ? new Set() : offSet,
      ghost,
      ghostOpacity,
      hideIds,
      simBuildings,
      heightById,
      colorById,
    });
    const { yAt, terrainGrp, greenGrp, roadGrp, bldgGrp, pin, buildingMeshes } = world;
    const floorColor = world.style.floor;

    const combined = explode ? new THREE.Group() : assembleCompleteMass(world);
    if (explode) {
      if (world.cake) combined.add(world.cake);
      combined.add(terrainGrp.clone(true));
      combined.add(roadGrp.clone(true));
      combined.add(bldgGrp.clone(true));
      if (pin) combined.add(pin.clone(true));
    }
    root.add(combined);

    if (explode) {
      const yTerrain = gap;
      const yGreen = gap * 2;
      const yRoads = gap * 3;
      const yBldg = gap * 4;

      const terrainLayer = new THREE.Group();
      terrainLayer.position.y = yTerrain;
      terrainLayer.add(
        buildCakeBase(
          radiusM,
          shape,
          style.terrain_layer_h_m,
          yAt,
          floorColor,
          "#e0e0e0",
        ),
      );
      terrainLayer.add(terrainGrp);
      if (pin) terrainLayer.add(pin);
      root.add(terrainLayer);

      const greenLayer = new THREE.Group();
      greenLayer.position.y = yGreen;
      greenLayer.add(greenGrp);
      root.add(greenLayer);

      const roadLayer = new THREE.Group();
      roadLayer.position.y = yRoads;
      roadLayer.add(roadGrp);
      root.add(roadLayer);

      const bldgLayer = new THREE.Group();
      bldgLayer.position.y = yBldg;
      bldgLayer.add(bldgGrp);
      root.add(bldgLayer);

      root.add(
        buildExplodeGuides(radiusM, shape, -style.base_h_m, yBldg + 24),
      );
    }

    let frame = 0;
    const clock = new THREE.Clock();
    const spinOffset = new THREE.Vector3();
    const spinSpherical = new THREE.Spherical();
    let orbitBusy = false;
    const onOrbitStart = () => {
      orbitBusy = true;
    };
    const onOrbitEnd = () => {
      orbitBusy = false;
    };
    controls.addEventListener("start", onOrbitStart);
    controls.addEventListener("end", onOrbitEnd);
    const floodGrp = new THREE.Group();
    floodGrp.renderOrder = 8;
    scene.add(floodGrp);
    floodGrpRef.current = floodGrp;
    const tick = () => {
      frame = requestAnimationFrame(tick);
      const dt = Math.min(0.05, clock.getDelta());
      controls.update();
      if (spinningRef.current && !orbitBusy && !selectModeRef.current) {
        spinOrbitClockwise(
          camera,
          controls,
          MASS_SPIN_RAD_PER_S * dt,
          spinOffset,
          spinSpherical,
        );
      }
      applyFloodFrame(floodGrp, floodRef.current, yAt, performance.now(), buildingMeshes);
      const shown = applyGrowthFrame(buildingMeshes, growthRef.current, performance.now());
      if (growthRef.current && shown != null) {
        growthHudSync.current(shown, growthRef.current.playing);
      }
      renderer.render(scene, camera);
    };
    tick();

    const onResize = () => {
      const nw = el.clientWidth || 640;
      const nh = el.clientHeight || 460;
      applyOrthoFrustum(camera, nw, nh, viewSize);
      renderer.setSize(nw, nh);
    };
    window.addEventListener("resize", onResize);
    const ro = new ResizeObserver(onResize);
    ro.observe(el);
    onResize();

    const highlightGrp = new THREE.Group();
    highlightGrp.renderOrder = 9;
    scene.add(highlightGrp);
    const selectLineMat = new THREE.LineBasicMaterial({
      color: SELECT_ACCENT,
      depthTest: false,
      depthWrite: false,
      transparent: true,
    });
    const boxLine = new THREE.Line(new THREE.BufferGeometry(), selectLineMat);
    boxLine.visible = false;
    boxLine.renderOrder = 20;
    boxLine.frustumCulled = false;
    const hoverLine = new THREE.Line(
      new THREE.BufferGeometry(),
      new THREE.LineBasicMaterial({
        color: SELECT_ACCENT,
        depthTest: false,
        depthWrite: false,
        transparent: true,
        opacity: 0.7,
      }),
    );
    hoverLine.visible = false;
    hoverLine.renderOrder = 20;
    hoverLine.frustumCulled = false;
    const selectFill = new THREE.Mesh(
      new THREE.BufferGeometry(),
      new THREE.MeshBasicMaterial({
        color: SELECT_ACCENT,
        transparent: true,
        opacity: 0.16,
        depthTest: false,
        depthWrite: false,
        side: THREE.DoubleSide,
      }),
    );
    selectFill.visible = false;
    selectFill.renderOrder = 19;
    selectFill.frustumCulled = false;
    const vertexGrp = new THREE.Group();
    vertexGrp.renderOrder = 22;
    scene.add(selectFill);
    scene.add(boxLine);
    scene.add(hoverLine);
    scene.add(vertexGrp);
    const ctx: PickCtx = {
      camera,
      renderer,
      controls,
      buildingMeshes,
      origin,
      highlightGrp,
      boxLine,
      hoverLine,
      selectFill,
      vertexGrp,
      raycaster: new THREE.Raycaster(),
      ground: new THREE.Plane(new THREE.Vector3(0, 1, 0), 0),
      yAt,
      centerPinGrp: new THREE.Group(),
    };
    scene.add(ctx.centerPinGrp);
    const savedPin = centerPinRef.current;
    if (savedPin) {
      const placed = lonLatToXZ(savedPin.lng, savedPin.lat, origin);
      showCenterPin(ctx, placed.x, placed.z);
    }
    pickCtxRef.current = ctx;
    controls.enableRotate = explode || !selectModeRef.current;
    renderer.domElement.style.cursor = explode || !selectModeRef.current ? "" : "crosshair";
    if (!explode && selectModeRef.current) {
      applyTopDown(camera, controls);
    }
    refreshHighlights(ctx, selectedIdsRef.current);
    if (draftPtsRef.current.length > 0) {
      drawWorldPolygon(ctx, draftPtsRef.current, false, null);
    } else {
      drawSelectBox(ctx, selectBoxRef.current);
    }

    const drag = {
      down: false,
      boxing: false,
      sx: 0,
      sy: 0,
      start: null as THREE.Vector3 | null,
    };

    const groundAt = (ev: PointerEvent): THREE.Vector3 | null => {
      const mouse = ndcFromPointer(ev, renderer.domElement);
      return hitGround(ctx.raycaster, camera, mouse, ctx.ground);
    };

    const redrawDraft = (hover: THREE.Vector3 | null) => {
      drawWorldPolygon(ctx, draftPtsRef.current, false, hover);
    };

    const cancelDraft = () => {
      draftPtsRef.current = [];
      setDraftCount(0);
      if (selectBoxRef.current) drawSelectBox(ctx, selectBoxRef.current);
      else hideSelectOverlay(ctx);
    };

    const closeDraft = (add: boolean) => {
      const pts = draftPtsRef.current;
      if (pts.length < 3) return;
      const hitIds = meshesInPolygon(ctx.buildingMeshes, pts);
      const ring = worldPtsToRing(pts, origin);
      draftPtsRef.current = [];
      setDraftCount(0);
      setSelectBox(ring);
      setSelectedIds((prev) => {
        const next = new Set(add ? prev : []);
        for (const id of hitIds) next.add(id);
        return [...next];
      });
      drawWorldPolygon(ctx, pts, true, null);
    };

    const onPointerDown = (ev: PointerEvent) => {
      if (explode || !selectModeRef.current || ev.button !== 0) return;
      drag.down = true;
      drag.boxing = false;
      drag.sx = ev.clientX;
      drag.sy = ev.clientY;
      drag.start = pickKindRef.current === "pin" ? null : groundAt(ev);
      renderer.domElement.setPointerCapture(ev.pointerId);
    };

    const onPointerMove = (ev: PointerEvent) => {
      if (explode || !selectModeRef.current || pickKindRef.current === "pin") return;
      const hover = groundAt(ev);
      if (draftPtsRef.current.length > 0) {
        if (drag.down && drag.start) {
          const dist = Math.hypot(ev.clientX - drag.sx, ev.clientY - drag.sy);
          if (dist >= DRAG_PX) drag.boxing = true;
        }
        redrawDraft(hover);
        return;
      }
      if (!drag.down || !drag.start) return;
      const dist = Math.hypot(ev.clientX - drag.sx, ev.clientY - drag.sy);
      if (dist < DRAG_PX && !drag.boxing) return;
      drag.boxing = true;
      ctx.selectFill.visible = false;
      clearVertexGrp(ctx.vertexGrp);
      ctx.hoverLine.visible = false;
      if (hover) setBoxCorners(boxLine, drag.start, hover, ctx.yAt);
    };

    const onPointerUp = (ev: PointerEvent) => {
      if (!drag.down) return;
      drag.down = false;
      try {
        renderer.domElement.releasePointerCapture(ev.pointerId);
      } catch {
        /* already released */
      }
      if (explode || !selectModeRef.current) {
        hideSelectOverlay(ctx);
        return;
      }
      if (pickKindRef.current === "pin") {
        const dist = Math.hypot(ev.clientX - drag.sx, ev.clientY - drag.sy);
        if (dist >= DRAG_PX) return;
        const hit = groundAt(ev);
        if (!hit) return;
        const ll = xzToLonLat(hit.x, hit.z, origin);
        hideSelectOverlay(ctx);
        draftPtsRef.current = [];
        setDraftCount(0);
        setSelectBox(null);
        setSelectedIds([]);
        setCenterPin({ lng: ll.lng, lat: ll.lat });
        showCenterPin(ctx, hit.x, hit.z);
        return;
      }
      const add = ev.shiftKey;
      const drafting = draftPtsRef.current.length > 0;
      if (!drafting && drag.boxing && drag.start) {
        const end = groundAt(ev);
        if (!end) {
          hideSelectOverlay(ctx);
          return;
        }
        const hitIds = meshesInBox(ctx.buildingMeshes, drag.start, end);
        setBoxCorners(boxLine, drag.start, end, ctx.yAt);
        const c1 = xzToLonLat(Math.min(drag.start.x, end.x), Math.min(drag.start.z, end.z), origin);
        const c2 = xzToLonLat(Math.max(drag.start.x, end.x), Math.min(drag.start.z, end.z), origin);
        const c3 = xzToLonLat(Math.max(drag.start.x, end.x), Math.max(drag.start.z, end.z), origin);
        const c4 = xzToLonLat(Math.min(drag.start.x, end.x), Math.max(drag.start.z, end.z), origin);
        const ring = [
          [c1.lng, c1.lat],
          [c2.lng, c2.lat],
          [c3.lng, c3.lat],
          [c4.lng, c4.lat],
          [c1.lng, c1.lat],
        ];
        setSelectBox(ring);
        setSelectedIds((prev) => {
          const next = new Set(add ? prev : []);
          for (const id of hitIds) next.add(id);
          return [...next];
        });
        return;
      }
      if (drag.boxing && drafting) {
        redrawDraft(groundAt(ev));
        return;
      }
      const hitPt = groundAt(ev);
      const first = draftPtsRef.current[0];
      const mouse = ndcFromPointer(ev, renderer.domElement);
      ctx.raycaster.setFromCamera(mouse, camera);
      const startHit = ctx.raycaster
        .intersectObjects(ctx.vertexGrp.children, false)
        .some((h) => Boolean(h.object.userData.isStart));
      const nearStart =
        Boolean(first) &&
        (screenDistTo(camera, renderer.domElement, first, ev.clientX, ev.clientY) <=
          POLY_CLOSE_PX ||
          (hitPt !== null && first.distanceTo(hitPt) <= POLY_CLOSE_M) ||
          startHit);
      if (first && draftPtsRef.current.length >= 3 && nearStart) {
        closeDraft(add);
        return;
      }
      if (!drafting) {
        const hits = ctx.raycaster.intersectObjects(ctx.buildingMeshes, false);
        const id = hits[0] ? Number(hits[0].object.userData.id) : NaN;
        if (Number.isFinite(id) && id !== 0) {
          if (!ev.shiftKey) setSelectBox(null);
          hideSelectOverlay(ctx);
          setSelectedIds((prev) => {
            if (ev.shiftKey) {
              const next = new Set(prev);
              if (next.has(id)) next.delete(id);
              else next.add(id);
              return [...next];
            }
            if (prev.length === 1 && prev[0] === id) return [];
            return [id];
          });
          return;
        }
      }
      if (!hitPt) return;
      if (!drafting && !add) {
        setSelectBox(null);
        setSelectedIds([]);
      }
      draftPtsRef.current = [...draftPtsRef.current, hitPt.clone()];
      setDraftCount(draftPtsRef.current.length);
      redrawDraft(null);
    };

    const onContextMenu = (ev: MouseEvent) => {
      if (explode || !selectModeRef.current || draftPtsRef.current.length === 0) return;
      ev.preventDefault();
      draftPtsRef.current = draftPtsRef.current.slice(0, -1);
      setDraftCount(draftPtsRef.current.length);
      if (draftPtsRef.current.length === 0) cancelDraft();
      else redrawDraft(null);
    };

    const canvas = renderer.domElement;
    canvas.addEventListener("pointerdown", onPointerDown);
    canvas.addEventListener("pointermove", onPointerMove);
    canvas.addEventListener("pointerup", onPointerUp);
    canvas.addEventListener("pointercancel", onPointerUp);
    canvas.addEventListener("contextmenu", onContextMenu);
    if (data) setSceneReady(true);

    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("resize", onResize);
      ro.disconnect();
      canvas.removeEventListener("pointerdown", onPointerDown);
      canvas.removeEventListener("pointermove", onPointerMove);
      canvas.removeEventListener("pointerup", onPointerUp);
      canvas.removeEventListener("pointercancel", onPointerUp);
      canvas.removeEventListener("contextmenu", onContextMenu);
      controls.removeEventListener("start", onOrbitStart);
      controls.removeEventListener("end", onOrbitEnd);
      pickCtxRef.current = null;
      floodGrpRef.current = null;
      scene.traverse((obj) => {
        const mesh = obj as THREE.Mesh;
        if (mesh.geometry) mesh.geometry.dispose();
        const mat = (mesh as THREE.Mesh).material;
        if (Array.isArray(mat)) mat.forEach((m) => m.dispose());
        else if (mat) mat.dispose();
      });
      renderer.dispose();
      el.innerHTML = "";
      sceneRef.current = null;
    };
    // catOf closes over palette state used in the effect body
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    buildings,
    roads,
    green,
    water,
    terrain,
    style,
    colorMode,
    exaggeration,
    showFloor,
    showRoads,
    showGreen,
    showTerrain,
    showSite,
    ghost,
    ghostOpacity,
    viewMode,
    explodeGapRatio,
    lat,
    lng,
    radiusM,
    shape,
    useColors,
    solidColor,
    ageRamp,
    heightRamp,
    offSet,
    hideIds,
    simBuildings,
    heightById,
    colorById,
  ]);

  useEffect(() => {
    selectBoxRef.current = selectBox;
    const ctx = pickCtxRef.current;
    if (!ctx || draftPtsRef.current.length > 0) return;
    drawSelectBox(ctx, selectBox);
  }, [selectBox]);

  useEffect(() => {
    selectModeRef.current = selectMode;
    const ctx = pickCtxRef.current;
    if (!ctx) return;
    const explode = viewMode === "explode";
    ctx.controls.enableRotate = explode || !selectMode;
    ctx.renderer.domElement.style.cursor = explode || !selectMode ? "" : "crosshair";
    if (explode) {
      savedViewRef.current = null;
      draftPtsRef.current = [];
      setDraftCount(0);
      hideSelectOverlay(ctx);
      if (selectMode) setSelectMode(false);
      return;
    }
    if (!selectMode) {
      draftPtsRef.current = [];
      setDraftCount(0);
      if (selectBoxRef.current) drawSelectBox(ctx, selectBoxRef.current);
      else hideSelectOverlay(ctx);
    }
    if (selectMode) {
      setSpinning(false);
      if (!savedViewRef.current) {
        savedViewRef.current = captureView(ctx.camera, ctx.controls);
      }
      applyTopDown(ctx.camera, ctx.controls);
    } else if (savedViewRef.current) {
      restoreOrbitView(ctx.camera, ctx.controls, savedViewRef.current);
      savedViewRef.current = null;
    }
  }, [selectMode, viewMode]);

  useEffect(() => {
    if (!selectMode) return;
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key !== "Escape") return;
      draftPtsRef.current = [];
      setDraftCount(0);
      const ctx = pickCtxRef.current;
      if (!ctx) return;
      if (selectBoxRef.current) drawSelectBox(ctx, selectBoxRef.current);
      else hideSelectOverlay(ctx);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selectMode]);

  useEffect(() => {
    selectedIdsRef.current = new Set(selectedIds);
    const ctx = pickCtxRef.current;
    if (ctx) refreshHighlights(ctx, selectedIdsRef.current);
  }, [selectedIds]);

  const selectedAreaM2 = useMemo(() => {
    const ctx = pickCtxRef.current;
    if (!ctx || selectedIds.length === 0) return 0;
    const ids = new Set(selectedIds);
    let area = 0;
    for (const mesh of ctx.buildingMeshes) {
      if (!ids.has(Number(mesh.userData.id))) continue;
      const d = mesh.userData as { minX: number; maxX: number; minZ: number; maxZ: number };
      area += Math.max(0, d.maxX - d.minX) * Math.max(0, d.maxZ - d.minZ);
    }
    return area;
  }, [selectedIds]);

  const clearSelection = () => {
    draftPtsRef.current = [];
    setDraftCount(0);
    setSelectedIds([]);
    setSelectBox(null);
    setCenterPin(null);
    const ctx = pickCtxRef.current;
    if (ctx) {
      hideSelectOverlay(ctx);
      ctx.centerPinGrp.clear();
    }
  };

  const resetSim = () => {
    setHideIds([]);
    setColorById({});
    setSimBuildings([]);
    setSimError("");
    setSimHistory([]);
    setSimPartial(null);
    setSimChat([]);
    setBriefMd(null);
    setBriefOpen(false);
    setSimAskHint("");
    setSimImage(null);
    setSimSession("grid");
    setHeightById({});
    floodRef.current = null;
    growthRef.current = null;
    setGrowthHud(null);
    clearSelection();
  };

  const clearPlay = () => {
    floodRef.current = null;
    growthRef.current = null;
    setGrowthHud(null);
    setHideIds([]);
    setColorById({});
    setHeightById({});
  };

  const applyAnalysis = (out: AnalysisResult, userText: string) => {
    const userTurn: SimChatTurn = { role: "user", content: userText };
    if (out.action === "brief" && out.status === "ok" && out.reply_ko) {
      setBriefMd(out.reply_ko);
      setBriefOpen(true);
      setSimChat((prev) => [
        ...prev,
        userTurn,
        { role: "assistant", content: "브리핑을 작성했습니다." },
      ]);
      return;
    }
    const botTurn: SimChatTurn = { role: "assistant", content: out.reply_ko || "" };
    setSimChat((prev) => [...prev, userTurn, botTurn]);
    if (out.action === "flood" && out.rainfall_mm) {
      const plan = planFlood({
        terrain: data?.terrain,
        origin: [lng, lat],
        radiusM,
        shape,
        rainfallMm: out.rainfall_mm,
        exag: Number(style?.terrain_exag) || 1,
        boxLl: selectBox,
      });
      growthRef.current = null;
      setGrowthHud(null);
      if (!plan) {
        floodRef.current = null;
        setSimChat((prev) => [
          ...prev,
          { role: "assistant", content: "지형(DEM)이 없어 침수 물을 올리지 못했습니다." },
        ]);
        return;
      }
      floodRef.current = {
        token: Date.now(),
        started: performance.now(),
        durationMs: 7000,
        plan,
      };
      return;
    }
    if (out.action === "timelapse" && out.start_year) {
      const nowYear = new Date().getFullYear();
      const startYear = out.start_year;
      const endYear = Math.max(nowYear, startYear);
      floodRef.current = null;
      growthRef.current = {
        token: Date.now(),
        startYear,
        endYear,
        playYear: startYear - 1,
        playing: true,
        lastTick: performance.now(),
        yearPerMs: Math.max(1, endYear - startYear + 1) / 14000,
      };
      setGrowthHud({ start: startYear, end: endYear, year: startYear, playing: true });
      return;
    }
    if (out.action === "edit") {
      if (out.updates?.length) {
        setHeightById((prev) => {
          const next = { ...prev };
          for (const update of out.updates || []) next[update.id] = update.height_m;
          return next;
        });
      }
      if (out.hide_ids?.length) {
        setHideIds((prev) => mergeHideIds(prev, out.hide_ids || []));
      }
      return;
    }
    if (Array.isArray(out.hide_ids)) setHideIds(out.hide_ids);
    if (out.action === "filter" || out.action === "scenario") {
      setColorById(colorsFromApi(out.colors));
    }
  };

  const runAnalysis = async (text: string, action: "auto" | "brief" = "auto") => {
    const userText = text.trim();
    if (!userText || simBusy) return;
    setSimBusy(true);
    setSimError("");
    try {
      const out = await askAnalysis({
        prompt: userText,
        action,
        lat,
        lng,
        r: radiusM,
        shape,
        includeAdjacent,
        buildingIds: selectedIds.filter((id) => id > 0),
        box: selectBox,
      });
      applyAnalysis(out, userText);
      if (action === "auto") setSimPrompt("");
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setSimError(message);
      setSimChat((prev) => [...prev, { role: "assistant", content: message }]);
    } finally {
      setSimBusy(false);
    }
  };

  const runSim = async () => {
    const prompt = simPrompt.trim();
    const hasImage = Boolean(simImage);
    if (!prompt && !hasImage) return;
    const userText = prompt || "이 이미지처럼 구성해줘";
    if (draftCount > 0) {
      const message = "영역을 닫은 뒤 보내 주세요. 시작점(흰 점)을 누르면 닫힙니다.";
      setSimError(message);
      setSimPrompt("");
      setSimChat((prev) => [
        ...prev,
        { role: "user", content: userText },
        { role: "assistant", content: message },
      ]);
      return;
    }
    const pinOnly = selectedIds.length === 0 && !selectBox;
    if (pinOnly && !centerPin) {
      const message = "도시 시뮬레이터로 건물, 영역, 또는 중심 핀을 먼저 선택하세요.";
      setSimError(message);
      setSimPrompt("");
      setSimChat((prev) => [
        ...prev,
        { role: "user", content: userText },
        { role: "assistant", content: message },
      ]);
      return;
    }
    setSimBusy(true);
    setSimError("");
    try {
      let box = selectBox;
      let buildingIds = selectedIds.filter((id) => id > 0);
      let pinNote = "";
      if (pinOnly && centerPin) {
        const stated = statedSiteWidthM(userText);
        const longer = stated ?? SIM_PIN_SITE_M;
        const aspect = hasImage && simImage ? await previewAspect(simImage.preview) : 1;
        const widthM = aspect >= 1 ? longer : longer * aspect;
        const depthM = aspect >= 1 ? longer / Math.max(aspect, 0.01) : longer;
        box = boxAroundPin(centerPin, [lng, lat], widthM, depthM);
        buildingIds = [];
        pinNote = `중심 핀 기준 ${Math.round(widthM)}m × ${Math.round(depthM)}m 영역에 맞췄습니다.`;
      }
      const out = await applySim({
        prompt: userText,
        lat,
        lng,
        r: radiusM,
        shape,
        includeAdjacent,
        buildingIds,
        box,
        history: simHistory,
        partial: simPartial,
        imageB64: simImage?.b64 ?? null,
        imageMime: simImage?.mime ?? null,
        session: hasImage ? "image_mass" : "grid",
        heightOverrides: heightById,
      });
      const userTurn: SimChatTurn = {
        role: "user",
        content: hasImage ? `${userText} (이미지 첨부)` : userText,
      };
      const baseReply = out.reply_ko || out.ask_ko || "";
      const botTurn: SimChatTurn = {
        role: "assistant",
        content: pinNote && out.status !== "ask" ? `${baseReply} ${pinNote}` : baseReply,
      };
      setSimChat((prev) => [...prev, userTurn, botTurn]);
      setSimHistory((prev) => [...prev, userTurn, botTurn]);
      setSimPartial(out.partial || null);
      setSimPrompt("");
      setSimImage(null);
      if (out.status === "ask") {
        setSimAskHint((out.ask_ko || out.reply_ko || "").trim());
        return;
      }
      setSimAskHint("");
      if (out.action === "edit") {
        if (out.updates?.length) {
          setHeightById((prev) => {
            const next = { ...prev };
            for (const update of out.updates || []) next[update.id] = update.height_m;
            return next;
          });
        }
        if (out.hide_ids?.length) {
          setHideIds((prev) => mergeHideIds(prev, out.hide_ids || []));
        }
      } else {
        setHideIds((prev) => mergeHideIds(prev, out.hide_ids || []));
        setSimBuildings((prev) =>
          appendSimBuildings(prev, (out.buildings?.features || []) as Feature[]),
        );
        setSimSession(out.session === "image_mass" ? "image_mass" : "grid");
      }
      clearSelection();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setSimError(message);
      setSimPrompt("");
      setSimChat((prev) => [...prev, { role: "assistant", content: message }]);
    } finally {
      setSimBusy(false);
    }
  };

  const downloadBlob = (blob: Blob, name: string) => {
    const a = document.createElement("a");
    const url = URL.createObjectURL(blob);
    a.href = url;
    a.download = name;
    a.click();
    URL.revokeObjectURL(url);
  };

  const saveGlb = () => {
    const scene = sceneRef.current;
    if (!scene) return;
    const exporter = new GLTFExporter();
    exporter.parse(
      scene,
      (result) => {
        const blob =
          result instanceof ArrayBuffer
            ? new Blob([result], { type: "model/gltf-binary" })
            : new Blob([JSON.stringify(result)], { type: "application/json" });
        downloadBlob(blob, "seocho_3d.glb");
      },
      () => undefined,
      { binary: true },
    );
  };

  const savePng = () => {
    const ctx = pickCtxRef.current;
    const scene = sceneRef.current;
    if (!ctx || !scene) return;
    ctx.renderer.render(scene, ctx.camera);
    ctx.renderer.domElement.toBlob((blob) => {
      if (!blob) return;
      downloadBlob(blob, "sitedia-mass.png");
    }, "image/png");
  };

  const toggleOff = (key: string, on: boolean) => {
    setOff((prev) => {
      const cur = new Set(prev[colorMode]);
      if (on) cur.delete(key);
      else cur.add(key);
      return { ...prev, [colorMode]: [...cur] };
    });
  };

  const setAll = (on: boolean) => {
    setOff((prev) => ({
      ...prev,
      [colorMode]: on ? [] : rows.map((r) => r.key),
    }));
  };

  const resetUseColors = () => setUseColors({ ...USE_COLORS });

  const onRowColor = (key: string, color: string) => {
    if (colorMode === "solid") setSolidColor(color);
    else if (colorMode === "use") setUseColors((p) => ({ ...p, [key]: color }));
    else if (colorMode === "age" && key === "미상") {
      /* 미상만 직접 고름 — 램프는 그라데이션 색 */
    }
  };

  const applyAgeBase = (hex: string) => {
    setAgeBase(hex);
    setAgeRamp(tintRamp(AGE_RAMP, hex));
  };
  const applyHeightBase = (hex: string) => {
    setHeightBase(hex);
    setHeightRamp(tintRamp(HEIGHT_RAMP, hex));
  };

  const viewer = (
    <div className="mass-wrap">
      <div className="mass-stage">
        <div ref={mountRef} className="mass-canvas" />
        {loading3d ? (
          <div className="mass-load" role="status">
            <StarLoader count={3} size={72} gap={18} speed={1.15} />
            <p>{MASS_LOAD_LINES[loadLine]}</p>
          </div>
        ) : null}
        <button
          type="button"
          className={`mass-spin-btn${spinning ? " is-pause" : ""}`}
          aria-pressed={spinning}
          aria-label={spinning ? "회전 일시정지" : "360도 회전"}
          title={spinning ? "일시정지" : "360° 회전"}
          onClick={() => {
            if (spinning) {
              setSpinning(false);
              return;
            }
            if (selectMode) setSelectMode(false);
            setSpinning(true);
          }}
        >
          {spinning ? (
            <span className="mass-spin-pause" aria-hidden="true">
              <i />
              <i />
            </span>
          ) : (
            <img src={icon360} alt="" className="mass-spin-icon" draggable={false} />
          )}
        </button>
        {simTab ? (
          <div className={`mass-area-wrap${selectMode ? " on" : ""}`}>
            <span className="mass-area-tag">영역 그리기</span>
            <button
              type="button"
              className={`mass-area-btn${selectMode ? " on" : ""}`}
              aria-pressed={selectMode}
              aria-label={selectMode ? "영역 그리기 끄기" : "영역 그리기"}
              title="드래그 또는 꼭짓점을 이어 영역을 만듭니다"
              onClick={() => {
                setPickKind("area");
                setPinNotice(false);
                setSelectMode((on) => !on);
              }}
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M12 22s7-7.2 7-12a7 7 0 1 0-14 0c0 4.8 7 12 7 12z" />
                <circle cx="12" cy="10" r="2.2" />
              </svg>
            </button>
          </div>
        ) : null}
        {simTab && selectMode ? (
          <p className="mass-area-hint">드래그로 상자, 클릭으로 다각형. 흰 점에서 닫기</p>
        ) : null}
        {simTab && growthHud ? (
          <>
            <div className="mass-year">
              <span className="mass-year-label">{growthHud.year}년</span>
              <button
                type="button"
                className="mass-pause"
                aria-label={growthHud.playing ? "일시정지" : "다시 재생"}
                title={growthHud.playing ? "일시정지" : "다시 재생"}
                onClick={() => {
                  const job = growthRef.current;
                  if (!job) return;
                  job.playing = !job.playing;
                  job.lastTick = performance.now();
                  setGrowthHud((prev) => (prev ? { ...prev, playing: job.playing } : prev));
                }}
              >
                {growthHud.playing ? (
                  <span className="mass-pause-bars" aria-hidden="true">
                    <i />
                    <i />
                  </span>
                ) : (
                  <span className="mass-play-tri" aria-hidden="true" />
                )}
              </button>
            </div>
            <label className="mass-year-scrub">
              <span>{growthHud.start}</span>
              <input
                type="range"
                min={growthHud.start}
                max={growthHud.end}
                step={1}
                value={Math.min(growthHud.end, Math.max(growthHud.start, growthHud.year))}
                aria-label="성장 연도"
                style={
                  {
                    "--range": `${
                      ((Math.min(growthHud.end, Math.max(growthHud.start, growthHud.year)) -
                        growthHud.start) /
                        Math.max(1, growthHud.end - growthHud.start)) *
                      100
                    }%`,
                  } as CSSProperties
                }
                onChange={(event) => {
                  const job = growthRef.current;
                  const year = Number(event.target.value);
                  if (!job) return;
                  job.playYear = year;
                  job.playing = false;
                  job.lastTick = performance.now();
                  setGrowthHud((prev) => (prev ? { ...prev, year, playing: false } : prev));
                }}
              />
              <span>{growthHud.end}</span>
            </label>
          </>
        ) : null}
      </div>

      <div className="mass-controls">
        {simTab ? (
          <div className={`sim-side-chat${guideOpen ? " is-open" : ""}`}>
            <button
              type="button"
              className="sim-guide-toggle"
              aria-expanded={guideOpen}
              onClick={() => setGuideOpen((open) => !open)}
            >
              {guideOpen ? "기능 설명 접기" : "기능 설명 열기"}
            </button>
            {guideOpen ? (
            <ol className="sim-guide">
              {ANALYSIS_GUIDE.map((item) => (
                <li key={item.title}>
                  <b>{item.title}</b>
                  <p>{item.body}</p>
                  <p className="sim-ex">
                    <em>프롬프트</em>
                    <span>
                      {item.note ? `(${item.note}) ` : ""}
                      {item.prompt}
                    </span>
                    <button
                      type="button"
                      aria-label={`${item.prompt} 복사`}
                      onClick={() => {
                        void copyPromptText(item.prompt).then(() => {
                          setCopiedPrompt(item.prompt);
                          window.setTimeout(() => {
                            setCopiedPrompt((current) => (current === item.prompt ? null : current));
                          }, 1200);
                        });
                      }}
                    >
                      {copiedPrompt === item.prompt ? "복사됨" : "복사"}
                    </button>
                  </p>
                </li>
              ))}
            </ol>
            ) : null}
            <div className="sim-chat">
              {simChat.map((message, index) => (
                <p
                  key={`${message.role}-${index}`}
                  className={message.role === "assistant" ? "sim-reply" : "sim-user"}
                >
                  {message.content}
                </p>
              ))}
            </div>
            {simChat.length > 0 || hideIds.length > 0 || Object.keys(colorById).length > 0 ? (
              <div className="row">
                <button type="button" onClick={clearSelection} disabled={simBusy || (!selectBox && selectedIds.length === 0 && draftCount === 0)}>
                  선택 해제
                </button>
                <button
                  type="button"
                  onClick={clearPlay}
                  disabled={simBusy}
                >
                  표시 초기화
                </button>
                <button type="button" onClick={resetSim} disabled={simBusy}>
                  대화 초기화
                </button>
                {briefMd ? (
                  <button type="button" onClick={() => setBriefOpen(true)} disabled={simBusy}>
                    브리핑 보기
                  </button>
                ) : null}
              </div>
            ) : null}
            <form
              className="sim-composer"
              onSubmit={(event) => {
                event.preventDefault();
                void runAnalysis(simPrompt);
              }}
            >
              <input
                type="text"
                className="sim-input"
                value={simPrompt}
                placeholder="프롬프트를 입력하세요"
                disabled={simBusy}
                onChange={(event) => setSimPrompt(event.target.value)}
              />
              <button
                type="submit"
                className="sim-send"
                aria-label={simBusy ? "적용 중" : "보내기"}
                disabled={simBusy || !simPrompt.trim()}
              >
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M12 19V6M12 6l-6 6M12 6l6 6" />
                </svg>
              </button>
            </form>
            {simError && simChat.at(-1)?.content !== simError ? (
              <p className="sim-error">{simError}</p>
            ) : null}
          </div>
        ) : (
          <>
        <div className="ctrl-block">
          <span className="ctrl-label">색</span>
          <div className="row toggles">
            {(
              [
                ["use", "용도"],
                ["age", "연대"],
                ["height", "높이"],
                ["solid", "단색"],
              ] as const
            ).map(([k, label]) => (
              <button
                key={k}
                type="button"
                className={colorMode === k ? "on" : ""}
                onClick={() => setColorMode(k)}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        <label className="ctrl-block">
          높이 과장 {exaggeration.toFixed(1)}×
          <input
            type="range"
            min={50}
            max={300}
            step={10}
            value={exaggerationPct}
            style={
              { "--range": `${((exaggerationPct - 50) / 250) * 100}%` } as CSSProperties
            }
            onChange={(e) => setExaggerationPct(Number(e.target.value))}
          />
        </label>

        {(colorMode === "age" || colorMode === "height") && (
          <div className="ctrl-block">
            <span className="ctrl-label">그라데이션 색</span>
            <div className="row" style={{ alignItems: "center" }}>
              <input
                type="color"
                value={colorMode === "age" ? ageBase : heightBase}
                onChange={(e) =>
                  colorMode === "age"
                    ? applyAgeBase(e.target.value)
                    : applyHeightBase(e.target.value)
                }
              />
              <span className="hint" style={{ margin: 0, flex: 1 }}>
                색상만 바꿉니다 — 명암 단계는 그대로입니다.
              </span>
              <button
                type="button"
                className="ghost-btn"
                onClick={() => {
                  if (colorMode === "age") {
                    setAgeBase("#b34a35");
                    setAgeRamp([...AGE_RAMP]);
                  } else {
                    setHeightBase("#3b82f6");
                    setHeightRamp([...HEIGHT_RAMP]);
                  }
                }}
              >
                되돌리기
              </button>
            </div>
          </div>
        )}

        <div className="pal open mass-pal checks">
          <label className="row">
            <input
              type="checkbox"
              checked={includeAdjacent}
              disabled={adjacentBusy}
              onChange={(e) => onIncludeAdjacentChange(e.target.checked)}
            />
            <span>인접 자치구</span>
          </label>
          <label className="row">
            <input type="checkbox" checked={showFloor} onChange={(e) => setShowFloor(e.target.checked)} />
            <span>바닥판</span>
          </label>
          <label className="row">
            <input type="checkbox" checked={showRoads} onChange={(e) => setShowRoads(e.target.checked)} />
            <span>도로</span>
          </label>
          <label className="row">
            <input type="checkbox" checked={showGreen} onChange={(e) => setShowGreen(e.target.checked)} />
            <span>녹지·하천</span>
          </label>
          <label className="row">
            <input type="checkbox" checked={showTerrain} onChange={(e) => setShowTerrain(e.target.checked)} />
            <span>지형</span>
          </label>
          <label className="row">
            <input type="checkbox" checked={showSite} onChange={(e) => setShowSite(e.target.checked)} />
            <span>대지 표시</span>
          </label>
          <label className="row">
            <input type="checkbox" checked={ghost} onChange={(e) => setGhost(e.target.checked)} />
            <span>끈 건물 반투명</span>
          </label>
        </div>

        {ghost ? (
          <label className="ctrl-block">
            끈 건물 투명도 {ghostOpacity}%
            <input
              type="range"
              min={3}
              max={100}
              step={1}
              value={ghostOpacity}
              style={{ "--range": `${((ghostOpacity - 3) / 97) * 100}%` } as CSSProperties}
              onChange={(e) => setGhostOpacity(Number(e.target.value))}
            />
          </label>
        ) : null}

        {!simTab ? (
        <div className="ctrl-block">
          <span className="ctrl-label">보기</span>
          <div className="row toggles">
            <button
              type="button"
              className={viewMode === "complete" ? "on" : ""}
              onClick={() => setViewMode("complete")}
            >
              완성 모형
            </button>
            <button
              type="button"
              className={viewMode === "explode" ? "on" : ""}
              onClick={() => setViewMode("explode")}
            >
              분해 다이어그램
            </button>
          </div>
          {viewMode === "explode" && (
            <>
              <p className="hint">
                위부터 건물 · 도로 · 녹지 · 지형, 맨 아래는 두꺼운 바닥판 위에 모두 합친 완성
                모형입니다.
              </p>
              <label className="ctrl-block explode-gap">
                레이어 간격 {Math.round(Math.max(80, radiusM * explodeGapRatio))} m
                <input
                  type="range"
                  min={12}
                  max={80}
                  step={2}
                  value={explodeGapPct}
                  style={
                    {
                      "--range": `${((explodeGapPct - 12) / 68) * 100}%`,
                    } as CSSProperties
                  }
                  onChange={(e) => setExplodeGapPct(Number(e.target.value))}
                />
              </label>
            </>
          )}
        </div>
        ) : null}

        <div className="pal open mass-pal">
          {rows.map((r) => {
            const isOff = offSet.has(r.key);
            return (
              <label key={r.key} className={`row${isOff ? " off" : ""}`}>
                {colorMode !== "solid" ? (
                  <input
                    type="checkbox"
                    checked={!isOff}
                    onChange={(e) => toggleOff(r.key, e.target.checked)}
                  />
                ) : null}
                <input
                  type="color"
                  value={r.color}
                  disabled={!r.edit}
                  title={r.edit ? undefined : "그라데이션 색으로 한 번에 바꿉니다"}
                  onChange={(e) => onRowColor(r.key, e.target.value)}
                />
                <span>{r.label}</span>
              </label>
            );
          })}
          {colorMode !== "solid" ? (
            <div className="acts">
              <button type="button" onClick={() => setAll(true)}>
                전체 켜기
              </button>
              <button type="button" onClick={() => setAll(false)}>
                전체 끄기
              </button>
              {colorMode === "use" ? (
                <button type="button" onClick={resetUseColors}>
                  색 초기화
                </button>
              ) : null}
            </div>
          ) : null}
        </div>

        <p className="hint">
          매스 {data?.stats?.mass_count ?? buildings.length}동
          {simBuildings.length > 0
            ? simSession === "image_mass"
              ? ` · 미리보기 ${simBuildings.length}동`
              : ` · 시뮬 ${simBuildings.length}동`
            : ""}
          {includeAdjacent ? " · 서초+강남·동작·관악" : " · 서초만"}
          {adjacentBusy ? " · 다시 그리는 중" : ""}
        </p>
            <div className="row">
              <button type="button" onClick={saveGlb}>
                GLB 저장
              </button>
              <button type="button" onClick={savePng}>
                PNG 저장
              </button>
            </div>
            <p className="hint" style={{ margin: 0 }}>
              높이가 없는 건축물은 층수×3.25m로 표현, 높이와 층수 데이터가 없는 건물은 3D 렌더링
              제외
            </p>
          </>
        )}
      </div>
    </div>
  );

  return (
    <>
      {viewer}
      {briefOpen && briefMd ? (
        <div className="brief-back" role="presentation" onClick={() => setBriefOpen(false)}>
          <section
            className="brief-card"
            role="dialog"
            aria-labelledby="brief-title"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="brief-body">{renderBrief(briefMd)}</div>
            <div className="brief-actions">
              <button type="button" onClick={() => downloadBrief(briefMd)}>
                마크다운 저장
              </button>
              <button
                type="button"
                onClick={() => {
                  void copyBrief(briefMd).then(() => {
                    setBriefCopied(true);
                    window.setTimeout(() => setBriefCopied(false), 1500);
                  });
                }}
              >
                {briefCopied ? "복사했습니다" : "본문 복사"}
              </button>
              <button type="button" className="primary" onClick={() => setBriefOpen(false)}>
                닫기
              </button>
            </div>
          </section>
        </div>
      ) : null}
    </>
  );
}
