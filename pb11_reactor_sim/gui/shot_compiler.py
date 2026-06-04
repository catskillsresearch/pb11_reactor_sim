# ==============================================================================
# Copyright 2026 Catskills Research Company
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================

"""
Precompute a full Arm → Fire → quiescent shot for synced GUI playback.

Several physics snapshots per phase (particle motion), then narration-first
stretch + audio mix (same recipe as MP4 export). Voice clips come from the
warmed disk cache only (no synthesis during fast-forward capture).
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from pb11_reactor_sim.engine.base import ReactorSimulation
from pb11_reactor_sim.engine.shot_sequence import ShotPhase
from pb11_reactor_sim.gui.audio_synth import FrameMeta, mix_tracks, pad_audio, synthesize_shot_audio
from pb11_reactor_sim.gui.chattts_narration import phase_segments
from pb11_reactor_sim.gui.export_timeline import (
    BED_DUCK_FACTOR,
    BED_LEVEL,
    FPS,
    TimelineSegment,
    build_playback_timeline,
)
from pb11_reactor_sim.gui.shot_script import frame_meta_for_phase, scripted_phase_keys
from pb11_reactor_sim.gui.sim_stepping import is_fast_gui_frame, substeps_per_frame

ProgressCallback = Callable[[int, int, str], None]
GrabCanvas = Callable[[], bytes | None]
GrabDiagnostics = Callable[[], bytes | None]
TickUi = Callable[[], None]


@dataclass(frozen=True)
class CompiledPlayback:
    canvas_frames: list[bytes]
    diag_frames: list[bytes]
    meta: list[FrameMeta]
    mixed_audio: np.ndarray
    duration_s: float
    arm_end_ix: int
    fire_start_ix: int
    arm_end_s: float
    fire_start_s: float
    from_quiescent: bool
    timeline_segments: tuple[TimelineSegment, ...] = ()


# Snapshots per phase before stretch; more clips → visible macroparticle motion.
_CAPTURE_GRABS_PER_PHASE = 20
_MAX_SNAPS_PER_PHASE = 28


def _phase_index_bounds(meta: list[FrameMeta], phase: str) -> tuple[int, int]:
    for key, start, end in phase_segments(meta):
        if key == phase:
            return start, end
    return 0, 0


def _segment_bounds(
    segments: list[TimelineSegment],
    phase: str,
) -> tuple[float, float]:
    for seg in segments:
        if seg.phase == phase:
            return seg.start_s, seg.end_s
    return 0.0, 0.0


def _shot_intensity(reactor: ReactorSimulation) -> float:
    nbi = float(getattr(reactor, "_nbi_scale", 0.0))
    if nbi > 0:
        return nbi
    return min(1.0, max(0.0, reactor.last_q_net / 1.8))


def _grab_snapshot(
    reactor: ReactorSimulation,
    phase_key: str,
    *,
    grab_canvas: GrabCanvas,
    grab_diag: GrabDiagnostics,
    tick_ui: TickUi,
    refresh_canvas: Callable[[], None],
    refresh_diag: Callable[[], None],
) -> tuple[bytes, bytes, FrameMeta] | None:
    refresh_canvas()
    refresh_diag()
    tick_ui()
    c = grab_canvas()
    d = grab_diag()
    if not c or not d:
        return None
    meta = frame_meta_for_phase(phase_key)
    meta = FrameMeta(
        phase=phase_key,
        fast_forward=is_fast_gui_frame(reactor) if reactor.shot_phase == ShotPhase.FIRING else meta.fast_forward,
        intensity=_shot_intensity(reactor),
    )
    return c, d, meta


def _capture_phase_clips(
    reactor: ReactorSimulation,
    *,
    from_quiescent: bool,
    grab_canvas: GrabCanvas,
    grab_diag: GrabDiagnostics,
    tick_ui: TickUi,
    refresh_canvas: Callable[[], None],
    refresh_diag: Callable[[], None],
) -> tuple[list[bytes], list[bytes], list[FrameMeta]]:
    """Multiple PNGs per scripted phase so compile playback shows particle motion."""
    expected = scripted_phase_keys(type(reactor), from_quiescent=from_quiescent)
    phase_clips: dict[str, list[tuple[bytes, bytes, FrameMeta]]] = defaultdict(list)

    def append_snap(phase_key: str, snap: tuple[bytes, bytes, FrameMeta] | None) -> None:
        if snap is None:
            return
        clips = phase_clips[phase_key]
        if len(clips) >= _MAX_SNAPS_PER_PHASE:
            return
        clips.append(snap)

    def grab(phase_key: str) -> None:
        append_snap(
            phase_key,
            _grab_snapshot(
                reactor,
                phase_key,
                grab_canvas=grab_canvas,
                grab_diag=grab_diag,
                tick_ui=tick_ui,
                refresh_canvas=refresh_canvas,
                refresh_diag=refresh_diag,
            ),
        )

    reactor.apply_controls(reactor.controls)
    reactor.arm_shot()
    refresh_canvas()
    refresh_diag()
    tick_ui()

    if not from_quiescent:
        for _ in range(6):
            grab("armed")
            tick_ui()

    if not reactor.fire_shot():
        raise RuntimeError("fire_shot failed after arm")

    prev_key = reactor._fire_phase_key
    ticks_in_phase = 0
    step_budget = 12_000

    for _ in range(step_budget):
        substeps = substeps_per_frame(reactor)
        for _ in range(substeps):
            reactor.step()

        if reactor.shot_phase == ShotPhase.FIRING:
            key = reactor._fire_phase_key
        elif reactor.shot_phase == ShotPhase.QUIESCENT:
            key = "quiescent"
        else:
            key = reactor.shot_phase.value

        if key != prev_key:
            grab(prev_key)
            ticks_in_phase = 0
            prev_key = key

        ticks_in_phase += 1
        if reactor.shot_phase == ShotPhase.FIRING:
            interval = 1
        else:
            interval = max(1, _CAPTURE_GRABS_PER_PHASE // 6)
        if ticks_in_phase == 1 or ticks_in_phase % interval == 0:
            grab(key)

        if reactor.shot_phase == ShotPhase.QUIESCENT:
            grab("quiescent")
            break

    ordered_c: list[bytes] = []
    ordered_d: list[bytes] = []
    ordered_m: list[FrameMeta] = []
    for phase in expected:
        for canvas, diag, meta in phase_clips.get(phase, []):
            ordered_c.append(canvas)
            ordered_d.append(diag)
            ordered_m.append(meta)

    if not ordered_c:
        raise RuntimeError("compile captured no phase snapshots")
    return ordered_c, ordered_d, ordered_m


def compile_shot_playback(
    reactor: ReactorSimulation,
    *,
    from_quiescent: bool,
    reactor_name: str,
    grab_canvas: GrabCanvas,
    grab_diag: GrabDiagnostics,
    tick_ui: TickUi,
    refresh_canvas: Callable[[], None],
    refresh_diag: Callable[[], None],
    progress: ProgressCallback | None = None,
) -> CompiledPlayback:
    """Build MP4-quality stretched playback from phase snapshots + cached voice."""
    if progress is not None:
        progress(0, 4, "snapshots")

    canvas_raw, diag_raw, meta_raw = _capture_phase_clips(
        reactor,
        from_quiescent=from_quiescent,
        grab_canvas=grab_canvas,
        grab_diag=grab_diag,
        tick_ui=tick_ui,
        refresh_canvas=refresh_canvas,
        refresh_diag=refresh_diag,
    )

    if progress is not None:
        progress(1, 4, "voice timeline")

    timeline = build_playback_timeline(
        canvas_raw,
        diag_raw,
        meta_raw,
        reactor_name=reactor_name,
    )
    if not timeline.diag_frames:
        raise RuntimeError("playback timeline missing diagnostics frames")

    if progress is not None:
        progress(2, 4, "facility audio")

    bed = synthesize_shot_audio(timeline.meta, fps=FPS)
    n = max(bed.size, timeline.narration.size)
    bed = pad_audio(bed, n)

    if progress is not None:
        progress(3, 4, "mixing")

    mixed = mix_tracks(
        bed,
        timeline.narration,
        bed_level=BED_LEVEL,
        duck_with_voice=BED_DUCK_FACTOR,
        voice_mask=timeline.voice_mask,
    )

    arm_start, arm_end = _phase_index_bounds(timeline.meta, "armed")
    fire_phases = reactor.shot_ops().phases_for_fire(from_quiescent)
    fire_phase = fire_phases[0].key if fire_phases else "gas_fill"
    fire_start, _ = _phase_index_bounds(timeline.meta, fire_phase)
    if fire_start == 0 and arm_end > 0:
        fire_start = arm_end

    _, arm_end_s = _segment_bounds(timeline.segments, "armed")
    fire_start_s, _ = _segment_bounds(timeline.segments, fire_phase)
    if fire_start_s == 0.0 and arm_end_s > 0.0:
        fire_start_s = arm_end_s

    if progress is not None:
        progress(4, 4, "done")

    return CompiledPlayback(
        canvas_frames=timeline.frames,
        diag_frames=timeline.diag_frames,
        meta=timeline.meta,
        mixed_audio=mixed,
        duration_s=timeline.duration_s,
        arm_end_ix=arm_end,
        fire_start_ix=fire_start,
        arm_end_s=arm_end_s,
        fire_start_s=fire_start_s,
        from_quiescent=from_quiescent,
        timeline_segments=tuple(timeline.segments),
    )
