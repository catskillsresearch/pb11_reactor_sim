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
Sparse 2D Poisson solver with Dirichlet conductor masks.

Solves :math:`\\nabla^2 \\Phi = -\\rho / \\epsilon_0` on a uniform rectangular
grid using a 5-point finite-difference Laplacian assembled once as a CSR matrix
(``scipy.sparse``). Cells flagged as conductors are pinned to prescribed
potentials (Dirichlet boundary conditions), which is how electrodes, grids and
charged walls are represented for every reactor model.

The factorization (``scipy.sparse.linalg.splu``) is cached and only rebuilt when
the conductor mask or its prescribed potentials change, so per-step solves are a
cheap back-substitution -- fast enough for the real-time GUI loop.
"""
from __future__ import annotations

import numpy as np
import numpy.typing as npt
import scipy.sparse as sp
import scipy.sparse.linalg as spla

from pb11_reactor_sim.physics import constants as C

FloatArray = npt.NDArray[np.float64]
BoolArray = npt.NDArray[np.bool_]


class PoissonSolver:
    """Cached LU-factored 2D Poisson solver on a uniform grid.

    Parameters
    ----------
    nx, ny:
        Number of grid nodes in x and y.
    dx, dy:
        Cell spacing in metres.
    """

    def __init__(self, nx: int, ny: int, dx: float, dy: float) -> None:
        self.nx = int(nx)
        self.ny = int(ny)
        self.dx = float(dx)
        self.dy = float(dy)
        self._n = self.nx * self.ny

        # Base Laplacian (interior 5-point stencil, Dirichlet=0 domain border).
        self._laplacian = self._build_laplacian()
        self._mask_signature: bytes | None = None
        self._dirichlet_potentials: FloatArray | None = None
        self._lu: spla.SuperLU | None = None

    # -- matrix assembly ----------------------------------------------------
    def _build_laplacian(self) -> sp.csr_matrix:
        """Assemble the negative Laplacian operator ``A`` for ``A Phi = b``.

        We solve ``-nabla^2 Phi = rho / eps0`` so ``A`` is the positive-definite
        negative Laplacian, giving a symmetric system that factorizes cleanly.
        """
        nx, ny = self.nx, self.ny
        inv_dx2 = 1.0 / (self.dx * self.dx)
        inv_dy2 = 1.0 / (self.dy * self.dy)

        main = np.full(self._n, 2.0 * (inv_dx2 + inv_dy2))

        # x-neighbours (i +/- 1): offsets of +/-1, but must not wrap across rows.
        offdiag_x = np.full(self._n, -inv_dx2)
        # zero the coupling between last column of a row and first of next row
        offdiag_x_upper = offdiag_x.copy()
        offdiag_x_lower = offdiag_x.copy()
        for j in range(ny):
            # node index = j * nx + i
            row_start = j * nx
            offdiag_x_upper[row_start + nx - 1] = 0.0  # no i+1 at right edge
            offdiag_x_lower[row_start] = 0.0           # no i-1 at left edge

        # y-neighbours (j +/- 1): offsets of +/- nx.
        offdiag_y = np.full(self._n, -inv_dy2)

        diagonals = [
            main,
            offdiag_x_upper[:-1],
            offdiag_x_lower[1:],
            offdiag_y[:-nx],
            offdiag_y[nx:],
        ]
        offsets = [0, 1, -1, nx, -nx]
        return sp.diags(diagonals, offsets, format="csr")

    # -- conductor masks ----------------------------------------------------
    def set_conductors(self, conductor_mask: BoolArray, potentials: FloatArray) -> None:
        """Pin conductor cells to fixed potentials (Dirichlet rows).

        Parameters
        ----------
        conductor_mask:
            ``(ny, nx)`` boolean array; ``True`` cells are conductors.
        potentials:
            ``(ny, nx)`` float array of prescribed potentials [V] (only the
            conductor cells are used).
        """
        flat_mask = np.ascontiguousarray(conductor_mask.ravel())
        flat_pot = np.ascontiguousarray(potentials.ravel().astype(np.float64))

        signature = flat_mask.tobytes() + flat_pot.tobytes()
        if signature == self._mask_signature and self._lu is not None:
            self._dirichlet_potentials = flat_pot
            return

        a = self._laplacian.tolil()
        idx = np.flatnonzero(flat_mask)
        for k in idx:
            a.rows[k] = [k]
            a.data[k] = [1.0]
        a_csr = a.tocsr()
        self._lu = spla.splu(a_csr.tocsc())
        self._mask_signature = signature
        self._dirichlet_potentials = flat_pot
        self._conductor_idx = idx

    # -- solve --------------------------------------------------------------
    def solve(self, rho: FloatArray) -> FloatArray:
        """Solve for the electrostatic potential ``Phi`` [V].

        Parameters
        ----------
        rho:
            ``(ny, nx)`` charge density [C/m^3].

        Returns
        -------
        Phi : ``(ny, nx)`` float array of potential [V].
        """
        if self._lu is None or self._dirichlet_potentials is None:
            # No conductors set: ground the domain border implicitly (A is the
            # plain Laplacian with Dirichlet-0 borders baked into the stencil).
            self.set_conductors(
                np.zeros((self.ny, self.nx), dtype=bool),
                np.zeros((self.ny, self.nx), dtype=np.float64),
            )
        assert self._lu is not None and self._dirichlet_potentials is not None

        b = rho.ravel().astype(np.float64) / C.VACUUM_PERMITTIVITY
        # Overwrite conductor rows with their prescribed potential.
        b[self._conductor_idx] = self._dirichlet_potentials[self._conductor_idx]
        phi = self._lu.solve(b)
        return phi.reshape(self.ny, self.nx)

    # -- field gradient -----------------------------------------------------
    def electric_field(self, phi: FloatArray) -> tuple[FloatArray, FloatArray]:
        """Return ``(Ex, Ey)`` from ``E = -grad Phi`` via central differences."""
        ey, ex = np.gradient(-phi, self.dy, self.dx)
        return ex, ey
