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
HB11 Energy laser-driven block-ignition core model.

2D XY slice through a spherical reaction chamber (grounded wall). A small solid
fuel target sits at the centre on a positioner; a high-voltage spherical
collector grid surrounds it for direct electrostatic energy conversion.

Design physics
--------------
* Ponderomotive acceleration from a localized 2D Gaussian laser pulse:

  .. math:: \\vec{F}_p = -\\frac{e^2}{4 m_e \\omega^2} \\nabla \\langle E^2 \\rangle

* Electrostatic deceleration / direct collection: Poisson's equation
  ``nabla^2 Phi = -rho/eps0`` is solved with the grid pinned to ``V_grid`` so
  outward-streaming protons are decelerated and their charge is collected on the
  grid (DC current).

Controls: laser intensity ``I_laser`` and collector grid voltage ``V_grid``
(up to 3 MV).
"""
from __future__ import annotations

import numpy as np

from pb11_reactor_sim.engine.base import (
    BoundaryShape,
    ControlSpec,
    Grid,
    ReactorSimulation,
    StructureLabel,
)
from pb11_reactor_sim.engine.shot_sequence import FirePhase, ShotOps
from pb11_reactor_sim.engine.particles import ParticleSpecies
from pb11_reactor_sim.physics import constants as C
from pb11_reactor_sim.physics import processes as P

_RNG = np.random.default_rng(11)

#: Laser angular frequency for a ~1 micron drive laser [rad/s].
_LASER_OMEGA = 2.0 * np.pi * C.SPEED_OF_LIGHT / 1.06e-6


class HB11Reactor(ReactorSimulation):
    """Laser block-ignition target chamber with a direct-conversion grid."""

    display_name = "HB11 Laser"
    display_field_kind = "phi"

    CHAMBER_RADIUS = 0.45
    GRID_RADIUS = 0.32
    TARGET_RADIUS = 0.03
    LASER_WAIST = 0.05

    def __init__(self, grid: Grid | None = None, field_solver=None) -> None:
        if grid is None:
            grid = Grid(nx=161, ny=161, x0=-0.5, y0=-0.5, Lx=1.0, Ly=1.0)
        if field_solver is None:
            from pb11_reactor_sim.engine.pic_backend import make_backend

            field_solver = make_backend()
        self.collected_charge: float = 0.0
        self.collector_current: float = 0.0
        self._e2_field = None  # cached laser <E^2> envelope
        super().__init__(grid, field_solver)

    # -- declarations -------------------------------------------------------
    @classmethod
    def control_specs(cls) -> list[ControlSpec]:
        return [
            ControlSpec("i_laser", "Laser Intensity", 1.0, 100.0, 30.0, units="x1e20 W/cm^2"),
            ControlSpec("v_grid", "Grid Voltage", 0.0, 3.0, 1.5, units="MV"),
        ]

    def default_dt(self) -> float:
        return 5.0e-12  # 5 ps

    @classmethod
    def shot_ops(cls) -> ShotOps:
        return ShotOps(
            requires_rearm_between_shots=True,
            arm_callout="ARMED — chamber pumped, grid at V, fresh target loaded.",
            fire_phases=(
                FirePhase("grid_charge", 0.002e-6, "Grid at voltage — stand by"),
                FirePhase("laser_countdown", 0.004e-6, "T−3…2…1 — laser chain armed"),
                FirePhase("main_pulse", 0.04e-6, "FIRE — main laser pulse"),
                FirePhase("afterglow", 0.015e-6, "Afterglow — plasma cooling"),
            ),
            quiescent_callout="Quiescent — target spent (Arm before next Fire).",
        )

    def enter_unarmed(self) -> None:
        self.species.clear()
        self.collected_charge = 0.0
        self.collector_current = 0.0
        self.T_i_keV = 0.05
        self.T_e_keV = 0.05
        self.n_e = 1.0e14
        self.n_p = 5.0e13
        self.n_B = 5.0e12
        self.phi = self.grid.zeros()

    def enter_armed(self) -> None:
        self.collected_charge = 0.0
        self.collector_current = 0.0
        self.T_i_keV = 0.1
        self.T_e_keV = 0.08
        self.n_e = 1.0e22
        self.n_p = 5.0e21
        self.n_B = 5.0e20
        self.species.clear()
        protons = ParticleSpecies(C.PROTON, macro_weight=1.0e10)
        boron = ParticleSpecies(C.BORON11, macro_weight=1.0e9)
        electrons = ParticleSpecies(C.ELECTRON, macro_weight=1.0e10)
        alphas = ParticleSpecies(C.ALPHA, macro_weight=1.0e8)
        self.species = {"p": protons, "B": boron, "e": electrons, "alpha": alphas}
        self._spawn_block(400)

    def enter_quiescent(self) -> None:
        self.T_i_keV = max(self.T_i_keV * 0.4, 0.2)
        self.T_e_keV = max(self.T_e_keV * 0.5, 0.1)
        self.n_e = max(self.n_e * 0.2, 1.0e18)

    def discharge_phase_key(self) -> str:
        return "main_pulse"

    def skip_to_discharge_label(self) -> str:
        return "Skip to laser pulse"

    def prepare_skipped_to_discharge(self) -> None:
        if "p" not in self.species or self.species["p"].count < 100:
            if not self.species:
                self.seed_particles()
            else:
                self._spawn_block(600)
        i_laser = self.controls.get("i_laser", 30.0)
        self.T_i_keV = 120.0 + 6.0 * i_laser
        self.T_e_keV = 0.18 * self.T_i_keV
        self.n_e = 1.0e22
        self.n_p = 5.0e21
        self.n_B = 5.0e20
        self._snap_particles_to_temperature()

    def on_fire_phase_begin(self, phase_key: str) -> None:
        if phase_key == "main_pulse" and self.species["p"].count < 100:
            self._spawn_block(600)

    def _hot_phase_keys(self) -> frozenset[str]:
        return frozenset({"main_pulse", "afterglow"})

    def on_fire_phase_tick(self, phase_key: str, dt: float) -> None:
        if phase_key == "quiescent":
            self.T_i_keV = max(self.T_i_keV - dt / 1.5e-6, 0.15)
            self.T_e_keV = max(self.T_e_keV - dt / 2.0e-6, 0.08)
            for sym, sp in self.species.items():
                if sp.count > 80 and _RNG.random() < dt / 3.0e-6:
                    keep = _RNG.choice(sp.count, max(sp.count // 2, 1), replace=False)
                    m = np.zeros(sp.count, dtype=bool)
                    m[keep] = True
                    sp.keep(m)

    # -- geometry -----------------------------------------------------------
    def build_geometry(self) -> None:
        g = self.grid
        X, Y = g.meshgrid()
        R = np.hypot(X, Y)

        self.conductor_mask = np.zeros((g.ny, g.nx), dtype=bool)
        self.conductor_potential = g.zeros()

        # Grounded spherical chamber wall (thin shell at chamber radius).
        wall = (R >= self.CHAMBER_RADIUS) & (R <= self.CHAMBER_RADIUS + 0.04)
        self.conductor_mask |= wall
        self.conductor_potential[wall] = 0.0

        # HV spherical collector grid (thin shell), biased to +V_grid.
        v_grid = self.controls.get("v_grid", 1.5) * 1.0e6
        grid_shell = (R >= self.GRID_RADIUS) & (R <= self.GRID_RADIUS + 0.02)
        # Make it a *grid* (gaps) so it is permeable to particles but biased.
        theta = np.arctan2(Y, X)
        gaps = (np.floor(theta / (np.pi / 12.0)).astype(int) % 2) == 0
        grid_cells = grid_shell & gaps
        self.conductor_mask |= grid_cells
        self.conductor_potential[grid_cells] = v_grid
        self._grid_cells = grid_cells

        # Central solid fuel target on its positioner.
        target = R <= self.TARGET_RADIUS
        self.conductor_mask |= target
        self.conductor_potential[target] = 0.0
        # Positioner stalk (thin vertical solid from bottom wall to target).
        stalk = (np.abs(X) <= 0.01) & (Y <= -self.TARGET_RADIUS) & (Y >= -self.CHAMBER_RADIUS)
        self.conductor_mask |= stalk

        self.plasma_mask = R < self.GRID_RADIUS

        wall_c = (150, 210, 255)
        grid_c = (255, 220, 150)
        target_c = (255, 160, 160)
        self.boundaries = [
            BoundaryShape("circle", (0.0, 0.0, self.CHAMBER_RADIUS), wall_c),
            BoundaryShape("circle", (0.0, 0.0, self.GRID_RADIUS), grid_c),
            BoundaryShape("circle", (0.0, 0.0, self.TARGET_RADIUS), target_c),
            BoundaryShape("line", (0.0, -self.TARGET_RADIUS, 0.0, -self.CHAMBER_RADIUS), (200, 200, 200)),
        ]
        self.labels = [
            # Placed at the top of each ring so a horizontal label lies tangent.
            StructureLabel("Grounded Spherical Chamber Wall", 0.0, self.CHAMBER_RADIUS + 0.03,
                           wall_c, angle=0.0, anchor=(0.5, 0.5)),
            StructureLabel("HV Collector Grid", 0.0, self.GRID_RADIUS + 0.025,
                           grid_c, angle=0.0, anchor=(0.5, 0.5)),
            StructureLabel("Fuel Target", self.TARGET_RADIUS + 0.015, 0.055,
                           target_c, angle=0.0, anchor=(0.0, 0.5)),
            StructureLabel("Target Positioner", 0.012, -self.CHAMBER_RADIUS + 0.10,
                           (210, 210, 210), angle=90.0, anchor=(0.5, 0.5)),
        ]
        self._build_laser_envelope()

    def _build_laser_envelope(self) -> None:
        """Cache the laser ``<E^2>`` Gaussian envelope and its gradient."""
        g = self.grid
        X, Y = g.meshgrid()
        # Laser enters from -x, focused on the target; localized 2D Gaussian.
        r2 = X * X + Y * Y
        self._e2_shape = np.exp(-r2 / (self.LASER_WAIST**2))

    # -- particles ----------------------------------------------------------
    def seed_particles(self) -> None:
        # Start with a dense, cold block of fuel at the target surface.
        protons = ParticleSpecies(C.PROTON, macro_weight=1.0e10)
        boron = ParticleSpecies(C.BORON11, macro_weight=1.0e9)
        electrons = ParticleSpecies(C.ELECTRON, macro_weight=1.0e10)
        alphas = ParticleSpecies(C.ALPHA, macro_weight=1.0e8)
        self.species = {"p": protons, "B": boron, "e": electrons, "alpha": alphas}
        self._spawn_block(800)

    def _spawn_block(self, n: int) -> None:
        """Spawn a cold fuel block as a thin shell on the target surface."""
        ang = _RNG.uniform(0, 2 * np.pi, n)
        r = self.TARGET_RADIUS + _RNG.uniform(0.0, 0.01, n)
        x = r * np.cos(ang)
        y = r * np.sin(ang)
        cold = 1.0e4
        self.species["p"].spawn(x, y, _RNG.normal(0, cold, n), _RNG.normal(0, cold, n), np.zeros(n))
        nb = n // 4
        ang_b = _RNG.uniform(0, 2 * np.pi, nb)
        rb = self.TARGET_RADIUS + _RNG.uniform(0.0, 0.01, nb)
        self.species["B"].spawn(
            rb * np.cos(ang_b), rb * np.sin(ang_b),
            _RNG.normal(0, cold, nb), _RNG.normal(0, cold, nb), np.zeros(nb),
        )
        self.species["e"].spawn(x, y, _RNG.normal(0, cold * 40, n), _RNG.normal(0, cold * 40, n), np.zeros(n))

    # -- dynamics -----------------------------------------------------------
    def on_controls(self) -> None:
        self.build_geometry()

    def _ponderomotive_accel(self, sym: str):
        """Return an ``accel_fn(x, y)`` for the ponderomotive force on a species."""
        sp = self.species[sym]
        q = abs(sp.species.charge)
        m = sp.species.mass
        i_laser = self.controls.get("i_laser", 30.0)
        # Peak <E^2> scales with intensity; F_p = -(e^2/4 m_e w^2) grad<E^2>.
        # Ions feel the force through the charge-separation field; we apply an
        # effective ponderomotive push scaled to the species charge/mass.
        e2_peak = 2.0e21 * i_laser  # [V^2/m^2], order-of-magnitude for block ignition
        waist2 = self.LASER_WAIST**2

        def accel_fn(x: np.ndarray, y: np.ndarray):
            r2 = x * x + y * y
            env = np.exp(-r2 / waist2)
            # grad<E^2> = <E^2>_peak * env * (-2/waist^2) * (x, y)
            grad_x = e2_peak * env * (-2.0 / waist2) * x
            grad_y = e2_peak * env * (-2.0 / waist2) * y
            # F_p = -coeff_norm * grad ; outward push from focus.
            scale = (q * q) / (4.0 * m * _LASER_OMEGA**2) / e2_peak
            return -scale * grad_x, -scale * grad_y

        return accel_fn

    def advance_particles(self, dt: float) -> None:
        g = self.grid
        # Electrostatic field from grid voltage + space charge.
        self.solve_fields()

        for sym, sp in self.species.items():
            if sp.count == 0:
                continue
            # Ponderomotive block acceleration near the focus.
            accel = self._ponderomotive_accel(sym)
            sp.push_rk4(accel, dt)
            # Electrostatic deceleration via gathered E-field.
            ex_p, ey_p = sp.gather_field(self.ex, self.ey, g.x0, g.y0, g.dx, g.dy)
            qm = sp.species.charge / sp.species.mass
            sp.vx += qm * ex_p * dt
            sp.vy += qm * ey_p * dt
            sp.x += sp.vx * dt
            sp.y += sp.vy * dt

        self._collect_on_grid(dt)
        self._fusion_alpha_production(dt)

    def _collect_on_grid(self, dt: float) -> None:
        """Absorb protons/alphas reaching the collector grid; tally DC charge."""
        collected_q = 0.0
        for sym in ("p", "alpha"):
            sp = self.species[sym]
            if sp.count == 0:
                continue
            r = np.hypot(sp.x, sp.y)
            hit = r >= self.GRID_RADIUS
            if np.any(hit):
                collected_q += float(np.sum(hit)) * abs(sp.species.charge) * sp.macro_weight
                sp.keep(~hit)
        # Remove anything past the chamber wall.
        for sym, sp in self.species.items():
            if sp.count == 0:
                continue
            r = np.hypot(sp.x, sp.y)
            sp.keep(r < self.CHAMBER_RADIUS)
        self.collected_charge += collected_q
        self.collector_current = collected_q / max(dt, 1e-18)

    def _fusion_alpha_production(self, dt: float) -> None:
        if not self.shot_physics_enabled:
            return
        rate = self.last_p_fusion
        n_new = int(min(15, rate * 1.0e-6))
        if n_new <= 0:
            return
        ang = _RNG.uniform(0, 2 * np.pi, n_new)
        r = _RNG.uniform(0.0, self.TARGET_RADIUS * 2, n_new)
        x = r * np.cos(ang)
        y = r * np.sin(ang)
        # Born-alpha speeds sampled from the real p-11B spectrum, ejected
        # isotropically; the energy spread is what the grid voltage acts on.
        speed = P.alpha_speeds_from_energies(P.sample_alpha_energies_J(n_new, _RNG))
        self.species["alpha"].spawn(
            x, y, speed * np.cos(ang), speed * np.sin(ang), np.zeros(n_new)
        )
        if self.species["alpha"].count > 1200:
            sp = self.species["alpha"]
            keep = _RNG.choice(sp.count, 1200, replace=False)
            m = np.zeros(sp.count, bool)
            m[keep] = True
            sp.keep(m)
        if self.shot_physics_enabled and self.species["p"].count < 200:
            self._spawn_block(400)

    def update_plasma_state(self, dt: float) -> None:
        i_laser = self.controls.get("i_laser", 30.0)
        # Block ignition drives a very hot, non-thermal ion population.
        t_target = 120.0 + 6.0 * i_laser
        self.T_i_keV += (t_target - self.T_i_keV) * min(1.0, dt / 5.0e-11)
        # Electrons stay much colder on the picosecond timescale (key HB11 idea).
        self.T_e_keV += (0.18 * self.T_i_keV - self.T_e_keV) * min(1.0, dt / 2.0e-10)
        self.n_e = 1.0e29  # solid-density compressed block
        self.n_p = 0.5 * self.n_e
        self.n_B = 0.1 * self.n_e

    def energy_confinement_time(self) -> float:
        return 1.0e-11  # inertial (block) confinement timescale
