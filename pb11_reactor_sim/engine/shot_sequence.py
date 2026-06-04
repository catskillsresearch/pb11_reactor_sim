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
Operational shot sequencing: Arm / Fire / quiesce / optional repeat Fire.

Each reactor declares a :class:`ShotOps` profile (countdown phases, whether a
new **Arm** is required before the next **Fire**). The simulator starts
**unarmed** with an empty chamber; **Arm** prepares a shot, **Fire** runs the
countdown through flat-top / pinch / pulse and into **quiescent** cooldown.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ShotPhase(str, Enum):
    """High-level operational state visible in the GUI."""

    UNARMED = "unarmed"
    ARMED = "armed"
    FIRING = "firing"
    QUIESCENT = "quiescent"


@dataclass(frozen=True)
class FirePhase:
    """One step in the Fire countdown (duration in seconds of sim time)."""

    key: str
    duration_s: float
    callout: str


@dataclass(frozen=True)
class ShotOps:
    """Reactor-specific shot procedure metadata (for logic + tutorial text)."""

    #: If True, another **Fire** after quiescence requires **Arm** first.
    requires_rearm_between_shots: bool
    #: Status line right after **Arm**.
    arm_callout: str
    #: Phases executed on **Fire** from **ARMED** (full shot).
    fire_phases: tuple[FirePhase, ...]
    #: Shorter sequence when **Fire** is pressed from **QUIESCENT** (repeat shot).
    refire_phases: tuple[FirePhase, ...] | None = None
    #: Message shown in quiescent state.
    quiescent_callout: str = "Shot complete — plasma quiescing."

    def phases_for_fire(self, from_quiescent: bool) -> tuple[FirePhase, ...]:
        if from_quiescent and self.refire_phases is not None:
            return self.refire_phases
        return self.fire_phases
