import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import type { MassData } from "./MassViewer";
import {
  applyClock,
  dateLabel,
  minutesToHhmm,
  presetDate,
  solarPosition,
  sunriseSunsetMinutes,
  sunSamples,
  trueSolarWindowStd,
  type DateKind,
} from "../solar";
import { hoursToRgb, type HeightBuilding, type SunHoursResult } from "../sunHours";
import { downloadBlob } from "../palette";
import {
  applyOrthoFrustum,
  assembleCompleteMass,
  buildMassWorld,
  colorByUse,
  MASS_ORTHO_DEFAULT_ZOOM,
  massBuildings,
  polysOf,
  type Feature,
} from "../massWorld";

type Props = {
  data: MassData | null;
  lat: number;
  lng: number;
  radiusM: number;
  shape: "circle" | "rect";
  busy?: boolean;
};

function sunVector(alt: number, az: number, dist: number): THREE.Vector3 {
  const a = (alt * Math.PI) / 180;
  const z = (az * Math.PI) / 180;
  return new THREE.Vector3(
    Math.sin(z) * Math.cos(a) * dist,
    Math.sin(a) * dist,
    -Math.cos(z) * Math.cos(a) * dist,
  );
}

function sunOnPlate(
  alt: number,
  az: number,
  plateR: number,
  liftY: number,
): THREE.Vector3 {
  const v = sunVector(alt, az, plateR);
  v.y += liftY;
  return v;
}

function fitSunShadow(
  sun: THREE.DirectionalLight,
  radiusM: number,
  groundY: number,
): void {
  sun.target.position.set(0, groundY, 0);
  const dist = sun.position.length() || 1;
  const sinAlt = Math.max(0.14, Math.abs(sun.position.y) / dist);
  const span = Math.min(radiusM * 5.2, radiusM * (1.4 / sinAlt));
  const cam = sun.shadow.camera;
  cam.left = -span;
  cam.right = span;
  cam.top = span;
  cam.bottom = -span;
  cam.near = 0.5;
  cam.far = radiusM * 14;
  cam.updateProjectionMatrix();
  sun.target.updateMatrixWorld();
  sun.updateMatrixWorld();
}

function toHeightBuildings(feats: Feature[]): HeightBuilding[] {
  const out: HeightBuilding[] = [];
  for (const f of feats) {
    const h = Number(f.properties.height_m);
    const floors = Number(f.properties.floors);
    const height = h > 0.2 ? h : floors > 0 ? floors * 3.25 : 0;
    if (height <= 0.2) continue;
    out.push({ rings: polysOf(f), height });
  }
  return out;
}

const DATE_BTNS: { id: DateKind; label: string }[] = [
  { id: "summer", label: "하지" },
  { id: "spring", label: "춘분" },
  { id: "autumn", label: "추분" },
  { id: "winter", label: "동지" },
  { id: "now", label: "지금" },
];

