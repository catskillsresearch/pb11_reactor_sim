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
    narration_enabled,
    phase_segments,
    sanitize_narration_line,
    synthesize_speech,
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
    scripts = PHASE_NARRATION.get(reactor_name, PHASE_NARRATION["TAE FRC"])
    out_frames: list[bytes] = []
    out_meta: list[FrameMeta] = []
    segments: list[TimelineSegment] = []
    cursor_s = 0.0

    for phase, start_f, end_f in phase_segments(meta):
        clip = frames[start_f:end_f]
        clip_meta = meta[start_f:end_f]
        if not clip:
            continue

        raw_line = scripts.get(phase)
        if raw_line and narration_enabled():
            text = sanitize_narration_line(raw_line)
            speech = synthesize_speech(text)
            speech_dur_s = speech.size / sample_rate if speech.size else 0.0
            window_s = speech_dur_s + POST_PAUSE_S
            n_out = max(len(clip), max(1, int(math.ceil(window_s * fps))))
            stretched = _stretch_frames(clip, n_out)
            stretched_meta = _stretch_meta(clip_meta, n_out)
            if speech.size:
                segments.append(
                    TimelineSegment(
                        phase=phase,
                        text=text,
                        speech=speech,
                        speech_dur_s=speech_dur_s,
                        start_s=cursor_s,
                        end_s=cursor_s + window_s,
                    )
                )
            for png in stretched:
                sub = text if with_subtitles and speech.size else None
                out_frames.append(burn_subtitle(png, sub) if sub else png)
            out_meta.extend(stretched_meta)
            cursor_s += n_out / fps
        else:
            out_frames.extend(clip)
            out_meta.extend(clip_meta)
            cursor_s += len(clip) / fps

    duration_s = cursor_s
    narr, voice_mask = _assemble_narration(segments, duration_s, sample_rate)
    return ExportTimeline(
        frames=out_frames,
        meta=out_meta,
        segments=segments,
        narration=narr,
        voice_mask=voice_mask,
        duration_s=duration_s,
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
        end = min(n, offset + seg.speech.size)
        m = end - offset
        if m > 0:
            track[offset:end] += seg.speech[:m]
        # Duck reactor bed for the full segment window (speech + post-pause).
        win_end = min(n, int(round(seg.end_s * sample_rate)))
        if win_end > offset:
            mask[offset:win_end] = 1.0
    peak = float(np.max(np.abs(track)))
    if peak > 1e-6:
        track *= min(1.0, 0.95 / peak)
    return track, mask
