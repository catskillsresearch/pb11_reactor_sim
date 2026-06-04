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

"""Concrete reactor models (TAE FRC, HB11 laser, LPPFusion DPF)."""
from __future__ import annotations

from pb11_reactor_sim.engine.base import ReactorSimulation
from pb11_reactor_sim.reactors.hb11 import HB11Reactor
from pb11_reactor_sim.reactors.lpp import LPPReactor
from pb11_reactor_sim.reactors.tae import TAEReactor

#: Registry consumed by the GUI dropdown: display name -> class.
REACTOR_REGISTRY: dict[str, type[ReactorSimulation]] = {
    TAEReactor.display_name: TAEReactor,
    HB11Reactor.display_name: HB11Reactor,
    LPPReactor.display_name: LPPReactor,
}

__all__ = ["TAEReactor", "HB11Reactor", "LPPReactor", "REACTOR_REGISTRY"]
