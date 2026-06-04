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
LPPFusion Dense Plasma Focus (DPF) core model.

2D XY slice through the coaxial electrode assembly: a hollow inner anode of
radius ``a`` surrounded by outer cathode rods at radius ``b``. A capacitor bank
drives a current that forms a plasma sheath; the sheath runs down the electrodes
and pinches on axis, generating an enormous azimuthal magnetic field.

Design physics
--------------
* Snowplow sheath dynamics (axial run-down of accreted mass ``M(z)``):

  .. math:: \\frac{d}{dt}\\left(M(z)\\frac{dz}{dt}\\right)
            = \\frac{\\mu_0 I(t)^2}{4\\pi}\\ln\\!\\left(\\frac{b}{a}\\right)

* Self-field:  ``B_theta = mu0 I / (2 pi r)``.
* Quantum Magnetic Bremsstrahlung Suppression: when ``B_theta > 1e5 T`` the
  Bremsstrahlung is suppressed by ``exp(-B / B_crit)``.

Controls: capacitor-bank voltage ``V_cap`` and operating gas pressure ``P0`` of
the hydrogen-boron mixture.
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

_RNG = np.random.default_rng(13)

#: Critical field for quantum magnetic Bremsstrahlung suppression [T].
B_CRIT = 1.0e5


