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
Vectorized macroparticle species container and pushers.

Each :class:`ParticleSpecies` stores positions and velocities for a population
of macroparticles (numpy arrays, fully vectorized). The class provides:

* bilinear charge deposition onto the grid (cloud-in-cell),
* bilinear field gathering from grid nodes to particle positions,
* a relativistic-capable **Boris** pusher (E and B), and
* a plain **RK4** pusher for purely electrostatic / force-field motion.

Coordinates use the convention ``x`` -> grid axis 1 (columns), ``y`` -> grid
axis 0 (rows), consistent with :class:`~pb11_reactor_sim.engine.poisson.PoissonSolver`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

from pb11_reactor_sim.physics.constants import Species

FloatArray = npt.NDArray[np.float64]


@dataclass
class ParticleSpecies:
    """A population of macroparticles of a single physical species.

    Parameters
    ----------
    species:
        Static :class:`Species` data (charge, mass, color, ...).
    macro_weight:
        Number of real particles represented by one macroparticle.
    x, y:
        Position arrays [m] (shape ``(N,)``).
    vx, vy, vz:
        Velocity arrays [m/s] (shape ``(N,)``). ``vz`` tracks out-of-plane
        motion used by the magnetized (Boris) pusher.
    """

    species: Species
    macro_weight: float
    x: FloatArray = field(default_factory=lambda: np.zeros(0))
    y: FloatArray = field(default_factory=lambda: np.zeros(0))
    vx: FloatArray = field(default_factory=lambda: np.zeros(0))
    vy: FloatArray = field(default_factory=lambda: np.zeros(0))
    vz: FloatArray = field(default_factory=lambda: np.zeros(0))

    @property
    def count(self) -> int:
        return int(self.x.size)

    # -- population helpers -------------------------------------------------
    def spawn(
        self,
        x: FloatArray,
        y: FloatArray,
        vx: FloatArray,
        vy: FloatArray,
        vz: FloatArray | None = None,
    ) -> None:
        """Append new macroparticles to the population (vectorized)."""
        vz_arr = np.zeros_like(x) if vz is None else vz
        self.x = np.concatenate([self.x, x])
        self.y = np.concatenate([self.y, y])
        self.vx = np.concatenate([self.vx, vx])
        self.vy = np.concatenate([self.vy, vy])
        self.vz = np.concatenate([self.vz, vz_arr])

    def keep(self, mask: npt.NDArray[np.bool_]) -> None:
        """Retain only macroparticles where ``mask`` is True (in place)."""
        self.x = self.x[mask]
        self.y = self.y[mask]
        self.vx = self.vx[mask]
        self.vy = self.vy[mask]
        self.vz = self.vz[mask]

    # -- grid interpolation -------------------------------------------------
    def _cell_weights(
        self, x0: float, y0: float, dx: float, dy: float, nx: int, ny: int
    ) -> tuple[FloatArray, FloatArray, FloatArray, FloatArray, FloatArray, FloatArray]:
        """Return clamped integer indices and bilinear weights for each particle."""
        fx = (self.x - x0) / dx
        fy = (self.y - y0) / dy
        ix = np.clip(np.floor(fx).astype(np.intp), 0, nx - 2)
        iy = np.clip(np.floor(fy).astype(np.intp), 0, ny - 2)
        wx = np.clip(fx - ix, 0.0, 1.0)
        wy = np.clip(fy - iy, 0.0, 1.0)
        return ix, iy, wx, wy, fx, fy

    def deposit_charge(
        self, rho: FloatArray, x0: float, y0: float, dx: float, dy: float
    ) -> None:
        """Cloud-in-cell deposit of this species' charge density into ``rho``.

        ``rho`` (shape ``(ny, nx)``) is modified in place (added to).
        """
        if self.count == 0:
            return
        ny, nx = rho.shape
        ix, iy, wx, wy, _, _ = self._cell_weights(x0, y0, dx, dy, nx, ny)
        cell_charge = self.species.charge * self.macro_weight / (dx * dy)

        w00 = (1.0 - wx) * (1.0 - wy) * cell_charge
        w10 = wx * (1.0 - wy) * cell_charge
        w01 = (1.0 - wx) * wy * cell_charge
        w11 = wx * wy * cell_charge

        np.add.at(rho, (iy, ix), w00)
        np.add.at(rho, (iy, ix + 1), w10)
        np.add.at(rho, (iy + 1, ix), w01)
        np.add.at(rho, (iy + 1, ix + 1), w11)

    def gather_field(
        self, fx_grid: FloatArray, fy_grid: FloatArray, x0: float, y0: float, dx: float, dy: float
    ) -> tuple[FloatArray, FloatArray]:
        """Bilinear gather of a vector field at particle positions."""
        if self.count == 0:
            return np.zeros(0), np.zeros(0)
        ny, nx = fx_grid.shape
        ix, iy, wx, wy, _, _ = self._cell_weights(x0, y0, dx, dy, nx, ny)

        def _interp(grid: FloatArray) -> FloatArray:
            return (
                grid[iy, ix] * (1.0 - wx) * (1.0 - wy)
                + grid[iy, ix + 1] * wx * (1.0 - wy)
                + grid[iy + 1, ix] * (1.0 - wx) * wy
                + grid[iy + 1, ix + 1] * wx * wy
            )

        return _interp(fx_grid), _interp(fy_grid)

    def gather_scalar(
        self, grid: FloatArray, x0: float, y0: float, dx: float, dy: float
    ) -> FloatArray:
        """Bilinear gather of a scalar field at particle positions."""
        if self.count == 0:
            return np.zeros(0)
        ny, nx = grid.shape
        ix, iy, wx, wy, _, _ = self._cell_weights(x0, y0, dx, dy, nx, ny)
        return (
            grid[iy, ix] * (1.0 - wx) * (1.0 - wy)
            + grid[iy, ix + 1] * wx * (1.0 - wy)
            + grid[iy + 1, ix] * (1.0 - wx) * wy
            + grid[iy + 1, ix + 1] * wx * wy
        )

    # -- pushers ------------------------------------------------------------
    def push_boris(
        self,
        ex: FloatArray,
        ey: FloatArray,
        bz: FloatArray,
        dt: float,
    ) -> None:
        """Boris push with in-plane E (``ex``, ``ey``) and out-of-plane B (``bz``).

        Field arrays are evaluated *at particle positions* (already gathered).
        Updates ``vx, vy, vz`` then advances positions by ``dt``.
        """
        if self.count == 0:
            return
        q = self.species.charge
        m = self.species.mass
        qmdt2 = (q / m) * 0.5 * dt

        # Half acceleration from E.
        vmx = self.vx + qmdt2 * ex
        vmy = self.vy + qmdt2 * ey
        vmz = self.vz

        # Rotation from B (only Bz -> rotation in x-y plane).
        tz = qmdt2 * bz
        t2 = tz * tz
        sz = 2.0 * tz / (1.0 + t2)

        vprimex = vmx + vmy * tz
        vprimey = vmy - vmx * tz

        vpx = vmx + vprimey * sz
        vpy = vmy - vprimex * sz
        vpz = vmz

        # Second half acceleration from E.
        self.vx = vpx + qmdt2 * ex
        self.vy = vpy + qmdt2 * ey
        self.vz = vpz

        self.x = self.x + self.vx * dt
        self.y = self.y + self.vy * dt

    def push_rk4(
        self,
        accel_fn,
        dt: float,
    ) -> None:
        """RK4 push under a position-dependent acceleration field.

        Parameters
        ----------
        accel_fn:
            Callable ``(x, y) -> (ax, ay)`` returning acceleration arrays [m/s^2].
        dt:
            Timestep [s].
        """
        if self.count == 0:
            return

        def deriv(x: FloatArray, y: FloatArray, vx: FloatArray, vy: FloatArray):
            ax, ay = accel_fn(x, y)
            return vx, vy, ax, ay

        x0, y0, vx0, vy0 = self.x, self.y, self.vx, self.vy

        k1x, k1y, k1vx, k1vy = deriv(x0, y0, vx0, vy0)
        k2x, k2y, k2vx, k2vy = deriv(
            x0 + 0.5 * dt * k1x, y0 + 0.5 * dt * k1y, vx0 + 0.5 * dt * k1vx, vy0 + 0.5 * dt * k1vy
        )
        k3x, k3y, k3vx, k3vy = deriv(
            x0 + 0.5 * dt * k2x, y0 + 0.5 * dt * k2y, vx0 + 0.5 * dt * k2vx, vy0 + 0.5 * dt * k2vy
        )
        k4x, k4y, k4vx, k4vy = deriv(
            x0 + dt * k3x, y0 + dt * k3y, vx0 + dt * k3vx, vy0 + dt * k3vy
        )

        self.x = x0 + (dt / 6.0) * (k1x + 2 * k2x + 2 * k3x + k4x)
        self.y = y0 + (dt / 6.0) * (k1y + 2 * k2y + 2 * k3y + k4y)
        self.vx = vx0 + (dt / 6.0) * (k1vx + 2 * k2vx + 2 * k3vx + k4vx)
        self.vy = vy0 + (dt / 6.0) * (k1vy + 2 * k2vy + 2 * k3vy + k4vy)
