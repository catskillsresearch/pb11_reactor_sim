# ==============================================================================
# Copyright 2026 Catskills Research Company
# 
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# 
#     http://apache.org
# 
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================

"""
Procedural shot audio for MP4 export.

Synthesizes phase-appropriate facility sounds (vacuum hum, coil ramp, beam
drive, fusion flat-top, ramp-down) synced to the per-frame metadata captured
during recording. Pure NumPy — no extra audio dependencies.
"""
from __future__ import annotations

import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

_RNG = np.random.default_rng(11)

FPS = 30.0
SAMPLE_RATE = 48_000


@dataclass(frozen=True)
class FrameMeta:
    """One GUI frame's operational context for sound design."""

    phase: str = ""
    fast_forward: bool = False
    intensity: float = 0.0  # beam / discharge level in [0, 1]


def synthesize_frame_chunk(
    meta: FrameMeta,
    *,
    fps: float = FPS,
    sample_rate: int = SAMPLE_RATE,
    frame_index: int = 0,
) -> np.ndarray:
    """One GUI-frame of procedural bed audio (for live playback)."""
    n_per = max(1, int(round(sample_rate / fps)))
    t0 = frame_index / fps
    t = t0 + np.arange(n_per, dtype=np.float64) / sample_rate
    local = (t - t0) * fps
    return _synthesize_frame(meta, t, local, frame_index=frame_index).astype(np.float32)


def synthesize_shot_audio(
    meta: list[FrameMeta],
    *,
    fps: float = FPS,
    sample_rate: int = SAMPLE_RATE,
) -> np.ndarray:
    """Return mono float32 audio [-1, 1] matching ``len(meta)`` video frames."""
    if not meta:
        return np.zeros(0, dtype=np.float32)

    n_per = int(round(sample_rate / fps))
    chunks: list[np.ndarray] = []
    for i, m in enumerate(meta):
        t0 = i / fps
        t = t0 + np.arange(n_per, dtype=np.float64) / sample_rate
        local = (t - t0) * fps  # 0..1 within this frame
        chunk = _synthesize_frame(m, t, local, frame_index=i)
        chunks.append(chunk.astype(np.float32))
    out = np.concatenate(chunks)
    return _normalize(out, peak=0.92)


def write_wav(path: Path | str, samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> None:
    """Write mono 16-bit PCM WAV."""
    pcm = np.clip(samples, -1.0, 1.0)
    pcm_i16 = (pcm * 32767.0).astype(np.int16)
    path = Path(path)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_i16.tobytes())


# ---------------------------------------------------------------------------
# Per-frame synthesis
# ---------------------------------------------------------------------------
def _synthesize_frame(
    m: FrameMeta,
    t: np.ndarray,
    local: np.ndarray,
    *,
    frame_index: int,
) -> np.ndarray:
    phase = m.phase or "idle"
    ff = m.fast_forward
    amp = float(np.clip(m.intensity, 0.0, 1.0))
    rate = 4.0 if ff else 1.0

    bed = _facility_hum(t, level=0.06)
    sig = bed.copy()

    if phase in ("idle", "unarmed", "armed"):
        sig += _vacuum_pump(t, level=0.05)
    elif phase == "gas_fill":
        sig += _gas_puff(t, local, rate=rate)
    elif phase in ("field_ramp",):
        sig += _coil_ramp(t, local, rate=rate)
    elif phase == "formation":
        sig += _plasma_ignition(t, local, rate=rate)
    elif phase == "nbi_heat":
        sig += _nbi_ramp(t, local, rate=rate, level=0.35 + 0.45 * amp)
    elif phase in ("flat_top", "main_pulse", "pinch"):
        sig += _discharge_flat_top(t, local, level=0.45 + 0.5 * amp, frame_index=frame_index)
    elif phase in ("ramp_down", "afterglow", "recovery"):
        sig += _power_down(t, local, rate=rate)
    elif phase == "quiescent":
        sig += _cooldown(t, local)
    elif phase == "grid_charge":
        sig += _hv_static(t, local, rate=rate)
    elif phase == "laser_countdown":
        sig += _countdown_beeps(t, local, t0=t[0])
    elif phase == "trigger":
        sig += _relay_click(t, local)
    elif phase == "rundown":
        sig += _sheath_accel(t, local, rate=rate)
    elif phase == "disrupt":
        sig += _disrupt_bang(t, local)
    else:
        sig += _facility_hum(t, level=0.04)

    return sig


