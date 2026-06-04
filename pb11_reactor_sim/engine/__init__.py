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

"""Numerical engine: grid, Poisson solver, particle pushers, PIC backends."""
from __future__ import annotations

__all__ = [
    "Grid",
    "PoissonSolver",
    "ParticleSpecies",
    "ReactorSimulation",
    "make_backend",
]

from pb11_reactor_sim.engine.base import Grid, ReactorSimulation
from pb11_reactor_sim.engine.particles import ParticleSpecies
from pb11_reactor_sim.engine.pic_backend import make_backend
from pb11_reactor_sim.engine.poisson import PoissonSolver
