// Música del equipo donde corre el navegador. Los archivos no se suben al servidor.
const DB_NAME = "spotyofo-local";
const DB_VERSION = 1;
const AUDIO = new Set(["mp3", "m4a", "mp4", "aac", "flac", "ogg", "oga", "opus", "wav"]);
const IMAGE = new Set(["jpg", "jpeg", "png", "webp"]);
const COVER_NAMES = ["cover", "folder", "front", "album"];

let sessionFiles = null;

function extOf(name) {
  const dot = String(name).lastIndexOf(".");
  return dot < 0 ? "" : name.slice(dot + 1).toLowerCase();
}

function stemOf(name) {
  const dot = String(name).lastIndexOf(".");
  return dot < 0 ? name : name.slice(0, dot);
}

function openDb() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains("kv")) db.createObjectStore("kv");
      if (!db.objectStoreNames.contains("likes")) db.createObjectStore("likes");
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

function kvGet(key) {
  return openDb().then((db) => new Promise((resolve, reject) => {
    const tx = db.transaction("kv", "readonly");
    const request = tx.objectStore("kv").get(key);
    request.onsuccess = () => resolve(request.result || null);
    request.onerror = () => reject(request.error);
    tx.oncomplete = () => db.close();
  }));
}

function kvSet(key, value) {
  return openDb().then((db) => new Promise((resolve, reject) => {
    const tx = db.transaction("kv", "readwrite");
    tx.objectStore("kv").put(value, key);
    tx.oncomplete = () => {
      db.close();
      resolve();
    };
    tx.onerror = () => reject(tx.error);
  }));
}

function kvDelete(key) {
  return openDb().then((db) => new Promise((resolve, reject) => {
    const tx = db.transaction("kv", "readwrite");
    tx.objectStore("kv").delete(key);
    tx.oncomplete = () => {
      db.close();
      resolve();
    };
    tx.onerror = () => reject(tx.error);
  }));
}

export function canPickDirectory() {
  return window.isSecureContext && typeof window.showDirectoryPicker === "function";
}

export async function rootStatus() {
  if (sessionFiles?.length) {
    return { state: "ready", name: sessionRootName(), mode: "files" };
  }
  if (!canPickDirectory()) return { state: "unsupported" };
  let handle = null;
  try {
    handle = await kvGet("root");
  } catch {
    return { state: "empty" };
  }
  if (!handle || typeof handle.queryPermission !== "function") return { state: "empty" };
  try {
    const perm = await handle.queryPermission({ mode: "read" });
    if (perm === "granted") return { state: "ready", name: handle.name, mode: "handle" };
    return { state: "prompt", name: handle.name };
  } catch {
    return { state: "empty" };
  }
}

export async function pickDirectory() {
  const handle = await window.showDirectoryPicker({ mode: "read", id: "spotyofo-music" });
  await kvSet("root", handle);
  sessionFiles = null;
  return handle.name;
}

export async function grantDirectory() {
  const handle = await kvGet("root");
  if (!handle || typeof handle.requestPermission !== "function") return false;
  const perm = await handle.requestPermission({ mode: "read" });
  return perm === "granted";
}

export function rememberFiles(fileList) {
  sessionFiles = [...fileList];
}

export async function clearFolder() {
  sessionFiles = null;
  try {
    await kvDelete("root");
  } catch {
    /* sin carpeta guardada */
  }
}

async function likedSet() {
  try {
    const db = await openDb();
    const keys = await new Promise((resolve, reject) => {
      const request = db.transaction("likes", "readonly").objectStore("likes").getAllKeys();
      request.onsuccess = () => resolve(new Set(request.result.map(String)));
      request.onerror = () => reject(request.error);
    });
    db.close();
    return keys;
  } catch {
    return new Set();
  }
}