export function SunViewer({ data, lat, lng, radiusM, shape, busy }: Props) {
  const mountRef = useRef<HTMLDivElement | null>(null);
  const [view, setView] = useState<"model" | "sunhours">("model");
  const [kind, setKind] = useState<DateKind>("summer");
  const [minutes, setMinutes] = useState(12 * 60);
  const [showShadow, setShowShadow] = useState(true);
  const [showPath, setShowPath] = useState(true);
  const [showHourly, setShowHourly] = useState(true);
  const [showCompass, setShowCompass] = useState(true);
  const [castOnly, setCastOnly] = useState(false);
  const [hoursBusy, setHoursBusy] = useState(false);
  const [hours, setHours] = useState<SunHoursResult | null>(null);
  const hoursRef = useRef<SunHoursResult | null>(null);
  const optsRef = useRef({
    kind,
    minutes,
    showShadow,
    showPath,
    showHourly,
    showCompass,
    castOnly,
    view,
  });
  const camApi = useRef<{
    north: () => void;
    top: () => void;
    capture: (plateOnly: boolean) => void;
  } | null>(null);

  useEffect(() => {
    optsRef.current = {
      kind,
      minutes,
      showShadow,
      showPath,
      showHourly,
      showCompass,
      castOnly,
      view,
    };
  }, [kind, minutes, showShadow, showPath, showHourly, showCompass, castOnly, view]);

  const dt = useMemo(() => applyClock(presetDate(kind), minutes), [kind, minutes]);
  const pos = useMemo(() => solarPosition(lat, lng, dt), [lat, lng, dt]);
  const riseSet = useMemo(() => sunriseSunsetMinutes(lat, lng, dt), [lat, lng, dt]);
  const noonPos = useMemo(() => {
    const noon = applyClock(dt, solarPosition(lat, lng, applyClock(dt, 12 * 60)).solarNoonMinutes);
    return solarPosition(lat, lng, noon);
  }, [lat, lng, dt]);
  const win = useMemo(() => trueSolarWindowStd(lat, lng, dt, 9, 15), [lat, lng, dt]);

  useEffect(() => {
    if (view !== "sunhours") return;
    const feats = massBuildings(data);
    const buildings = toHeightBuildings(feats);
    setHoursBusy(true);
    const worker = new Worker(new URL("../sunHours.worker.ts", import.meta.url), {
      type: "module",
    });
    worker.onmessage = (ev: MessageEvent<SunHoursResult>) => {
      hoursRef.current = ev.data;
      setHours(ev.data);
      setHoursBusy(false);
      worker.terminate();
    };
    worker.onerror = () => {
      setHoursBusy(false);
      worker.terminate();
    };
    worker.postMessage({
      buildings,
      lat,
      lng,
      radiusM,
      dateIso: dt.toISOString(),
      n: radiusM > 900 ? 48 : 64,
      stepMin: 10,
    });
    return () => worker.terminate();
  }, [view, data, lat, lng, radiusM, dt]);

  useEffect(() => {
    const el = mountRef.current;
    if (!el) return;
    const w = el.clientWidth || 640;
    const h = el.clientHeight || 460;
    const world = buildMassWorld({
      data,
      lat,
      lng,
      radiusM,
      shape,
      colorOf: colorByUse,
      shadowGround: true,
      floorColor: "#ffffff",
    });
    const plateR = radiusM * 1.06;
    const liftY = world.yAt(0, 0);
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(world.style.bg);
    const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, -8000, 16000);
    const viewSize = radiusM * 2.35;
    applyOrthoFrustum(camera, w, h, viewSize);
    camera.zoom = MASS_ORTHO_DEFAULT_ZOOM;
    camera.updateProjectionMatrix();
    const dist = radiusM * 2.2;
    camera.position.set(dist * 0.95, dist * 0.72, dist * 0.95);
    const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
    renderer.setSize(w, h);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.toneMapping = THREE.NoToneMapping;
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFShadowMap;
    el.innerHTML = "";
    el.appendChild(renderer.domElement);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.minPolarAngle = 0;
    controls.maxPolarAngle = Math.PI / 2;
    controls.minZoom = 0.35;
    controls.maxZoom = 12;
    controls.mouseButtons.RIGHT = THREE.MOUSE.PAN;
    controls.target.set(0, 0, 0);

    const ambient = new THREE.AmbientLight(0xffffff, 0.58);
    scene.add(ambient);
    const hemi = new THREE.HemisphereLight(0xffffff, 0xcdd0d4, 0.18);
    scene.add(hemi);
    const keyLight = new THREE.DirectionalLight(0xffffff, 0.55);
    keyLight.position.set(radiusM * 0.85, radiusM * 1.15, radiusM * 0.32);
    scene.add(keyLight);
    const fill = new THREE.DirectionalLight(0xffffff, 0.12);
    fill.position.set(-radiusM * 0.55, radiusM * 0.35, -radiusM * 0.7);
    scene.add(fill);
    const sun = new THREE.DirectionalLight(0xfff1d0, 0.9);
    sun.castShadow = true;
    sun.shadow.mapSize.set(4096, 4096);
    sun.shadow.radius = 1.25;
    sun.shadow.bias = -0.0012;
    sun.shadow.normalBias = 0.08;
    scene.add(sun);
    scene.add(sun.target);

    const massRoot = assembleCompleteMass(world);
    scene.add(massRoot);
    for (const mesh of world.buildingMeshes) {
      const mats = mesh.material;
      if (Array.isArray(mats) && mats.length >= 2) {
        mesh.userData.basic = mats[0];
        mesh.userData.lambert = mats[1];
        mesh.userData.massMats = mats;
      } else {
        mesh.userData.basic = mats;
        mesh.userData.lambert = mats;
        mesh.userData.massMats = mats;
      }
    }

    const overlay = new THREE.Group();
    scene.add(overlay);
    const heatMesh = new THREE.Mesh(
      new THREE.PlaneGeometry(radiusM * 2, radiusM * 2, 1, 1),
      new THREE.MeshBasicMaterial({ visible: false }),
    );
    heatMesh.rotation.x = -Math.PI / 2;
    heatMesh.position.y = liftY + 1.2;
    overlay.add(heatMesh);

    const sunMarker = new THREE.Mesh(
      new THREE.SphereGeometry(radiusM * 0.018, 16, 16),
      new THREE.MeshBasicMaterial({ color: "#f2c14e" }),
    );
    overlay.add(sunMarker);

    const clearKind = (kindName: string) => {
      const doomed = overlay.children.filter((c) => c.userData.kind === kindName);
      for (const child of doomed) {
        overlay.remove(child);
        child.traverse((obj) => {
          const mesh = obj as THREE.Mesh;
          mesh.geometry?.dispose?.();
          const mat = mesh.material as THREE.Material | THREE.Material[] | undefined;
          if (Array.isArray(mat)) mat.forEach((m) => m.dispose());
          else mat?.dispose?.();
        });
      }
    };

    let overlayKey = "";
    let raf = 0;
    const tick = () => {
      const o = optsRef.current;
      const when = applyClock(presetDate(o.kind), o.minutes);
      const sp = solarPosition(lat, lng, when);
      const night = sp.altitudeDeg <= 0.15;
      const sv = sunVector(Math.max(sp.altitudeDeg, 0.5), sp.azimuthDeg, radiusM * 4);
      sun.position.copy(sv);
      const shadowOn = o.showShadow && !night && o.view === "model";
      sun.castShadow = shadowOn;
      sun.visible = shadowOn;
      if (world.shadowCatcher) world.shadowCatcher.visible = shadowOn;
      if (shadowOn) fitSunShadow(sun, radiusM, liftY);
      keyLight.visible = o.view === "model" && !o.castOnly && !shadowOn;
      fill.visible = o.view === "model" && !o.castOnly;
      if (o.castOnly || o.view === "sunhours") {
        ambient.intensity = 0.95;
        hemi.intensity = 0.05;
        sun.intensity = night ? 0 : 1.05;
      } else {
        ambient.intensity = 0.58;
        hemi.intensity = 0.18;
        sun.intensity = night ? 0 : 0.95;
      }
      for (const mesh of world.buildingMeshes) {
        if (o.view === "sunhours") {
          mesh.material = mesh.userData.basic;
        } else if (shadowOn) {
          mesh.material = mesh.userData.lambert;
        } else {
          mesh.material = mesh.userData.massMats;
        }
        mesh.castShadow = shadowOn;
      }
      sunMarker.visible = o.view === "model" && o.showPath && !night;
      if (sunMarker.visible) {
        sunMarker.position.copy(sunOnPlate(Math.max(sp.altitudeDeg, 0.5), sp.azimuthDeg, plateR, liftY));
      }

      const hr = hoursRef.current;
      const key = [
        o.view,
        o.kind,
        o.showPath,
        o.showHourly,
        o.showCompass,
        hr?.n ?? 0,
        hr?.stats.meanH ?? 0,
      ].join("|");
      if (key !== overlayKey) {
        overlayKey = key;
        clearKind("path");
        if (o.view === "model" && o.showPath) {
          const kinds: DateKind[] = ["summer", "spring", "autumn", "winter"];
          const cols: Record<DateKind, string> = {
            summer: "#e8a33a",
            spring: "#6aa84f",
            autumn: "#c27ba0",
            winter: "#3d85c6",
            now: "#888",
          };
          for (const k of kinds) {
            const samples = sunSamples(lat, lng, presetDate(k), 20);
            if (samples.length < 2) continue;
            const pts = samples.map((s) =>
              sunOnPlate(s.altitudeDeg, s.azimuthDeg, plateR, liftY),
            );
            const g = new THREE.BufferGeometry().setFromPoints(pts);
            const line = new THREE.Line(
              g,
              new THREE.LineBasicMaterial({ color: cols[k], transparent: true, opacity: 0.85 }),
            );
            line.userData.kind = "path";
            overlay.add(line);
            if (o.showHourly) {
              for (const s of samples) {
                if (s.trueSolarMinutes % 60 > 12 && s.trueSolarMinutes % 60 < 48) continue;
                const p = sunOnPlate(s.altitudeDeg, s.azimuthDeg, plateR, liftY);
                const dot = new THREE.Mesh(
                  new THREE.SphereGeometry(radiusM * 0.008, 8, 8),
                  new THREE.MeshBasicMaterial({ color: cols[k] }),
                );
                dot.position.copy(p);
                dot.userData.kind = "path";
                overlay.add(dot);
              }
            }
          }
        }
        clearKind("compass");
        if (o.showCompass) {
          const r = radiusM * 1.08;
          const ticks: [string, number][] = [
            ["N", 0],
            ["E", 90],
            ["S", 180],
            ["W", 270],
          ];
          for (const [lab, az] of ticks) {
            const rad = (az * Math.PI) / 180;
            const x = Math.sin(rad) * r;
            const z = -Math.cos(rad) * r;
            const canvas = document.createElement("canvas");
            canvas.width = 64;
            canvas.height = 64;
            const ctx = canvas.getContext("2d");
            if (ctx) {
              ctx.fillStyle = "#222";
              ctx.font = "28px sans-serif";
              ctx.textAlign = "center";
              ctx.textBaseline = "middle";
              ctx.fillText(lab, 32, 32);
            }
            const spr = new THREE.Sprite(
              new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(canvas), depthTest: false }),
            );
            spr.position.set(x, world.yAt(x, z) + 8, z);
            spr.scale.set(radiusM * 0.1, radiusM * 0.1, 1);
            spr.userData.kind = "compass";
            overlay.add(spr);
          }
        }
        if (o.view === "sunhours" && hr) {
          const n = hr.n;
          const geo = new THREE.PlaneGeometry(radiusM * 2, radiusM * 2, n - 1, n - 1);
          const colors = new Float32Array(n * n * 3);
          for (let i = 0; i < n; i++) {
            for (let j = 0; j < n; j++) {
              const idx = i * n + j;
              const [cr, cg, cb] =
                hr.height[idx] > 0.2
                  ? [230, 228, 222]
                  : hoursToRgb(hr.hours[idx], hr.stats.dayLengthH);
              colors[idx * 3] = cr / 255;
              colors[idx * 3 + 1] = cg / 255;
              colors[idx * 3 + 2] = cb / 255;
            }
          }
          geo.setAttribute("color", new THREE.BufferAttribute(colors, 3));
          heatMesh.geometry.dispose();
          heatMesh.geometry = geo;
          const mat = heatMesh.material as THREE.MeshBasicMaterial;
          mat.vertexColors = true;
          mat.visible = true;
          mat.needsUpdate = true;
        } else {
          (heatMesh.material as THREE.MeshBasicMaterial).visible = false;
        }
      }

      controls.update();
      renderer.render(scene, camera);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);

    const onResize = () => {
      const nw = el.clientWidth || w;
      const nh = el.clientHeight || h;
      applyOrthoFrustum(camera, nw, nh, viewSize);
      renderer.setSize(nw, nh);
    };
    window.addEventListener("resize", onResize);

    camApi.current = {
      north: () => {
        camera.position.set(0.01, dist * 0.85, dist * 1.15);
        controls.target.set(0, 0, 0);
        controls.update();
      },
      top: () => {
        camera.position.set(0, radiusM * 3, 0.01);
        controls.target.set(0, 0, 0);
        camera.up.set(0, 0, -1);
        controls.update();
        camera.up.set(0, 1, 0);
      },
      capture: (plateOnly: boolean) => {
        renderer.render(scene, camera);
        const o = optsRef.current;
        const when = applyClock(presetDate(o.kind), o.minutes);
        const sp = solarPosition(lat, lng, when);
        if (plateOnly) {
          renderer.domElement.toBlob((blob) => {
            if (blob) downloadBlob(blob, "sun-plate.png");
          });
          return;
        }
        const src = renderer.domElement;
        const out = document.createElement("canvas");
        out.width = src.width;
        out.height = src.height + 72;
        const ctx = out.getContext("2d");
        if (!ctx) return;
        ctx.fillStyle = world.style.bg;
        ctx.fillRect(0, 0, out.width, out.height);
        ctx.drawImage(src, 0, 0);
        ctx.fillStyle = "#222";
        ctx.font = "14px sans-serif";
        ctx.fillText(
          `${dateLabel(o.kind, when)} · 고도 ${sp.altitudeDeg.toFixed(1)}° · 방위 ${sp.azimuthDeg.toFixed(0)}°`,
          16,
          src.height + 28,
        );
        out.toBlob((blob) => {
          if (blob) downloadBlob(blob, "sun.png");
        });
      },
    };

    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", onResize);
      controls.dispose();
      renderer.dispose();
      scene.clear();
      camApi.current = null;
    };
  }, [data, lat, lng, radiusM, shape]);

  const status = hours && view === "sunhours"
    ? `완료 — 낮 길이 ${hours.stats.dayLengthH}시간 · 평균 일조 ${hours.stats.meanH}시간 · 1시간 미만 ${hours.stats.below1hPct}%`
    : hoursBusy && view === "sunhours"
      ? "일조시간 계산 중…"
      : busy
        ? "모형을 준비하는 중…"
        : "";
  const status2 =
    hours && view === "sunhours"
      ? `09~15시 연속 2시간 확보 ${hours.stats.consec2hPct}% · 08~16시 총 4시간 ${hours.stats.total4hPct}%`
      : "";

  return (
    <div className="mass-wrap">
      <div className="mass-stage">
        <div className="mass-canvas" ref={mountRef} />
      </div>

      <aside className="mass-controls">
        <div className="ctrl-block">
          <span className="ctrl-label">보기</span>
          <div className="row toggles">
            <button
              type="button"
              className={view === "model" ? "on" : ""}
              onClick={() => setView("model")}
            >
              3D 모형
            </button>
            <button
              type="button"
              className={view === "sunhours" ? "on" : ""}
              onClick={() => setView("sunhours")}
            >
              일조시간 지도
            </button>
          </div>
        </div>

        <div className="ctrl-block">
          <span className="ctrl-label">날짜</span>
          <div className="row toggles sun-dates">
            {DATE_BTNS.map((b) => (
              <button
                key={b.id}
                type="button"
                className={kind === b.id ? "on" : ""}
                onClick={() => {
                  setKind(b.id);
                  if (b.id === "now") {
                    const n = new Date();
                    setMinutes(n.getHours() * 60 + n.getMinutes());
                  }
                }}
              >
                {b.label}
              </button>
            ))}
          </div>
        </div>

        {view === "model" ? (
          <label className="ctrl-block">
            시각 {minutesToHhmm(minutes)}
            <input
              type="range"
              min={0}
              max={1439}
              value={minutes}
              style={{ "--range": `${(minutes / 1439) * 100}%` } as CSSProperties}
              onChange={(e) => setMinutes(Number(e.target.value))}
            />
          </label>
        ) : null}

        {view === "model" ? (
          <div className="pal open mass-pal checks sun-checks">
            <label className="row">
              <input type="checkbox" checked={showShadow} onChange={(e) => setShowShadow(e.target.checked)} />
              <span>그림자</span>
            </label>
            <label className="row">
              <input type="checkbox" checked={showPath} onChange={(e) => setShowPath(e.target.checked)} />
              <span>태양 궤적</span>
            </label>
            <label className="row">
              <input type="checkbox" checked={showHourly} onChange={(e) => setShowHourly(e.target.checked)} />
              <span>시간별 태양</span>
            </label>
            <label className="row">
              <input type="checkbox" checked={showCompass} onChange={(e) => setShowCompass(e.target.checked)} />
              <span>방위 눈금</span>
            </label>
            <label className="row">
              <input type="checkbox" checked={castOnly} onChange={(e) => setCastOnly(e.target.checked)} />
              <span>드리운 그림자만</span>
            </label>
          </div>
        ) : null}

        <div className="ctrl-block">
          <span className="ctrl-label">시점</span>
          <div className="row toggles">
            <button type="button" onClick={() => camApi.current?.north()}>
              정북 정렬
            </button>
            <button type="button" onClick={() => camApi.current?.top()}>
              평면뷰
            </button>
          </div>
        </div>

        <p className="sun-meta">
          {dateLabel(kind, dt)} · 일출 {riseSet.rise != null ? minutesToHhmm(riseSet.rise) : "—"} · 일몰{" "}
          {riseSet.set != null ? minutesToHhmm(riseSet.set) : "—"} · 남중고도 {noonPos.altitudeDeg.toFixed(1)}°
          <br />
          현재 고도 {pos.altitudeDeg.toFixed(1)}° · 방위 {pos.azimuthDeg.toFixed(0)}° · UTC+9
        </p>

        <div className="row">
          <button type="button" onClick={() => camApi.current?.capture(false)}>
            PNG 저장
          </button>
          <button type="button" onClick={() => camApi.current?.capture(true)}>
            도판만 PNG
          </button>
        </div>

        {status ? <p className="sun-stats">{status}</p> : null}
        {status2 ? <p className="sun-stats">{status2}</p> : null}

        <p className="sun-note">
          창은 진태양시입니다 — 09~15시 = 이 대지 표준시 {minutesToHhmm(win.start)}~
          {minutesToHhmm(win.end)}.
        </p>
        <p className="sun-note">
          태양 위치는 대지의 위도·경도에서 계산합니다(NOAA 알고리즘) — 받아오는 자료가 없어 즉시
          그려집니다. 시각은 그 대지의 표준시이고 서머타임은 적용하지 않습니다.
        </p>
        <p className="sun-note">
          그림자는 세워진 건물만 드리웁니다 — 수목·차양·높이를 모르는 건물은 모형에 없습니다.
          형태와 향을 읽는 용도이지 법정 일조권 검토가 아닙니다.
        </p>
      </aside>
    </div>
  );
}
