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

"""GUI physics stepping rates (shared by live sim, audio compile, and shot capture)."""
from __future__ import annotations

from pb11_reactor_sim.engine.base import ReactorSimulation

#: Physics substeps advanced per GUI frame (keep in sync with ``app.py``).
SUBSTEPS_PER_FRAME = 4
STARTUP_SUBSTEP_MULT = 35
PLATEAU_SUBSTEP_MULT = 10
TAIL_SUBSTEP_MULT = 4


def substeps_per_frame(reactor: ReactorSimulation) -> int:
    """Match :meth:`~pb11_reactor_sim.app.PlasmaSimApp._substeps_per_frame`."""
    if reactor.is_startup_countdown():
        return SUBSTEPS_PER_FRAME * STARTUP_SUBSTEP_MULT
    if reactor.is_plateau_fast_forward():
        return SUBSTEPS_PER_FRAME * PLATEAU_SUBSTEP_MULT
    if reactor.is_tail_fast_forward():
        return SUBSTEPS_PER_FRAME * TAIL_SUBSTEP_MULT
    return SUBSTEPS_PER_FRAME


def hud_speed_mode(reactor: ReactorSimulation) -> str:
    if reactor.is_startup_countdown():
        return f"FF×{STARTUP_SUBSTEP_MULT}"
    if reactor.is_plateau_fast_forward():
        return f"FF×{PLATEAU_SUBSTEP_MULT}"
    if reactor.is_tail_fast_forward():
        return f"FF×{TAIL_SUBSTEP_MULT}"
    return "1×"


def is_fast_gui_frame(reactor: ReactorSimulation) -> bool:
    return (
        reactor.is_startup_countdown()
        or reactor.is_plateau_fast_forward()
        or reactor.is_tail_fast_forward()
    )
