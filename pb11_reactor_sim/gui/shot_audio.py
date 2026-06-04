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
Pre-built shot audio (same mix as MP4 export) for GUI playback and recording mux.

Build once from the reactor shot script, play with ``ShotAudioPlayer``, and reuse
the recording-based mix when saving MP4.
"""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from pb11_reactor_sim.engine.base import ReactorSimulation
from pb11_reactor_sim.engine.shot_sequence import FirePhase
from pb11_reactor_sim.gui.audio_synth import FPS, SAMPLE_RATE, FrameMeta, mix_tracks, pad_audio, synthesize_shot_audio
from pb11_reactor_sim.gui.chattts_narration import (
    POST_PAUSE_S,
    narration_enabled,
)
from pb11_reactor_sim.gui.export_timeline import speech_for_phase_line
from pb11_reactor_sim.gui.export_timeline import (
    BED_DUCK_FACTOR,
    BED_LEVEL,
    TimelineSegment,
    _assemble_narration,
    build_export_timeline,
)
from pb11_reactor_sim.gui.narration_scripts import PHASE_NARRATION

ProgressCallback = Callable[[int, int, str], None]

# Phases that fast-forward in the GUI (pre-discharge countdown).
_PRE_DISCHARGE = frozenset(
    {
        "armed",
        "gas_fill",
        "field_ramp",
        "formation",
        "nbi_heat",
        "grid_charge",
        "laser_countdown",
        "trigger",
        "rundown",
    }
)
# Bed-only segment length when a phase has no narration line (armed/quiescent only).
_BED_ONLY_FRAMES = 30

# Keep in sync with ``app.py`` — GUI physics substeps per refresh tick.
_SUBSTEPS_PER_FRAME = 4
_STARTUP_SUBSTEP_MULT = 35
_PLATEAU_SUBSTEP_MULT = 10
_TAIL_SUBSTEP_MULT = 4


def _substep_mult_for_phase(
    phase_key: str,
    active_phases: tuple[FirePhase, ...],
    *,
    discharge_key: str,
    plateau_keys: frozenset[str],
    tail_keys: frozenset[str],
) -> int:
    """Match :meth:`~pb11_reactor_sim.app.MainWindow._substeps_per_frame`."""
    keys = [p.key for p in active_phases]
    if phase_key not in keys:
        return 1
    if phase_key in plateau_keys:
        return _PLATEAU_SUBSTEP_MULT
    if phase_key in tail_keys:
        return _TAIL_SUBSTEP_MULT
    try:
        if keys.index(phase_key) < keys.index(discharge_key):
            return _STARTUP_SUBSTEP_MULT
    except ValueError:
        pass
    return 1


def _gui_frames_for_phase(phase: FirePhase, *, dt: float, substep_mult: int) -> int:
    """GUI frames needed to advance ``phase.duration_s`` at the live sim step rate."""
    sim_dt_per_gui = _SUBSTEPS_PER_FRAME * substep_mult * dt
    return max(1, int(math.ceil(phase.duration_s / sim_dt_per_gui)))


@dataclass(frozen=True)
class CompiledShotAudio:
    mixed: np.ndarray
    meta: list[FrameMeta]
    duration_s: float
    segments: list[TimelineSegment]


def mix_shot_audio(
    meta: list[FrameMeta],
    segments: list[TimelineSegment],
    duration_s: float,
    *,
    sample_rate: int = SAMPLE_RATE,
) -> np.ndarray:
    """Same 2× bed + ducked narration mix used for MP4 mux."""
    bed = synthesize_shot_audio(meta, fps=FPS)
    narr, voice_mask = _assemble_narration(segments, duration_s, sample_rate)
    n = max(bed.size, narr.size if narr is not None else 0)
    bed = pad_audio(bed, n)
    return mix_tracks(
        bed,
        narr,
        bed_level=BED_LEVEL,
        duck_with_voice=BED_DUCK_FACTOR,
        voice_mask=voice_mask,
    )


def build_scripted_shot_audio(
    reactor_cls: type[ReactorSimulation],
    *,
    from_quiescent: bool = False,
    progress: ProgressCallback | None = None,
) -> CompiledShotAudio:
    """Compile the full Arm → Fire → quiescent audio track from the shot script."""
    ops = reactor_cls.shot_ops()
    reactor_name = reactor_cls.display_name
    scripts = PHASE_NARRATION.get(reactor_name, PHASE_NARRATION["TAE FRC"])

    phase_keys: list[str] = []
    if not from_quiescent:
        phase_keys.append("armed")
    phase_keys.extend(p.key for p in ops.phases_for_fire(from_quiescent))
    phase_keys.append("quiescent")

    probe = reactor_cls()
    dt = probe.default_dt()
    discharge_key = probe.discharge_phase_key()
    plateau_keys = probe.plateau_phase_keys()
    tail_keys = probe.tail_fast_phase_keys()
    active_phases = ops.phases_for_fire(from_quiescent)
    phase_by_key = {p.key: p for p in active_phases}

    meta: list[FrameMeta] = []
    segments: list[TimelineSegment] = []
    cursor_s = 0.0
    total = len(phase_keys)

    for i, phase_key in enumerate(phase_keys):
        if progress is not None:
            progress(i, total, phase_key)

        ff = phase_key in _PRE_DISCHARGE
        intensity = 0.35 if phase_key in ("flat_top", "main_pulse", "pinch") else 0.2
        raw = scripts.get(phase_key)
        speech = np.zeros(0, dtype=np.float32)
        text = ""
        if raw and narration_enabled():
            speech, speech_dur_s, text = speech_for_phase_line(raw)
        else:
            speech_dur_s = 0.0

        fire_phase = phase_by_key.get(phase_key)
        if fire_phase is not None:
            sm = _substep_mult_for_phase(
                phase_key,
                active_phases,
                discharge_key=discharge_key,
                plateau_keys=plateau_keys,
                tail_keys=tail_keys,
            )
            gui_frames = _gui_frames_for_phase(fire_phase, dt=dt, substep_mult=sm)
        else:
            gui_frames = _BED_ONLY_FRAMES

        if speech.size:
            window_s = speech_dur_s + POST_PAUSE_S
            n_frames = max(max(1, int(math.ceil(window_s * FPS))), gui_frames)
            segments.append(
                TimelineSegment(
                    phase=phase_key,
                    text=text,
                    speech=speech,
                    speech_dur_s=speech_dur_s,
                    start_s=cursor_s,
                    end_s=cursor_s + window_s,
                )
            )
        else:
            n_frames = gui_frames

        for _ in range(n_frames):
            meta.append(FrameMeta(phase=phase_key, fast_forward=ff, intensity=intensity))
        cursor_s += n_frames / FPS

    if progress is not None:
        progress(total, total, "mixing")

    mixed = mix_shot_audio(meta, segments, cursor_s)
    return CompiledShotAudio(mixed=mixed, meta=meta, duration_s=cursor_s, segments=segments)


def build_recorded_shot_audio(
    frames: list[bytes],
    meta: list[FrameMeta],
    *,
    reactor_name: str,
    progress: ProgressCallback | None = None,
) -> CompiledShotAudio:
    """Compile mixed audio from captured frames (matches MP4 export timeline)."""
    if progress is not None:
        progress(0, 3, "narration timeline")
    timeline = build_export_timeline(
        frames,
        meta,
        reactor_name=reactor_name,
        with_subtitles=False,
    )
    if progress is not None:
        progress(1, 3, "reactor bed")
    bed = synthesize_shot_audio(timeline.meta, fps=FPS)
    if progress is not None:
        progress(2, 3, "mixing")
    n = max(bed.size, timeline.narration.size)
    bed = pad_audio(bed, n)
    mixed = mix_tracks(
        bed,
        timeline.narration,
        bed_level=BED_LEVEL,
        duck_with_voice=BED_DUCK_FACTOR,
        voice_mask=timeline.voice_mask,
    )
    if progress is not None:
        progress(3, 3, "done")
    return CompiledShotAudio(
        mixed=mixed,
        meta=timeline.meta,
        duration_s=timeline.duration_s,
        segments=timeline.segments,
    )
