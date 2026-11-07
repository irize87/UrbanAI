import * as THREE from "three";
import {
  AGE_UNKNOWN_3D,
  GHOST_OPAQUE,
  GHOST_TRANSLUCENT,
  USE_COLORS,
} from "./palette";
import {
  buildCakeBase,
  buildContourLines,
  buildRoadRibbon,
  buildSitePin,
  buildTerrainSurface,
  tessellateThenDrape,
  lonLatToXZ,
  makeGroundSampler,
  mergeStyle,
  roadHalfWidth,
  ringCentroidXZ,
  sampleElev,
  xzToLonLat,
  type MassStyle,
  type TerrainGrid,
} from "./massTerrain";

export type Feature = {
  properties: Record<string, unknown>;
  geometry: { type: string; coordinates: number[][][] | number[][][][] };
};

export type MassData = {
  buildings?: { features: Feature[] };
  roads?: { features: Feature[] };
  green?: { features: Feature[] };
  water?: { features: Feature[] };
  features?: Feature[];
  clip?: { type: string; coordinates: number[][][] };
  terrain?: TerrainGrid | null;
  style?: Partial<MassStyle>;
  include_adjacent?: boolean;
  stats?: {
    mass_count?: number;
    include_adjacent?: boolean;
    by_sgg?: Record<string, number>;
  };
};

/** OrbitControls 휠 1노치: zoom *= 0.95(줌아웃). 회전 뷰는 10노치 당김. */
export const ORBIT_WHEEL_ZOOM_SCALE = 0.95;
export const MASS_ORTHO_DEFAULT_ZOOM_NOTCHES = 10;
export const MASS_ORTHO_DEFAULT_ZOOM = Math.pow(
  1 / ORBIT_WHEEL_ZOOM_SCALE,
  MASS_ORTHO_DEFAULT_ZOOM_NOTCHES,
);

export type MassColorFn = (f: Feature) => { key: string; color: string };

export type MassWorldOpts = {
  data: MassData | null;
  lat: number;
  lng: number;
  radiusM: number;
  shape: "circle" | "rect";
  showFloor?: boolean;
  showRoads?: boolean;
  showGreen?: boolean;
  showTerrain?: boolean;
  showSite?: boolean;
  exaggeration?: number;
  colorOf: MassColorFn;
  offSet?: Set<string>;
  ghost?: boolean;
  ghostOpacity?: number;
  hideIds?: number[];
  simBuildings?: Feature[];
  heightById?: Record<number, number>;
  colorById?: Record<number, string>;
  /** 일조 그림자용. 바닥 위에 ShadowMaterial 캐처를 얹고 건물이 드리우게. */
  shadowGround?: boolean;
  /** 지형·케이크 윗면 색. 없으면 style.floor */
  floorColor?: string;
};

export type MassWorld = {
  style: MassStyle;
  origin: [number, number];
  yAt: (x: number, z: number) => number;
  buildingMeshes: THREE.Mesh[];
  cake: THREE.Group | null;
  terrainGrp: THREE.Group;
  greenGrp: THREE.Group;
  roadGrp: THREE.Group;
  bldgGrp: THREE.Group;
  pin: THREE.Group | null;
  shadowCatcher: THREE.Mesh | null;
};

export function applyOrthoFrustum(
  camera: THREE.OrthographicCamera,
  width: number,
  height: number,
  viewSize: number,
): void {
  const aspect = width / Math.max(1, height);
  camera.left = (-viewSize * aspect) / 2;
  camera.right = (viewSize * aspect) / 2;
  camera.top = viewSize / 2;
  camera.bottom = -viewSize / 2;
  camera.near = -8000;
  camera.far = 16000;
  camera.updateProjectionMatrix();
}

export function colorByUse(f: Feature): { key: string; color: string } {
  const raw = String(f.properties.use_cat || "미상");
  const k = USE_COLORS[raw] ? raw : "미상";
  return { key: k, color: USE_COLORS[k] || AGE_UNKNOWN_3D };
}

export function massBuildings(data: MassData | null): Feature[] {
  return (data?.buildings?.features || data?.features || []) as Feature[];
}

