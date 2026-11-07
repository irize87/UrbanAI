import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import JSZip from "jszip";
import {
  fetchHealth,
  fetchMass3d,
  fetchPanelSvg,
  fetchPanels,
  geocode,
  reverseGeocode,
  triggerSync,
  type PanelMeta,
} from "./api";
import { SiteMap, type BasemapId } from "./components/SiteMap";
import { MassViewer, type MassData } from "./components/MassViewer";
import { SunViewer } from "./components/SunViewer";
import { CityViewer } from "./components/CityViewer";
import { DiagramCell, DiagramLegend } from "./components/DiagramCell";
import { HIGH_ACCENT, LOWRISE_ACCENT, stylePanelSvg } from "./palette";
import seochoMark from "./assets/seocho-mark.png";
import "./App.css";

const DEFAULT = { lat: 37.4837, lng: 127.0324 };
const PNG_PX = { min: 1000, max: 4000, step: 100, def: 2000 };

const BASEMAPS: {
  id: BasemapId;
  label: string;
  needsKey?: "vworld" | "carto";
}[] = [
  { id: "vworld", label: "VWorld", needsKey: "vworld" },
  { id: "osm", label: "상세지도" },
  { id: "carto", label: "단순지도", needsKey: "carto" },
];

export default function App() {
  const [lat, setLat] = useState(DEFAULT.lat);
  const [lng, setLng] = useState(DEFAULT.lng);
  const [siteAddress, setSiteAddress] = useState("");
  const [pngPx, setPngPx] = useState(PNG_PX.def);
  const [radius, setRadius] = useState(800);
  const [shape, setShape] = useState<"circle" | "rect">("circle");
  const [panels, setPanels] = useState<PanelMeta[]>([]);
  const [svgs, setSvgs] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [health, setHealth] = useState<{
    vworld_key_configured?: boolean;
    carto_key_configured?: boolean;
    cdse_key_configured?: boolean;
    clova_key_configured?: boolean;
    seoul_open_key_configured?: boolean;
    building_count?: number;
    street_tree_count?: number;
    using_fixtures?: boolean;
    data_mode?: string;
    sync_running?: boolean;
    sync_message?: string | null;
    bbox?: { min_lat: number; min_lng: number; max_lat: number; max_lng: number };
  } | null>(null);
  const [query, setQuery] = useState("");
  const [mass, setMass] = useState<MassData | null>(null);
  const [massStamp, setMassStamp] = useState<{
    lat: number;
    lng: number;
    r: number;
    shape: "circle" | "rect";
    adj: boolean;
  } | null>(null);
  const massReq = useRef(0);
  const [loadingKeys, setLoadingKeys] = useState<string[]>([]);
  const [includeAdjacent, setIncludeAdjacent] = useState(true);
  const [massBusy, setMassBusy] = useState(false);
  const [tab, setTab] = useState<"site" | "mass" | "sim" | "sun" | "diagrams" | "city" | "settings">("site");
  const [cityOpened, setCityOpened] = useState(false);
  const [basemap, setBasemap] = useState<BasemapId>("vworld");
  const [basemapOpen, setBasemapOpen] = useState(false);
  const [accents, setAccents] = useState<Record<string, string>>({
    high: HIGH_ACCENT,
    lowrise: LOWRISE_ACCENT,
  });
  const [catColors, setCatColors] = useState<Record<string, Record<string, string>>>({});
  const [catOff, setCatOff] = useState<Record<string, string[]>>({});
  const [thresholds, setThresholds] = useState<Record<string, number>>({
    high: 35,
    lowrise: 12,
  });
  const [heatMetric, setHeatMetric] = useState("area");
  const [adminToken, setAdminToken] = useState("");
  const [syncNote, setSyncNote] = useState("");
  const thrTimer = useRef<number | null>(null);

  useEffect(() => {
    if (!basemapOpen) return;
    const close = () => setBasemapOpen(false);
    window.addEventListener("click", close);
    return () => window.removeEventListener("click", close);
  }, [basemapOpen]);

  const bbox = useMemo(
    () =>
      health?.bbox || {
        min_lat: 37.45,
        min_lng: 126.98,
        max_lat: 37.52,
        max_lng: 127.08,
      },
    [health],
  );

  useEffect(() => {
    void (async () => {
      try {
        const [h, p] = await Promise.all([fetchHealth(), fetchPanels()]);
        setHealth(h);
        setPanels(p.panels);
        const nextAccents: Record<string, string> = {};
        const nextCats: Record<string, Record<string, string>> = {};
        const nextOff: Record<string, string[]> = {};
        const nextThr: Record<string, number> = {};
        for (const panel of p.panels) {
          if (panel.key === "high") nextAccents[panel.key] = HIGH_ACCENT;
          else if (panel.key === "lowrise") nextAccents[panel.key] = LOWRISE_ACCENT;
          else if (panel.color) nextAccents[panel.key] = panel.color;
          if (panel.cats) {
            nextCats[panel.key] = Object.fromEntries(
              panel.cats.map((c) => [c.key, c.color]),
            );
            nextOff[panel.key] = panel.cats.filter((c) => c.off).map((c) => c.key);
          }
          if (panel.threshold && panel.thr_default != null) {
            nextThr[panel.key] = panel.thr_default;
          }
          if (panel.key === "heat" && panel.metrics?.[0]?.[0]) {
            setHeatMetric(panel.metrics[0][0]);
          }
        }
        setAccents(nextAccents);
        setCatColors(nextCats);
        setCatOff(nextOff);
        setThresholds((prev) => ({ ...prev, ...nextThr }));
      } catch (e) {
        setStatus(`API 연결 실패: ${e}`);
      }
    })();
  }, []);

  useEffect(() => {
    if (!health?.sync_running) return;
    const id = window.setInterval(() => {
      void fetchHealth().then(setHealth).catch(() => undefined);
    }, 4000);
    return () => window.clearInterval(id);
  }, [health?.sync_running]);

  const onMove = useCallback((nlat: number, nlng: number) => {
    setLat(nlat);
    setLng(nlng);
  }, []);

  useEffect(() => {
    const ac = new AbortController();
    const timer = window.setTimeout(() => {
      void reverseGeocode(lat, lng, ac.signal)
        .then((r) => {
          if (r.address) setSiteAddress(r.address);
        })
        .catch((err: unknown) => {
          if (err instanceof DOMException && err.name === "AbortError") return;
        });
    }, 280);
    return () => {
      ac.abort();
      window.clearTimeout(timer);
    };
  }, [lat, lng]);

  const loadMass = async (adj = includeAdjacent) => {
    const req = ++massReq.current;
    const here = { lat, lng, r: radius, shape, adj };
    setMassBusy(true);
    setMass(null);
    setMassStamp(null);
    try {
      const m = await fetchMass3d({
        lat: here.lat,
        lng: here.lng,
        r: here.r,
        shape: here.shape,
        includeAdjacent: here.adj,
      });
      if (req !== massReq.current) return;
      setMass(m);
      setMassStamp(here);
    } finally {
      if (req === massReq.current) setMassBusy(false);
    }
  };

  const massIsCurrent = () =>
    !!mass &&
    !!massStamp &&
    Math.abs(massStamp.lat - lat) < 1e-7 &&
    Math.abs(massStamp.lng - lng) < 1e-7 &&
    massStamp.r === radius &&
    massStamp.shape === shape &&
    massStamp.adj === includeAdjacent;

  const open3d = (next: "mass" | "sim" | "sun") => {
    setTab(next);
    if (!massIsCurrent()) void loadMass().catch((err) => setStatus(String(err)));
  };

  const generate = async () => {
    const heavy = new Set(["sun_hours", "trees", "aerial"]);
    const keys = [
      ...panels.filter((p) => !heavy.has(p.key)),
      ...panels.filter((p) => heavy.has(p.key)),
    ];
    setBusy(true);
    setSvgs({});
    setLoadingKeys(keys.map((p) => p.key));
    setTab("diagrams");
    setStatus("다이어그램 생성 중…");
    try {
      let done = 0;
      let cursor = 0;
      const limit = Math.min(4, keys.length);
      const workers = Array.from({ length: limit }, async () => {
        while (cursor < keys.length) {
          const p = keys[cursor];
          cursor += 1;
          const res = await fetchPanelSvg({
            panel: p.key,
            lat,
            lng,
            r: radius,
            shape,
            thr: p.threshold ? thresholds[p.key] : undefined,
            includeAdjacent,
            metric: p.key === "heat" ? heatMetric : undefined,
          });
          done += 1;
          setSvgs((prev) => ({ ...prev, [p.key]: res.svg }));
          setLoadingKeys((prev) => prev.filter((k) => k !== p.key));
          setStatus(
            p.key === "sun_hours" ? "일조시간 계산 중…" : `생성 중… ${done}장`,
          );
        }
      });
      await Promise.all(workers);
      void loadMass(includeAdjacent).catch((e) => setStatus(String(e)));
      setStatus(`완료 — ${done}장`);
    } catch (e) {
      setStatus(String(e));
    } finally {
      setLoadingKeys([]);
      setBusy(false);
    }
  };

  const refetchPanel = async (key: string, thr?: number, metric?: string) => {
    setLoadingKeys((prev) => (prev.includes(key) ? prev : [...prev, key]));
    setSvgs((prev) => {
      const next = { ...prev };
      delete next[key];
      return next;
    });
    try {
      const res = await fetchPanelSvg({
        panel: key,
        lat,
        lng,
        r: radius,
        shape,
        thr,
        includeAdjacent,
        metric: key === "heat" ? (metric || heatMetric) : undefined,
      });
      setSvgs((prev) => ({ ...prev, [key]: res.svg }));
    } finally {
      setLoadingKeys((prev) => prev.filter((k) => k !== key));
    }
  };

  const downloadZip = async () => {
    const zip = new JSZip();
    Object.entries(svgs).forEach(([k, svg]) => {
      const p = panels.find((x) => x.key === k);
      const styled = p
        ? stylePanelSvg(svg, k, {
            accent: accents[k],
            cats: catColors[k],
            off: catOff[k],
          })
        : svg;
      zip.file(`${k}.svg`, styled);
    });
    const blob = await zip.generateAsync({ type: "blob" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `sitedia-seocho-${lat.toFixed(5)}-${lng.toFixed(5)}.zip`;
    a.click();
  };

  const search = async () => {
    try {
      const res = await geocode(query);
      const hit = (res.items || []).find((x: { in_seocho: boolean }) => x.in_seocho);
      if (!hit) {
        setStatus("서초구 내 결과가 없습니다. (VWorld 키 필요)");
        return;
      }
      setLat(hit.lat);
      setLng(hit.lng);
      setSiteAddress(String(hit.address || hit.title || ""));
      setStatus(`이동: ${hit.address || hit.title}`);
    } catch (e) {
      setStatus(String(e));
    }
  };

  return (
    <div className="app">
      <header className="hero">
        <h1 className="app-title">
          <img className="app-title-mark" src={seochoMark} alt="서초" />
          <span className="app-title-text">AI 도시 관리 시스템</span>
        </h1>
      </header>

      <nav className="tabs" role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "site"}
          className={tab === "site" ? "on" : ""}
          onClick={() => setTab("site")}
        >
          <span className="no">01</span>지도
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "mass"}
          className={tab === "mass" ? "on" : ""}
          onClick={() => open3d("mass")}
        >
          <span className="no">02</span>3D 시각화
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "sim"}
          className={tab === "sim" ? "on" : ""}
          onClick={() => open3d("sim")}
        >
          <span className="no">03</span>AI 도시 분석
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "sun"}
          className={tab === "sun" ? "on" : ""}
          onClick={() => open3d("sun")}
        >
          <span className="no">04</span>일조·그림자
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "diagrams"}
          className={tab === "diagrams" ? "on" : ""}
          onClick={() => setTab("diagrams")}
        >
          <span className="no">05</span>다이어그램
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "city"}
          className={tab === "city" ? "on" : ""}
          onClick={() => {
            setCityOpened(true);
            setTab("city");
          }}
        >
          <span className="no">06</span>전역 3D 지도
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "settings"}
          className={tab === "settings" ? "on" : ""}
          onClick={() => setTab("settings")}
        >
          <span className="no">07</span>정보
        </button>
      </nav>

      <div className="tab-body">
      {tab === "site" && (
      <div className="workspace">
        <section className="map-pane">
          <div
            className={`basemap-dropdown${basemapOpen ? " open" : ""}`}
            onClick={(e) => e.stopPropagation()}
          >
            <button
              type="button"
              className="basemap-trigger"
              aria-haspopup="listbox"
              aria-expanded={basemapOpen}
              onClick={() => setBasemapOpen((v) => !v)}
            >
              {BASEMAPS.find((b) => b.id === basemap)?.label ?? "VWorld"}
              <span className="caret" aria-hidden>
                ▾
              </span>
            </button>
            {basemapOpen && (
              <ul className="basemap-menu" role="listbox">
                {BASEMAPS.map((b) => {
                  const locked =
                    (b.needsKey === "vworld" && !health?.vworld_key_configured) ||
                    (b.needsKey === "carto" && !health?.carto_key_configured);
                  const lockHint =
                    b.needsKey === "vworld"
                      ? ".env에 VWORLD_API_KEY가 필요합니다"
                      : ".env에 CARTO_API_KEY가 필요합니다";
                  return (
                    <li key={b.id}>
                      <button
                        type="button"
                        role="option"
                        aria-selected={basemap === b.id}
                        className={basemap === b.id ? "on" : ""}
                        disabled={locked}
                        title={locked ? lockHint : b.label}
                        onClick={() => {
                          if (locked) return;
                          setBasemap(b.id);
                          setBasemapOpen(false);
                        }}
                      >
                        {b.label}
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
          <SiteMap
            lat={lat}
            lng={lng}
            radiusM={radius}
            shape={shape}
            bbox={bbox}
            basemap={
              (basemap === "vworld" && !health?.vworld_key_configured) ||
              (basemap === "carto" && !health?.carto_key_configured)
                ? "osm"
                : basemap
            }
            vworldReady={!!health?.vworld_key_configured}
            onMove={onMove}
          />
        </section>

        <aside className="side">
          <label>
            주소 검색
            <div className="row">
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="예: 서초동 / 서초대로"
                onKeyDown={(e) => e.key === "Enter" && void search()}
              />
              <button type="button" onClick={() => void search()}>
                검색
              </button>
            </div>
          </label>

          <div className="site-place">
            <div className="site-place-addr">
              <span className="site-dot" aria-hidden />
              <strong>{siteAddress || "선택한 위치"}</strong>
            </div>
            <div className="site-place-xy">
              {lat.toFixed(5)}, {lng.toFixed(5)}
            </div>
          </div>

          <div className="row toggles">
            <button
              type="button"
              className={shape === "circle" ? "on" : ""}
              onClick={() => setShape("circle")}
            >
              원형
            </button>
            <button
              type="button"
              className={shape === "rect" ? "on" : ""}
              onClick={() => setShape("rect")}
            >
              사각형
            </button>
          </div>

          <label>
            분석 반경 {radius}m
            <input
              type="range"
              min={200}
              max={1500}
              step={50}
              value={radius}
              style={{ "--range": `${((radius - 200) / 1300) * 100}%` } as CSSProperties}
              onChange={(e) => setRadius(Number(e.target.value))}
            />
          </label>

          <label className="range-field">
            <span className="range-head">
              <span>PNG 해상도</span>
              <span>{pngPx}px</span>
            </span>
            <input
              type="range"
              min={PNG_PX.min}
              max={PNG_PX.max}
              step={PNG_PX.step}
              value={pngPx}
              style={
                {
                  "--range": `${((pngPx - PNG_PX.min) / (PNG_PX.max - PNG_PX.min)) * 100}%`,
                } as CSSProperties
              }
              onChange={(e) => setPngPx(Number(e.target.value))}
            />
          </label>

          <label className="adj-check">
            <input
              type="checkbox"
              checked={includeAdjacent}
              onChange={(e) => setIncludeAdjacent(e.target.checked)}
            />
            인접지역 포함
          </label>

          <button type="button" className="primary" disabled={busy} onClick={() => void generate()}>
            {busy ? "분석 중…" : "분석 시작"}
          </button>
          <button type="button" disabled={!Object.keys(svgs).length} onClick={() => void downloadZip()}>
            SVG 전체 받기 (ZIP)
          </button>
          {health?.using_fixtures ? (
            <p className="data-warn">
              지금 DB는 개발용 샘플 {health.building_count}동입니다. 실제 서초 건물·필지가
              아닙니다. 정보 탭에서 <strong>실데이터 동기화</strong>를 실행한 뒤 다시 생성하세요.
            </p>
          ) : null}
          {health?.sync_running ? (
            <p className="data-warn on">{health.sync_message || "실데이터 받는 중…"}</p>
          ) : null}

          <p className="status">{status}</p>
        </aside>
      </div>
      )}

      {tab === "mass" && (
        <section className="mass-section">
          <MassViewer
            data={mass}
            lat={lat}
            lng={lng}
            radiusM={radius}
            shape={shape}
            includeAdjacent={includeAdjacent}
            adjacentBusy={massBusy}
            onIncludeAdjacentChange={(v) => {
              setIncludeAdjacent(v);
              void loadMass(v).catch((e) => setStatus(String(e)));
            }}
          />
        </section>
      )}

      {tab === "sim" && (
        <section className="mass-section">
          <MassViewer
            mode="sim"
            data={mass}
            lat={lat}
            lng={lng}
            radiusM={radius}
            shape={shape}
            includeAdjacent={includeAdjacent}
            adjacentBusy={massBusy}
            onIncludeAdjacentChange={(v) => {
              setIncludeAdjacent(v);
              void loadMass(v).catch((e) => setStatus(String(e)));
            }}
          />
        </section>
      )}

      {tab === "sun" && (
        <section className="mass-section">
          <SunViewer
            data={mass}
            lat={lat}
            lng={lng}
            radiusM={radius}
            shape={shape}
            busy={massBusy}
          />
        </section>
      )}

      {cityOpened && (
        <section className="mass-section" hidden={tab !== "city"}>
          <CityViewer active={tab === "city"} />
        </section>
      )}

      {tab === "diagrams" && (
        <section className="diagrams-section sheet-on">
          <div className="shead">
            <h2>다이어그램</h2>
          </div>
          <div className="gallery">
            {panels.map((p) => (
                <DiagramCell
                  key={p.key}
                  panel={p}
                  svg={svgs[p.key]}
                  loading={loadingKeys.includes(p.key)}
                  accent={
                    p.key === "high"
                      ? (accents.high || HIGH_ACCENT)
                      : p.key === "lowrise"
                        ? (accents.lowrise || LOWRISE_ACCENT)
                        : (accents[p.key] ?? p.color)
                  }
                  cats={catColors[p.key] || {}}
                  off={catOff[p.key] || []}
                  threshold={p.threshold ? (thresholds[p.key] ?? p.thr_default) : null}
                  metric={p.key === "heat" ? heatMetric : null}
                  pngPx={pngPx}
                  onAccent={(color) =>
                    setAccents((prev) => ({ ...prev, [p.key]: color }))
                  }
                  onCatColor={(key, color) =>
                    setCatColors((prev) => ({
                      ...prev,
                      [p.key]: { ...(prev[p.key] || {}), [key]: color },
                    }))
                  }
                  onToggleCat={(key, on) =>
                    setCatOff((prev) => {
                      const cur = new Set(prev[p.key] || []);
                      if (on) cur.delete(key);
                      else cur.add(key);
                      return { ...prev, [p.key]: [...cur] };
                    })
                  }
                  onToggleAll={(on) =>
                    setCatOff((prev) => ({
                      ...prev,
                      [p.key]: on ? [] : (p.cats || []).map((c) => c.key),
                    }))
                  }
                  onResetColors={() =>
                    setCatColors((prev) => ({
                      ...prev,
                      [p.key]: Object.fromEntries(
                        (p.cats || []).map((c) => [c.key, c.color]),
                      ),
                    }))
                  }
                  onThreshold={(value) => {
                    setThresholds((prev) => ({ ...prev, [p.key]: value }));
                    if (thrTimer.current) window.clearTimeout(thrTimer.current);
                    thrTimer.current = window.setTimeout(() => {
                      if (svgs[p.key] && value >= 3 && value <= 300) {
                        void refetchPanel(p.key, value);
                      }
                    }, 400) as unknown as number;
                  }}
                  onMetric={(value) => {
                    setHeatMetric(value);
                    if (svgs[p.key]) void refetchPanel(p.key, undefined, value);
                  }}
                />
              ))}
          </div>
          {Object.keys(svgs).length > 0 ? (
            <DiagramLegend
              panels={panels}
              catColors={catColors}
              catOff={catOff}
              accents={accents}
            />
          ) : null}
        </section>
      )}

      {tab === "settings" && (
        <section className="settings sheet-on">
          <h2>현재 데이터 현황</h2>
          <div className="stats">
            <div>
              <span>건물(DB)</span>
              <strong>{health?.building_count ?? "—"}</strong>
              {health?.using_fixtures ? <em className="stat-note">샘플</em> : null}
            </div>
            <div>
              <span>가로수(DB)</span>
              <strong>{health?.street_tree_count ?? "—"}</strong>
            </div>
            <div>
              <span>범위</span>
              <strong>서초구</strong>
            </div>
            <div>
              <span>VWorld</span>
              <strong>{health?.vworld_key_configured ? "설정됨" : "미설정"}</strong>
            </div>
            <div>
              <span>서울 OpenAPI</span>
              <strong>{health?.seoul_open_key_configured ? "설정됨" : "미설정"}</strong>
            </div>
          </div>
          <h2>데이터 출처</h2>
          <table className="sources-table">
            <thead>
              <tr>
                <th>항목</th>
                <th>출처</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>건물·높이·용도·준공일</td>
                <td>국토교통부 GIS건물통합정보 + 도로명주소 건물(VWorld) 보강</td>
              </tr>
              <tr>
                <td>용도지역·공원녹지·지적</td>
                <td>VWorld(국토교통부 공간정보 오픈플랫폼)</td>
              </tr>
              <tr>
                <td>도로·가구·역·정류장</td>
                <td>OpenStreetMap 기여자</td>
              </tr>
              <tr>
                <td>지형·등고선·경사도</td>
                <td>Copernicus DEM (CDSE Sentinel Hub Process API)</td>
              </tr>
              <tr>
                <td>항공사진·수목</td>
                <td>VWorld 항공영상 RGB HSV+ExG (수관·개체)</td>
              </tr>
              <tr>
                <td>가로수</td>
                <td>서울열린데이터광장 가로수 위치 (큰 도로 빈자리 보강, 월간 로컬 저장)</td>
              </tr>
              <tr>
                <td>일조·그림자</td>
                <td>대지 위도·경도 NOAA 태양위치 + 높이 있는 건물 그림자(브라우저)</td>
              </tr>
            </tbody>
          </table>
          <div className="admin-sync">
            <input
              type="password"
              value={adminToken}
              autoComplete="off"
              placeholder="관리 토큰"
              aria-label="관리 토큰"
              onChange={(event) => setAdminToken(event.target.value)}
            />
            <button
              type="button"
              className="primary"
              disabled={Boolean(health?.sync_running) || !adminToken.trim()}
              onClick={async () => {
                try {
                  const result = await triggerSync(adminToken.trim());
                  const note = result.message || "실데이터 동기화 시작";
                  setSyncNote(note);
                  setStatus(note);
                  const next = await fetchHealth();
                  setHealth(next);
                } catch (err) {
                  const note = err instanceof Error ? err.message : "동기화 요청 실패";
                  setSyncNote(note);
                  setStatus(note);
                }
              }}
            >
              {health?.sync_running ? "동기화 중…" : "실데이터 동기화"}
            </button>
          </div>
          {syncNote ? <p className="status">{syncNote}</p> : null}
        </section>
      )}
      </div>
    </div>
  );
}