def _facility_hum(t: np.ndarray, *, level: float) -> np.ndarray:
    return level * (
        np.sin(2 * np.pi * 60.0 * t)
        + 0.45 * np.sin(2 * np.pi * 120.0 * t + 0.3)
        + 0.08 * _RNG.standard_normal(len(t))
    )


def _vacuum_pump(t: np.ndarray, *, level: float) -> np.ndarray:
    wobble = 1.0 + 0.04 * np.sin(2 * np.pi * 2.7 * t)
    return level * wobble * (
        0.6 * np.sin(2 * np.pi * 90.0 * t) + 0.25 * np.sin(2 * np.pi * 180.0 * t)
    )


def _gas_puff(t: np.ndarray, local: np.ndarray, *, rate: float) -> np.ndarray:
    env = np.exp(-local * 3.5 * rate) * (1.0 - np.exp(-local * 18 * rate))
    hiss = _filtered_noise(len(t), cutoff=0.35)
    return 0.35 * env * hiss + 0.08 * np.sin(2 * np.pi * (220 + 80 * local * rate) * t)


def _coil_ramp(t: np.ndarray, local: np.ndarray, *, rate: float) -> np.ndarray:
    f0, f1 = 70.0, 240.0
    f = f0 + (f1 - f0) * np.clip(local * rate, 0, 1)
    phase = 2 * np.pi * np.cumsum(f) / SAMPLE_RATE
    whine = np.sin(phase) + 0.35 * np.sin(phase * 2.01)
    env = 0.15 + 0.85 * np.clip(local * rate, 0, 1)
    return 0.28 * env * whine


def _plasma_ignition(t: np.ndarray, local: np.ndarray, *, rate: float) -> np.ndarray:
    crack = _filtered_noise(len(t), cutoff=0.5) * np.exp(-local * 2.0 * rate)
    ring = 0.2 * np.sin(2 * np.pi * 160.0 * t) * np.exp(-local * 4 * rate)
    pop = 0.15 * np.sin(2 * np.pi * 55.0 * t) * (1.0 - np.exp(-local * 30 * rate))
    return crack + ring + pop


def _nbi_ramp(t: np.ndarray, local: np.ndarray, *, rate: float, level: float) -> np.ndarray:
    f = 280.0 + 1400.0 * np.clip(local * rate, 0, 1)
    beam = np.sin(2 * np.pi * f * t) + 0.4 * np.sin(2 * np.pi * f * 1.97 * t)
    growl = _filtered_noise(len(t), cutoff=0.12) * np.clip(local * rate, 0, 1)
    return level * (0.22 * beam + 0.18 * growl)


def _discharge_flat_top(
    t: np.ndarray,
    local: np.ndarray,
    *,
    level: float,
    frame_index: int,
) -> np.ndarray:
    sub = 0.35 * np.sin(2 * np.pi * 42.0 * t)
    beam = 0.22 * (
        np.sin(2 * np.pi * 185.0 * t)
        + 0.5 * np.sin(2 * np.pi * 370.0 * t + 0.2)
    )
    # ICC segment ripple (audible down-shift of the ~MHz pickup).
    icc = 0.12 * np.sin(2 * np.pi * 880.0 * t) * (1.0 + 0.35 * np.sin(2 * np.pi * 6.5 * t))
    rumble = 0.1 * _filtered_noise(len(t), cutoff=0.08)
    # Sparse fusion crackle — deterministic from frame index.
    crack = np.zeros_like(t)
    if frame_index % 17 == 3:
        crack = 0.25 * _filtered_noise(len(t), cutoff=0.4) * np.exp(-local * 6)
    return level * (sub + beam + icc + rumble + crack)


def _power_down(t: np.ndarray, local: np.ndarray, *, rate: float) -> np.ndarray:
    f = 200.0 * (1.0 - 0.75 * np.clip(local * rate, 0, 1))
    decay = 1.0 - np.clip(local * rate, 0, 1)
    return decay * 0.3 * np.sin(2 * np.pi * f * t)


