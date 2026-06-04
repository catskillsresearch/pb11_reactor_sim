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
pb11_reactor_sim
================

Interactive 2D core-slice simulator and visualizer for three distinct
proton-boron-11 (p-11B) reactor concepts:

* ``TAEReactor``  -- TAE Technologies Field-Reversed Configuration (FRC).
* ``HB11Reactor`` -- HB11 Energy laser-driven block-ignition target chamber.
* ``LPPReactor``  -- LPPFusion Dense Plasma Focus (DPF).

The physics core is a 2D electromagnetic / electrostatic particle-in-cell (PIC)
step: a self-consistent scipy engine (sparse Poisson field solve + Boris/RK4
particle pushers).

A coupled auxiliary process loop (Bremsstrahlung radiation, ion-electron
collisional relaxation, p-11B fusion power, and net gain Q) runs alongside the
PIC step every timestep.

The GUI is built on PySide6 + pyqtgraph.

Entry point::

    ./pb11_reactor_sim/run.sh
    # or, with the environment already configured:
    python -m pb11_reactor_sim
"""
from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
