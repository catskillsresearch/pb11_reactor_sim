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
Generic reactor-simulation scaffolding shared by all three concepts.

This module defines:

* :class:`Grid`          -- uniform 2D grid metadata + coordinate helpers.
* :class:`ControlSpec`   -- declarative slider description for the GUI.
* :class:`StructureLabel`-- a persistent text annotation drawn on the canvas.
* :class:`Diagnostics`   -- rolling time-history buffers for the 1D plots.
* :class:`ReactorSimulation` -- abstract base class managing grid allocation,
  conductor masks, the PIC field-solve backend, the generic step loop, and the
  coupled auxiliary process equations (Bremsstrahlung, ion-electron relaxation,
  fusion power, Q_net).

Concrete reactors (TAE / HB11 / LPP) subclass :class:`ReactorSimulation` and
implement the abstract hooks for geometry, particle seeding, control handling,
and their design-specific physics.
"""
from __future__ import annotations

import abc
from collections import deque
from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

from pb11_reactor_sim.engine.particles import ParticleSpecies
from pb11_reactor_sim.engine.poisson import PoissonSolver
from pb11_reactor_sim.engine.shot_sequence import FirePhase, ShotOps, ShotPhase
from pb11_reactor_sim.physics import processes as P

FloatArray = npt.NDArray[np.float64]
BoolArray = npt.NDArray[np.bool_]


# ---------------------------------------------------------------------------
# Grid
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Grid:
    """Uniform rectangular 2D grid covering ``[x0, x0+Lx] x [y0, y0+Ly]``."""

    nx: int
    ny: int
    x0: float
    y0: float
    Lx: float
    Ly: float

    @property
    def dx(self) -> float:
        return self.Lx / (self.nx - 1)

    @property
    def dy(self) -> float:
        return self.Ly / (self.ny - 1)

    @property
    def extent(self) -> tuple[float, float, float, float]:
        """``(x_min, x_max, y_min, y_max)`` in metres (for pyqtgraph image rect)."""
        return (self.x0, self.x0 + self.Lx, self.y0, self.y0 + self.Ly)

    def meshgrid(self) -> tuple[FloatArray, FloatArray]:
        """Return ``(X, Y)`` node-coordinate arrays of shape ``(ny, nx)``."""
        xs = np.linspace(self.x0, self.x0 + self.Lx, self.nx)
        ys = np.linspace(self.y0, self.y0 + self.Ly, self.ny)
        return np.meshgrid(xs, ys)

    def zeros(self) -> FloatArray:
        return np.zeros((self.ny, self.nx), dtype=np.float64)


# ---------------------------------------------------------------------------
# GUI control declaration
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ControlSpec:
    """Declarative description of one GUI slider.

    The slider reports a float in ``[minimum, maximum]``; ``key`` is the name
    used in the control dictionary passed to :meth:`ReactorSimulation.apply_controls`.
    """

    key: str
    label: str
    minimum: float
    maximum: float
    default: float
    units: str = ""
    log: bool = False


@dataclass(frozen=True)
class StructureLabel:
    """Persistent text annotation for a structural element on the 2D canvas.

    ``angle`` rotates the text (degrees, CCW) so it can lie flat along a
    boundary; ``anchor`` is the pyqtgraph text anchor (0..1 in each axis).
    """

    text: str
    x: float
    y: float
    color: tuple[int, int, int] = (235, 235, 235)
    angle: float = 0.0
    anchor: tuple[float, float] = (0.5, 0.5)


@dataclass(frozen=True)
class BoundaryShape:
    """A dashed outline drawn to mark a physical boundary on the 2D canvas.

    ``shape`` is one of ``"circle"``, ``"rect"``, ``"line"``:

    * ``circle`` -- ``coords = (cx, cy, r)``
    * ``rect``   -- ``coords = (x0, y0, x1, y1)`` (opposite corners)
    * ``line``   -- ``coords = (x1, y1, x2, y2)``
    """

    shape: str
    coords: tuple[float, ...]
    color: tuple[int, int, int] = (150, 210, 255)


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------
@dataclass
class Diagnostics:
    """Rolling time-history buffers feeding the 1D diagnostic plots."""

    maxlen: int = 2000
    time: deque[float] = field(default_factory=lambda: deque(maxlen=2000))
    T_i: deque[float] = field(default_factory=lambda: deque(maxlen=2000))
    T_e: deque[float] = field(default_factory=lambda: deque(maxlen=2000))
    p_fusion: deque[float] = field(default_factory=lambda: deque(maxlen=2000))
    p_brems: deque[float] = field(default_factory=lambda: deque(maxlen=2000))
    p_cond: deque[float] = field(default_factory=lambda: deque(maxlen=2000))
    q_net: deque[float] = field(default_factory=lambda: deque(maxlen=2000))
    q_plasma: deque[float] = field(default_factory=lambda: deque(maxlen=2000))

    def append(
        self,
        t: float,
        T_i: float,
        T_e: float,
        p_fusion: float,
        p_brems: float,
        p_cond: float,
        q_net: float,
        q_plasma: float | None = None,
    ) -> None:
        self.time.append(t)
        self.T_i.append(T_i)
        self.T_e.append(T_e)
        self.p_fusion.append(p_fusion)
        self.p_brems.append(p_brems)
        self.p_cond.append(p_cond)
        self.q_net.append(q_net)
        self.q_plasma.append(q_plasma if q_plasma is not None else q_net)

    def clear(self) -> None:
        for d in (
            self.time,
            self.T_i,
            self.T_e,
            self.p_fusion,
            self.p_brems,
            self.p_cond,
            self.q_net,
            self.q_plasma,
        ):
            d.clear()


# ---------------------------------------------------------------------------
# Abstract reactor simulation
# ---------------------------------------------------------------------------
class ReactorSimulation(abc.ABC):
    """Abstract base managing grid, masks, PIC backend, and the process loop.

    Subclasses must define :attr:`display_name` and implement the abstract
    hooks. The public :meth:`step` and :meth:`reset` methods orchestrate the
    generic loop and should not normally be overridden.
    """

    #: Human-readable name shown in the reactor dropdown.
    display_name: str = "Reactor"
    #: Field shown on the 2D canvas: ``"phi"`` (potential) or ``"bz"`` (B-field).
    display_field_kind: str = "phi"

    def __init__(self, grid: Grid, field_solver: "FieldSolveBackend") -> None:
        self.grid = grid
        self.backend = field_solver
        self.poisson = PoissonSolver(grid.nx, grid.ny, grid.dx, grid.dy)

        # Field storage (ny, nx).
        self.phi: FloatArray = grid.zeros()
        self.ex: FloatArray = grid.zeros()
        self.ey: FloatArray = grid.zeros()
        self.bz: FloatArray = grid.zeros()
        self.rho: FloatArray = grid.zeros()

        # Geometry.
        self.conductor_mask: BoolArray = np.zeros((grid.ny, grid.nx), dtype=bool)
        self.conductor_potential: FloatArray = grid.zeros()
        self.plasma_mask: BoolArray = np.ones((grid.ny, grid.nx), dtype=bool)
        self.labels: list[StructureLabel] = []
        self.boundaries: list[BoundaryShape] = []

        # Particle species keyed by symbol.
        self.species: dict[str, ParticleSpecies] = {}

        # 0D plasma state (scalars), evolved by the energy model.
        self.T_i_keV: float = 50.0
        self.T_e_keV: float = 20.0
        self.n_e: float = 1.0e20
        self.n_p: float = 5.0e19
        self.n_B: float = 5.0e18

        # Control values (filled from defaults).
        self.controls: dict[str, float] = {c.key: c.default for c in self.control_specs()}

        # Diagnostics + bookkeeping.
        self.diagnostics = Diagnostics()
        self.time: float = 0.0
        self.dt: float = self.default_dt()
        self.step_index: int = 0

        # Latest auxiliary power densities [W/m^3] for display / readout.
        self.last_p_fusion: float = 0.0
        self.last_p_brems: float = 0.0
        self.last_p_cond: float = 0.0
        self.last_q_net: float = 0.0
        self.last_p_nbi: float = 0.0
        self.last_p_icc: float = 0.0
        self.last_q_plasma: float = 0.0

        # Shot sequencing (Arm / Fire / quiesce).
        self.shot_phase: ShotPhase = ShotPhase.UNARMED
        self.shot_callout: str = "Unarmed — press Arm shot to prepare."
        self._fire_phase_index: int = -1
        self._fire_phase_key: str = ""
        self._phase_elapsed_s: float = 0.0
        self._fire_from_quiescent: bool = False
        self._active_fire_phases: tuple[FirePhase, ...] = ()

        self.build_geometry()
        self.enter_unarmed()

        # Q_net optimizer uses hot flat-top 0D scalars, not the cold pre-shot state.
        self._eval_init_state = self._capture_eval_state()
        self._eval_init_state.update(self._optimizer_flat_top_state())

    # -- abstract hooks -----------------------------------------------------
    @classmethod
    @abc.abstractmethod
    def control_specs(cls) -> list[ControlSpec]:
        """Return the slider declarations for this reactor."""

    @abc.abstractmethod
    def default_dt(self) -> float:
        """Return the simulation timestep [s]."""

    @abc.abstractmethod
    def build_geometry(self) -> None:
        """Populate conductor mask/potential, plasma mask, and labels."""

    @abc.abstractmethod
    def seed_particles(self) -> None:
        """Create macroparticle populations (used at formation / target load)."""

    @classmethod
    @abc.abstractmethod
    def shot_ops(cls) -> ShotOps:
        """Return this reactor's Arm / Fire operational profile."""

    @abc.abstractmethod
    def enter_unarmed(self) -> None:
        """Vacuum / idle: empty chamber, cold scalars, no discharge."""

    @abc.abstractmethod
    def enter_armed(self) -> None:
        """Pre-shot ready state (gas fill, bank charged, target loaded, etc.)."""

    @abc.abstractmethod
    def enter_quiescent(self) -> None:
        """Post-shot cooldown (particles and energy decaying)."""

    @abc.abstractmethod
    def on_fire_phase_begin(self, phase_key: str) -> None:
        """Hook when a Fire countdown phase starts."""

    @abc.abstractmethod
    def on_fire_phase_tick(self, phase_key: str, dt: float) -> None:
        """Hook each timestep while a Fire phase is active."""

    @abc.abstractmethod
    def advance_particles(self, dt: float) -> None:
        """Advance fields + particles one step (design-specific physics).

        Implementations should use :meth:`solve_fields` for the electrostatic
        solve and the :class:`ParticleSpecies` pushers, then handle boundaries,
        collection, and fusion product creation.
        """

    @abc.abstractmethod
    def update_plasma_state(self, dt: float) -> None:
        """Update the 0D plasma state (``T_i``, ``T_e``, densities) for this step.

        This sets the scalars consumed by :meth:`compute_processes`.
        """

    # -- shared services ----------------------------------------------------
    def solve_fields(self) -> None:
        """Deposit charge, solve for the potential via the active backend, get E."""
        self.rho.fill(0.0)
        for sp in self.species.values():
            sp.deposit_charge(self.rho, self.grid.x0, self.grid.y0, self.grid.dx, self.grid.dy)
        self.phi = self.backend.solve_potential(
            self.rho, self.poisson, self.conductor_mask, self.conductor_potential
        )
        self.ex, self.ey = self.poisson.electric_field(self.phi)

    def _process_powers(self) -> tuple[float, float, float, float]:
        """Evaluate the coupled process equations from the current 0D state.

        Returns ``(p_fusion, p_brems, p_cond, q_net)`` in W/m^3 (and dimensionless
        for Q). Pure -- no side effects -- so it can be reused by both the live
        diagnostics loop and the Q-optimizer.
        """
        z_eff = P.z_effective({1: self.n_p, 5: self.n_B}, self.n_e)
        p_brems = float(P.bremsstrahlung_power_density(z_eff, self.n_e, self.T_e_keV))
        p_brems = self.apply_brems_suppression(p_brems)
        p_fusion = float(P.fusion_power_density(self.n_p, self.n_B, self.T_i_keV))
        p_cond = float(
            P.conduction_loss_density(self.n_e, self.T_e_keV, self.energy_confinement_time())
        )
        q = float(P.q_net(p_fusion, p_brems, p_cond))
        return p_fusion, p_brems, p_cond, q

    def compute_processes(self) -> None:
        """Evaluate the process equations and record them in diagnostics."""
        p_fusion, p_brems, p_cond, q = self._process_powers()

        self.last_p_fusion = p_fusion
        self.last_p_brems = p_brems
        self.last_p_cond = p_cond
        self.last_q_net = q

        q_plasma = getattr(self, "last_q_plasma", q)
        self.diagnostics.append(
            self.time * 1.0e6,  # display time in microseconds
            self.T_i_keV,
            self.T_e_keV,
            p_fusion,
            p_brems,
            p_cond,
            q,
            q_plasma=q_plasma,
        )

    # Overridable physics knobs --------------------------------------------
    def energy_confinement_time(self) -> float:
        """Energy confinement time [s] used by the conduction loss term."""
        return 1.0e-3

    def apply_brems_suppression(self, p_brems: float) -> float:
        """Hook for reactor-specific Bremsstrahlung suppression (LPP overrides)."""
        return p_brems

    # -- shot operations (Arm / Fire) ---------------------------------------
    def can_fire(self) -> bool:
        """Whether **Fire** is allowed from the current operational state."""
        ops = self.shot_ops()
        if self.shot_phase == ShotPhase.ARMED:
            return True
        if self.shot_phase == ShotPhase.QUIESCENT and not ops.requires_rearm_between_shots:
            return True
        return False

    def discharge_phase_key(self) -> str:
        """Shot phase where the main discharge begins (subclasses override)."""
        return "flat_top"

    def skip_to_discharge_label(self) -> str:
        """Button text for :meth:`skip_to_discharge`."""
        return "Skip to flat-top"

    def is_startup_countdown(self) -> bool:
        """True during fast-forward pre-discharge phases after **Fire**."""
        if self.shot_phase != ShotPhase.FIRING or not self._active_fire_phases:
            return False
        keys = [p.key for p in self._active_fire_phases]
        target = self.discharge_phase_key()
        if self._fire_phase_key not in keys or target not in keys:
            return False
        return keys.index(self._fire_phase_key) < keys.index(target)

    def plateau_phase_keys(self) -> frozenset[str]:
        """Long hold phases (flat-top / main pulse) that may be GUI fast-forwarded."""
        return frozenset({self.discharge_phase_key()})

    def tail_fast_phase_keys(self) -> frozenset[str]:
        """Short cooldown phases after the main discharge."""
        return frozenset({"ramp_down", "recovery", "afterglow"})

    def is_plateau_fast_forward(self) -> bool:
        return (
            self.shot_phase == ShotPhase.FIRING
            and self._fire_phase_key in self.plateau_phase_keys()
        )

    def is_tail_fast_forward(self) -> bool:
        return (
            self.shot_phase == ShotPhase.FIRING
            and self._fire_phase_key in self.tail_fast_phase_keys()
        )

    def can_skip_to_discharge(self) -> bool:
        return self.is_startup_countdown()

    def prepare_skipped_to_discharge(self) -> None:
        """Set fields/particles as if pre-discharge countdown phases finished."""

    def skip_to_discharge(self) -> bool:
        """Jump to the main discharge phase (flat-top / pulse / pinch)."""
        if not self.can_skip_to_discharge():
            return False
        target = self.discharge_phase_key()
        for i, phase in enumerate(self._active_fire_phases):
            if phase.key != target:
                continue
            self._fire_phase_index = i
            self._phase_elapsed_s = 0.0
            self.prepare_skipped_to_discharge()
            self._fire_phase_key = phase.key
            self.shot_callout = phase.callout
            self.on_fire_phase_begin(phase.key)
            return True
        return False

    def _snap_particles_to_temperature(self, n_passes: int = 12) -> None:
        for _ in range(n_passes):
            self._relax_particle_velocities(self.dt, tau_s=5.0e-9)

    def arm_shot(self) -> None:
        """Prepare a new shot (always allowed except while firing)."""
        if self.shot_phase == ShotPhase.FIRING:
            return
        self.time = 0.0
        self.step_index = 0
        self.diagnostics.clear()
        self.species.clear()
        self.phi = self.grid.zeros()
        self.ex = self.grid.zeros()
        self.ey = self.grid.zeros()
        self.rho = self.grid.zeros()
        self.last_p_fusion = 0.0
        self.last_p_brems = 0.0
        self.last_p_cond = 0.0
        self.last_q_net = 0.0
        self.last_p_nbi = 0.0
        self.last_p_icc = 0.0
        self.last_q_plasma = 0.0
        self.enter_armed()
        self.build_geometry()
        self.on_controls()
        self.shot_phase = ShotPhase.ARMED
        self.shot_callout = self.shot_ops().arm_callout
        self._fire_phase_index = -1
        self._fire_phase_key = ""

    def fire_shot(self) -> bool:
        """Begin the Fire countdown; returns False if not allowed."""
        if not self.can_fire():
            return False
        ops = self.shot_ops()
        from_quiescent = self.shot_phase == ShotPhase.QUIESCENT
        self._fire_from_quiescent = from_quiescent
        self._active_fire_phases = ops.phases_for_fire(from_quiescent)
        if not self._active_fire_phases:
            return False
        self.time = 0.0
        self.step_index = 0
        self.diagnostics.clear()
        self._fire_phase_index = 0
        self._phase_elapsed_s = 0.0
        self._begin_fire_phase(0)
        self.shot_phase = ShotPhase.FIRING
        return True

    def _begin_fire_phase(self, index: int) -> None:
        phase = self._active_fire_phases[index]
        self._fire_phase_key = phase.key
        self._phase_elapsed_s = 0.0
        self.shot_callout = phase.callout
        self.on_fire_phase_begin(phase.key)

    def _advance_shot_clock(self, dt: float) -> None:
        if self.shot_phase != ShotPhase.FIRING:
            return
        self._phase_elapsed_s += dt
        phase = self._active_fire_phases[self._fire_phase_index]
        if self._phase_elapsed_s < phase.duration_s:
            return
        next_index = self._fire_phase_index + 1
        if next_index < len(self._active_fire_phases):
            self._fire_phase_index = next_index
            self._begin_fire_phase(next_index)
            return
        self._finish_shot()

    def _finish_shot(self) -> None:
        self.enter_quiescent()
        self.shot_phase = ShotPhase.QUIESCENT
        self.shot_callout = self.shot_ops().quiescent_callout
        self._fire_phase_key = ""
        self._fire_phase_index = -1

    @property
    def shot_physics_enabled(self) -> bool:
        """Full PIC + fusion physics active only during hot discharge phases."""
        return self._fire_phase_key in self._hot_phase_keys()

    def _hot_phase_keys(self) -> frozenset[str]:
        return frozenset({"flat_top", "nbi_heat", "main_pulse", "pinch", "pulse"})

    def _optimizer_flat_top_state(self) -> dict[str, float]:
        """0D state the Q_net search advances from (discharge flat-top, not pre-shot)."""
        return {
            "T_i_keV": 50.0,
            "T_e_keV": 20.0,
            "n_e": 1.0e20,
            "n_p": 5.0e19,
            "n_B": 5.0e18,
            "time": 0.0,
        }

    def particle_count(self) -> int:
        """Total macroparticles across all species."""
        return sum(sp.count for sp in self.species.values())

    def _should_update_plasma_state(self) -> bool:
        """Hot discharge model — not during pre-shot standby or scripted ramps."""
        if self.shot_phase == ShotPhase.FIRING:
            return self.shot_physics_enabled
        return False

    def _should_advance_particles(self) -> bool:
        """PIC push when macroparticles exist (or hot discharge phase)."""
        if self.shot_phase == ShotPhase.FIRING:
            if self._fire_phase_key == "gas_fill":
                return False
            return self.particle_count() > 0 or self.shot_physics_enabled
        if self.shot_phase == ShotPhase.QUIESCENT:
            return self.particle_count() > 0
        return False

    def _relax_particle_velocities(self, dt: float, tau_s: float = 2.0e-7) -> None:
        """Relax macroparticle speeds toward the current 0D ``T_i`` / ``T_e``.

        Without this, the scalar temperature can rise while the dots keep their
        cold launch speeds and barely move on the canvas.
        """
        from pb11_reactor_sim.physics import constants as C

        blend = min(1.0, dt / tau_s)
        if blend <= 0.0 or not self.species:
            return
        rng = np.random.default_rng(self.step_index & 0xFFFF)
        for sym, sp in self.species.items():
            if sp.count == 0:
                continue
            t_kev = max(self.T_e_keV if sym == "e" else self.T_i_keV, 0.02)
            vth = float(np.sqrt(t_kev * C.KEV_TO_JOULE / sp.species.mass))
            sp.vx += (rng.normal(0.0, vth, sp.count) - sp.vx) * blend
            sp.vy += (rng.normal(0.0, vth, sp.count) - sp.vy) * blend

    def _tick_quiescent(self, dt: float) -> None:
        """Slow cooldown between shots while paused or waiting for next Fire."""
        if self.shot_phase != ShotPhase.QUIESCENT:
            return
        self.on_fire_phase_tick("quiescent", dt)
        if self._should_advance_particles():
            self.advance_particles(dt)
            self._relax_particle_velocities(dt)
        self.compute_processes()
        self.time += dt
        self.step_index += 1

    # -- fast Q evaluation (for the optimizer) ------------------------------
    def _capture_eval_state(self) -> dict[str, float]:
        """Snapshot the mutable 0D state restored between optimizer trials."""
        return {
            "T_i_keV": self.T_i_keV,
            "T_e_keV": self.T_e_keV,
            "n_e": self.n_e,
            "n_p": self.n_p,
            "n_B": self.n_B,
            "time": 0.0,
        }

    def reset_eval_state(self) -> None:
        """Restore the 0D state to its initial snapshot (subclasses extend)."""
        for key, value in self._eval_init_state.items():
            setattr(self, key, value)

    def advance_state_only(self, dt: float) -> None:
        """Advance only the 0D plasma state (no particles/fields).

        Used by the optimizer to evaluate steady-state Q cheaply. Subclasses
        with control-driven dynamic state (e.g. LPP's circuit/snowplow) override
        this to advance that scalar state too.
        """
        self.update_plasma_state(dt)

    def evaluate_qnet(self, controls: dict[str, float], max_steps: int = 1600, window: int = 200) -> float:
        """Return the (windowed-mean) steady-state ``Q_net`` for given controls.

        Resets the 0D state, applies the candidate controls *without* rebuilding
        geometry, advances the lightweight state model to (quasi-)steady state,
        and averages Q over the final ``window`` steps. This is fast (scalar
        math only) and never touches the PIC particles, fields, or backend, so
        it is safe to call from a worker thread.
        """
        self.reset_eval_state()
        self.controls.update(controls)
        dt = self.dt
        q_window: list[float] = []
        prev_q: float | None = None
        for i in range(max_steps):
            self.advance_state_only(dt)
            self.time += dt
            _, _, _, q = self._process_powers()
            q_window.append(q)
            if len(q_window) > window:
                q_window.pop(0)
            if i > window and prev_q is not None and abs(q - prev_q) <= 1.0e-4 * max(abs(q), 1.0e-30):
                break
            prev_q = q
        return float(np.mean(q_window)) if q_window else 0.0

    # -- generic loop -------------------------------------------------------
    def step(self) -> None:
        """Advance the simulation by one timestep (generic orchestration)."""
        if self.shot_phase == ShotPhase.UNARMED:
            return
        if self.shot_phase == ShotPhase.FIRING:
            self.on_fire_phase_tick(self._fire_phase_key, self.dt)
            if self._should_advance_particles():
                self.advance_particles(self.dt)
                self._relax_particle_velocities(self.dt)
            if self._should_update_plasma_state():
                self.update_plasma_state(self.dt)
            self.compute_processes()
            self.time += self.dt
            self.step_index += 1
            self._advance_shot_clock(self.dt)
            return
        if self.shot_phase == ShotPhase.QUIESCENT:
            self._tick_quiescent(self.dt)
            return
        if self.shot_phase == ShotPhase.ARMED:
            return  # standby: wait for Fire (Play does not heat an empty chamber)

    def reset(self) -> None:
        """Return to unarmed idle (factory / slider defaults)."""
        self.time = 0.0
        self.step_index = 0
        self.diagnostics.clear()
        self.enter_unarmed()
        self.build_geometry()
        self.on_controls()
        self.shot_phase = ShotPhase.UNARMED
        self.shot_callout = "Unarmed — press Arm shot to prepare."
        self._fire_phase_index = -1
        self._fire_phase_key = ""

    def apply_controls(self, values: dict[str, float]) -> None:
        """Merge new slider values; subclasses may react via :meth:`on_controls`."""
        self.controls.update(values)
        self.on_controls()

    def on_controls(self) -> None:
        """Hook called after controls change (subclasses may rebuild geometry)."""

    # -- display helpers ----------------------------------------------------
    def display_field(self) -> tuple[FloatArray, str]:
        """Return the 2D field to render and its label."""
        if self.display_field_kind == "bz":
            return self.bz, "B_z [T]"
        return self.phi, "Phi [V]"

    def display_field_levels(self) -> tuple[float, float] | None:
        """Optional fixed ``(lo, hi)`` colour-scale limits for the 2D canvas.

        Returning ``None`` lets the canvas derive limits from the field min/max.
        Subclasses may override for stable colouring (e.g. symmetric ``±B0``).
        """
        return None

    def particle_overlay(self) -> dict[str, tuple[FloatArray, FloatArray, tuple[int, int, int]]]:
        """Return ``{symbol: (x, y, rgb)}`` for the scatter overlay."""
        out: dict[str, tuple[FloatArray, FloatArray, tuple[int, int, int]]] = {}
        for sym, sp in self.species.items():
            out[sym] = (sp.x, sp.y, sp.species.color)
        return out


# Backend protocol is imported lazily to avoid a circular import at module load.
from pb11_reactor_sim.engine.pic_backend import FieldSolveBackend  # noqa: E402
