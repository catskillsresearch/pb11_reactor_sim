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

"""Shared shot phase ordering for audio compile and playback."""
from __future__ import annotations

from pb11_reactor_sim.engine.base import ReactorSimulation
from pb11_reactor_sim.gui.audio_synth import FrameMeta

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


def scripted_phase_keys(
    reactor_cls: type[ReactorSimulation],
    *,
    from_quiescent: bool,
) -> list[str]:
    ops = reactor_cls.shot_ops()
    keys: list[str] = []
    if not from_quiescent:
        keys.append("armed")
    keys.extend(p.key for p in ops.phases_for_fire(from_quiescent))
    keys.append("quiescent")
    return keys


def frame_meta_for_phase(phase_key: str) -> FrameMeta:
    ff = phase_key in _PRE_DISCHARGE
    intensity = 0.35 if phase_key in ("flat_top", "main_pulse", "pinch") else 0.2
    return FrameMeta(phase=phase_key, fast_forward=ff, intensity=intensity)
