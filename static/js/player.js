import { api } from "./api.js?v=20260930e";

export const EQ_FREQS = [31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000];
export const EQ_LABELS = ["31", "62", "125", "250", "500", "1k", "2k", "4k", "8k", "16k"];
export const EQ_PRESETS = {
  plano: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
  "más graves": [6, 5, 4, 2, 0.5, 0, 0, 0, 0, 0],
  "más agudos": [0, 0, 0, 0, 0, 0.5, 1.5, 3, 4.5, 5.5],
  voz: [-2, -2, -1, 1, 3, 4, 3.5, 2, 0, -1],
  rock: [4.5, 3.5, 2, -0.5, -1.5, -0.5, 1.5, 3, 4, 4.5],
  pop: [-1, 0, 1.5, 3, 3.5, 2, 0, -0.5, -1, -1],
  jazz: [3, 2, 1, 1.5, -1, -1, 0, 1, 2, 3],
  electrónica: [5, 4.5, 2, 0, -1.5, 1, 0, 1.5, 4, 5],
  clásica: [3, 2.5, 1.5, 1, 0, 0, 0, 1.5, 2.5, 3.5],
  acústica: [3, 2.5, 2, 1, 1.5, 1.5, 2, 2.5, 2, 1.5],
  calidez: [4, 3.5, 2.5, 1.5, 0.5, 0, -0.5, -1.5, -2, -2.5],
  noche: [3, 2.5, 1, 0, -1, -1, -1.5, -2.5, -3, -3.5],
  club: [6, 5.5, 3, 0, -2, -1.5, 0, 2, 3.5, 4],
};

