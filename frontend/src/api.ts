const rawBase = import.meta.env.BASE_URL || "/";
export const API_BASE = rawBase.endsWith("/") ? rawBase.slice(0, -1) : rawBase;

export type PanelMeta = {
  key: string;
  label: string;
  ko: string;
  color: string | null;
  cats: { key: string; label: string; color: string; off?: boolean }[] | null;
  ramp: string[] | null;
  threshold: boolean;
  thr_default: number | null;
  metrics: [string, string][] | null;
  source?: string;
  color_locked?: boolean;
};

export async function fetchHealth() {
  const res = await fetch(`${API_BASE}/api/health`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function fetchPanels(): Promise<{ panels: PanelMeta[] }> {
  const res = await fetch(`${API_BASE}/api/panels`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function fetchPanelSvg(params: {
  panel: string;
  lat: number;
  lng: number;
  r: number;
  shape: "circle" | "rect";
  thr?: number;
  color?: string;
  includeAdjacent?: boolean;
  metric?: string;
}) {
  const q = new URLSearchParams({
    panel: params.panel,
    lat: String(params.lat),
    lng: String(params.lng),
    r: String(params.r),
    shape: params.shape,
  });
  if (params.thr != null) q.set("thr", String(params.thr));
  if (params.color) q.set("color", params.color);
  q.set("include_adjacent", params.includeAdjacent ? "true" : "false");
  if (params.metric) q.set("metric", params.metric);
  const res = await fetch(`${API_BASE}/api/panel?${q}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<{ panel: string; svg: string; feature_count?: number }>;
}

export type CityLngLat = [number, number];
export type CityRing = CityLngLat[];
export type CityPoly = CityRing[];

export type CityBounds = {
  min_lng: number;
  min_lat: number;
  max_lng: number;
  max_lat: number;
};

export type CityMeta = {
  rev: string;
  origin: { lng: number; lat: number };
  seocho_bounds: CityBounds;
  bounds: CityBounds;
  context_buffer_m: number;
  style: Record<string, unknown>;
};

export type CityBuilding = {
  u: string;
  h: number;
  rings: CityRing[];
};

export type CityRoad = {
  c: string;
  lines: CityRing[];
};

export type CityBuildings = {
  rev: string;
  items: CityBuilding[];
};

export type CityGround = {
  rev: string;
  boundary: CityPoly[];
  roads: CityRoad[];
  green: CityPoly[];
  water: CityPoly[];
  terrain: {
    lats: number[];
    lngs: number[];
    grid: number[][];
    min?: number;
    max?: number;
    relief_m?: number;
  } | null;
};

export type CityContext = {
  rev: string;
  plate: CityPoly[];
  buildings: CityRing[];
  roads: CityRoad[];
};

async function fetchJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<T>;
}

export function fetchCityMeta() {
  return fetchJson<CityMeta>("/api/city3d/meta");
}

export function fetchCityBuildings() {
  return fetchJson<CityBuildings>("/api/city3d/buildings");
}

export function fetchCityGround() {
  return fetchJson<CityGround>("/api/city3d/ground");
}

export function fetchCityContext() {
  return fetchJson<CityContext>("/api/city3d/context");
}

export async function fetchMass3d(params: {
  lat: number;
  lng: number;
  r: number;
  shape: "circle" | "rect";
  includeAdjacent?: boolean;
}) {
  const q = new URLSearchParams({
    lat: String(params.lat),
    lng: String(params.lng),
    r: String(params.r),
    shape: params.shape,
  });
  q.set("include_adjacent", params.includeAdjacent ? "true" : "false");
  const res = await fetch(`${API_BASE}/api/mass3d?${q}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function geocode(q: string) {
  const res = await fetch(`${API_BASE}/api/geocode?q=${encodeURIComponent(q)}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function reverseGeocode(
  lat: number,
  lng: number,
  signal?: AbortSignal,
) {
  const q = new URLSearchParams({ lat: String(lat), lng: String(lng) });
  const res = await fetch(`${API_BASE}/api/reverse-geocode?${q}`, { signal });
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<{ lat: number; lng: number; address: string | null }>;
}

function adminHeaders(adminToken: string): HeadersInit {
  return { "X-Admin-Token": adminToken };
}

async function adminError(res: Response): Promise<string> {
  const text = await res.text();
  try {
    const body = JSON.parse(text) as { detail?: unknown };
    if (typeof body.detail === "string" && body.detail) return body.detail;
  } catch {
    /* JSON이 아니면 원문을 그대로 쓴다. */
  }
  return text || `요청 실패 (${res.status})`;
}

export async function triggerSync(adminToken: string) {
  const res = await fetch(`${API_BASE}/api/admin/sync`, {
    method: "POST",
    headers: adminHeaders(adminToken),
  });
  if (!res.ok) throw new Error(await adminError(res));
  return res.json();
}

export async function reloadFixtures(adminToken: string, force = false) {
  const q = force ? "?force=true" : "";
  const res = await fetch(`${API_BASE}/api/admin/fixtures${q}`, {
    method: "POST",
    headers: adminHeaders(adminToken),
  });
  if (!res.ok) throw new Error(await adminError(res));
  return res.json();
}

export type SimChatTurn = { role: "user" | "assistant"; content: string };

export type SimApplyResult = {
  status: "ask" | "ok";
  reply_ko: string;
  ask_ko: string | null;
  spec: {
    use_cat: string;
    height_m: number;
    floors: number | null;
    typology: string;
    coverage: number;
    setback_m: number;
    gap_m: number;
    reply_ko: string;
    count: number | null;
  } | null;
  partial: Record<string, unknown> | null;
  session?: "grid" | "image_mass" | string;
  mass_count?: number;
  hide_ids: number[];
  buildings: {
    type: string;
    features: {
      properties: Record<string, unknown>;
      geometry: { type: string; coordinates: number[][][] | number[][][][] };
    }[];
  };
  area_m2: number;
  replaced_count: number;
  sim_count: number;
  action?: "edit" | "create";
  updates?: { id: number; height_m: number }[];
};

export async function applySim(params: {
  prompt: string;
  lat: number;
  lng: number;
  r: number;
  shape: "circle" | "rect";
  includeAdjacent?: boolean;
  buildingIds: number[];
  box: number[][] | null;
  history: SimChatTurn[];
  partial: Record<string, unknown> | null;
  imageB64?: string | null;
  imageMime?: string | null;
  session?: "auto" | "grid" | "image_mass";
  heightOverrides?: Record<number, number>;
}): Promise<SimApplyResult> {
  const res = await fetch(`${API_BASE}/api/sim/apply`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      prompt: params.prompt,
      lat: params.lat,
      lng: params.lng,
      r: params.r,
      shape: params.shape,
      include_adjacent: Boolean(params.includeAdjacent),
      building_ids: params.buildingIds,
      box: params.box,
      history: params.history,
      partial: params.partial,
      image_b64: params.imageB64 || null,
      image_mime: params.imageMime || null,
      session: params.session || "auto",
      height_overrides: Object.fromEntries(
        Object.entries(params.heightOverrides || {}).map(([id, height]) => [String(id), height]),
      ),
    }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export type AnalysisResult = {
  status: "ok" | "ask";
  action: "filter" | "brief" | "scenario" | "edit" | "ask" | "flood" | "timelapse";
  reply_ko: string;
  hide_ids: number[] | null;
  colors: Record<string, string> | null;
  match_count: number;
  updates: { id: number; height_m: number }[];
  rainfall_mm?: number | null;
  start_year?: number | null;
};

export async function askAnalysis(params: {
  prompt: string;
  action?: "auto" | "brief";
  lat: number;
  lng: number;
  r: number;
  shape: "circle" | "rect";
  includeAdjacent?: boolean;
  buildingIds: number[];
  box: number[][] | null;
}): Promise<AnalysisResult> {
  const res = await fetch(`${API_BASE}/api/analysis/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      prompt: params.prompt,
      action: params.action || "auto",
      lat: params.lat,
      lng: params.lng,
      r: params.r,
      shape: params.shape,
      include_adjacent: Boolean(params.includeAdjacent),
      building_ids: params.buildingIds,
      box: params.box,
    }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}