export function ringToShape(ring: number[][], origin: [number, number]): THREE.Shape {
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

export function polyToShape(poly: number[][][], origin: [number, number]): THREE.Shape | null {
  if (!poly[0] || poly[0].length < 3) return null;
  const shape = ringToShape(poly[0], origin);
  for (let i = 1; i < poly.length; i++) {
    const hole = poly[i];
    if (!hole || hole.length < 3) continue;
    shape.holes.push(ringToShape(hole, origin));
  }
  return shape;
}

export function polysOf(f: Feature): number[][][][] {
  if (f.geometry.type === "Polygon") return [f.geometry.coordinates as number[][][]];
  return f.geometry.coordinates as number[][][][];
}

function makeGreenMaterial(color: string): THREE.MeshBasicMaterial {
  return new THREE.MeshBasicMaterial({
    color,
    side: THREE.DoubleSide,
    polygonOffset: true,
    polygonOffsetFactor: -2,
    polygonOffsetUnits: -2,
    depthWrite: true,
  });
}

function makeWaterMaterial(color: string): THREE.MeshBasicMaterial {
  return new THREE.MeshBasicMaterial({
    color,
    side: THREE.DoubleSide,
    polygonOffset: true,
    polygonOffsetFactor: -4,
    polygonOffsetUnits: -4,
    depthWrite: true,
  });
}

function makeRoadMaterial(color: string): THREE.MeshBasicMaterial {
  return new THREE.MeshBasicMaterial({
    color,
    side: THREE.DoubleSide,
    polygonOffset: true,
    polygonOffsetFactor: -8,
    polygonOffsetUnits: -8,
    depthWrite: true,
  });
}

export function makeMassingMaterials(
  color: string,
  opacity = 1,
): [THREE.MeshBasicMaterial, THREE.MeshLambertMaterial] {
  const shared = {
    transparent: opacity < 1,
    opacity,
    depthWrite: opacity >= 1,
    polygonOffset: true,
    polygonOffsetFactor: 1,
    polygonOffsetUnits: 1,
  };
  return [
    new THREE.MeshBasicMaterial({ color, ...shared }),
    new THREE.MeshLambertMaterial({ color, flatShading: true, ...shared }),
  ];
}

function addDrapedPolys(
  group: THREE.Group,
  features: Feature[],
  origin: [number, number],
  yAt: (x: number, z: number) => number,
  mat: THREE.MeshBasicMaterial,
  depth: number,
  lift: number,
  renderOrder: number,
): void {
  for (const f of features) {
    for (const poly of polysOf(f)) {
      try {
        const shp = polyToShape(poly, origin);
        if (!shp) continue;
        let g: THREE.BufferGeometry;
        try {
          g = new THREE.ExtrudeGeometry(shp, {
            depth,
            bevelEnabled: false,
            steps: 1,
            curveSegments: 1,
          });
        } catch {
          g = new THREE.ShapeGeometry(shp);
        }
        g.rotateX(-Math.PI / 2);
        g = tessellateThenDrape(g, yAt, lift);
        const mesh = new THREE.Mesh(g, mat);
        mesh.renderOrder = renderOrder;
        group.add(mesh);
      } catch {
        /* skip */
      }
    }
  }
}

function addShadowCatcher(terrainGrp: THREE.Group): THREE.Mesh | null {
  let source: THREE.Mesh | null = null;
  terrainGrp.traverse((obj) => {
    const mesh = obj as THREE.Mesh;
    if (mesh.isMesh && !source) source = mesh;
  });
  if (!source) return null;
  const catcher = new THREE.Mesh(
    source.geometry,
    new THREE.ShadowMaterial({
      color: 0x1a1a1a,
      opacity: 0.55,
      side: THREE.DoubleSide,
      depthWrite: false,
      // 도로 MeshBasic은 polygonOffset -8이라, 그보다 강하게 앞으로 당겨야
      // 깊이 테스트에서 지지 않고 길 위에도 그림자가 남는다.
      polygonOffset: true,
      polygonOffsetFactor: -20,
      polygonOffsetUnits: -20,
    }),
  );
  catcher.receiveShadow = true;
  catcher.castShadow = false;
  catcher.renderOrder = 8;
  catcher.position.y = 1.05;
  return catcher;
}

/**
 * 02 3D 탭과 같은 케이크·지형·녹지·도로·용도색 매스.
 */
export function buildMassWorld(opts: MassWorldOpts): MassWorld {
  const radiusM = opts.radiusM;
  const shape = opts.shape;
  const origin: [number, number] = [opts.lng, opts.lat];
  const style = mergeStyle(opts.data?.style);
  const showFloor = opts.showFloor !== false;
  const showRoads = opts.showRoads !== false;
  const showGreen = opts.showGreen !== false;
  const showTerrain = opts.showTerrain !== false;
  const showSite = opts.showSite !== false;
  const exaggeration = opts.exaggeration ?? 1;
  const ghost = opts.ghost !== false;
  const ghostOpacity = opts.ghostOpacity ?? 20;
  const offSet = opts.offSet || new Set<string>();
  const hideSet = new Set(opts.hideIds || []);
  const buildings = massBuildings(opts.data).filter((f) => {
    const bid = Number(f.properties.id);
    return !Number.isFinite(bid) || !hideSet.has(bid);
  });
  const simBuildings = opts.simBuildings || [];
  const roads = opts.data?.roads?.features || [];
  const green = opts.data?.green?.features || [];
  const water = opts.data?.water?.features || [];
  const terrain = (opts.data?.terrain || null) as TerrainGrid | null;

  const { yAt } = makeGroundSampler(
    terrain,
    origin,
    radiusM,
    shape,
    showTerrain,
    style.terrain_exag,
  );
  const floorColor = opts.floorColor || style.floor;

  const terrainGrp = new THREE.Group();
  terrainGrp.renderOrder = 0;
  if (showFloor || showTerrain) {
    terrainGrp.add(
      buildTerrainSurface(radiusM, shape, style.terrain_n, yAt, floorColor),
    );
  }
  if (style.draw_contours && showTerrain && terrain) {
    const contours = buildContourLines(
      terrain,
      origin,
      radiusM,
      shape,
      style.contour_m,
      yAt,
      style.contour,
      style.terrain_n,
    );
    if (contours) terrainGrp.add(contours);
  }

  const pin = showSite ? buildSitePin(yAt(0, 0)) : null;

  const greenLift = Math.max(0.04, style.green_lift_m);
  const waterLift = Math.max(greenLift + 0.04, style.water_lift_m);
  const roadLift = Math.max(waterLift + 0.06, Math.min(style.road_lift_m, 0.55));

  const greenGrp = new THREE.Group();
  if (showGreen) {
    const slabDepth = Math.min(style.green_depth_m, 0.08);
    addDrapedPolys(
      greenGrp,
      green,
      origin,
      yAt,
      makeGreenMaterial(style.green || "#c3e6a4"),
      slabDepth,
      greenLift,
      1,
    );
    addDrapedPolys(
      greenGrp,
      water,
      origin,
      yAt,
      makeWaterMaterial(style.water || "#8ecae6"),
      slabDepth,
      waterLift,
      2,
    );
  }

  const roadGrp = new THREE.Group();
  if (showRoads) {
    const roadMatCache = new Map<number, THREE.MeshBasicMaterial>();
    const roadColor = style.road;
    const sorted = [...roads].sort((a, b) => {
      const wa = roadHalfWidth(a.properties.road_class, style) ?? 0;
      const wb = roadHalfWidth(b.properties.road_class, style) ?? 0;
      return wa - wb;
    });
    for (const f of sorted) {
      const half = roadHalfWidth(f.properties.road_class, style);
      if (half == null) continue;
      const g = f.geometry;
      const lines =
        g.type === "LineString"
          ? [g.coordinates as unknown as number[][]]
          : g.type === "MultiLineString"
            ? (g.coordinates as unknown as number[][][])
            : [];
      if (!lines.length && (g.type === "Polygon" || g.type === "MultiPolygon")) {
        for (const poly of polysOf(f)) {
          const shp = ringToShape(poly[0], origin);
          const raw = new THREE.ShapeGeometry(shp);
          raw.rotateX(-Math.PI / 2);
          const geom = tessellateThenDrape(raw, yAt, roadLift);
          const mesh = new THREE.Mesh(geom, makeRoadMaterial(roadColor));
          mesh.renderOrder = 3;
          roadGrp.add(mesh);
        }
      }
      let mat = roadMatCache.get(half);
      if (!mat) {
        mat = makeRoadMaterial(roadColor);
        roadMatCache.set(half, mat);
      }
      for (const line of lines) {
        const pts = line.map((c) => {
          const { x, z } = lonLatToXZ(c[0], c[1], origin);
          return new THREE.Vector3(x, yAt(x, z) + roadLift, z);
        });
        const ribbon = buildRoadRibbon(pts, half, roadColor, mat);
        if (ribbon) {
          ribbon.renderOrder = 3;
          roadGrp.add(ribbon);
        }
      }
    }
  }

  const renderBuildings: Feature[] = [...buildings, ...simBuildings];
  const bldgGrp = new THREE.Group();
  const buildingMeshes: THREE.Mesh[] = [];
  const bldgMatCache = new Map<
    string,
    [THREE.MeshBasicMaterial, THREE.MeshLambertMaterial]
  >();
  for (const f of renderBuildings) {
    const bid = Number(f.properties.id);
    const overridden = Number.isFinite(bid) ? opts.heightById?.[bid] : undefined;
    const baseHeight =
      overridden != null && Number.isFinite(overridden)
        ? overridden
        : Number(f.properties.height_m) || 10;
    const height = baseHeight * exaggeration;
    const isSim = Boolean(f.properties.sim);
    const forced = Number.isFinite(bid) ? opts.colorById?.[bid] : undefined;
    const cat = isSim
      ? {
          key: String(f.properties.use_cat || "미상"),
          color:
            USE_COLORS[String(f.properties.use_cat || "미상")] || USE_COLORS["미상"],
        }
      : opts.colorOf(f);
    const isOff = !isSim && offSet.has(cat.key);
    const color = forced
      ? forced
      : isOff
        ? ghost
          ? GHOST_TRANSLUCENT
          : GHOST_OPAQUE
        : cat.color;
    const opacity = isOff && ghost ? ghostOpacity / 100 : 1;
    const matKey = `${color}|${opacity}`;
    let mats = bldgMatCache.get(matKey);
    if (!mats) {
      mats = makeMassingMaterials(color, opacity);
      bldgMatCache.set(matKey, mats);
    }
    for (const poly of polysOf(f)) {
      try {
        const shp = ringToShape(poly[0], origin);
        const geom = new THREE.ExtrudeGeometry(shp, {
          depth: height,
          bevelEnabled: false,
        });
        geom.rotateX(-Math.PI / 2);
        geom.computeBoundingBox();
        const { x: cx, z: cz } = ringCentroidXZ(poly[0], origin);
        const ll = xzToLonLat(cx, cz, origin);
        const groundElev = terrain ? sampleElev(terrain, ll.lat, ll.lng) : null;
        const mesh = new THREE.Mesh(geom, mats);
        mesh.position.y = yAt(cx, cz);
        mesh.renderOrder = 4;
        const bb = geom.boundingBox;
        mesh.userData = {
          id: Number.isFinite(bid) ? bid : 0,
          use_cat: f.properties.use_cat,
          height_m: f.properties.height_m,
          sim: isSim,
          year: Number(f.properties.year) || 0,
          groundElev,
          minX: bb ? bb.min.x : 0,
          maxX: bb ? bb.max.x : 0,
          minZ: bb ? bb.min.z : 0,
          maxZ: bb ? bb.max.z : 0,
        };
        bldgGrp.add(mesh);
        buildingMeshes.push(mesh);
      } catch {
        /* skip bad geom */
      }
    }
  }

  const cake = showFloor
    ? buildCakeBase(radiusM, shape, style.base_h_m, yAt, floorColor)
    : null;

  let shadowCatcher: THREE.Mesh | null = null;
  if (opts.shadowGround) {
    shadowCatcher = addShadowCatcher(terrainGrp);
    for (const mesh of buildingMeshes) {
      mesh.castShadow = true;
      mesh.receiveShadow = true;
    }
  }

  return {
    style,
    origin,
    yAt,
    buildingMeshes,
    cake,
    terrainGrp,
    greenGrp,
    roadGrp,
    bldgGrp,
    pin,
    shadowCatcher,
  };
}

export function assembleCompleteMass(world: MassWorld): THREE.Group {
  const combined = new THREE.Group();
  if (world.cake) combined.add(world.cake);
  combined.add(world.terrainGrp);
  combined.add(world.greenGrp);
  combined.add(world.roadGrp);
  if (world.shadowCatcher) combined.add(world.shadowCatcher);
  combined.add(world.bldgGrp);
  if (world.pin) combined.add(world.pin);
  return combined;
}
