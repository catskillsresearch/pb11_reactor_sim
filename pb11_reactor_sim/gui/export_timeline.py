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
Narration-first export timeline: synthesize voice, extend/hold video frames, subtitles.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from pb11_reactor_sim.gui.audio_synth import FPS, SAMPLE_RATE, FrameMeta, pad_audio
from pb11_reactor_sim.gui.chattts_narration import (
    POST_PAUSE_S,
    _normalize_narration_text,
    format_numbered_subtitle,
    narration_enabled,
    phase_segments,
)
from pb11_reactor_sim.gui.narration_scripts import PHASE_NARRATION
from pb11_reactor_sim.gui.video_subtitles import burn_subtitle

# Bed mix: 2× reactor level normally; duck to ~1× (prior level) during each callout.
BED_LEVEL = 1.44
BED_DUCK_FACTOR = 0.5  # 1.44 × 0.5 ≈ prior 0.72


@dataclass(frozen=True)
class TimelineSegment:
    phase: str
    text: str
    speech: np.ndarray
    speech_dur_s: float
    start_s: float
    end_s: float
    post_pause_s: float = POST_PAUSE_S


@dataclass
class ExportTimeline:
    frames: list[bytes]
    meta: list[FrameMeta]
    segments: list[TimelineSegment]
    narration: np.ndarray
    voice_mask: np.ndarray
    duration_s: float
    diag_frames: list[bytes] | None = None


def speech_for_phase_line(
    raw_line: str,
    *,
    sample_rate: int = SAMPLE_RATE,
) -> tuple[np.ndarray, float, str]:
    """Cached speech for timeline assembly.

    Returns ``(full_audio, core_duration_s, display_text)``. Core duration
    excludes the playback trailer so segment windows match callouts.
    """
    if not narration_enabled() or not raw_line.strip():
        return np.zeros(0, dtype=np.float32), 0.0, ""
    from pb11_reactor_sim.gui.narration_cache import (
        PLAYBACK_TRAILER_S,
        synthesize_and_cache,
    )

    text = _normalize_narration_text(raw_line)
    full = synthesize_and_cache(text)
    trailer_n = int(round(PLAYBACK_TRAILER_S * sample_rate))
    core = full[:-trailer_n] if full.size > trailer_n else full
    core_dur = core.size / sample_rate if core.size else 0.0
    return full, core_dur, text


def _phase_stretch_plan(
    meta: list[FrameMeta],
    *,
    reactor_name: str,
    fps: float = FPS,
    sample_rate: int = SAMPLE_RATE,
) -> tuple[list[tuple[str, int, int, int, TimelineSegment | None, str | None]], float]:
    """Per phase: (phase, start, end, n_out, segment, subtitle_text), total duration [s]."""
    scripts = PHASE_NARRATION.get(reactor_name, PHASE_NARRATION["TAE FRC"])
    phase_runs = phase_segments(meta)
    n_phases = len(phase_runs)
    plans: list[tuple[str, int, int, int, TimelineSegment | None, str | None]] = []
    cursor_s = 0.0

    for seq, (phase, start_f, end_f) in enumerate(phase_runs, start=1):
        clip_len = end_f - start_f
        if clip_len <= 0:
            continue

        raw_line = scripts.get(phase)
        segment: TimelineSegment | None = None
        subtitle: str | None = None
        if raw_line and narration_enabled():
            speech, speech_dur_s, text = speech_for_phase_line(raw_line, sample_rate=sample_rate)
            window_s = speech_dur_s + POST_PAUSE_S
            n_out = max(clip_len, max(1, int(math.ceil(window_s * fps))))
            if speech.size:
                segment = TimelineSegment(
                    phase=phase,
                    text=text,
                    speech=speech,
                    speech_dur_s=speech_dur_s,
                    start_s=cursor_s,
                    end_s=cursor_s + window_s,
                )
                subtitle = format_numbered_subtitle(seq, n_phases, text)
        else:
            n_out = clip_len

        plans.append((phase, start_f, end_f, n_out, segment, subtitle))
        cursor_s += n_out / fps

    return plans, cursor_s


