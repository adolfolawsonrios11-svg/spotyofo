import { api } from "./api.js";

const PRESETS = {
  plano: [0, 0, 0],
  calidez: [4.5, 1.2, -2],
  voz: [-1.5, 4, 2.5],
  noche: [3, -1, -3.5],
  club: [6, -2, 3.5],
};

export function createPlayer() {
  const audio = new Audio();
  audio.preload = "metadata";
  const listeners = new Map();
  const state = {
    queue: [],
    index: -1,
    shuffle: false,
    repeat: "off",
    autoplay: true,
    volume: 0.85,
    muted: false,
    liked: false,
    playing: false,
    orderBefore: null,
  };
  let audioCtx = null;
  let filters = null;
  let analyser = null;
  let raf = 0;
  let startedId = 0;
  const freq = new Uint8Array(128);

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

  function loadPrefs() {
    try {
      const raw = JSON.parse(localStorage.getItem("spotyofo-player") || "{}");
      if (typeof raw.volume === "number") state.volume = raw.volume;
      if (raw.repeat) state.repeat = raw.repeat;
      state.shuffle = Boolean(raw.shuffle);
      if (typeof raw.autoplay === "boolean") state.autoplay = raw.autoplay;
      state.eq = raw.eq || [0, 0, 0];
    } catch {
      state.eq = [0, 0, 0];
    }
    if (!state.eq) state.eq = [0, 0, 0];
    audio.volume = state.volume;
  }

  function savePrefs() {
    localStorage.setItem(
      "spotyofo-player",
      JSON.stringify({
        volume: state.volume,
        repeat: state.repeat,
        shuffle: state.shuffle,
        autoplay: state.autoplay,
        eq: state.eq,
      }),
    );
  }

  function ensureGraph() {
    if (audioCtx) return;
    const Ctx = window.AudioContext || window.webkitAudioContext;
    audioCtx = new Ctx();
    const bass = audioCtx.createBiquadFilter();
    bass.type = "lowshelf";
    bass.frequency.value = 140;
    const mid = audioCtx.createBiquadFilter();
    mid.type = "peaking";
    mid.frequency.value = 1100;
    mid.Q.value = 0.9;
    const treble = audioCtx.createBiquadFilter();
    treble.type = "highshelf";
    treble.frequency.value = 5200;
    const gain = audioCtx.createGain();
    analyser = audioCtx.createAnalyser();
    analyser.fftSize = 256;
    const source = audioCtx.createMediaElementSource(audio);
    source.connect(bass);
    bass.connect(mid);
    mid.connect(treble);
    treble.connect(gain);
    gain.connect(analyser);
    analyser.connect(audioCtx.destination);
    filters = { bass, mid, treble, gain };
    applyEq(state.eq[0], state.eq[1], state.eq[2]);
  }

  function applyEq(bassDb, midDb, trebleDb) {
    state.eq = [bassDb, midDb, trebleDb];
    if (filters && audioCtx) {
      const now = audioCtx.currentTime;
      filters.bass.gain.setTargetAtTime(bassDb, now, 0.02);
      filters.mid.gain.setTargetAtTime(midDb, now, 0.02);
      filters.treble.gain.setTargetAtTime(trebleDb, now, 0.02);
    }
    savePrefs();
    emit("eq", state.eq);
  }

  function drawCanvas(canvas, count, tall) {
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const width = canvas.width;
    const height = canvas.height;
    ctx.clearRect(0, 0, width, height);
    const step = Math.floor(freq.length / count);
    const gap = tall ? 4 : 2;
    const barW = (width - gap * (count - 1)) / count;
    for (let i = 0; i < count; i += 1) {
      let value = 0;
      for (let j = 0; j < step; j += 1) value += freq[i * step + j] || 0;
      value /= step || 1;
      const h = Math.max(2, (value / 255) * height);
      const x = i * (barW + gap);
      const grad = ctx.createLinearGradient(0, height, 0, height - h);
      grad.addColorStop(0, "#2ef2c0");
      grad.addColorStop(1, "#8d7bff");
      ctx.fillStyle = grad;
      ctx.beginPath();
      if (ctx.roundRect) ctx.roundRect(x, height - h, barW, h, 3);
      else ctx.rect(x, height - h, barW, h);
      ctx.fill();
    }
  }

  function paint() {
    if (!analyser) return;
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
    if (!raf) loop();
  }

  function stopViz() {
    if (raf) cancelAnimationFrame(raf);
    raf = 0;
    ["p-viz", "now-viz"].forEach((id) => {
      const canvas = document.getElementById(id);
      if (!canvas) return;
      canvas.getContext("2d").clearRect(0, 0, canvas.width, canvas.height);
    });
  }

  async function playIndex(index) {
    if (!state.queue.length) return;
    state.index = (index + state.queue.length) % state.queue.length;
    const track = current();
    ensureGraph();
    if (audioCtx.state === "suspended") await audioCtx.resume();
    const nextUrl = new URL(track.audio_url, location.origin).href;
    if (audio.src !== nextUrl) {
      audio.src = track.audio_url;
    } else {
      audio.currentTime = 0;
    }
    try {
      await audio.play();
    } catch (error) {
      if (error.name !== "AbortError") emit("error", error);
      return;
    }
    state.playing = true;
    if (startedId !== track.id) {
      startedId = track.id;
      if (track.source !== "device") api.play(track.id).catch(() => {});
    }
    syncMediaSession(track);
    emit("state", snapshot());
    startViz();
  }

  function syncMediaSession(track) {
    if (!("mediaSession" in navigator)) return;
    navigator.mediaSession.metadata = new MediaMetadata({
      title: track.title,
      artist: track.artist,
      album: track.album,
    });
    navigator.mediaSession.setActionHandler("play", () => resume());
    navigator.mediaSession.setActionHandler("pause", () => pause());
    navigator.mediaSession.setActionHandler("previoustrack", () => previous());
    navigator.mediaSession.setActionHandler("nexttrack", () => next());
  }

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
      eq: state.eq,
    };
  }

  function playContext(tracks, index = 0, options = {}) {
    if (options.shuffle === false) state.shuffle = false;
    if (options.shuffle === true) state.shuffle = true;
    state.queue = tracks.slice();
    state.orderBefore = null;
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
    const list = tracks.slice();
    state.shuffle = true;
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
    ensureGraph();
    audioCtx.resume();
    audio.play().catch(() => {});
  }

  function pause() {
    audio.pause();
  }

  function toggle() {
    if (!current()) return;
    if (audio.paused) resume();
    else pause();
  }

  function next(fromEnd = false) {
    if (!state.queue.length) return;
    if (fromEnd && state.repeat === "one") {
      audio.currentTime = 0;
      resume();
      return;
    }
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
    if (audio.currentTime > 3) {
      audio.currentTime = 0;
      return;
    }
    playIndex(Math.max(0, state.index - 1));
  }

  async function continueRelated() {
    const track = current();
    if (!track || !state.autoplay) {
      state.playing = false;
      stopViz();
      emit("state", snapshot());
      return;
    }
    try {
      const related = await api.related(track.id);
      const known = new Set(state.queue.map((item) => item.id));
      const fresh = related.filter((item) => !known.has(item.id));
      if (!fresh.length) {
        state.playing = false;
        stopViz();
        emit("state", snapshot());
        return;
      }
      state.queue = state.queue.concat(fresh.slice(0, 1));
      playIndex(state.queue.length - 1);
    } catch {
      state.playing = false;
      stopViz();
      emit("state", snapshot());
    }
  }

  function seekRatio(ratio) {
    if (!audio.duration) return;
    audio.currentTime = Math.min(audio.duration, Math.max(0, ratio * audio.duration));
  }

  function seekBy(seconds) {
    if (!audio.duration) return;
    audio.currentTime = Math.min(audio.duration, Math.max(0, audio.currentTime + seconds));
  }

  function setVolume(value) {
    state.volume = Math.min(1, Math.max(0, value));
    state.muted = state.volume === 0;
    audio.volume = state.volume;
    savePrefs();
    emit("state", snapshot());
  }

  function toggleMute() {
    if (audio.volume > 0) {
      state.muted = true;
      audio.volume = 0;
    } else {
      state.muted = false;
      audio.volume = state.volume || 0.85;
      state.volume = audio.volume;
    }
    emit("state", snapshot());
  }

  function toggleShuffle() {
    state.shuffle = !state.shuffle;
    const track = current();
    if (state.shuffle && track) {
      state.orderBefore = state.queue.slice();
      const rest = state.queue.filter((item) => item.id !== track.id);
      shuffleArray(rest);
      state.queue = [track, ...rest];
      state.index = 0;
    } else if (state.orderBefore && track) {
      state.queue = state.orderBefore;
      state.index = Math.max(0, state.queue.findIndex((item) => item.id === track.id));
      state.orderBefore = null;
    }
    savePrefs();
    emit("state", snapshot());
  }

  function cycleRepeat() {
    state.repeat = state.repeat === "off" ? "all" : state.repeat === "all" ? "one" : "off";
    savePrefs();
    emit("state", snapshot());
  }

  function setAutoplay(value) {
    state.autoplay = value;
    savePrefs();
    emit("state", snapshot());
  }

  function enqueue(track) {
    state.queue.push(track);
    if (state.index < 0) playIndex(0);
    else emit("state", snapshot());
  }

  function playNext(track) {
    const at = Math.max(state.index, -1) + 1;
    state.queue.splice(at, 0, track);
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

  audio.addEventListener("play", () => {
    state.playing = true;
    emit("state", snapshot());
    startViz();
  });
  audio.addEventListener("pause", () => {
    state.playing = false;
    stopViz();
    emit("state", snapshot());
  });
  audio.addEventListener("timeupdate", () => {
    emit("time", { current: audio.currentTime, duration: audio.duration || 0 });
  });
  audio.addEventListener("ended", () => next(true));

  loadPrefs();

  return {
    on,
    current,
    snapshot,
    playContext,
    playShuffled,
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
      const preset = PRESETS[name] || PRESETS.plano;
      applyEq(preset[0], preset[1], preset[2]);
    },
    enqueue,
    playNext,
    playQueueIndex,
    removeQueued,
    setLiked,
    presets: PRESETS,
  };
}