class LPPReactor(ReactorSimulation):
    """Dense plasma focus with snowplow sheath dynamics and B-field suppression."""

    display_name = "LPP DPF"
    display_field_kind = "bz"

    ANODE_RADIUS = 0.05   # a
    CATHODE_RADIUS = 0.18  # b
    N_CATHODE_RODS = 12

    def __init__(self, grid: Grid | None = None, field_solver=None) -> None:
        if grid is None:
            grid = Grid(nx=161, ny=161, x0=-0.25, y0=-0.25, Lx=0.5, Ly=0.5)
        if field_solver is None:
            from pb11_reactor_sim.engine.pic_backend import make_backend

            field_solver = make_backend()
        # Circuit + snowplow state.
        self.current: float = 0.0          # I(t) [A]
        self.charge: float = 0.0           # capacitor charge proxy
        self.sheath_r: float = self.CATHODE_RADIUS  # collapsing sheath radius [m]
        self.sheath_v: float = 0.0         # dr/dt [m/s]
        self.b_pinch: float = 0.0          # peak azimuthal field at sheath [T]
        self._circuit_phase: float = 0.0
        super().__init__(grid, field_solver)

    # -- declarations -------------------------------------------------------
    @classmethod
    def control_specs(cls) -> list[ControlSpec]:
        return [
            ControlSpec("v_cap", "Capacitor Voltage", 10.0, 60.0, 35.0, units="kV"),
            ControlSpec("p0", "Gas Pressure", 0.5, 20.0, 6.0, units="Torr"),
        ]

    def default_dt(self) -> float:
        return 1.0e-9  # 1 ns

    @classmethod
    def shot_ops(cls) -> ShotOps:
        return ShotOps(
            requires_rearm_between_shots=True,
            arm_callout="ARMED — gas fill complete, capacitor bank charged.",
            fire_phases=(
                FirePhase("gas_fill", 0.15e-6, "Gas fill — H–B inventory in gap"),
                FirePhase("trigger", 0.05e-6, "T−1 — switch closes, trigger"),
                FirePhase("rundown", 0.2e-6, "Run-down — sheath accelerating"),
                FirePhase("pinch", 0.15e-6, "PINCH — focus on axis"),
                FirePhase("disrupt", 0.08e-6, "Disrupt — anode hit, energy release"),
                FirePhase("recovery", 0.25e-6, "Recovery — bank depleted, gas cooling"),
            ),
            quiescent_callout="Quiescent — bank empty (Arm to recharge & refill).",
        )

    def enter_unarmed(self) -> None:
        self.species.clear()
        self.current = 0.0
        self.charge = 0.0
        self.sheath_r = self.CATHODE_RADIUS
        self.sheath_v = 0.0
        self.b_pinch = 0.0
        self._circuit_phase = 0.0
        self.T_i_keV = 0.05
        self.T_e_keV = 0.05
        self.n_e = 1.0e16
        self.n_p = 5.0e15
        self.n_B = 5.0e14
        self.bz = self.grid.zeros()

    def enter_armed(self) -> None:
        self.current = 0.0
        self.charge = 1.0
        self.sheath_r = self.CATHODE_RADIUS
        self.sheath_v = 0.0
        self.b_pinch = 0.0
        self._circuit_phase = 0.0
        p0 = self.controls.get("p0", 6.0)
        self.n_e = 2.0e19 * (p0 / 6.0)
        self.n_p = 0.6 * self.n_e
        self.n_B = 0.08 * self.n_e
        self.T_i_keV = 0.3
        self.T_e_keV = 0.2
        self.seed_particles()

    def enter_quiescent(self) -> None:
        self.current = 0.0
        self.charge = 0.05
        self.sheath_r = self.CATHODE_RADIUS
        self.sheath_v = 0.0
        self.b_pinch = 0.0
        self.T_i_keV = max(self.T_i_keV * 0.25, 0.2)
        self.T_e_keV = max(self.T_e_keV * 0.3, 0.1)

    def discharge_phase_key(self) -> str:
        return "pinch"

    def skip_to_discharge_label(self) -> str:
        return "Skip to pinch"

    def prepare_skipped_to_discharge(self) -> None:
        self.charge = 1.0
        self._circuit_phase = 0.0
        self.time = 0.0
        self.sheath_r = self.CATHODE_RADIUS
        self.sheath_v = 0.0
        if not self.species:
            self.seed_particles()
        dt_step = 5.0e-9
        t_pre = 0.25e-6  # trigger + rundown before pinch
        for _ in range(int(t_pre / dt_step)):
            self._advance_circuit_and_snowplow(dt_step)
            self.time += dt_step
        self._update_b_field()
        v_cap = self.controls.get("v_cap", 35.0)
        p0 = self.controls.get("p0", 6.0)
        compression = self.CATHODE_RADIUS / max(self.sheath_r, self.ANODE_RADIUS)
        t_target = 30.0 + 6.0 * v_cap * min(compression, 6.0) / 6.0
        self.T_i_keV = t_target * 0.85
        self.T_e_keV = 0.5 * self.T_i_keV
        n_fill = 3.3e22 * (p0 / 760.0)
        self.n_e = n_fill * min(compression**2, 50.0) * 0.5
        self.n_p = 0.9 * self.n_e
        self.n_B = 0.02 * self.n_e
        self._snap_particles_to_temperature()

    def on_fire_phase_begin(self, phase_key: str) -> None:
        if phase_key == "trigger":
            self._circuit_phase = 0.0
            self.time = 0.0
            self.charge = 1.0
            self.sheath_r = self.CATHODE_RADIUS
            self.sheath_v = 0.0
            if not self.species:
                self.seed_particles()

    def on_fire_phase_tick(self, phase_key: str, dt: float) -> None:
        if phase_key == "quiescent":
            self.current = max(self.current - abs(self.current) * dt / 0.2e-6, 0.0)
            self.T_i_keV = max(self.T_i_keV - dt / 2.0e-6, 0.15)
            self._update_b_field()

    def _hot_phase_keys(self) -> frozenset[str]:
        return frozenset({"trigger", "rundown", "pinch", "disrupt"})

    # -- geometry -----------------------------------------------------------
    def build_geometry(self) -> None:
        g = self.grid
        X, Y = g.meshgrid()
        R = np.hypot(X, Y)
        theta = np.arctan2(Y, X)

        self.conductor_mask = np.zeros((g.ny, g.nx), dtype=bool)
        self.conductor_potential = g.zeros()

        v_cap = self.controls.get("v_cap", 35.0) * 1.0e3

        # Hollow inner anode: an annulus (hollow center), biased to +V_cap.
        anode = (R >= self.ANODE_RADIUS * 0.6) & (R <= self.ANODE_RADIUS)
        self.conductor_mask |= anode
        self.conductor_potential[anode] = v_cap

        # Outer cathode rods at radius b (grounded).
        rod_ang = (np.floor((theta + np.pi) / (2 * np.pi / self.N_CATHODE_RODS)).astype(int))
        rod_center = (rod_ang + 0.5) * (2 * np.pi / self.N_CATHODE_RODS) - np.pi
        near_rod = np.abs(np.angle(np.exp(1j * (theta - rod_center)))) < 0.12
        cathode = near_rod & (R >= self.CATHODE_RADIUS) & (R <= self.CATHODE_RADIUS + 0.025)
        self.conductor_mask |= cathode
        self.conductor_potential[cathode] = 0.0

        self.plasma_mask = (R > self.ANODE_RADIUS) & (R < self.CATHODE_RADIUS)

        anode_c = (255, 200, 150)
        cathode_c = (180, 200, 255)
        self.boundaries = [
            BoundaryShape("circle", (0.0, 0.0, self.ANODE_RADIUS), anode_c),
            BoundaryShape("circle", (0.0, 0.0, self.CATHODE_RADIUS), cathode_c),
        ]
        self.labels = [
            StructureLabel("Hollow Anode (a)", 0.0, self.ANODE_RADIUS + 0.02,
                           anode_c, angle=0.0, anchor=(0.5, 0.5)),
            StructureLabel("Cathode Rods (b)", 0.0, self.CATHODE_RADIUS + 0.02,
                           cathode_c, angle=0.0, anchor=(0.5, 0.5)),
            StructureLabel("Plasma Sheath", -self.CATHODE_RADIUS + 0.02, -self.CATHODE_RADIUS + 0.03,
                           (255, 150, 150), angle=0.0, anchor=(0.0, 0.5)),
            StructureLabel("Pinch / Focus Region", 0.012, 0.025,
                           (255, 255, 150), angle=0.0, anchor=(0.0, 0.5)),
        ]
        self._update_b_field()

    def _update_b_field(self) -> None:
        """Recompute the azimuthal field magnitude grid ``|B_theta|`` for display."""
        g = self.grid
        X, Y = g.meshgrid()
        R = np.hypot(X, Y)
        R_safe = np.maximum(R, self.ANODE_RADIUS)
        # B_theta = mu0 I / (2 pi r), only inside the current sheath radius.
        b = C.VACUUM_PERMEABILITY * self.current / (2.0 * np.pi * R_safe)
        b[R > self.sheath_r] *= 0.05  # field largely confined within the sheath
        self.bz = b

    # -- particles ----------------------------------------------------------
    def seed_particles(self) -> None:
        n = 1600
        protons = ParticleSpecies(C.PROTON, macro_weight=1.0e12)
        boron = ParticleSpecies(C.BORON11, macro_weight=1.0e11)
        electrons = ParticleSpecies(C.ELECTRON, macro_weight=1.0e12)
        alphas = ParticleSpecies(C.ALPHA, macro_weight=1.0e10)

        def fill(sp: ParticleSpecies, count: int, vth: float) -> None:
            ang = _RNG.uniform(0, 2 * np.pi, count)
            r = _RNG.uniform(self.ANODE_RADIUS + 0.005, self.CATHODE_RADIUS - 0.005, count)
            sp.spawn(
                r * np.cos(ang), r * np.sin(ang),
                _RNG.normal(0, vth, count), _RNG.normal(0, vth, count), np.zeros(count),
            )

        fill(protons, n, 3.0e5)
        fill(boron, n // 5, 1.0e5)
        fill(electrons, n, 1.0e7)
        self.species = {"p": protons, "B": boron, "e": electrons, "alpha": alphas}

    # -- dynamics -----------------------------------------------------------
    def on_controls(self) -> None:
        self.build_geometry()

    def _advance_circuit_and_snowplow(self, dt: float) -> None:
        """Integrate a damped LC discharge and the snowplow sheath ODE (RK4)."""
        v_cap_kv = self.controls.get("v_cap", 35.0)
        p0 = self.controls.get("p0", 6.0)  # Torr

        # Simple ringing RLC current: I(t) = I0 sin(w t) exp(-t/tau).
        omega = 2.0 * np.pi * 0.5e6  # ~0.5 MHz bank ring
        tau = 3.0e-6
        self._circuit_phase += omega * dt
        # Mega-ampere class bank: ~0.9 MA at 35 kV, scaling to >1.5 MA at 60 kV.
        i_peak = 2.5e4 * v_cap_kv
        self.current = i_peak * np.sin(self._circuit_phase) * np.exp(-self.time / tau)
        self.current = abs(self.current)

        # Snowplow: d/dt(M dz/dt) = mu0 I^2/(4 pi) ln(b/a). Here we collapse the
        # sheath radially; swept mass grows with gas pressure and swept volume.
        ln_ba = np.log(self.CATHODE_RADIUS / self.ANODE_RADIUS)
        drive = C.VACUUM_PERMEABILITY * self.current**2 / (4.0 * np.pi) * ln_ba
        # Accreted linear mass density ~ rho0 * swept area; rho0 from pressure.
        rho0 = 4.46e-4 * (p0 / 760.0)  # kg/m^3 for H2 at P0 Torr (approx)
        swept = max(self.CATHODE_RADIUS**2 - self.sheath_r**2, 1.0e-6)
        mass = max(rho0 * np.pi * swept, 1.0e-12)
        accel = -drive / mass  # inward (radius decreasing)
        self.sheath_v += accel * dt
        self.sheath_r += self.sheath_v * dt
        if self.sheath_r <= self.ANODE_RADIUS:
            # Pinch reached: bounce/reset to model the periodic refill.
            self.sheath_r = self.CATHODE_RADIUS
            self.sheath_v = 0.0

        # Peak azimuthal field at the sheath: B = mu0 I / (2 pi r).
        self.b_pinch = C.VACUUM_PERMEABILITY * self.current / (2.0 * np.pi * max(self.sheath_r, self.ANODE_RADIUS))

    def advance_state_only(self, dt: float) -> None:
        # Optimizer hook: advance the scalar circuit/snowplow + 0D state only.
        self._advance_circuit_and_snowplow(dt)
        self.update_plasma_state(dt)

    def reset_eval_state(self) -> None:
        super().reset_eval_state()
        self.current = 0.0
        self.charge = 0.0
        self.sheath_r = self.CATHODE_RADIUS
        self.sheath_v = 0.0
        self.b_pinch = 0.0
        self._circuit_phase = 0.0

    def advance_particles(self, dt: float) -> None:
        g = self.grid
        self._advance_circuit_and_snowplow(dt)
        self._update_b_field()

        # Magnetized push under the self-field; ions drift inward with the sheath.
        for sym, sp in self.species.items():
            if sp.count == 0:
                continue
            bz_p = sp.gather_scalar(self.bz, g.x0, g.y0, g.dx, g.dy)
            sp.push_boris(np.zeros(sp.count), np.zeros(sp.count), bz_p, dt)
            # Radial inward drift proportional to sheath velocity (snowplow sweep).
            r = np.hypot(sp.x, sp.y) + 1e-9
            ur = self.sheath_v
            sp.x += (sp.x / r) * ur * dt * 0.2
            sp.y += (sp.y / r) * ur * dt * 0.2

        self._confine_and_collect()
        self._fusion_alpha_production(dt)
        self.solve_fields()

    def _confine_and_collect(self) -> None:
        for sym, sp in self.species.items():
            if sp.count == 0:
                continue
            r = np.hypot(sp.x, sp.y)
            # Reflect off the anode surface, absorb at cathode radius.
            inside_anode = r < self.ANODE_RADIUS
            if np.any(inside_anode):
                nx_ = sp.x[inside_anode] / np.maximum(r[inside_anode], 1e-9)
                ny_ = sp.y[inside_anode] / np.maximum(r[inside_anode], 1e-9)
                sp.x[inside_anode] = nx_ * self.ANODE_RADIUS
                sp.y[inside_anode] = ny_ * self.ANODE_RADIUS
                sp.vx[inside_anode] *= -0.5
                sp.vy[inside_anode] *= -0.5
            sp.keep(r <= self.CATHODE_RADIUS)

    def _fusion_alpha_production(self, dt: float) -> None:
        if not self.shot_physics_enabled:
            return
        rate = self.last_p_fusion
        n_new = int(min(20, rate * 1.0e-5))
        if n_new <= 0:
            return
        ang = _RNG.uniform(0, 2 * np.pi, n_new)
        r = _RNG.uniform(0.0, self.ANODE_RADIUS, n_new)
        # Born-alpha speeds from the real p-11B spectrum, emitted from the pinch.
        speed = P.alpha_speeds_from_energies(P.sample_alpha_energies_J(n_new, _RNG))
        self.species["alpha"].spawn(
            r * np.cos(ang), r * np.sin(ang),
            speed * np.cos(ang) * 0.3, speed * np.sin(ang) * 0.3, np.zeros(n_new),
        )
        sp = self.species["alpha"]
        if sp.count > 1200:
            keep = _RNG.choice(sp.count, 1200, replace=False)
            m = np.zeros(sp.count, bool)
            m[keep] = True
            sp.keep(m)

    def apply_brems_suppression(self, p_brems: float) -> float:
        """Suppress Bremsstrahlung when the pinch field exceeds ``B_CRIT``."""
        return float(P.magnetic_bremsstrahlung_suppression(p_brems, self.b_pinch, B_CRIT))

    def update_plasma_state(self, dt: float) -> None:
        v_cap = self.controls.get("v_cap", 35.0)
        p0 = self.controls.get("p0", 6.0)
        # Pinch compression heats ions; hotter at higher bank voltage, tighter pinch.
        compression = self.CATHODE_RADIUS / max(self.sheath_r, self.ANODE_RADIUS)
        t_target = 30.0 + 6.0 * v_cap * min(compression, 6.0) / 6.0
        self.T_i_keV += (t_target - self.T_i_keV) * min(1.0, dt / 2.0e-7)
        self.T_e_keV += (0.5 * self.T_i_keV - self.T_e_keV) * min(1.0, dt / 5.0e-7)
        # Density from fill pressure, compressed at the pinch.
        n_fill = 3.3e22 * (p0 / 760.0)
        self.n_e = n_fill * min(compression**2, 50.0)
        self.n_p = 0.9 * self.n_e
        self.n_B = 0.02 * self.n_e

    def energy_confinement_time(self) -> float:
        return 1.0e-7

    def display_field(self):
        return self.bz, "|B_theta| [T]"