def stretch_clips_with_plan(
    frames: list[bytes],
    meta: list[FrameMeta],
    plans: list[tuple[str, int, int, int, TimelineSegment | None, str | None]],
    *,
    with_subtitles: bool = False,
) -> tuple[list[bytes], list[FrameMeta]]:
    """Apply a shared narration-first stretch plan to one frame list."""
    out_frames: list[bytes] = []
    out_meta: list[FrameMeta] = []
    for _phase, start_f, end_f, n_out, _segment, subtitle in plans:
        clip = frames[start_f:end_f]
        clip_meta = meta[start_f:end_f]
        if not clip:
            continue
        stretched = _stretch_frames(clip, n_out)
        stretched_meta = _stretch_meta(clip_meta, n_out)
        sub = subtitle if with_subtitles and subtitle else None
        for png in stretched:
            out_frames.append(burn_subtitle(png, sub) if sub else png)
        out_meta.extend(stretched_meta)
    return out_frames, out_meta


def build_export_timeline(
    frames: list[bytes],
    meta: list[FrameMeta],
    *,
    reactor_name: str,
    fps: float = FPS,
    sample_rate: int = SAMPLE_RATE,
    with_subtitles: bool = True,
) -> ExportTimeline:
    """Synthesize narration first, then stretch/hold source frames to fit."""
    plans, duration_s = _phase_stretch_plan(
        meta, reactor_name=reactor_name, fps=fps, sample_rate=sample_rate
    )
    segments = [p[4] for p in plans if p[4] is not None]
    out_frames, out_meta = stretch_clips_with_plan(
        frames, meta, plans, with_subtitles=with_subtitles
    )
    narr, voice_mask = _assemble_narration(segments, duration_s, sample_rate)
    return ExportTimeline(
        frames=out_frames,
        meta=out_meta,
        segments=segments,
        narration=narr,
        voice_mask=voice_mask,
        duration_s=duration_s,
    )


def build_playback_timeline(
    canvas_frames: list[bytes],
    diag_frames: list[bytes],
    meta: list[FrameMeta],
    *,
    reactor_name: str,
    fps: float = FPS,
    sample_rate: int = SAMPLE_RATE,
    with_subtitles: bool = True,
) -> ExportTimeline:
    """Stretch canvas + diagnostics in lockstep; mix narration (+ subtitles for GUI)."""
    plans, duration_s = _phase_stretch_plan(
        meta, reactor_name=reactor_name, fps=fps, sample_rate=sample_rate
    )
    segments = [p[4] for p in plans if p[4] is not None]
    out_canvas, out_meta = stretch_clips_with_plan(
        canvas_frames, meta, plans, with_subtitles=with_subtitles
    )
    out_diag, _ = stretch_clips_with_plan(
        diag_frames, meta, plans, with_subtitles=with_subtitles
    )
    narr, voice_mask = _assemble_narration(segments, duration_s, sample_rate)
    return ExportTimeline(
        frames=out_canvas,
        meta=out_meta,
        segments=segments,
        narration=narr,
        voice_mask=voice_mask,
        duration_s=duration_s,
        diag_frames=out_diag,
    )


def _stretch_frames(frames: list[bytes], n_out: int) -> list[bytes]:
    if n_out <= len(frames):
        return frames[:n_out]
    if len(frames) == 1:
        return frames * n_out
    out: list[bytes] = []
    for i in range(n_out):
        idx = (i * (len(frames) - 1)) // max(n_out - 1, 1)
        out.append(frames[idx])
    return out


def _stretch_meta(meta: list[FrameMeta], n_out: int) -> list[FrameMeta]:
    if n_out <= len(meta):
        return meta[:n_out]
    if len(meta) == 1:
        return meta * n_out
    out: list[FrameMeta] = []
    for i in range(n_out):
        idx = (i * (len(meta) - 1)) // max(n_out - 1, 1)
        out.append(meta[idx])
    return out


def _assemble_narration(
    segments: list[TimelineSegment],
    duration_s: float,
    sample_rate: int,
) -> tuple[np.ndarray, np.ndarray]:
    n = max(1, int(round(duration_s * sample_rate)))
    track = np.zeros(n, dtype=np.float32)
    mask = np.zeros(n, dtype=np.float32)
    for seg in segments:
        offset = int(round(seg.start_s * sample_rate))
        core_n = min(
            int(round(seg.speech_dur_s * sample_rate)),
            int(seg.speech.size),
        )
        if core_n > 0:
            end = min(n, offset + core_n)
            track[offset:end] += seg.speech[: core_n]
        # Duck reactor bed for the full segment window (speech + post-pause).
        win_end = min(n, int(round(seg.end_s * sample_rate)))
        if win_end > offset:
            mask[offset:win_end] = 1.0
    peak = float(np.max(np.abs(track)))
    if peak > 1e-6:
        track *= min(1.0, 0.95 / peak)
    return track, mask
