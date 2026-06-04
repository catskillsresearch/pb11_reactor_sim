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
Control-space optimizer for net gain ``Q_net``.

Each reactor declares its own control inputs (``ControlSpec`` list), so the
optimizer is fully generic: it builds a grid over whatever sliders the chosen
reactor exposes and evaluates the (windowed-mean) steady-state ``Q_net`` for
each combination via :meth:`ReactorSimulation.evaluate_qnet`.

Because ``evaluate_qnet`` advances only the lightweight 0D plasma-state model
(no PIC particles, fields, or backend), a full grid search over the 2D control
space is fast and thread-safe -- it can run in a worker thread without touching
Qt or the live simulation.

A coarse grid is followed by a local refinement pass around the best grid point,
giving a good optimum without an expensive dense sweep.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass

import numpy as np

from pb11_reactor_sim.engine.base import ReactorSimulation
from pb11_reactor_sim.engine.pic_backend import FieldSolveBackend


@dataclass
class OptimizeResult:
    """Outcome of a ``Q_net`` optimization run."""

    controls: dict[str, float]
    q_net: float
    n_evaluations: int


def _grid_search(
    reactor: ReactorSimulation,
    specs: list,
    axes: list[np.ndarray],
) -> tuple[dict[str, float], float, int]:
    """Exhaustively evaluate the cartesian product of ``axes``; return best."""
    best_controls: dict[str, float] = {s.key: s.default for s in specs}
    best_q = -np.inf
    count = 0
    for combo in itertools.product(*axes):
        controls = {spec.key: float(v) for spec, v in zip(specs, combo)}
        q = reactor.evaluate_qnet(controls)
        count += 1
        if q > best_q:
            best_q = q
            best_controls = controls
    return best_controls, float(best_q), count


def optimize_qnet(
    reactor_cls: type[ReactorSimulation],
    backend: FieldSolveBackend,
    coarse_points: int = 9,
    refine_points: int = 7,
) -> OptimizeResult:
    """Search a reactor's control space for the controls maximizing ``Q_net``.

    Parameters
    ----------
    reactor_cls:
        The reactor class to optimize (e.g. ``TAEReactor``).
    backend:
        Shared field-solve backend (unused by the 0D evaluation, but kept so the
        evaluation reactor is built consistently with the live one).
    coarse_points:
        Samples per control axis in the initial coarse grid.
    refine_points:
        Samples per axis in the local refinement grid around the coarse optimum.
    """
    reactor = reactor_cls(field_solver=backend)
    specs = list(reactor_cls.control_specs())

    coarse_axes = [np.linspace(s.minimum, s.maximum, coarse_points) for s in specs]
    best_controls, best_q, n1 = _grid_search(reactor, specs, coarse_axes)

    # Local refinement: zoom into +/- one coarse cell around each best value.
    refine_axes: list[np.ndarray] = []
    for spec in specs:
        span = (spec.maximum - spec.minimum) / max(coarse_points - 1, 1)
        center = best_controls[spec.key]
        lo = max(spec.minimum, center - span)
        hi = min(spec.maximum, center + span)
        refine_axes.append(np.linspace(lo, hi, refine_points))
    refined_controls, refined_q, n2 = _grid_search(reactor, specs, refine_axes)

    if refined_q >= best_q:
        best_controls, best_q = refined_controls, refined_q

    return OptimizeResult(controls=best_controls, q_net=best_q, n_evaluations=n1 + n2)
