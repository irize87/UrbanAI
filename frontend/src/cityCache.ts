import {
  fetchCityBuildings,
  fetchCityContext,
  fetchCityGround,
  fetchCityMeta,
  type CityBuildings,
  type CityContext,
  type CityGround,
  type CityMeta,
} from "./api";

export type CityBundle = {
  meta: CityMeta;
  buildings: CityBuildings;
  ground: CityGround;
  context: CityContext;
  fromCache: boolean;
};

const DB_NAME = "sitedia-city";
const STORE = "layers";

type LayerName = "buildings" | "ground" | "context";

let dbPromise: Promise<IDBDatabase | null> | null = null;

function openDb(): Promise<IDBDatabase | null> {
  if (dbPromise) return dbPromise;
  dbPromise = new Promise((resolve) => {
    if (typeof indexedDB === "undefined") {
      resolve(null);
      return;
    }
    const request = indexedDB.open(DB_NAME, 1);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(STORE)) db.createObjectStore(STORE);
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => resolve(null);
  });
  return dbPromise;
}

function idbGet(key: string): Promise<unknown | null> {
  return openDb().then(
    (db) =>
      new Promise((resolve) => {
        if (!db) {
          resolve(null);
          return;
        }
        const tx = db.transaction(STORE, "readonly");
        const request = tx.objectStore(STORE).get(key);
        request.onsuccess = () => resolve(request.result ?? null);
        request.onerror = () => resolve(null);
      }),
  );
}

function idbPut(key: string, value: unknown): Promise<void> {
  return openDb().then(
    (db) =>
      new Promise((resolve) => {
        if (!db) {
          resolve();
          return;
        }
        const tx = db.transaction(STORE, "readwrite");
        tx.objectStore(STORE).put(value, key);
        tx.oncomplete = () => resolve();
        tx.onerror = () => resolve();
      }),
  );
}

async function dropOtherRevisions(rev: string): Promise<void> {
  const db = await openDb();
  if (!db) return;
  await new Promise<void>((resolve) => {
    const tx = db.transaction(STORE, "readwrite");
    const store = tx.objectStore(STORE);
    const request = store.getAllKeys();
    request.onsuccess = () => {
      const keys = (request.result || []) as IDBValidKey[];
      for (const key of keys) {
        if (typeof key === "string" && !key.startsWith(`${rev}:`)) store.delete(key);
      }
    };
    tx.oncomplete = () => resolve();
    tx.onerror = () => resolve();
  });
}

async function loadLayer<T extends { rev: string }>(
  rev: string,
  name: LayerName,
  label: string,
  fetcher: () => Promise<T>,
  onStatus: (message: string) => void,
): Promise<{ data: T; fromCache: boolean }> {
  const key = `${rev}:${name}`;
  const cached = (await idbGet(key)) as T | null;
  if (cached && cached.rev === rev) {
    onStatus(`저장된 ${label}을 읽는 중…`);
    return { data: cached, fromCache: true };
  }
  onStatus(`서버에서 ${label}을 받는 중…`);
  const fresh = await fetcher();
  if (fresh.rev === rev) await idbPut(key, fresh);
  return { data: fresh, fromCache: false };
}

/** 버전을 확인한 뒤 건물·바닥·주변 레이어를 캐시 또는 서버에서 모은다. */
export async function loadCityBundle(
  onStatus: (message: string) => void,
): Promise<CityBundle> {
  onStatus("데이터 버전을 확인하는 중…");
  const meta = await fetchCityMeta();
  const [buildings, ground, context] = await Promise.all([
    loadLayer(meta.rev, "buildings", "서초 건물", fetchCityBuildings, onStatus),
    loadLayer(meta.rev, "ground", "서초 바닥", fetchCityGround, onStatus),
    loadLayer(meta.rev, "context", "경계 밖 바닥", fetchCityContext, onStatus),
  ]);
  const fromCache = buildings.fromCache && ground.fromCache && context.fromCache;
  void dropOtherRevisions(meta.rev);
  return {
    meta,
    buildings: buildings.data,
    ground: ground.data,
    context: context.data,
    fromCache,
  };
}
