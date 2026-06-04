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
PIC field-solve backend (scipy sparse Poisson).

The :class:`FieldSolveBackend` interface isolates the field solve from the rest
of the simulation. The live engine is a vectorized 2D PIC core: cloud-in-cell
charge deposition, 5-point Poisson with Dirichlet conductor masks, and
Boris/RK4 particle pushers (:mod:`poisson`, :mod:`particles`, :mod:`base`).
"""
from __future__ import annotations

import abc

import numpy as np
import numpy.typing as npt

from pb11_reactor_sim.engine.poisson import PoissonSolver

FloatArray = npt.NDArray[np.float64]
BoolArray = npt.NDArray[np.bool_]


class FieldSolveBackend(abc.ABC):
    """Abstract field-solve strategy for the PIC core."""

    label: str = "backend"

    @abc.abstractmethod
    def solve_potential(
        self,
        rho: FloatArray,
        poisson: PoissonSolver,
        conductor_mask: BoolArray,
        conductor_potential: FloatArray,
    ) -> FloatArray:
        """Solve ``-nabla^2 Phi = rho/eps0`` with Dirichlet conductor cells."""


class ScipyPoissonBackend(FieldSolveBackend):
    """Self-consistent scipy sparse Poisson solver."""

    label = "scipy FD PIC"

    def solve_potential(
        self,
        rho: FloatArray,
        poisson: PoissonSolver,
        conductor_mask: BoolArray,
        conductor_potential: FloatArray,
    ) -> FloatArray:
        poisson.set_conductors(conductor_mask, conductor_potential)
        return poisson.solve(rho)


def make_backend() -> FieldSolveBackend:
    """Construct the scipy field-solve backend used by the GUI."""
    return ScipyPoissonBackend()
