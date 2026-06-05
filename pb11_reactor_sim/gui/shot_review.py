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

"""Compiled-shot review segments (numbered callouts, frame/audio bounds)."""
from __future__ import annotations

from dataclasses import dataclass

from pb11_reactor_sim.gui.audio_synth import FPS
from pb11_reactor_sim.gui.chattts_narration import (
    _normalize_narration_text,
    format_numbered_subtitle,
    phase_segments,
)
from pb11_reactor_sim.gui.narration_scripts import PHASE_NARRATION
from pb11_reactor_sim.gui.export_timeline import TimelineSegment
from pb11_reactor_sim.gui.shot_compiler import CompiledPlayback
from pb11_reactor_sim.gui.shot_script import scripted_phase_keys


@dataclass(frozen=True)
class ReviewSegment:
    seq: int
    total: int
    phase: str
    callout: str
    subtitle: str
    start_ix: int
    end_ix: int  # exclusive
    start_ms: int
    speech_end_ms: int  # exclusive — audible voice incl. trailer padding
    end_ms: int  # exclusive, matches stretched frame span


def build_review_segments(
    playback: CompiledPlayback,
    reactor_name: str,
    *,
    reactor_cls: type | None = None,
) -> list[ReviewSegment]:
    """One segment per stretched phase run; sequence matches scripted shot order."""
    scripts = PHASE_NARRATION.get(reactor_name, PHASE_NARRATION["TAE FRC"])
    audio_by_phase: dict[str, TimelineSegment] = {
        s.phase: s for s in playback.timeline_segments
    }
    runs = phase_segments(playback.meta)
    total = len(runs)
    out: list[ReviewSegment] = []

    if reactor_cls is not None:
        expected = scripted_phase_keys(reactor_cls, from_quiescent=playback.from_quiescent)
        present = [phase for phase, _, _ in runs]
        if present != expected:
            missing = [p for p in expected if p not in present]
            extra = [p for p in present if p not in expected]
            if missing or extra:
                import logging

                logging.getLogger(__name__).warning(
                    "Review sequence mismatch (expected %s, got %s); missing=%s extra=%s",
                    expected,
                    present,
                    missing,
                    extra,
                )

    for seq, (phase, start_ix, end_ix) in enumerate(runs, start=1):
        raw = scripts.get(phase, "")
        if raw:
            text = _normalize_narration_text(raw)
        else:
            text = phase.replace("_", " ")
        subtitle = format_numbered_subtitle(seq, total, text)
        callout = text.split(".")[0] if text else phase
        start_ms = int(round(start_ix / FPS * 1000.0))
        end_ix_ex = max(start_ix + 1, end_ix)
        end_ms = int(round(end_ix_ex / FPS * 1000.0))
        audio = audio_by_phase.get(phase)
        if audio is not None:
            start_ms = int(round(audio.start_s * 1000.0))
            speech_end_ms = int(round((audio.start_s + audio.speech_dur_s) * 1000.0))
            end_ms = int(round(audio.end_s * 1000.0))
            min_speech_ms = max(250, int(round(1000.0 / FPS)))
            speech_end_ms = max(start_ms + min_speech_ms, speech_end_ms)
        else:
            speech_end_ms = end_ms
        out.append(
            ReviewSegment(
                seq=seq,
                total=total,
                phase=phase,
                callout=callout,
                subtitle=subtitle,
                start_ix=start_ix,
                end_ix=end_ix_ex,
                start_ms=start_ms,
                speech_end_ms=speech_end_ms,
                end_ms=end_ms,
            )
        )
    return out
