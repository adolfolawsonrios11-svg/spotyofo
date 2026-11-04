/* SpotyOFO: descargas offline de Me gusta (Cache Storage + IndexedDB).
 * La cuenta demo no puede descargar (misma idea que Mis radios).
 * Al cerrar sesión el service worker borra las cachés; aquí también limpiamos el índice. */
const DB_NAME = "spotyofo-offline";
const DB_VERSION = 1;
const STORE = "tracks";
export const OFFLINE_CACHE = "spotyofo-offline-audio";
const DATA_SAVER_KEY = "spotyofo-data-saver";

function openDb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE)) {
        db.createObjectStore(STORE, { keyPath: "id" });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error || new Error("IndexedDB"));
  });
}

function txDone(tx) {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error || new Error("IDB tx"));
    tx.onabort = () => reject(tx.error || new Error("IDB abort"));
  });
}

async function allMeta() {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, "readonly");
    const req = tx.objectStore(STORE).getAll();
    req.onsuccess = () => resolve(req.result || []);
    req.onerror = () => reject(req.error);
  });
}

async function getMeta(id) {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, "readonly");
    const req = tx.objectStore(STORE).get(Number(id));
    req.onsuccess = () => resolve(req.result || null);
    req.onerror = () => reject(req.error);
  });
}

async function putMeta(row) {
  const db = await openDb();
  const tx = db.transaction(STORE, "readwrite");
  tx.objectStore(STORE).put(row);
  await txDone(tx);
}

async function deleteMeta(id) {
  const db = await openDb();
  const tx = db.transaction(STORE, "readwrite");
  tx.objectStore(STORE).delete(Number(id));
  await txDone(tx);
}

export function dataSaverOn() {
  try {
    return localStorage.getItem(DATA_SAVER_KEY) === "1";
  } catch {
    return false;
  }
}

export function setDataSaver(on) {
  try {
    localStorage.setItem(DATA_SAVER_KEY, on ? "1" : "0");
  } catch {
    /* private mode */
  }
}

export function pickDownloadUrl(track) {
  if (track?.lite_url) return track.lite_url;
  return track?.audio_url || null;
}

export function pickPlayUrl(track) {
  if (dataSaverOn() && track?.lite_url) return track.lite_url;
  return track?.audio_url || null;
}

export async function listDownloaded() {
  return allMeta();
}

export async function isDownloaded(id) {
  return Boolean(await getMeta(id));
}

export async function storageSummary() {
  const rows = await allMeta();
  const bytes = rows.reduce((sum, row) => sum + (Number(row.size) || 0), 0);
  return { count: rows.length, bytes, tracks: rows };
}

export function formatBytes(n) {
  const value = Number(n) || 0;
  if (value < 1024) return `${value} B`;
  if (value < 1048576) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1048576).toFixed(1)} MB`;
}

export async function downloadTrack(track, { demo = false, onProgress } = {}) {
  if (demo) {
    const err = new Error("La cuenta demo puede escuchar, pero no descargar para usar sin internet.");
    err.code = 403;
    throw err;
  }
  if (!track?.id || !pickDownloadUrl(track)) {
    throw new Error("Falta la canción o su audio.");
  }
  if (!("caches" in window)) {
    throw new Error("Este navegador no permite guardar audio sin internet.");
  }
  const url = pickDownloadUrl(track);
  const existing = await getMeta(track.id);
  if (existing && existing.url === url) {
    return existing;
  }
  if (typeof onProgress === "function") onProgress(0);
  const response = await fetch(url, { credentials: "same-origin", cache: "no-store" });
  if (!response.ok) {
    throw new Error(response.status === 401 ? "Sesión vencida." : `No se pudo descargar (${response.status}).`);
  }
  const blob = await response.blob();
  if (typeof onProgress === "function") onProgress(1);
  const cache = await caches.open(OFFLINE_CACHE);
  const headers = new Headers({
    "Content-Type": blob.type || response.headers.get("Content-Type") || "application/octet-stream",
    "Content-Length": String(blob.size),
    "X-SpotyOFO-Offline": "1",
  });
  await cache.put(url, new Response(blob, { status: 200, headers }));
  // También bajo audio_url por si el reproductor pide el original y hay copia lite en caché con otra URL.
  if (track.audio_url && track.audio_url !== url) {
    // no duplicamos el blob en otra clave distinta: el player usa pickPlayUrl
  }
  const row = {
    id: Number(track.id),
    title: track.title || "",
    artist: track.artist || "",
    url,
    audio_url: track.audio_url || "",
    lite_url: track.lite_url || null,
    size: blob.size,
    codec: track.lite_codec || (url.endsWith(".webm") ? "opus" : url.endsWith(".m4a") ? "aac" : "original"),
    downloaded_at: new Date().toISOString(),
  };
  await putMeta(row);
  return row;
}

export async function removeDownload(id) {
  const row = await getMeta(id);
  if (row?.url && "caches" in window) {
    const cache = await caches.open(OFFLINE_CACHE);
    await cache.delete(row.url);
  }
  await deleteMeta(id);
}

export async function clearAll() {
  const rows = await allMeta();
  if ("caches" in window) {
    await caches.delete(OFFLINE_CACHE);
  }
  const db = await openDb();
  const tx = db.transaction(STORE, "readwrite");
  tx.objectStore(STORE).clear();
  await txDone(tx);
  return rows.length;
}

export async function mergeOfflineFlags(tracks) {
  const rows = await allMeta();
  const map = new Map(rows.map((row) => [Number(row.id), row]));
  for (const track of tracks || []) {
    const row = map.get(Number(track.id));
    track.offline = Boolean(row);
    track.offline_size = row ? row.size : null;
  }
  return tracks;
}
