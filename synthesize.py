"""Sintetiza el catálogo original y lo guarda en MariaDB.

Uso:
  python synthesize.py
  python synthesize.py --force

--force regenera solo el catálogo editorial. Las canciones subidas (static/audio/user,
static/covers/user), las listas propias y los Me gusta se conservan.
"""

import sys
import wave
from pathlib import Path

import numpy as np

from catalog import ALBUMS, ARTISTS, PLAYLISTS, SCALES, TRACKS
from db import connect, init_schema, reset_catalog, restore_catalog_refs

ROOT = Path(__file__).resolve().parent
AUDIO_DIR = ROOT / "static" / "audio"
COVER_DIR = ROOT / "static" / "covers"
SR = 22050

STYLES = {
    "lofi": {
        "swing": 0.16,
        "kick": [1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0],
        "snare": [0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 1],
        "hat": [1, 0, 1, 1, 1, 0, 1, 0, 1, 1, 1, 0, 1, 0, 1, 1],
        "hat_amp": 0.055,
        "kick_amp": 0.78,
        "snare_amp": 0.26,
        "vinyl": 0.03,
        "reverb": 0.34,
        "pad_amp": 0.055,
        "melody_amp": 0.2,
        "sidechain": 0.22,
        "bass": "warm",
        "pad": "sine",
        "lead": "tri",
    },
    "electro": {
        "swing": 0.0,
        "kick": [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0],
        "snare": [0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0],
        "hat": [0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0],
        "open_hat": [0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 1],
        "hat_amp": 0.07,
        "kick_amp": 0.86,
        "snare_amp": 0.32,
        "vinyl": 0.0,
        "reverb": 0.18,
        "pad_amp": 0.04,
        "melody_amp": 0.18,
        "sidechain": 0.55,
        "bass": "offbeat",
        "pad": "softsaw",
        "lead": "softsaw",
    },
    "ambient": {
        "swing": 0.0,
        "kick": [1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
        "snare": [0] * 16,
        "hat": [0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0],
        "hat_amp": 0.03,
        "kick_amp": 0.28,
        "snare_amp": 0.0,
        "vinyl": 0.015,
        "reverb": 0.62,
        "pad_amp": 0.08,
        "melody_amp": 0.14,
        "sidechain": 0.08,
        "bass": "sparse",
        "pad": "sine",
        "lead": "sine",
    },
    "latin": {
        "swing": 0.0,
        "kick": [1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 0, 0],
        "snare": [0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0],
        "hat": [1, 1, 1, 1, 1, 0, 1, 1, 1, 1, 1, 0, 1, 1, 1, 1],
        "clave": [1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0],
        "hat_amp": 0.05,
        "kick_amp": 0.7,
        "snare_amp": 0.22,
        "vinyl": 0.0,
        "reverb": 0.22,
        "pad_amp": 0.045,
        "melody_amp": 0.2,
        "sidechain": 0.18,
        "bass": "sync",
        "pad": "sine",
        "lead": "tri",
    },
    "jazz": {
        "swing": 0.22,
        "kick": [1, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0],
        "snare": [0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0],
        "ride": [1, 0, 2, 0, 1, 0, 2, 0, 1, 0, 2, 0, 1, 0, 2, 1],
        "hat_amp": 0.0,
        "kick_amp": 0.42,
        "snare_amp": 0.16,
        "ride_amp": 0.11,
        "vinyl": 0.01,
        "reverb": 0.3,
        "pad_amp": 0.05,
        "melody_amp": 0.16,
        "sidechain": 0.05,
        "bass": "walking",
        "pad": "sine",
        "lead": "tri",
    },
    "alt": {
        "swing": 0.0,
        "kick": [1, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 1, 0, 0, 0, 0],
        "snare": [0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0],
        "hat": [1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0],
        "hat_amp": 0.06,
        "kick_amp": 0.8,
        "snare_amp": 0.34,
        "vinyl": 0.0,
        "reverb": 0.16,
        "pad_amp": 0.035,
        "melody_amp": 0.2,
        "sidechain": 0.3,
        "bass": "eighths",
        "pad": "softsaw",
        "lead": "softsaw",
    },
}


def degree_to_midi(root, scale, degree, octave=0):
    span = len(scale)
    return root + scale[degree % span] + 12 * (degree // span + octave)


def chord_midis(root, scale, degree):
    return [degree_to_midi(root, scale, degree + step) for step in (0, 2, 4)]


def voice_around(midis, center):
    voiced = []
    for note in midis:
        while note < center - 6:
            note += 12
        while note > center + 8:
            note -= 12
        voiced.append(note)
    return voiced


def adsr(n, sr, attack, decay, sustain, release):
    env = np.full(n, sustain, dtype=np.float32)
    a = min(n, max(1, int(attack * sr)))
    d = min(max(n - a, 0), max(0, int(decay * sr)))
    r = min(n, max(1, int(release * sr)))
    env[:a] = np.linspace(0, 1, a, endpoint=False, dtype=np.float32)
    if d:
        env[a : a + d] = np.linspace(1, sustain, d, endpoint=False, dtype=np.float32)
    env[-r:] *= np.linspace(1, 0, r, dtype=np.float32)
    return env


def tone(freq, dur, sr, kind):
    n = max(1, int(dur * sr))
    t = np.arange(n, dtype=np.float32) / sr
    phase = 2 * np.pi * freq * t
    sine = np.sin(phase).astype(np.float32)
    if kind == "sine":
        wave = sine
    elif kind == "tri":
        wave = (2 / np.pi * np.arcsin(np.clip(sine, -1, 1))).astype(np.float32)
    else:
        saw = (2 * np.mod(freq * t, 1) - 1).astype(np.float32)
        wave = sine * 0.72 + saw * 0.28
    return wave


def mix_mono(dst, src, start, pan):
    if src.size == 0:
        return
    angle = (pan + 1) * np.pi / 4
    left = src * np.cos(angle)
    right = src * np.sin(angle)
    stereo = np.stack((left, right), axis=1)
    if start < 0:
        stereo = stereo[-start:]
        start = 0
    end = min(len(dst), start + len(stereo))
    count = end - start
    if count <= 0:
        return
    dst[start:end] += stereo[:count]


def schedule(pattern, bars, step, swing, sr, rng, humanize=False):
    onsets = []
    total = bars * 16
    span = int(0.004 * sr)
    for index in range(total):
        level = pattern[index % len(pattern)]
        if not level:
            continue
        when = index * step
        if swing and index % 2 == 1:
            when += step * swing
        sample = int(when * sr)
        if humanize and span:
            sample += int(rng.integers(-span, span + 1))
        onsets.append((max(0, sample), 1.25 if level == 2 else 1.0))
    return onsets


def synth_kick(sr, amp, rng):
    n = int(0.28 * sr)
    t = np.arange(n, dtype=np.float32) / sr
    freq = 148 * np.exp(-t * 22) + 44
    phase = 2 * np.pi * np.cumsum(freq) / sr
    body = np.sin(phase) * np.exp(-t * 8)
    click = rng.normal(0, 1, n).astype(np.float32) * np.exp(-t * 95)
    env = np.exp(-t * 8).astype(np.float32)
    return (body * 0.95 + click * 0.16).astype(np.float32) * amp, env * amp


def synth_snare(sr, amp, rng):
    n = int(0.18 * sr)
    t = np.arange(n, dtype=np.float32) / sr
    noise = rng.normal(0, 1, n).astype(np.float32)
    noise = np.diff(noise, prepend=noise[0])
    tone_wave = np.sin(2 * np.pi * 188 * t) * np.exp(-t * 20)
    return ((noise * 0.55 + tone_wave * 0.4) * np.exp(-t * 15) * amp).astype(np.float32)


def synth_hat(sr, amp, rng, opened=False):
    dur = 0.2 if opened else 0.07
    n = int(dur * sr)
    t = np.arange(n, dtype=np.float32) / sr
    noise = np.diff(rng.normal(0, 1, n).astype(np.float32), prepend=0.0)
    decay = 12 if opened else 46
    return (noise * np.exp(-t * decay) * amp).astype(np.float32)


def synth_clave(sr, amp):
    n = int(0.055 * sr)
    t = np.arange(n, dtype=np.float32) / sr
    freq = 1900 * np.exp(-t * 28) + 820
    phase = 2 * np.pi * np.cumsum(freq) / sr
    return (np.sin(phase) * np.exp(-t * 42) * amp).astype(np.float32)


def synth_ride(sr, amp, rng):
    n = int(0.32 * sr)
    t = np.arange(n, dtype=np.float32) / sr
    noise = np.diff(rng.normal(0, 1, n).astype(np.float32), prepend=0.0)
    ping = np.sin(2 * np.pi * 740 * t) * np.exp(-t * 9)
    return ((noise * 0.32 + ping * 0.28) * np.exp(-t * 4.5) * amp).astype(np.float32)


def place_note(bus, start_s, dur_s, midi, amp, kind, pan, sr):
    if midi is None or amp <= 0 or dur_s <= 0:
        return
    freq = 440.0 * (2 ** ((midi - 69) / 12))
    wave = tone(freq, dur_s, sr, kind)
    if kind == "sine":
        env = adsr(len(wave), sr, 0.03, 0.12, 0.8, 0.08)
    elif kind == "tri":
        env = adsr(len(wave), sr, 0.012, 0.06, 0.65, 0.05)
    else:
        env = adsr(len(wave), sr, 0.008, 0.05, 0.55, 0.04)
    mix_mono(bus, wave * env * amp, int(start_s * sr), pan)


def add_reverb(stereo, sr, amount):
    if amount <= 0:
        return stereo
    wet = np.zeros_like(stereo)
    taps = ((31, 0.4, -0.35), (47, 0.3, 0.4), (71, 0.22, -0.15), (103, 0.14, 0.25), (149, 0.09, 0.0))
    for ms, gain, pan in taps:
        delay = int(ms * sr / 1000)
        delayed = np.zeros_like(stereo)
        delayed[delay:] = stereo[:-delay] * gain
        angle = (pan + 1) * np.pi / 4
        delayed[:, 0] *= np.cos(angle)
        delayed[:, 1] *= np.sin(angle)
        wet += delayed
    return stereo * (1 - amount * 0.25) + wet * amount


def render_track(spec):
    style = STYLES[spec["style"]]
    scale = SCALES[spec["scale"]]
    rng = np.random.default_rng(spec["seed"])
    bpm = spec["bpm"]
    bars = spec["bars"]
    beat = 60.0 / bpm
    step = beat / 4
    eighth = beat / 2
    total = beat * 4 * bars
    n = int(total * SR)
    drums = np.zeros((n, 2), dtype=np.float32)
    bass = np.zeros((n, 2), dtype=np.float32)
    music = np.zeros((n, 2), dtype=np.float32)
    kick_env = np.zeros(n, dtype=np.float32)

    for start, vel in schedule(style["kick"], bars, step, style["swing"], SR, rng):
        hit, env = synth_kick(SR, style["kick_amp"] * vel, rng)
        mix_mono(drums, hit, start, 0.0)
        end = min(n, start + len(env))
        kick_env[start:end] += env[: end - start]

    if style["snare_amp"]:
        for start, vel in schedule(style["snare"], bars, step, style["swing"], SR, rng):
            hit = synth_snare(SR, style["snare_amp"] * vel, rng)
            mix_mono(drums, hit, start, -0.08)

    if style.get("hat_amp"):
        for start, vel in schedule(style["hat"], bars, step, style["swing"], SR, rng, humanize=True):
            mix_mono(drums, synth_hat(SR, style["hat_amp"] * vel * float(rng.uniform(0.75, 1)), rng), start, 0.42)

    if style.get("open_hat"):
        for start, vel in schedule(style["open_hat"], bars, step, 0, SR, rng):
            mix_mono(drums, synth_hat(SR, style["hat_amp"] * 1.4 * vel, rng, opened=True), start, 0.28)

    if style.get("clave"):
        for start, vel in schedule(style["clave"], bars, step, 0, SR, rng):
            mix_mono(drums, synth_clave(SR, 0.16 * vel), start, 0.22)

    if style.get("ride"):
        for start, vel in schedule(style["ride"], bars, step, style["swing"], SR, rng):
            mix_mono(drums, synth_ride(SR, style.get("ride_amp", 0.1) * vel, rng), start, 0.3)

    if style["vinyl"]:
        noise = rng.normal(0, 1, n).astype(np.float32)
        kernel = np.ones(48, dtype=np.float32) / 48
        vinyl = np.convolve(noise, kernel, mode="same") * style["vinyl"]
        mix_mono(drums, vinyl, 0, 0.0)

    root = spec["root"]
    progression = spec["progression"]
    for bar in range(bars):
        degree = progression[bar % len(progression)]
        tones = voice_around(chord_midis(root, scale, degree), 64)
        start = bar * beat * 4
        for index, midi in enumerate(tones):
            place_note(music, start, beat * 4 * 0.98, midi, style["pad_amp"], style["pad"], -0.15 + index * 0.15, SR)
            place_note(music, start, beat * 4 * 0.98, midi + 0.04, style["pad_amp"] * 0.45, "sine", 0.2, SR)

        motif = spec["motif"]
        for step_i in range(8):
            slot = (bar * 8 + step_i) % len(motif)
            deg = motif[slot]
            if deg < 0:
                continue
            midi = degree_to_midi(root, scale, deg, 1)
            while midi < root + 12:
                midi += 12
            while midi > root + 28:
                midi -= 12
            place_note(
                music,
                start + step_i * eighth,
                eighth * (0.92 if spec["style"] == "ambient" else 0.78),
                midi,
                style["melody_amp"],
                style["lead"],
                -0.28,
                SR,
            )

        add_bass_bar(bass, spec, style, scale, bar, beat, degree, progression, root)

    peak_kick = float(np.max(kick_env)) or 1.0
    duck = 1 - np.clip(kick_env / peak_kick, 0, 1)[:, None] * style["sidechain"]
    bass *= duck
    music *= duck
    music = add_reverb(music, SR, style["reverb"])
    bass = add_reverb(bass, SR, min(0.08, style["reverb"] * 0.25))
    mix = drums + bass + music
    mix -= mix.mean(axis=0, keepdims=True)
    fade = int(0.02 * SR)
    if fade:
        ramp = np.linspace(0, 1, fade, dtype=np.float32)[:, None]
        mix[:fade] *= ramp
        mix[-fade:] *= ramp[::-1]
    rms = float(np.sqrt(np.mean(mix**2) + 1e-9))
    mix *= float(np.clip(0.11 / rms, 0.45, 3.2))
    peak = float(np.max(np.abs(mix)))
    if peak > 0.98:
        mix *= 0.98 / peak
    return mix.astype(np.float32)


def add_bass_bar(bus, spec, style, scale, bar, beat, degree, progression, root):
    mode = style["bass"]
    bar_start = bar * beat * 4
    fundamental = degree_to_midi(root, scale, degree, 0)
    while fundamental > 48:
        fundamental -= 12
    while fundamental < 34:
        fundamental += 12
    kind = "sine" if mode in {"warm", "sparse", "walking"} else "softsaw"
    amp = 0.34 if spec["style"] != "ambient" else 0.2

    if mode == "warm":
        for beat_i in (0, 2):
            place_note(bus, bar_start + beat_i * beat, beat * 0.9, fundamental, amp, kind, 0, SR)
            place_note(bus, bar_start + beat_i * beat, beat * 0.7, fundamental + 12, amp * 0.22, "tri", 0, SR)
    elif mode == "sparse":
        place_note(bus, bar_start, beat * 3.2, fundamental, amp, "sine", 0, SR)
    elif mode == "offbeat":
        for eighth_i in range(8):
            if eighth_i % 2 == 0:
                continue
            place_note(bus, bar_start + eighth_i * beat / 2, beat * 0.42, fundamental, amp * 0.95, kind, 0, SR)
    elif mode == "sync":
        pattern = (0, 3, 4, 6)
        for eighth_i in pattern:
            place_note(bus, bar_start + eighth_i * beat / 2, beat * 0.42, fundamental, amp, kind, 0, SR)
    elif mode == "eighths":
        for eighth_i in range(8):
            interval = 7 if eighth_i in {3, 6} else 0
            place_note(bus, bar_start + eighth_i * beat / 2, beat * 0.42, fundamental + interval, amp * 0.85, kind, 0, SR)
    elif mode == "walking":
        tones = chord_midis(root, scale, degree)
        next_degree = progression[(bar + 1) % len(progression)]
        next_root = degree_to_midi(root, scale, next_degree, 0)
        for beat_i in range(4):
            if beat_i == 0:
                note = tones[0]
            elif beat_i == 3:
                note = next_root - 1
            else:
                note = tones[beat_i % len(tones)]
            while note > 52:
                note -= 12
            while note < 38:
                note += 12
            place_note(bus, bar_start + beat_i * beat, beat * 0.86, note, amp * 0.9, "sine", 0, SR)


def write_wav(path, audio):
    pcm = np.clip(audio, -1, 1)
    ints = (pcm * 32767).astype(np.int16)

    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(SR)
        handle.writeframes(ints.tobytes())


def svg_doc(c1, c2, body):
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 640" role="img">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="{c1}"/>
      <stop offset="100%" stop-color="{c2}"/>
    </linearGradient>
  </defs>
  <rect width="640" height="640" fill="url(#bg)"/>
  {body}
</svg>
"""


def cover_body(layout):
    if layout == "tide":
        return """
        <circle cx="430" cy="210" r="150" fill="#f4fff9" fill-opacity="0.9"/>
        <circle cx="430" cy="210" r="112" fill="none" stroke="#0e3a42" stroke-opacity="0.35" stroke-width="10"/>
        <path d="M0 430 C140 380 220 480 340 440 C470 396 540 470 640 430 L640 640 L0 640 Z" fill="#07343a" fill-opacity="0.55"/>
        <path d="M0 490 C160 450 250 530 390 500 C500 476 560 530 640 500 L640 640 L0 640 Z" fill="#e8fff7" fill-opacity="0.28"/>
        """
    if layout == "circuit":
        return """
        <g fill="none" stroke="#ffe4f2" stroke-opacity="0.45" stroke-width="3">
          <path d="M80 120 H280 V250 H470 V140 H580"/>
          <path d="M70 470 H230 V340 H400 V500 H590"/>
          <path d="M160 80 V220 H300 V420"/>
        </g>
        <g fill="#fff">
          <circle cx="280" cy="250" r="14"/><circle cx="470" cy="140" r="10"/>
          <circle cx="230" cy="340" r="12"/><circle cx="400" cy="500" r="16"/>
        </g>
        <circle cx="460" cy="300" r="92" fill="none" stroke="#fff" stroke-width="18" stroke-opacity="0.85"/>
        """
    if layout == "fog":
        return """
        <circle cx="210" cy="250" r="180" fill="#ffffff" fill-opacity="0.16"/>
        <circle cx="390" cy="300" r="210" fill="#ffffff" fill-opacity="0.18"/>
        <circle cx="320" cy="240" r="70" fill="#ffffff" fill-opacity="0.8"/>
        <rect x="70" y="470" width="500" height="8" rx="4" fill="#ffffff" fill-opacity="0.45"/>
        <rect x="120" y="500" width="360" height="6" rx="3" fill="#ffffff" fill-opacity="0.28"/>
        """
    if layout == "sun":
        return """
        <circle cx="320" cy="300" r="120" fill="#fff6e4"/>
        <g stroke="#fff6e4" stroke-width="10" stroke-linecap="round">
          <path d="M320 90 V150"/><path d="M320 450 V510"/>
          <path d="M120 300 H180"/><path d="M460 300 H520"/>
          <path d="M170 150 L210 190"/><path d="M430 410 L470 450"/>
          <path d="M470 150 L430 190"/><path d="M210 410 L170 450"/>
        </g>
        <path d="M40 520 Q320 430 600 520" fill="none" stroke="#3a220c" stroke-opacity="0.45" stroke-width="16"/>
        """
    if layout == "club":
        return """
        <circle cx="320" cy="320" r="210" fill="none" stroke="#f3edff" stroke-opacity="0.35" stroke-width="2"/>
        <circle cx="320" cy="320" r="150" fill="none" stroke="#f3edff" stroke-opacity="0.5" stroke-width="2"/>
        <circle cx="320" cy="320" r="78" fill="#f3edff"/>
        <circle cx="320" cy="320" r="18" fill="#1a1030"/>
        <path d="M120 470 Q320 390 520 470" fill="none" stroke="#f3edff" stroke-opacity="0.4" stroke-width="8"/>
        """
    return """
    <polygon points="90,90 300,70 560,160 520,520 140,560" fill="#1a0c0a" fill-opacity="0.35"/>
    <polygon points="180,140 470,180 430,470 150,420" fill="#fff1ec" fill-opacity="0.88"/>
    <rect x="250" y="230" width="150" height="150" fill="none" stroke="#2c1612" stroke-width="12"/>
    <path d="M80 500 H560" stroke="#2c1612" stroke-opacity="0.4" stroke-width="8"/>
    """


def write_cover(path, c1, c2, layout):
    path.write_text(svg_doc(c1, c2, cover_body(layout)), encoding="utf-8")


def seed():
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    COVER_DIR.mkdir(parents=True, exist_ok=True)
    conn = connect()
    init_schema(conn)
    saved_refs = reset_catalog(conn)
    artist_ids = {}
    album_ids = {}
    track_ids = {}
    with conn.cursor() as cur:
        for artist in ARTISTS:
            cover = f"{artist['slug']}.svg"
            write_cover(COVER_DIR / cover, artist["accent_a"], artist["accent_b"], artist["layout"])
            cur.execute(
                """
                INSERT INTO artists (slug, name, genre, origin, bio, accent_a, accent_b, cover_file)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (artist["slug"], artist["name"], artist["genre"], artist["origin"], artist["bio"], artist["accent_a"], artist["accent_b"], cover),
            )
            artist_ids[artist["slug"]] = cur.lastrowid

        for album in ALBUMS:
            cover = f"{album['slug']}.svg"
            write_cover(COVER_DIR / cover, album["accent_a"], album["accent_b"], album["layout"])
            cur.execute(
                """
                INSERT INTO albums (slug, artist_id, title, year, credit, accent_a, accent_b, cover_file)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (album["slug"], artist_ids[album["artist"]], album["title"], album["year"], album["credit"], album["accent_a"], album["accent_b"], cover),
            )
            album_ids[album["slug"]] = cur.lastrowid

        album_artist = {album["slug"]: album["artist"] for album in ALBUMS}
        numbers = {}
        for spec in TRACKS:
            print(f"Sintetizando {spec['title']}...")
            audio = render_track(spec)
            filename = f"{spec['slug']}.wav"
            write_wav(AUDIO_DIR / filename, audio)
            duration_ms = int(round(len(audio) / SR * 1000))
            album_slug = spec["album"]
            numbers[album_slug] = numbers.get(album_slug, 0) + 1
            genre = next(item["genre"] for item in ARTISTS if item["slug"] == album_artist[album_slug])
            cur.execute(
                """
                INSERT INTO tracks (
                  slug, album_id, artist_id, title, track_number, duration_ms, audio_file,
                  genre, bpm, musical_key
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    spec["slug"],
                    album_ids[album_slug],
                    artist_ids[album_artist[album_slug]],
                    spec["title"],
                    numbers[album_slug],
                    duration_ms,
                    filename,
                    genre,
                    spec["bpm"],
                    spec["musical_key"],
                ),
            )
            track_ids[spec["slug"]] = cur.lastrowid
            peak = float(np.max(np.abs(audio)))
            print(f"  {duration_ms / 1000:.1f}s  pico {peak:.2f}")

        for index, playlist in enumerate(PLAYLISTS, start=1):
            cover = f"lista-{index}.svg"
            write_cover(COVER_DIR / cover, playlist["accent_a"], playlist["accent_b"], playlist["layout"])
            cur.execute(
                """
                INSERT INTO playlists (name, description, editorial, accent_a, accent_b, cover_file)
                VALUES (%s, %s, 1, %s, %s, %s)
                """,
                (playlist["name"], playlist["description"], playlist["accent_a"], playlist["accent_b"], cover),
            )
            playlist_id = cur.lastrowid
            for position, slug in enumerate(playlist["tracks"]):
                cur.execute(
                    "INSERT INTO playlist_tracks (playlist_id, track_id, position) VALUES (%s, %s, %s)",
                    (playlist_id, track_ids[slug], position),
                )
    restore_catalog_refs(conn, saved_refs)
    conn.close()
    print("Catálogo listo en MariaDB y en static/.")


if __name__ == "__main__":
    if "--force" not in sys.argv:
        conn = connect()
        init_schema(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS total FROM tracks WHERE source = 'catalog'")
            total = cur.fetchone()["total"]
        conn.close()
        if total:
            print(f"Ya hay {total} pistas. Usa --force para regenerar el catálogo editorial.")
            sys.exit(0)
    seed()