const PREFS_KEY = "spotyofo-player";
const AUDIO_KEY = "spotyofo-audio";
// Teléfonos y tabletas: la salida directa del elemento <audio> es lo único que los sistemas
// mantienen sonando con la pantalla apagada. El motor «Completo» (Web Audio) queda opcional.
const UA = typeof navigator === "object" ? navigator.userAgent || "" : "";
export const IS_IOS = /iPad|iPhone|iPod/.test(UA) || (typeof navigator === "object" && navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
export const IS_MOBILE = IS_IOS || /Android|Mobile/i.test(UA) || Boolean(typeof navigator === "object" && navigator.userAgentData?.mobile);
export function resolveEngine(mode) {
  if (mode === "webaudio" || mode === "native") return mode;
  return IS_MOBILE ? "native" : "webaudio";
}
const AUDIO_DEFAULTS = {
  version: 3,
  engine: "auto",
  eqOn: true,
  eq: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
  preamp: 0,
  preset: "plano",
  custom: {},
  crossfade: 0,
  normalize: false,
  speed: 1,
  preservePitch: true,
  mono: false,
  preload: true,
  sinkId: "",
};

function clamp(value, low, high, fallback = low) {
  const number = Number(value);
  if (!Number.isFinite(number)) return fallback;
  return Math.min(high, Math.max(low, number));
}

function round1(value) {
  return Math.round(value * 10) / 10;
}

function dbToGain(db) {
  return 10 ** (db / 20);
}

// Convierte el ecualizador antiguo de 3 bandas (graves, medios, agudos) a 10 bandas.
function fromLegacy(eq) {
  const [bass, mid, treble] = (Array.isArray(eq) ? eq : []).map((value) => clamp(value, -12, 12, 0));
  const weights = [
    [1, 0, 0], [1, 0, 0], [0.75, 0.15, 0], [0.35, 0.4, 0], [0, 0.8, 0],
    [0, 1, 0], [0, 0.6, 0.25], [0, 0.2, 0.7], [0, 0, 1], [0, 0, 1],
  ];
  return weights.map(([wb, wm, wt]) => round1(clamp((bass || 0) * wb + (mid || 0) * wm + (treble || 0) * wt, -12, 12, 0)));
}

function sameBands(a, b) {
  return a.length === b.length && a.every((value, i) => Math.abs(value - b[i]) < 0.05);
}

function cleanBands(list) {
  if (!Array.isArray(list)) return AUDIO_DEFAULTS.eq.slice();
  if (list.length === 3) return fromLegacy(list);
  return EQ_FREQS.map((_, i) => round1(clamp(list[i], -12, 12, 0)));
}

function sanitize(raw) {
  const cfg = { ...AUDIO_DEFAULTS, eq: AUDIO_DEFAULTS.eq.slice(), custom: {} };
  if (!raw || typeof raw !== "object") return cfg;
  // Hasta la versión 2 el motor se guardaba solo, sin que la persona lo eligiera: pasa a «auto».
  if (raw.version >= 3) cfg.engine = ["auto", "webaudio", "native"].includes(raw.engine) ? raw.engine : "auto";
  else cfg.engine = raw.engine === "native" ? "native" : "auto";
  cfg.eqOn = raw.eqOn !== false;
  cfg.eq = cleanBands(raw.eq);
  cfg.preamp = round1(clamp(raw.preamp, -12, 12, 0));
  cfg.preset = typeof raw.preset === "string" ? raw.preset.slice(0, 40) : "plano";
  if (raw.custom && typeof raw.custom === "object") {
    Object.entries(raw.custom).slice(0, 30).forEach(([name, bands]) => {
      const key = String(name).trim().slice(0, 24);
      if (key && !EQ_PRESETS[key]) cfg.custom[key] = cleanBands(bands);
    });
  }
  cfg.crossfade = Math.round(clamp(raw.crossfade, 0, 12, 0));
  cfg.normalize = Boolean(raw.normalize);
  cfg.speed = Math.round(clamp(raw.speed, 0.5, 2, 1) * 100) / 100;
  cfg.preservePitch = raw.preservePitch !== false;
  cfg.mono = Boolean(raw.mono);
  cfg.preload = raw.preload !== false;
  cfg.sinkId = typeof raw.sinkId === "string" ? raw.sinkId.slice(0, 200) : "";
  return cfg;
}

function loadAudio() {
  try {
    const saved = localStorage.getItem(AUDIO_KEY);
    if (saved) return sanitize(JSON.parse(saved));
    const legacy = JSON.parse(localStorage.getItem(PREFS_KEY) || "{}");
    const cfg = sanitize({});
    if (Array.isArray(legacy.eq)) {
      cfg.eq = fromLegacy(legacy.eq);
      cfg.preset = sameBands(cfg.eq, EQ_PRESETS.plano) ? "plano" : "personalizado";
    }
    return cfg;
  } catch {
    return sanitize({});
  }
}

function canSetVolume() {
  try {
    const probe = document.createElement("audio");
    probe.volume = 0.5;
    return Math.abs(probe.volume - 0.5) < 0.01;
  } catch {
    return false;
  }
}

function silentWavUrl() {
  const rate = 8000;
  const samples = 800;
  const buffer = new ArrayBuffer(44 + samples * 2);
  const view = new DataView(buffer);
  const text = (offset, value) => [...value].forEach((ch, i) => view.setUint8(offset + i, ch.charCodeAt(0)));
  text(0, "RIFF");
  view.setUint32(4, 36 + samples * 2, true);
  text(8, "WAVE");
  text(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, rate, true);
  view.setUint32(28, rate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  text(36, "data");
  view.setUint32(40, samples * 2, true);
  return URL.createObjectURL(new Blob([buffer], { type: "audio/wav" }));
}

function equalPower(from, to, steps = 64) {
  const curve = new Float32Array(steps);
  for (let i = 0; i < steps; i += 1) {
    const x = i / (steps - 1);
    const up = Math.sin((x * Math.PI) / 2);
    const down = Math.cos((x * Math.PI) / 2);
    curve[i] = to > from ? from + (to - from) * up : to + (from - to) * down;
  }
  return curve;
}

export function createPlayer() {
  const listeners = new Map();
  const state = {
    queue: [],
    index: -1,
    shuffle: false,
    repeat: "off",
    autoplay: true,
    volume: 0.85,
    muted: false,
    playing: false,
    orderBefore: null,
  };
  let ctxSeq = 0; // identifica la última cola armada con playContext/playShuffled
  const cfg = loadAudio();
  const engine = resolveEngine(cfg.engine);
  const volumeControl = canSetVolume();
  const decks = [makeDeck(0), makeDeck(1)];
  let active = 0;
  let ctx = null;
  let nodes = null;
  let analyser = null;
  let raf = 0;
  let tickTimer = 0;
  let startedKey = "";
  let fade = null;
  let prep = null;
  let playToken = 0;
  let silentUrl = "";
  let sessionReady = false;
  let lastPosition = 0;
  const freq = new Uint8Array(128);
  // Recuperación: la música debe seguir aunque se corte la red o el sistema suspenda el audio.
  const rec = {
    want: false, // la persona quiere que suene (solo cambia con play/pausa explícitos)
    retries: 0,
    failed: 0,
    lastTime: 0,
    lastProgressAt: 0,
    internalAt: 0,
    stallTimer: 0,
    retryTimer: 0,
    recovering: false,
    retryFrom: 0,
  };
  const markInternal = () => {
    rec.internalAt = Date.now();
  };

  function emit(name, detail) {
    (listeners.get(name) || []).forEach((fn) => fn(detail));
  }

  function on(name, fn) {
    const list = listeners.get(name) || [];
    list.push(fn);
    listeners.set(name, list);
  }

  function current() {
    return state.queue[state.index] || null;
  }

  function activeDeck() {
    return decks[active];
  }

  function idleDeck() {
    return decks[1 - active];
  }

  function makeDeck(n) {
    const el = new Audio();
    el.preload = "auto";
    el.setAttribute("playsinline", "");
    const deck = { n, el, source: null, gain: null, track: null, loadedUrl: "", unlocked: false, unlocking: false, level: 1 };
    el.addEventListener("play", () => {
      if (deck !== activeDeck() || deck.unlocking) return;
      rec.want = true; // también cuando se reanuda desde la pantalla de bloqueo o los auriculares
      state.playing = true;
      updatePlaybackState();
      emit("state", snapshot());
      startViz();
      startTick();
    });
    el.addEventListener("pause", () => {
      if (deck !== activeDeck() || deck.unlocking) return;
      // Pausa que no pedimos nosotros (sistema, auriculares, llamada): se respeta, salvo que venga
      // de un error, del final de la pista o de un cambio interno de archivo.
      if (!el.ended && !el.error && !rec.recovering && Date.now() - rec.internalAt > 1500) rec.want = false;
      if (rec.recovering || (rec.want && (el.error || el.ended))) return;
      state.playing = false;
      updatePlaybackState();
      stopViz();
      stopTick();
      emit("state", snapshot());
    });
    el.addEventListener("playing", () => {
      if (deck !== activeDeck() || deck.unlocking) return;
      clearStall();
      rec.recovering = false;
      rec.lastProgressAt = Date.now();
      updatePlaybackState();
      updatePosition();
    });
    el.addEventListener("timeupdate", () => {
      if (deck !== activeDeck() || !deck.loadedUrl) return;
      emit("time", { current: el.currentTime, duration: el.duration || 0 });
      noteProgress(el);
      const now = Date.now();
      if (now - lastPosition > 1000) {
        lastPosition = now;
        updatePosition();
      }
      onTick();
    });
    ["waiting", "stalled"].forEach((type) => el.addEventListener(type, () => {
      if (deck === activeDeck() && deck.loadedUrl && rec.want) armStall();
    }));
    ["loadedmetadata", "durationchange", "ratechange", "seeked"].forEach((type) => el.addEventListener(type, () => {
      if (deck === activeDeck()) updatePosition();
    }));
    el.addEventListener("ended", () => {
      if (deck === activeDeck() && deck.loadedUrl) onEnded();
    });
    el.addEventListener("error", () => {
      if (deck === activeDeck() && deck.loadedUrl && el.error && !deck.unlocking) recover("error");
    });
    return deck;
  }

  // ---------- preferencias ----------

  function loadPrefs() {
    try {
      const raw = JSON.parse(localStorage.getItem(PREFS_KEY) || "{}");
      if (typeof raw.volume === "number") state.volume = clamp(raw.volume, 0, 1, 0.85);
      if (["off", "all", "one"].includes(raw.repeat)) state.repeat = raw.repeat;
      state.shuffle = Boolean(raw.shuffle);
      if (typeof raw.autoplay === "boolean") state.autoplay = raw.autoplay;
    } catch {
      /* preferencias dañadas: se usan las de fábrica */
    }
  }

  function savePrefs() {
    try {
      localStorage.setItem(
        PREFS_KEY,
        JSON.stringify({
          volume: state.volume,
          repeat: state.repeat,
          shuffle: state.shuffle,
          autoplay: state.autoplay,
          eq: cfg.eq,
        }),
      );
    } catch {
      /* almacenamiento lleno o bloqueado */
    }
  }

  function saveAudio() {
    try {
      localStorage.setItem(AUDIO_KEY, JSON.stringify(cfg));
    } catch {
      /* almacenamiento lleno o bloqueado */
    }
    savePrefs();
  }

  // ---------- grafo de audio ----------

  function ensureGraph() {
    if (engine !== "webaudio" || ctx) return;
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return;
    try {
      ctx = new Ctx({ latencyHint: "playback" });
    } catch {
      ctx = new Ctx();
    }
    const input = ctx.createGain();
    const preamp = ctx.createGain();
    const bands = EQ_FREQS.map((frequency, i) => {
      const band = ctx.createBiquadFilter();
      band.type = i === 0 ? "lowshelf" : i === EQ_FREQS.length - 1 ? "highshelf" : "peaking";
      band.frequency.value = frequency;
      band.Q.value = 1.4;
      return band;
    });
    const mono = ctx.createGain();
    mono.channelInterpretation = "speakers";
    const dry = ctx.createGain();
    const comp = ctx.createDynamicsCompressor();
    comp.threshold.value = -26;
    comp.knee.value = 24;
    comp.ratio.value = 3.5;
    comp.attack.value = 0.006;
    comp.release.value = 0.35;
    const makeup = ctx.createGain();
    makeup.gain.value = dbToGain(7);
    const wet = ctx.createGain();
    const master = ctx.createGain();
    const limiter = ctx.createDynamicsCompressor();
    limiter.threshold.value = -1;
    limiter.knee.value = 0;
    limiter.ratio.value = 20;
    limiter.attack.value = 0.002;
    limiter.release.value = 0.12;
    analyser = ctx.createAnalyser();
    analyser.fftSize = 256;
    input.connect(preamp);
    let last = preamp;
    bands.forEach((band) => {
      last.connect(band);
      last = band;
    });
    last.connect(mono);
    mono.connect(dry);
    dry.connect(master);
    mono.connect(comp);
    comp.connect(makeup);
    makeup.connect(wet);
    wet.connect(master);
    master.connect(limiter);
    limiter.connect(analyser);
    analyser.connect(ctx.destination);
    nodes = { input, preamp, bands, mono, dry, comp, makeup, wet, master, limiter };
    decks.forEach((deck) => {
      deck.source = ctx.createMediaElementSource(deck.el);
      deck.gain = ctx.createGain();
      deck.gain.gain.value = deck.level;
      deck.source.connect(deck.gain);
      deck.gain.connect(input);
      deck.el.volume = 1;
    });
    // Si el sistema suspende o interrumpe el audio (llamada, pantalla apagada), se reanuda en
    // cuanto se pueda; si falla en segundo plano, se reintenta al volver o con el siguiente gesto.
    ctx.addEventListener?.("statechange", () => {
      if (ctx.state !== "running" && rec.want) resumeContext();
    });
    applyEqNodes();
    applyMono();
    applyNormalize();
    applyVolume();
    if (cfg.sinkId && typeof ctx.setSinkId === "function") ctx.setSinkId(cfg.sinkId === "default" ? "" : cfg.sinkId).catch(() => {});
  }

  function resumeContext() {
    if (ctx && ctx.state !== "running" && ctx.state !== "closed") ctx.resume().catch(() => {});
  }

  // ---------- recuperación de cortes ----------

  function noteProgress(el) {
    if (rec.recovering) return; // mientras se recarga, currentTime vuelve a 0 un instante
    const t = el.currentTime;
    if (Math.abs(t - rec.lastTime) > 0.05) {
      rec.lastTime = t;
      rec.lastProgressAt = Date.now();
      if (rec.retries && t > (rec.retryFrom || 0) + 5) rec.retries = 0;
      if (rec.failed && t > 5) rec.failed = 0;
    }
  }

  function clearStall() {
    if (rec.stallTimer) clearTimeout(rec.stallTimer);
    rec.stallTimer = 0;
  }

  function armStall() {
    if (rec.stallTimer) return;
    rec.stallTimer = setTimeout(checkStall, 12000);
  }

  function checkStall() {
    rec.stallTimer = 0;
    const el = activeDeck().el;
    if (!rec.want || !activeDeck().loadedUrl || el.paused && !el.error && !rec.recovering && !state.playing) return;
    if (Date.now() - rec.lastProgressAt < 10000) {
      if (el.readyState < 3) armStall();
      return;
    }
    recover("stall");
  }

  // Vigilancia: se llama con timeupdate, con el temporizador y al volver a la pestaña.
  function watchdog() {
    if (!rec.want || !current()) return;
    resumeContext();
    const deck = activeDeck();
    const el = deck.el;
    if (!deck.loadedUrl || fade) return;
    const stuckFor = Date.now() - rec.lastProgressAt;
    const duration = el.duration;
    // Algunos móviles no disparan «ended» si el archivo termina un poco antes de lo anunciado.
    if (!el.paused && Number.isFinite(duration) && duration > 0 && el.currentTime >= duration - 0.4 && stuckFor > 3000) {
      onEnded();
      return;
    }
    if (el.error) {
      if (!rec.retryTimer) recover("error");
      return;
    }
    if (!el.paused && el.readyState < 3 && stuckFor > 15000 && !rec.retryTimer) recover("stall");
  }

  // Espera entre reintentos: cortes cortos se retoman en 1 s; cortes largos, hasta ~1 minuto.
  const RETRY_DELAYS = [800, 2500, 5000, 10000, 20000, 30000];

  function recover(reason) {
    const deck = activeDeck();
    const track = current();
    if (!track || !deck.loadedUrl) return;
    if (rec.retryTimer) return;
    clearStall();
    const position = rec.lastTime || deck.el.currentTime || 0;
    // Sin red: no se gastan reintentos; se retoma apenas vuelva la conexión (evento «online»).
    if (typeof navigator === "object" && navigator.onLine === false) {
      if (!rec.recovering) emit("notice", "Sin conexión: la música seguirá cuando vuelva la red.");
      rec.recovering = true;
      rec.retryFrom = position;
      rec.retryTimer = setTimeout(() => {
        rec.retryTimer = 0;
        if (rec.want && activeDeck() === deck && current() === track) recover(reason);
        else rec.recovering = false;
      }, 15000);
      return;
    }
    // Archivo dañado (no se puede decodificar): no tiene sentido insistir mucho.
    const code = deck.el.error ? deck.el.error.code : 0;
    const maxRetries = code === 3 ? 1 : code === 4 ? 3 : RETRY_DELAYS.length;
    if (rec.retries >= maxRetries) {
      rec.retries = 0;
      rec.failed += 1;
      rec.recovering = false;
      if (rec.failed >= 3 || (upcomingIndex() === null && !state.autoplay)) {
        rec.want = false;
        state.playing = false;
        updatePlaybackState();
        emit("error", new Error(`No se pudo cargar «${track.title}». Revisa la conexión y vuelve a tocar reproducir.`));
        emit("state", snapshot());
        return;
      }
      emit("notice", `No se pudo cargar «${track.title}»; paso a la siguiente.`);
      next(true, { skipBroken: true });
      return;
    }
    rec.retries += 1;
    rec.recovering = true;
    rec.retryFrom = position;
    if (rec.retries === 1) emit("notice", reason === "stall" ? "Conexión lenta: retomando la canción…" : "Se cortó la conexión: retomando la canción…");
    rec.retryTimer = setTimeout(() => {
      rec.retryTimer = 0;
      if (!rec.want || activeDeck() !== deck || current() !== track) {
        rec.recovering = false;
        return;
      }
      reloadAt(deck, position);
    }, RETRY_DELAYS[rec.retries - 1]);
  }

  function reloadAt(deck, position) {
    const el = deck.el;
    markInternal();
    const url = deck.loadedUrl;
    el.src = url;
    el.load();
    applySpeedTo(deck);
    const seek = () => {
      if (position > 0 && Number.isFinite(el.duration)) {
        try {
          el.currentTime = Math.min(position, Math.max(0, el.duration - 1));
        } catch {
          /* aún sin datos */
        }
      }
    };
    el.addEventListener("loadedmetadata", seek, { once: true });
    resumeContext();
    rec.lastProgressAt = Date.now();
    el.play().catch((error) => {
      if (error.name === "NotAllowedError") {
        rec.recovering = false;
        rec.want = false;
        state.playing = false;
        emit("error", error);
        emit("state", snapshot());
      }
    });
    armStall();
  }

  function setParam(param, value) {
    if (!ctx) return;
    const now = ctx.currentTime;
    param.cancelScheduledValues(now);
    param.setTargetAtTime(value, now, 0.03);
  }

  function applyEqNodes() {
    if (!nodes) return;
    nodes.bands.forEach((band, i) => setParam(band.gain, cfg.eqOn ? cfg.eq[i] : 0));
    setParam(nodes.preamp.gain, dbToGain(cfg.eqOn ? cfg.preamp : 0));
  }

  function applyMono() {
    if (!nodes) return;
    nodes.mono.channelCountMode = cfg.mono ? "explicit" : "max";
    nodes.mono.channelCount = cfg.mono ? 1 : 2;
  }

  function applyNormalize() {
    if (!nodes) return;
    setParam(nodes.dry.gain, cfg.normalize ? 0 : 1);
    setParam(nodes.wet.gain, cfg.normalize ? 1 : 0);
  }

  function applyNativeVolume(deck) {
    if (engine === "webaudio" && nodes) return;
    try {
      deck.el.volume = clamp((state.muted ? 0 : state.volume) * deck.level, 0, 1, 0);
    } catch {
      /* iOS no permite cambiar el volumen del elemento */
    }
  }

  function applyVolume() {
    if (nodes) setParam(nodes.master.gain, state.muted ? 0 : state.volume);
    else decks.forEach(applyNativeVolume);
  }

  function applySpeedTo(deck) {
    const el = deck.el;
    el.defaultPlaybackRate = cfg.speed;
    el.playbackRate = cfg.speed;
    if ("preservesPitch" in el) el.preservesPitch = cfg.preservePitch;
    if ("webkitPreservesPitch" in el) el.webkitPreservesPitch = cfg.preservePitch;
    if ("mozPreservesPitch" in el) el.mozPreservesPitch = cfg.preservePitch;
  }

  function deckLevel(deck, value, seconds = 0) {
    const from = deck.level;
    deck.level = value;
    if (nodes && deck.gain) {
      const param = deck.gain.gain;
      const now = ctx.currentTime;
      const startValue = param.value;
      param.cancelScheduledValues(now);
      if (seconds > 0) {
        try {
          param.setValueCurveAtTime(equalPower(startValue, value), now, seconds);
        } catch {
          param.setValueAtTime(startValue, now);
          param.linearRampToValueAtTime(value, now + seconds);
        }
      } else {
        param.setValueAtTime(value, now);
      }
      return;
    }
    if (deck.fadeTimer) clearInterval(deck.fadeTimer);
    deck.fadeTimer = 0;
    if (seconds <= 0) {
      applyNativeVolume(deck);
      return;
    }
    const start = Date.now();
    deck.fadeTimer = setInterval(() => {
      const x = Math.min(1, (Date.now() - start) / (seconds * 1000));
      const eased = value > from ? Math.sin((x * Math.PI) / 2) : 1 - Math.cos((x * Math.PI) / 2);
      deck.level = from + (value - from) * eased;
      applyNativeVolume(deck);
      if (x >= 1) {
        clearInterval(deck.fadeTimer);
        deck.fadeTimer = 0;
        deck.level = value;
      }
    }, 50);
  }

  // ---------- visualizador ----------

  function drawCanvas(canvas, count, tall) {
    if (!canvas || !canvas.offsetParent) return;
    const c2d = canvas.getContext("2d");
    const width = canvas.width;
    const height = canvas.height;
    c2d.clearRect(0, 0, width, height);
    const step = Math.floor(freq.length / count);
    const gap = tall ? 4 : 2;
    const barW = (width - gap * (count - 1)) / count;
    for (let i = 0; i < count; i += 1) {
      let value = 0;
      for (let j = 0; j < step; j += 1) value += freq[i * step + j] || 0;
      value /= step || 1;
      const h = Math.max(2, (value / 255) * height);
      const x = i * (barW + gap);
      const grad = c2d.createLinearGradient(0, height, 0, height - h);
      grad.addColorStop(0, "#2ef2c0");
      grad.addColorStop(1, "#8d7bff");
      c2d.fillStyle = grad;
      c2d.beginPath();
      if (c2d.roundRect) c2d.roundRect(x, height - h, barW, h, 3);
      else c2d.rect(x, height - h, barW, h);
      c2d.fill();
    }
  }

  function paint() {
    if (!analyser || document.hidden) return;
    analyser.getByteFrequencyData(freq);
    drawCanvas(document.getElementById("p-viz"), 16, false);
    const now = document.getElementById("now");
    if (now && !now.hidden) drawCanvas(document.getElementById("now-viz"), 42, true);
  }

  function loop() {
    paint();
    raf = requestAnimationFrame(loop);
  }

  function startViz() {
    if (!raf && analyser) loop();
  }

  // En segundo plano no se dibuja (ahorra batería); el audio no depende del dibujo.
  function stopVizLoopOnly() {
    if (raf) cancelAnimationFrame(raf);
    raf = 0;
  }

  function stopViz() {
    if (raf) cancelAnimationFrame(raf);
    raf = 0;
    ["p-viz", "now-viz"].forEach((id) => {
      const canvas = document.getElementById(id);
      if (canvas) canvas.getContext("2d").clearRect(0, 0, canvas.width, canvas.height);
    });
  }

  // ---------- transiciones ----------

  function unlockIdle() {
    const deck = idleDeck();
    if (deck.unlocked || deck.unlocking || deck.loadedUrl) return;
    deck.unlocking = true;
    if (!silentUrl) silentUrl = silentWavUrl();
    deck.el.muted = true;
    deck.el.src = silentUrl;
    deck.el
      .play()
      .then(() => {
        deck.el.pause();
        deck.unlocked = true;
      })
      .catch(() => {})
      .finally(() => {
        deck.el.muted = false;
        deck.unlocking = false;
      });
  }

  function loadInto(deck, track) {
    deck.track = track;
    deck.loadedUrl = track.audio_url;
    deck.el.muted = false;
    deck.el.preload = "auto";
    deck.el.src = track.audio_url;
    applySpeedTo(deck);
  }

  function upcomingIndex() {
    if (state.index < state.queue.length - 1) return state.index + 1;
    if (state.repeat === "all" && state.queue.length) return 0;
    return null;
  }

  function prepValid() {
    return Boolean(prep && prep.ready && prep.index >= 0 && prep.track && state.queue[prep.index] === prep.track && upcomingIndex() === prep.index);
  }

  function idleHas(track) {
    const deck = idleDeck();
    return Boolean(track && deck.track === track && deck.loadedUrl === track.audio_url && !deck.el.error);
  }

  function invalidateNext() {
    prep = null;
  }

  async function prepareNext() {
    const token = {};
    prep = { token, index: -1, track: null, ready: false };
    let index = upcomingIndex();
    const playing = current();
    if (index === null && state.autoplay && playing && playing.source !== "device") {
      try {
        const related = await api.related(playing.id);
        if (prep?.token !== token) return;
        const knownIds = new Set(state.queue.map((item) => String(item.id)));
        const fresh = related.filter((item) => !knownIds.has(String(item.id)));
        if (fresh.length) {
          state.queue = state.queue.concat(fresh.slice(0, 1));
          index = state.queue.length - 1;
          emit("state", snapshot());
        }
      } catch {
        /* sin relacionadas: se detiene al final */
      }
    }
    if (prep?.token !== token) return;
    if (index !== null) {
      prep.index = index;
      prep.track = state.queue[index];
      if ((cfg.preload || cfg.crossfade > 0) && !fade && !idleHas(prep.track)) loadInto(idleDeck(), prep.track);
    }
    prep.ready = true;
  }

  function crossfadeSupported() {
    return engine === "webaudio" ? Boolean(nodes) : volumeControl;
  }

  function onTick() {
    watchdog();
    const deck = activeDeck();
    const el = deck.el;
    const duration = el.duration;
    if (!Number.isFinite(duration) || duration <= 0 || el.paused || fade || state.repeat === "one") return;
    const remaining = (duration - el.currentTime) / (el.playbackRate || 1);
    if (!prep && remaining <= Math.max(cfg.crossfade + 10, 25)) prepareNext();
    // Con la pantalla apagada no se arranca un segundo elemento: el cambio lo hace «ended»
    // en el mismo elemento, que es el que el sistema deja seguir sonando.
    if (
      cfg.crossfade > 0
      && !document.hidden
      && crossfadeSupported()
      && remaining <= cfg.crossfade + 0.05
      && duration > cfg.crossfade * 2 + 2
      && prepValid()
      && idleHas(prep.track)
    ) {
      startCrossfade(prep.index, Math.max(0.5, Math.min(cfg.crossfade, remaining)));
    }
  }

  function startTick() {
    if (!tickTimer) tickTimer = setInterval(onTick, 1000);
  }

  function stopTick() {
    if (tickTimer) clearInterval(tickTimer);
    tickTimer = 0;
  }

  function startCrossfade(index, seconds) {
    const from = activeDeck();
    const to = idleDeck();
    const token = ++playToken;
    markInternal();
    fade = { from, to, timer: 0, token, started: false };
    deckLevel(to, 0);
    applySpeedTo(to);
    try {
      to.el.currentTime = 0;
    } catch {
      /* aún sin datos */
    }
    to.el
      .play()
      .then(() => {
        if (token !== playToken || !fade) return;
        fade.started = true;
        deckLevel(from, 0, seconds);
        deckLevel(to, 1, seconds);
        active = to.n;
        state.index = index;
        prep = null;
        onTrackStarted(to.track);
        fade.timer = setTimeout(finishFade, seconds * 1000 + 120);
      })
      .catch(() => {
        // Sin permiso para reproducir el segundo elemento (iOS): se pasa a la siguiente al terminar.
        fade = null;
        deckLevel(to, 1);
        if (prep) prep.noFade = true;
      });
  }

  function finishFade() {
    if (!fade) return;
    const { from, to, timer, started } = fade;
    fade = null;
    markInternal();
    clearTimeout(timer);
    if (!started) {
      to.el.pause();
      deckLevel(to, 1);
      return;
    }
    from.el.pause();
    deckLevel(from, 1);
    deckLevel(to, 1);
  }

  function onEnded() {
    if (state.repeat === "one") {
      const el = activeDeck().el;
      el.currentTime = 0;
      el.play().catch(() => {});
      return;
    }
    const target = prepValid() ? prep.index : null;
    if (target !== null && idleHas(prep.track) && !document.hidden) {
      swapTo(target);
      return;
    }
    if (target !== null) {
      playIndex(target, { allowSwap: false });
      return;
    }
    next(true);
  }

  function swapTo(index) {
    const from = activeDeck();
    const to = idleDeck();
    const token = ++playToken;
    markInternal();
    rec.want = true;
    deckLevel(to, 1);
    applySpeedTo(to);
    try {
      to.el.currentTime = 0;
    } catch {
      /* aún sin datos */
    }
    to.el
      .play()
      .then(() => {
        if (token !== playToken) return;
        from.el.pause();
        active = to.n;
        state.index = index;
        prep = null;
        onTrackStarted(to.track);
      })
      .catch(() => {
        if (token !== playToken) return;
        playIndex(index, { allowSwap: false });
      });
  }

  function onTrackStarted(track) {
    const deck = activeDeck();
    deck.unlocked = true;
    state.playing = true;
    rec.want = true;
    rec.recovering = false;
    rec.lastTime = deck.el.currentTime || 0;
    rec.lastProgressAt = Date.now();
    const key = `${track.id}|${track.audio_url}`;
    if (startedKey !== key) {
      startedKey = key;
      if (track.source !== "device") api.play(track.id).catch(() => {});
    }
    syncMediaSession(track);
    updatePlaybackState();
    emit("state", snapshot());
    startViz();
    startTick();
  }

  function playIndex(index, options = {}) {
    if (!state.queue.length) return Promise.resolve();
    const token = ++playToken;
    finishFade();
    markInternal();
    rec.want = true;
    rec.retries = 0;
    rec.lastTime = 0;
    rec.lastProgressAt = Date.now();
    clearStall();
    if (rec.retryTimer) clearTimeout(rec.retryTimer);
    rec.retryTimer = 0;
    rec.recovering = false;
    state.index = ((index % state.queue.length) + state.queue.length) % state.queue.length;
    const track = current();
    ensureGraph();
    resumeContext();
    let deck = activeDeck();
    // En segundo plano se reutiliza el mismo elemento: cambiar a otro suele estar bloqueado.
    if (options.allowSwap !== false && !document.hidden && idleHas(track)) {
      deck.el.pause();
      active = 1 - active;
      deck = activeDeck();
    } else if (deck.loadedUrl !== track.audio_url || deck.el.error) {
      loadInto(deck, track);
    } else {
      try {
        deck.el.currentTime = 0;
      } catch {
        /* aún sin datos */
      }
    }
    const other = idleDeck();
    if (!other.el.paused && !other.unlocking) other.el.pause();
    if (!document.hidden) unlockIdle();
    deckLevel(deck, 1);
    applySpeedTo(deck);
    applyNativeVolume(deck);
    prep = null;
    return deck.el
      .play()
      .then(() => {
        if (token === playToken) onTrackStarted(track);
      })
      .catch((error) => {
        if (token !== playToken || error.name === "AbortError") return;
        if (error.name === "NotAllowedError" || error.name === "NotSupportedError") {
          if (error.name === "NotSupportedError" && deck.el.error) return; // lo atiende recover()
          rec.want = false;
          state.playing = false;
          emit("error", error);
          emit("state", snapshot());
          return;
        }
        recover("error");
      });
  }

  // ---------- Media Session (pantalla bloqueada, auriculares, notificación) ----------

  function syncMediaSession(track) {
    if (!("mediaSession" in navigator)) return;
    try {
      const artwork = track.cover_url
        ? [{ src: new URL(track.cover_url, location.href).href, sizes: "512x512" }]
        : [];
      navigator.mediaSession.metadata = new MediaMetadata({
        title: track.title || "",
        artist: track.artist || "",
        album: track.album || "",
        artwork,
      });
    } catch {
      /* MediaMetadata no disponible */
    }
    if (sessionReady) return;
    sessionReady = true;
    const handlers = {
      play: () => resume(),
      pause: () => pause(),
      stop: () => {
        pause();
        seekRatio(0);
      },
      previoustrack: () => previous(),
      nexttrack: () => next(),
      seekbackward: (details) => seekBy(-(details?.seekOffset || 10)),
      seekforward: (details) => seekBy(details?.seekOffset || 10),
      seekto: (details) => {
        finishFade();
        const el = activeDeck().el;
        if (!Number.isFinite(details?.seekTime)) return;
        if (details.fastSeek && typeof el.fastSeek === "function") el.fastSeek(details.seekTime);
        else el.currentTime = details.seekTime;
        updatePosition();
      },
    };
    Object.entries(handlers).forEach(([action, handler]) => {
      try {
        navigator.mediaSession.setActionHandler(action, handler);
      } catch {
        /* acción no soportada en este navegador */
      }
    });
  }

  function updatePlaybackState() {
    if (!("mediaSession" in navigator)) return;
    try {
      navigator.mediaSession.playbackState = current() ? (state.playing ? "playing" : "paused") : "none";
    } catch {
      /* sin soporte */
    }
  }

  function updatePosition() {
    if (!("mediaSession" in navigator) || typeof navigator.mediaSession.setPositionState !== "function") return;
    const el = activeDeck().el;
    const duration = el.duration;
    if (!Number.isFinite(duration) || duration <= 0) return;
    try {
      navigator.mediaSession.setPositionState({
        duration,
        playbackRate: el.playbackRate || 1,
        position: Math.min(duration, Math.max(0, el.currentTime)),
      });
    } catch {
      /* valores fuera de rango */
    }
  }

  // ---------- API pública ----------

  function snapshot() {
    return {
      track: current(),
      index: state.index,
      queue: state.queue,
      playing: state.playing,
      shuffle: state.shuffle,
      repeat: state.repeat,
      autoplay: state.autoplay,
      volume: state.volume,
      muted: state.muted,
      eq: cfg.eq.slice(),
      speed: cfg.speed,
      crossfade: cfg.crossfade,
    };
  }

  function playContext(tracks, index = 0, options = {}) {
    ctxSeq += 1;
    if (options.shuffle === false) state.shuffle = false;
    if (options.shuffle === true) state.shuffle = true;
    state.queue = tracks.slice();
    state.orderBefore = null;
    invalidateNext();
    if (state.shuffle && state.queue.length > 1) {
      const currentTrack = state.queue[index];
      const rest = state.queue.filter((_, i) => i !== index);
      shuffleArray(rest);
      state.orderBefore = tracks.slice();
      state.queue = [currentTrack, ...rest];
      index = 0;
    }
    if (options.shuffle === false || options.shuffle === true) savePrefs();
    return playIndex(index);
  }

  function playShuffled(tracks) {
    ctxSeq += 1;
    const list = tracks.slice();
    state.shuffle = true;
    invalidateNext();
    if (list.length > 1) {
      const start = Math.floor(Math.random() * list.length);
      const first = list[start];
      const rest = list.filter((_, i) => i !== start);
      shuffleArray(rest);
      state.orderBefore = list;
      state.queue = [first, ...rest];
    } else {
      state.orderBefore = null;
      state.queue = list;
    }
    savePrefs();
    return playIndex(0);
  }

  function resume() {
    if (!current()) return;
    const deck = activeDeck();
    ensureGraph();
    resumeContext();
    rec.want = true;
    rec.lastProgressAt = Date.now();
    if (!deck.loadedUrl) {
      playIndex(state.index);
      return;
    }
    if (deck.el.error) {
      rec.retries = 0;
      recover("error");
      return;
    }
    if (!document.hidden) unlockIdle();
    deck.el.play().catch((error) => {
      if (error.name === "AbortError") return;
      if (error.name === "NotAllowedError") {
        rec.want = false;
        emit("error", error);
        return;
      }
      recover("error");
    });
  }

  function pause() {
    rec.want = false;
    clearStall();
    if (rec.retryTimer) clearTimeout(rec.retryTimer);
    rec.retryTimer = 0;
    rec.recovering = false;
    finishFade();
    activeDeck().el.pause();
  }

  function toggle() {
    if (!current()) return;
    if (activeDeck().el.paused) resume();
    else pause();
  }

  function next(fromEnd = false, options = {}) {
    if (!state.queue.length) return;
    if (fromEnd && state.repeat === "one" && !options.skipBroken) {
      activeDeck().el.currentTime = 0;
      resume();
      return;
    }
    finishFade();
    if (state.index < state.queue.length - 1) {
      playIndex(state.index + 1);
      return;
    }
    if (state.repeat === "all") {
      playIndex(0);
      return;
    }
    continueRelated();
  }

  function previous() {
    if (!current()) return;
    finishFade();
    const el = activeDeck().el;
    if (el.currentTime > 3) {
      el.currentTime = 0;
      return;
    }
    playIndex(Math.max(0, state.index - 1));
  }

  async function continueRelated() {
    const track = current();
    const stop = () => {
      rec.want = false;
      state.playing = false;
      stopViz();
      stopTick();
      updatePlaybackState();
      emit("state", snapshot());
    };
    if (!track || !state.autoplay || track.source === "device") {
      stop();
      return;
    }
    try {
      const related = await api.related(track.id);
      const knownIds = new Set(state.queue.map((item) => String(item.id)));
      const fresh = related.filter((item) => !knownIds.has(String(item.id)));
      if (!fresh.length) {
        stop();
        return;
      }
      state.queue = state.queue.concat(fresh.slice(0, 1));
      playIndex(state.queue.length - 1);
    } catch {
      stop();
    }
  }

  function seekRatio(ratio) {
    finishFade();
    const el = activeDeck().el;
    if (!el.duration) return;
    el.currentTime = Math.min(el.duration, Math.max(0, ratio * el.duration));
    updatePosition();
  }

  function seekBy(seconds) {
    finishFade();
    const el = activeDeck().el;
    if (!el.duration) return;
    el.currentTime = Math.min(el.duration, Math.max(0, el.currentTime + seconds));
    updatePosition();
  }

  function setVolume(value) {
    state.volume = clamp(value, 0, 1, state.volume);
    state.muted = state.volume === 0;
    applyVolume();
    savePrefs();
    emit("state", snapshot());
  }

  function toggleMute() {
    if (!state.muted && state.volume > 0) {
      state.muted = true;
    } else {
      state.muted = false;
      if (!state.volume) state.volume = 0.85;
    }
    applyVolume();
    emit("state", snapshot());
  }

  function toggleShuffle() {
    state.shuffle = !state.shuffle;
    const track = current();
    if (state.shuffle && track) {
      state.orderBefore = state.queue.slice();
      const rest = state.queue.filter((item) => item !== track);
      shuffleArray(rest);
      state.queue = [track, ...rest];
      state.index = 0;
    } else if (state.orderBefore && track) {
      state.queue = state.orderBefore;
      state.index = Math.max(0, state.queue.indexOf(track));
      state.orderBefore = null;
    }
    invalidateNext();
    savePrefs();
    emit("state", snapshot());
  }

  function cycleRepeat() {
    state.repeat = state.repeat === "off" ? "all" : state.repeat === "all" ? "one" : "off";
    invalidateNext();
    savePrefs();
    emit("state", snapshot());
  }

  function setAutoplay(value) {
    state.autoplay = Boolean(value);
    invalidateNext();
    savePrefs();
    emit("state", snapshot());
  }

  function contextId() {
    return ctxSeq;
  }

  // Completa la cola que armó playContext/playShuffled (arranque rápido: primero suena la primera pista
  // y el resto llega después). Solo actúa si esa cola sigue siendo la vigente. Con aleatorio activo,
  // mezcla lo que falta por sonar junto con lo nuevo, así el orden aleatorio abarca toda la lista.
  function extendQueue(tracks, ctx) {
    if (ctx !== ctxSeq || !tracks || !tracks.length) return 0;
    const keyOf = (item) => `${item.source === "device" ? "d" : "t"}:${item.id}`;
    const have = new Set(state.queue.map(keyOf));
    const fresh = tracks.filter((item) => {
      const key = keyOf(item);
      if (have.has(key)) return false;
      have.add(key);
      return true;
    });
    if (!fresh.length) return 0;
    if (state.shuffle && state.index >= 0) {
      const played = state.queue.slice(0, state.index + 1);
      const upcoming = state.queue.slice(state.index + 1).concat(fresh);
      shuffleArray(upcoming);
      state.queue = played.concat(upcoming);
    } else {
      state.queue = state.queue.concat(fresh);
    }
    if (state.orderBefore) state.orderBefore = state.orderBefore.concat(fresh);
    invalidateNext();
    emit("state", snapshot());
    return fresh.length;
  }

  function enqueue(track) {
    state.queue.push(track);
    invalidateNext();
    if (state.index < 0) playIndex(0);
    else emit("state", snapshot());
  }

  function playNext(track) {
    const at = Math.max(state.index, -1) + 1;
    state.queue.splice(at, 0, track);
    invalidateNext();
    if (state.index < 0) playIndex(0);
    else emit("state", snapshot());
  }

  function playQueueIndex(index) {
    playIndex(index);
  }

  function removeQueued(index) {
    if (index === state.index) return;
    state.queue.splice(index, 1);
    if (index < state.index) state.index -= 1;
    invalidateNext();
    emit("state", snapshot());
  }

  function setLiked(trackId, liked) {
    state.queue.forEach((track) => {
      if (String(track.id) === String(trackId)) track.liked = liked;
    });
    emit("state", snapshot());
  }

  function shuffleArray(list) {
    for (let i = list.length - 1; i > 0; i -= 1) {
      const j = Math.floor(Math.random() * (i + 1));
      [list[i], list[j]] = [list[j], list[i]];
    }
  }

  // ---------- configuración de audio ----------

  function audioSettings() {
    return JSON.parse(JSON.stringify(cfg));
  }

  function matchPreset(bands) {
    const builtIn = Object.entries(EQ_PRESETS).find(([, values]) => sameBands(values, bands));
    if (builtIn) return builtIn[0];
    const custom = Object.entries(cfg.custom).find(([, values]) => sameBands(values, bands));
    return custom ? custom[0] : "personalizado";
  }

  function changed() {
    saveAudio();
    emit("audio", audioSettings());
    emit("state", snapshot());
  }

  function setAudio(patch) {
    const next = sanitize({ ...cfg, ...patch });
    const keys = Object.keys(patch || {});
    keys.forEach((key) => {
      if (key in next) cfg[key] = next[key];
    });
    if (keys.some((key) => ["eq", "preamp", "eqOn"].includes(key))) applyEqNodes();
    if (keys.includes("eq") && !keys.includes("preset")) cfg.preset = matchPreset(cfg.eq);
    if (keys.includes("mono")) applyMono();
    if (keys.includes("normalize")) applyNormalize();
    if (keys.includes("speed") || keys.includes("preservePitch")) decks.forEach(applySpeedTo);
    if (keys.includes("crossfade") || keys.includes("preload")) invalidateNext();
    changed();
  }

  function setBand(index, db) {
    if (index < 0 || index >= EQ_FREQS.length) return;
    cfg.eq[index] = round1(clamp(db, -12, 12, 0));
    cfg.eqOn = true;
    cfg.preset = matchPreset(cfg.eq);
    applyEqNodes();
    changed();
  }

  function applyEqPreset(name) {
    const bands = EQ_PRESETS[name] || cfg.custom[name];
    if (!bands) return;
    cfg.eq = bands.slice();
    cfg.eqOn = true;
    cfg.preset = name;
    applyEqNodes();
    changed();
  }

  function saveCustomPreset(name) {
    const key = String(name || "").trim().slice(0, 24);
    if (!key) throw new Error("Ponle un nombre al ajuste.");
    if (EQ_PRESETS[key]) throw new Error("Ese nombre ya lo usa un ajuste de fábrica.");
    if (!cfg.custom[key] && Object.keys(cfg.custom).length >= 30) throw new Error("Máximo 30 ajustes propios.");
    cfg.custom[key] = cfg.eq.slice();
    cfg.preset = key;
    changed();
  }

  function deleteCustomPreset(name) {
    if (!cfg.custom[name]) return;
    delete cfg.custom[name];
    if (cfg.preset === name) cfg.preset = matchPreset(cfg.eq);
    changed();
  }

  function resetAudio() {
    const keep = { custom: cfg.custom, engine: cfg.engine, sinkId: cfg.sinkId };
    Object.assign(cfg, sanitize({}), keep);
    applyEqNodes();
    applyMono();
    applyNormalize();
    decks.forEach(applySpeedTo);
    invalidateNext();
    changed();
  }

  // Legado: el panel antiguo de 3 bandas.
  function applyEq(bassDb, midDb, trebleDb) {
    cfg.eq = fromLegacy([bassDb, midDb, trebleDb]);
    cfg.preset = matchPreset(cfg.eq);
    applyEqNodes();
    changed();
  }

  function sinkSupported() {
    if (!navigator.mediaDevices || typeof navigator.mediaDevices.enumerateDevices !== "function") return false;
    if (engine === "webaudio") {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      return Boolean(Ctx && "setSinkId" in Ctx.prototype);
    }
    return "setSinkId" in HTMLMediaElement.prototype;
  }

  function capabilities() {
    const probe = document.createElement("audio");
    return {
      engine,
      engineMode: cfg.engine,
      pendingEngine: resolveEngine(cfg.engine),
      autoEngine: resolveEngine("auto"),
      mobile: IS_MOBILE,
      ios: IS_IOS,
      webaudio: engine === "webaudio" && Boolean(window.AudioContext || window.webkitAudioContext),
      volumeControl: engine === "webaudio" || volumeControl,
      crossfade: engine === "webaudio" || volumeControl,
      effects: engine === "webaudio",
      pitch: "preservesPitch" in probe || "webkitPreservesPitch" in probe || "mozPreservesPitch" in probe,
      sink: sinkSupported(),
      selectOutput: Boolean(navigator.mediaDevices && typeof navigator.mediaDevices.selectAudioOutput === "function"),
      secure: Boolean(window.isSecureContext),
    };
  }

  async function listOutputs() {
    if (!sinkSupported()) return [];
    const devices = await navigator.mediaDevices.enumerateDevices();
    return devices
      .filter((device) => device.kind === "audiooutput")
      .map((device, i) => ({
        id: device.deviceId,
        label: device.label || (device.deviceId === "default" ? "Predeterminada del sistema" : `Salida ${i + 1}`),
      }));
  }

  async function requestOutputAccess() {
    if (typeof navigator.mediaDevices?.selectAudioOutput === "function") {
      const device = await navigator.mediaDevices.selectAudioOutput();
      await setOutput(device.deviceId);
      return;
    }
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    stream.getTracks().forEach((track) => track.stop());
  }

  async function setOutput(id) {
    const sink = String(id || "");
    if (engine === "webaudio") {
      ensureGraph();
      if (ctx && typeof ctx.setSinkId === "function") await ctx.setSinkId(sink === "default" ? "" : sink);
    } else {
      await Promise.all(decks.map((deck) => (typeof deck.el.setSinkId === "function" ? deck.el.setSinkId(sink) : null)));
    }
    cfg.sinkId = sink;
    changed();
  }

  function setEngine(name) {
    cfg.engine = ["auto", "webaudio", "native"].includes(name) ? name : "auto";
    saveAudio();
    emit("audio", audioSettings());
  }

  // Safari/iOS suspende el AudioContext: se reanuda con el siguiente gesto o al volver a la pestaña.
  const wake = () => {
    if (rec.want || state.playing) resumeContext();
  };
  ["pointerdown", "touchend", "keydown"].forEach((type) => document.addEventListener(type, wake, { passive: true }));
  document.addEventListener("visibilitychange", () => {
    wake();
    if (!document.hidden) {
      watchdog();
      if (state.playing) startViz();
    } else {
      stopVizLoopOnly();
    }
  });
  window.addEventListener("pageshow", () => {
    wake();
    watchdog();
  });
  document.addEventListener("resume", () => {
    wake();
    watchdog();
  });
  window.addEventListener("online", () => {
    const el = activeDeck().el;
    if (rec.want && activeDeck().loadedUrl && (el.error || el.readyState < 3)) {
      if (rec.retryTimer) clearTimeout(rec.retryTimer);
      rec.retryTimer = 0;
      recover("stall");
    }
  });

  loadPrefs();
  if (engine === "native" && cfg.sinkId) {
    decks.forEach((deck) => {
      if (typeof deck.el.setSinkId === "function") deck.el.setSinkId(cfg.sinkId).catch(() => {});
    });
  }
  decks.forEach((deck) => {
    applySpeedTo(deck);
    applyNativeVolume(deck);
  });

  return {
    on,
    current,
    snapshot,
    playContext,
    playShuffled,
    contextId,
    extendQueue,
    toggle,
    next,
    previous,
    seekRatio,
    seekBy,
    setVolume,
    toggleMute,
    toggleShuffle,
    cycleRepeat,
    setAutoplay,
    applyEq,
    applyPreset(name) {
      applyEqPreset(EQ_PRESETS[name] || cfg.custom[name] ? name : "plano");
    },
    enqueue,
    playNext,
    playQueueIndex,
    removeQueued,
    setLiked,
    presets: EQ_PRESETS,
    audioSettings,
    setAudio,
    setBand,
    applyEqPreset,
    saveCustomPreset,
    deleteCustomPreset,
    resetAudio,
    capabilities,
    listOutputs,
    requestOutputAccess,
    setOutput,
    setEngine,
    health() {
      const deck = activeDeck();
      return {
        engine,
        want: rec.want,
        playing: state.playing,
        paused: deck.el.paused,
        ctx: ctx ? ctx.state : null,
        retries: rec.retries,
        failed: rec.failed,
        recovering: rec.recovering,
        readyState: deck.el.readyState,
        time: deck.el.currentTime,
        index: state.index,
        deck: deck.n,
      };
    },
    position() {
      const el = activeDeck().el;
      return { current: el.currentTime || 0, duration: el.duration || 0 };
    },
  };
}