export async function setLikedMany(paths, liked) {
  const db = await openDb();
  await new Promise((resolve, reject) => {
    const tx = db.transaction("likes", "readwrite");
    const store = tx.objectStore("likes");
    for (const path of paths) {
      if (liked) store.put(true, path);
      else store.delete(path);
    }
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
  db.close();
}

function sessionRootName() {
  const first = sessionFiles?.[0];
  const parts = String(first?.webkitRelativePath || "").split("/").filter(Boolean);
  return parts.length > 1 ? parts[0] : "Este equipo";
}

function sessionEntries() {
  const rooted = sessionFiles.every((file) => {
    const parts = String(file.webkitRelativePath || "").split("/").filter(Boolean);
    return parts.length > 1 && parts[0] === sessionRootName();
  });
  return sessionFiles.map((file) => {
    const parts = String(file.webkitRelativePath || file.name).split("/").filter(Boolean);
    const relParts = rooted ? parts.slice(1) : parts;
    return { name: relParts[relParts.length - 1] || file.name, rel: relParts.join("/"), file };
  });
}

function childOf(rel, entryRel) {
  const prefix = rel ? `${rel}/` : "";
  if (rel && entryRel !== rel && !entryRel.startsWith(prefix)) return null;
  const rest = rel ? entryRel.slice(prefix.length) : entryRel;
  if (!rest || rest === entryRel && rel) return null;
  const slash = rest.indexOf("/");
  if (slash < 0) return { kind: "file", name: rest, rel: entryRel };
  const name = rest.slice(0, slash);
  return { kind: "dir", name, rel: rel ? `${rel}/${name}` : name };
}

function trackFromFile(file, rel, folderName, albumName, coverUrl, likes) {
  const parent = rel.includes("/") ? rel.slice(0, rel.lastIndexOf("/")) : "";
  return {
    id: rel,
    path: rel,
    folder: parent,
    title: stemOf(file.name),
    artist: folderName || "Este equipo",
    album: albumName || folderName || "Este equipo",
    duration_ms: 0,
    audio_url: URL.createObjectURL(file),
    cover_url: coverUrl || "",
    source: "local",
    liked: likes.has(rel),
    song_key: `local:${rel}`,
  };
}

async function listFromFiles(rel) {
  const rootName = sessionRootName();
  const entries = sessionEntries();
  const likes = await likedSet();
  const folders = new Map();
  const files = [];
  for (const entry of entries) {
    const child = childOf(rel, entry.rel);
    if (!child) continue;
    if (child.kind === "dir") {
      if (!folders.has(child.rel)) folders.set(child.rel, { name: child.name, path: child.rel, audio_count: 0, folder_count: 0 });
    } else if (AUDIO.has(extOf(child.name))) {
      files.push(entry);
    }
  }
  for (const folder of folders.values()) {
    const audioNames = new Set();
    const dirNames = new Set();
    for (const entry of entries) {
      const child = childOf(folder.path, entry.rel);
      if (!child) continue;
      if (child.kind === "dir") dirNames.add(child.name);
      else if (AUDIO.has(extOf(child.name))) audioNames.add(child.name);
    }
    folder.audio_count = audioNames.size;
    folder.folder_count = dirNames.size;
  }
  const cover = entries.find((entry) => {
    const child = childOf(rel, entry.rel);
    return child?.kind === "file" && IMAGE.has(extOf(child.name)) && COVER_NAMES.includes(stemOf(child.name).toLowerCase());
  });
  const coverUrl = cover ? URL.createObjectURL(cover.file) : "";
  const folderName = rel ? rel.split("/").filter(Boolean).pop() : rootName;
  const tracks = files
    .sort((a, b) => a.name.localeCompare(b.name, "es"))
    .map((entry) => trackFromFile(entry.file, entry.rel, folderName, folderName, coverUrl, likes));
  return shape(rootName, rel, folders, tracks);
}

async function resolveDir(root, rel) {
  let dir = root;
  for (const part of String(rel || "").split("/").filter(Boolean)) {
    dir = await dir.getDirectoryHandle(part);
  }
  return dir;
}

async function countHandle(dir) {
  let audio = 0;
  let folders = 0;
  for await (const entry of dir.values()) {
    if (entry.name.startsWith(".")) continue;
    if (entry.kind === "directory") folders += 1;
    else if (AUDIO.has(extOf(entry.name))) audio += 1;
  }
  return { audio, folders };
}

async function listFromHandle(rel) {
  const root = await kvGet("root");
  const dir = await resolveDir(root, rel);
  const likes = await likedSet();
  const folders = [];
  const audio = [];
  let coverHandle = null;
  for await (const entry of dir.values()) {
    if (entry.name.startsWith(".")) continue;
    if (entry.kind === "directory") {
      folders.push(entry);
    } else if (AUDIO.has(extOf(entry.name))) {
      audio.push(entry);
    } else if (!coverHandle && IMAGE.has(extOf(entry.name)) && COVER_NAMES.includes(stemOf(entry.name).toLowerCase())) {
      coverHandle = entry;
    }
  }
  const counted = [];
  for (const entry of folders) {
    const counts = await countHandle(entry);
    counted.push({
      name: entry.name,
      path: rel ? `${rel}/${entry.name}` : entry.name,
      audio_count: counts.audio,
      folder_count: counts.folders,
    });
  }
  const coverUrl = coverHandle ? URL.createObjectURL(await coverHandle.getFile()) : "";
  const folderName = rel ? rel.split("/").filter(Boolean).pop() : root.name;
  const tracks = [];
  for (const entry of audio.sort((a, b) => a.name.localeCompare(b.name, "es"))) {
    const file = await entry.getFile();
    const path = rel ? `${rel}/${entry.name}` : entry.name;
    tracks.push(trackFromFile(file, path, folderName, folderName, coverUrl, likes));
  }
  return shape(root.name, rel, counted, tracks);
}

function shape(rootName, rel, folders, tracks) {
  const parts = String(rel || "").split("/").filter(Boolean);
  const crumbs = parts.map((name, index) => ({
    name,
    path: parts.slice(0, index + 1).join("/"),
  }));
  const list = [...(folders instanceof Map ? folders.values() : folders)]
    .sort((a, b) => a.name.localeCompare(b.name, "es"));
  const liked = tracks.length > 0 && tracks.every((track) => track.liked);
  return {
    name: parts.length ? parts[parts.length - 1] : rootName,
    path: rel || "",
    location: `Este equipo · ${rootName}`,
    local: true,
    crumbs,
    folders: list,
    tracks,
    liked,
    cover_url: tracks.find((track) => track.cover_url)?.cover_url || "",
  };
}

export async function listDirectory(rel) {
  const status = await rootStatus();
  if (status.state !== "ready") {
    const error = new Error("Elige la carpeta de música de este equipo.");
    error.code = status.state;
    throw error;
  }
  if (status.mode === "files") return listFromFiles(rel || "");
  return listFromHandle(rel || "");
}
