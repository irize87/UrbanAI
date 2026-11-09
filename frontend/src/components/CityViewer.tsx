import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { MapControls } from "three/examples/jsm/controls/MapControls.js";
import { loadCityContext, loadCityCore } from "../cityCache";
import { appendCityContext, buildCityScene, disposeCity, type CityLayers } from "../cityWorld";
import { StarLoader } from "./StarLoader";

type Props = {
  active: boolean;
};

type Flags = {
  floor: boolean;
  terrain: boolean;
  roads: boolean;
  green: boolean;
  context: boolean;
};

function applyFlags(layers: CityLayers, flags: Flags): void {
  layers.floor.visible = flags.floor;
  layers.terrain.visible = flags.terrain;
  layers.roads.visible = flags.roads;
  layers.green.visible = flags.green;
  layers.plate.visible = flags.context;
  layers.contextBuildings.visible = flags.context;
  layers.contextRoads.visible = flags.context && flags.roads;
}

export function CityViewer({ active }: Props) {
  const mountRef = useRef<HTMLDivElement | null>(null);
  const layersRef = useRef<CityLayers | null>(null);
  const resizeRef = useRef<(() => void) | null>(null);
  const activeRef = useRef(active);
  const [status, setStatus] = useState("서초 전역 모델을 준비하는 중…");
  const [loading, setLoading] = useState(true);
  const [progress, setProgress] = useState(0);
  const [showFloor, setShowFloor] = useState(true);
  const [showTerrain, setShowTerrain] = useState(true);
  const [showRoads, setShowRoads] = useState(true);
  const [showGreen, setShowGreen] = useState(true);
  const [showContext, setShowContext] = useState(true);
  const flagsRef = useRef<Flags>({
    floor: true,
    terrain: true,
    roads: true,
    green: true,
    context: true,
  });

  useEffect(() => {
    activeRef.current = active;
    if (active) resizeRef.current?.();
  }, [active]);

  useEffect(() => {
    flagsRef.current = {
      floor: showFloor,
      terrain: showTerrain,
      roads: showRoads,
      green: showGreen,
      context: showContext,
    };
    if (layersRef.current) applyFlags(layersRef.current, flagsRef.current);
  }, [showFloor, showTerrain, showRoads, showGreen, showContext]);

  useEffect(() => {
    const el = mountRef.current;
    if (!el) return;
    const scene = new THREE.Scene();
    scene.background = new THREE.Color("#ffffff");
    const camera = new THREE.PerspectiveCamera(42, 1, 1, 50000);
    const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
    renderer.shadowMap.enabled = false;
    el.appendChild(renderer.domElement);
    const controls = new MapControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.maxPolarAngle = Math.PI / 2.05;
    controls.zoomToCursor = true;
    scene.add(new THREE.AmbientLight(0xffffff, 0.58));
    scene.add(new THREE.HemisphereLight(0xffffff, 0xcdd0d4, 0.18));
    const key = new THREE.DirectionalLight(0xffffff, 0.55);
    key.position.set(0.85, 1.15, 0.32);
    scene.add(key);
    const fill = new THREE.DirectionalLight(0xffffff, 0.12);
    fill.position.set(-0.55, 0.35, -0.7);
    scene.add(fill);

    const resize = () => {
      const width = el.clientWidth || 640;
      const height = el.clientHeight || 460;
      camera.aspect = width / Math.max(1, height);
      camera.updateProjectionMatrix();
      renderer.setSize(width, height);
    };
    resize();
    resizeRef.current = resize;
    const observer = new ResizeObserver(resize);
    observer.observe(el);
    scene.add(key.target);

    let stopped = false;
    let builtRoot: THREE.Object3D | null = null;
    const abort = new AbortController();
    const tick = () => {
      if (!activeRef.current) return;
      controls.update();
      renderer.render(scene, camera);
    };
    renderer.setAnimationLoop(tick);

    void (async () => {
      const report = (message: string, ratio: number) => {
        if (stopped) return;
        setStatus(message);
        setProgress(Math.max(0, Math.min(1, ratio)));
      };
      try {
        const bundle = await loadCityCore((message) => {
          let ratio = 0.08;
          if (message.includes("버전")) ratio = 0.04;
          else if (message.includes("건물")) ratio = 0.16;
          else if (message.includes("바닥")) ratio = 0.28;
          report(message, ratio);
        });
        if (stopped) return;
        const contextTask = loadCityContext(bundle.meta.rev, () => undefined);
        const fit = (built: Awaited<ReturnType<typeof buildCityScene>>) => {
          built.root.updateMatrixWorld(true);
          const box = new THREE.Box3().setFromObject(built.root);
          const center = box.getCenter(new THREE.Vector3());
          const size = box.getSize(new THREE.Vector3());
          const span = Math.max(size.x, size.z, built.span, 400);
          const dist = span * 0.78;
          camera.near = Math.max(1, span / 250);
          camera.far = span * 14;
          camera.position.set(center.x + dist * 0.28, dist * 0.7, center.z + dist * 0.42);
          controls.target.set(center.x, Math.min(size.y * 0.15, 80), center.z);
          controls.minDistance = span * 0.02;
          controls.maxDistance = span * 4;
          controls.update();
          key.position.set(center.x + span * 0.4, span * 0.9, center.z + span * 0.2);
          key.target.position.copy(controls.target);
          resize();
        };
        const built = await buildCityScene(
          bundle,
          (message, ratio) => report(message, ratio),
          abort.signal,
          (shell) => {
            if (stopped || builtRoot) return;
            scene.add(shell.root);
            builtRoot = shell.root;
            layersRef.current = shell.layers;
            applyFlags(shell.layers, flagsRef.current);
            fit(shell);
          },
        );
        if (stopped) return;
        if (!builtRoot) {
          scene.add(built.root);
          builtRoot = built.root;
          layersRef.current = built.layers;
          fit(built);
        }
        applyFlags(built.layers, flagsRef.current);
        const source = bundle.fromCache ? "저장된 데이터" : "서버에서 받은 데이터";
        const seochoCount = bundle.buildings.items.length.toLocaleString("ko-KR");
        setProgress(0.92);
        setLoading(false);
        setStatus(`서초 ${seochoCount}동 · 경계 밖을 붙이는 중… · ${source}`);
        const context = await contextTask;
        if (stopped) return;
        bundle.context = context.data;
        await appendCityContext(built, bundle, abort.signal);
        if (stopped) return;
        const outside = context.data.buildings.length.toLocaleString("ko-KR");
        setProgress(1);
        setStatus(`서초 ${seochoCount}동 · 경계 밖 ${outside}동 · ${source}`);
      } catch (error) {
        if (stopped || (error instanceof DOMException && error.name === "AbortError")) return;
        setLoading(false);
        setStatus(error instanceof Error ? error.message : String(error));
      }
    })();

    return () => {
      stopped = true;
      resizeRef.current = null;
      abort.abort();
      observer.disconnect();
      renderer.setAnimationLoop(null);
      controls.dispose();
      if (builtRoot) disposeCity(builtRoot);
      layersRef.current = null;
      renderer.dispose();
      el.innerHTML = "";
    };
  }, []);

  return (
    <div className="mass-wrap">
      <div className="mass-stage">
        <div ref={mountRef} className="mass-canvas" />
        {loading ? (
          <div className="mass-load" role="status">
            <StarLoader count={3} size={72} gap={18} speed={1.15} />
            <p>{status}</p>
            <div className="city-progress" aria-hidden="true">
              <div style={{ width: `${Math.round(progress * 100)}%` }} />
            </div>
            <p className="city-progress-label">{Math.round(progress * 100)}%</p>
          </div>
        ) : (
          <p className="city-status">{status}</p>
        )}
      </div>
      <aside className="mass-controls">
        <p className="hint">
          서초구 도시 3D 입체 모델링입니다.
          <br />
          <br />
          서초구 행정구역 경계 인근 600m까지를 표시합니다.
          <br />
          <br />
          드래그 : 이동
          <br />
          우클릭 드래그 : 회전
          <br />
          휠 : 확대/축소
        </p>
        <div className="pal open mass-pal checks">
          <label className="row">
            <input type="checkbox" checked={showFloor} onChange={(e) => setShowFloor(e.target.checked)} />
            <span>바닥판</span>
          </label>
          <label className="row">
            <input
              type="checkbox"
              checked={showTerrain}
              onChange={(e) => setShowTerrain(e.target.checked)}
            />
            <span>지형</span>
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
            <input
              type="checkbox"
              checked={showContext}
              onChange={(e) => setShowContext(e.target.checked)}
            />
            <span>경계 밖 2D</span>
          </label>
        </div>
      </aside>
    </div>
  );
}