def _cooldown(t: np.ndarray, local: np.ndarray) -> np.ndarray:
    decay = np.maximum(0.15, 1.0 - 0.35 * local)
    tick = 0.06 * np.sin(2 * np.pi * 800.0 * t) * np.exp(-((local - 0.5) ** 2) / 0.02)
    return decay * _facility_hum(t, level=0.05) + tick


def _hv_static(t: np.ndarray, local: np.ndarray, *, rate: float) -> np.ndarray:
    env = np.clip(local * rate, 0, 1)
    return env * 0.2 * _filtered_noise(len(t), cutoff=0.6)


def _countdown_beeps(t: np.ndarray, local: np.ndarray, *, t0: float) -> np.ndarray:
    # Three beeps spread across the phase frame.
    sig = np.zeros_like(t)
    for i, (offset, freq) in enumerate([(0.05, 880.0), (0.38, 988.0), (0.72, 1175.0)]):
        mask = (local >= offset) & (local < offset + 0.12)
        sig[mask] += 0.35 * np.sin(2 * np.pi * freq * (t[mask] - t0))
    return sig


def _relay_click(t: np.ndarray, local: np.ndarray) -> np.ndarray:
    click = np.exp(-local * 80) * _filtered_noise(len(t), cutoff=0.9)
    return 0.55 * click + 0.15 * np.sin(2 * np.pi * 120.0 * t) * np.exp(-local * 20)


def _sheath_accel(t: np.ndarray, local: np.ndarray, *, rate: float) -> np.ndarray:
    f = 100.0 + 900.0 * np.clip(local * rate, 0, 1)
    return 0.35 * np.sin(2 * np.pi * f * t) * (0.3 + 0.7 * local)


def _disrupt_bang(t: np.ndarray, local: np.ndarray) -> np.ndarray:
    bang = np.exp(-local * 25) * _filtered_noise(len(t), cutoff=0.25)
    thump = 0.4 * np.sin(2 * np.pi * 35.0 * t) * np.exp(-local * 8)
    return bang + thump


def _filtered_noise(n: int, *, cutoff: float) -> np.ndarray:
    """Simple one-pole low-pass filtered white noise."""
    raw = _RNG.standard_normal(n)
    if n == 0:
        return raw
    alpha = float(np.clip(cutoff, 0.01, 0.99))
    out = np.empty(n)
    out[0] = raw[0]
    for i in range(1, n):
        out[i] = alpha * out[i - 1] + (1.0 - alpha) * raw[i]
    return out / max(np.std(out), 1e-9)


def _normalize(samples: np.ndarray, *, peak: float) -> np.ndarray:
    mx = float(np.max(np.abs(samples))) if samples.size else 0.0
    if mx < 1e-9:
        return samples
    return samples * (peak / mx)


def pad_audio(samples: np.ndarray, length: int) -> np.ndarray:
    """Zero-pad or truncate mono audio to ``length`` samples."""
    if samples.size >= length:
        return samples[:length]
    out = np.zeros(length, dtype=np.float32)
    out[: samples.size] = samples
    return out


def mix_tracks(
    bed: np.ndarray,
    overlay: np.ndarray | None,
    *,
    bed_level: float = 1.44,
    overlay_level: float = 1.0,
    duck_with_voice: float = 0.5,
    voice_mask: np.ndarray | None = None,
) -> np.ndarray:
    """Mix procedural bed (2× level) with voice; duck bed to ~1× during callouts."""
    if overlay is None or overlay.size == 0:
        return _normalize(bed * bed_level, peak=0.92)
    n = max(bed.size, overlay.size)
    b = pad_audio(np.asarray(bed, dtype=np.float32), n)
    o = pad_audio(np.asarray(overlay, dtype=np.float32), n)
    if voice_mask is not None and voice_mask.size >= n:
        voice = voice_mask[:n] > 0.5
    else:
        voice = np.abs(o) > 1e-3
    duck = np.where(voice, duck_with_voice, 1.0).astype(np.float32)
    mixed = b * bed_level * duck + o * overlay_level
    return _normalize(mixed, peak=0.92)
