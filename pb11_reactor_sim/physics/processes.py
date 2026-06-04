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
Coupled core-process equations for the p-11B simulator (Step 1).

Every function is written to accept either Python floats or NumPy arrays
(scalars *or* 2D grid fields) so the same code drives both the 0D power-balance
diagnostics and the per-cell 2D field overlays. All routines are pure and
vectorized; no global state.

Unit conventions
----------------
* Temperatures ``T_e``, ``T_i`` are in **keV**.
* Number densities ``n_e``, ``n_i`` are in **m^-3**.
* Returned power densities are in **W/m^3**.

References
----------
* Relativistic Bremsstrahlung scaling: Rider (1995); Putvinski et al. (2019).
* Ion-electron Coulomb relaxation: Spitzer (1962) / NRL Plasma Formulary.
* p-11B reactivity fit: log-parabola shape consistent with the existing
  ``ssto/orbitron/simulator/fusion_pb11.py`` model, peak shifted toward the
  ~300 keV optimum requested by the spec.
"""
from __future__ import annotations

from typing import TypeVar, Union

import numpy as np
import numpy.typing as npt

from pb11_reactor_sim.physics import constants as C

# Accept scalars or arrays uniformly.
FloatOrArray = TypeVar("FloatOrArray", float, npt.NDArray[np.float64])
ArrayLike = Union[float, npt.NDArray[np.float64]]

# Bremsstrahlung leading coefficient from the spec [W m^3 keV^-0.5].
_BREMS_COEFF: float = 1.57e-40
# Relativistic correction slope (T_e in keV divided by m_e c^2 in keV).
_BREMS_REL_SLOPE: float = 1.71

# Coulomb logarithm default (weakly varying; ~10-20 for hot fusion plasmas).
DEFAULT_COULOMB_LOG: float = 15.0


# ---------------------------------------------------------------------------
# 0. Effective charge
# ---------------------------------------------------------------------------
def z_effective(
    densities: dict[int, ArrayLike],
    n_e: ArrayLike,
    *,
    floor: float = 1.0e6,
) -> ArrayLike:
    r"""Effective ion charge :math:`Z_{eff} = \sum_i n_i Z_i^2 / n_e`.

    Parameters
    ----------
    densities:
        Mapping of ion charge number ``Z_i`` -> ion number density ``n_i``
        (m^-3). Electrons are excluded.
    n_e:
        Electron number density (m^-3). May be scalar or array.
    floor:
        Small density floor (m^-3) to avoid division by zero in vacuum cells.
    """
    n_e_safe = np.maximum(n_e, floor)
    numerator: ArrayLike = 0.0
    for z_i, n_i in densities.items():
        numerator = numerator + np.asarray(n_i, dtype=float) * float(z_i) ** 2
    return numerator / n_e_safe


# ---------------------------------------------------------------------------
# 1. Bremsstrahlung radiation loss (relativistic-corrected)
# ---------------------------------------------------------------------------
def bremsstrahlung_power_density(
    z_eff: ArrayLike,
    n_e: ArrayLike,
    T_e_keV: ArrayLike,
) -> ArrayLike:
    r"""Relativistic Bremsstrahlung radiated power density [W/m^3].

    .. math::

        P_{Br} = 1.57\times10^{-40}\, Z_{eff}^2\, n_e^2\, \sqrt{T_e}
                 \left(1 + 1.71\frac{T_e}{m_e c^2}\right)

    with :math:`T_e` in keV and :math:`n_e` in m^-3.
    """
    t_e = np.maximum(np.asarray(T_e_keV, dtype=float), 0.0)
    rel_correction = 1.0 + _BREMS_REL_SLOPE * t_e / C.ELECTRON_REST_ENERGY_KEV
    return (
        _BREMS_COEFF
        * np.asarray(z_eff, dtype=float) ** 2
        * np.asarray(n_e, dtype=float) ** 2
        * np.sqrt(t_e)
        * rel_correction
    )


def magnetic_bremsstrahlung_suppression(
    p_brems: ArrayLike,
    b_field_T: ArrayLike,
    b_crit_T: float = 1.0e5,
) -> ArrayLike:
    r"""Quantum magnetic Bremsstrahlung suppression (LPPFusion DPF regime).

    When the self-generated magnetic field exceeds ``b_crit_T`` the emission is
    suppressed by :math:`\exp(-B / B_{crit})`.

    .. math::

        P_{Br,supp} = P_{Br}\, \exp(-B / B_{crit}) \quad (B > B_{crit})
    """
    b = np.asarray(b_field_T, dtype=float)
    factor = np.where(b > b_crit_T, np.exp(-b / b_crit_T), 1.0)
    return np.asarray(p_brems, dtype=float) * factor


# ---------------------------------------------------------------------------
# 2. Ion-electron collisional relaxation
# ---------------------------------------------------------------------------
def ie_relaxation_time(
    n_i: ArrayLike,
    T_e_keV: ArrayLike,
    z_i: float,
    *,
    coulomb_log: float = DEFAULT_COULOMB_LOG,
    floor: float = 1.0e6,
) -> ArrayLike:
    r"""Ion-electron energy equilibration time :math:`\tau_{ie}` [s].

    .. math::

        \tau_{ie} \propto \frac{T_e^{1.5}}{n_i Z_i^2 \ln\Lambda}

    The proportionality constant is the standard Spitzer electron-ion energy
    relaxation prefactor evaluated with ``T_e`` in keV and ``n_i`` in m^-3,
    giving a representative magnitude for hot fusion plasmas.
    """
    # Spitzer-class prefactor (SI/keV mixed) calibrated to NRL formulary
    # magnitudes: tau_ie ~ 3.5e11 * T_e[keV]^1.5 / (n_i[m^-3] Z^2 lnLambda) * (m_i/m_p)
    # For the relative power balance the constant only sets the timescale.
    prefactor = 3.44e11
    n_i_safe = np.maximum(np.asarray(n_i, dtype=float), floor)
    t_e = np.maximum(np.asarray(T_e_keV, dtype=float), 1.0e-3)
    return prefactor * t_e**1.5 / (n_i_safe * z_i**2 * coulomb_log)


def ie_relaxation_power_density(
    n_e: ArrayLike,
    T_i_keV: ArrayLike,
    T_e_keV: ArrayLike,
    n_i: ArrayLike,
    z_i: float,
    *,
    coulomb_log: float = DEFAULT_COULOMB_LOG,
) -> ArrayLike:
    r"""Ion -> electron collisional power transfer density [W/m^3].

    .. math::

        P_{i\to e} = \frac{3}{2} n_e \frac{T_i - T_e}{\tau_{ie}}

    Positive when ions are hotter than electrons (net heating of electrons).
    """
    tau = ie_relaxation_time(n_i, T_e_keV, z_i, coulomb_log=coulomb_log)
    delta_t_joule = (np.asarray(T_i_keV, dtype=float) - np.asarray(T_e_keV, dtype=float)) * C.KEV_TO_JOULE
    return 1.5 * np.asarray(n_e, dtype=float) * delta_t_joule / tau


# ---------------------------------------------------------------------------
# 3. p-11B fusion power density
# ---------------------------------------------------------------------------
# Log-parabola reactivity fit. Peak reactivity ~ 3e-22 m^3/s near 300 keV is a
# standard order-of-magnitude for p-11B; the log-parabola width reproduces the
# steep low-T rolloff and gentle high-T tail.
_SV_LOG_PEAK: float = -21.5          # log10(<sigma v> [m^3/s]) at the peak
_SV_LOG_T_PEAK: float = np.log10(300.0)  # peak near T_i = 300 keV
_SV_LOG_T_WIDTH: float = 2.0         # parabola curvature in log-T space


def pb11_reactivity(T_i_keV: ArrayLike) -> ArrayLike:
    r"""Maxwellian-averaged p-11B reactivity :math:`\langle\sigma v\rangle` [m^3/s].

    Parameterized log-parabola fit peaking near :math:`T_i \approx 300` keV.
    Clamped to a sensible temperature window [1, 2000] keV.
    """
    t = np.clip(np.asarray(T_i_keV, dtype=float), 1.0, 2000.0)
    log_t = np.log10(t)
    log_sv = _SV_LOG_PEAK - _SV_LOG_T_WIDTH * (log_t - _SV_LOG_T_PEAK) ** 2
    return np.power(10.0, log_sv)


def fusion_power_density(
    n_p: ArrayLike,
    n_B: ArrayLike,
    T_i_keV: ArrayLike,
    *,
    e_fusion_J: float = C.PB11_REACTION_ENERGY_J,
    sigma_v: ArrayLike | None = None,
) -> ArrayLike:
    r"""p-11B fusion power density [W/m^3].

    .. math::

        P_f = n_p\, n_B\, \langle\sigma v\rangle\, E_f, \qquad E_f = 8.7\,\text{MeV}

    If ``sigma_v`` is supplied it is used directly, otherwise
    :func:`pb11_reactivity` is evaluated at ``T_i_keV``.
    """
    sv = pb11_reactivity(T_i_keV) if sigma_v is None else np.asarray(sigma_v, dtype=float)
    return np.asarray(n_p, dtype=float) * np.asarray(n_B, dtype=float) * sv * e_fusion_J


# ---------------------------------------------------------------------------
# 3b. Born-alpha energy spectrum (sequential two-step decay)
# ---------------------------------------------------------------------------
# p + 11B proceeds through a 12C* compound nucleus that emits a primary alpha
# leaving 8Be, which then breaks into two more alphas:
#
#   alpha1 branch (dominant, ~90%):  p + 11B -> a1 + 8Be*(2+, 3.03 MeV) -> a1 + 2a
#   alpha0 branch (~10%):            p + 11B -> a0 + 8Be(g.s.)          -> a0 + 2a
#
# The three alphas therefore are NOT monoenergetic: two-body kinematics gives an
# energetic primary alpha and two lower-energy secondaries from the 8Be breakup.
# The intermediates (12C*, 8Be) live ~1e-21 - 1e-16 s -- far below any PIC
# timestep -- so they are not transported; only their imprint on the alpha
# spectrum is kept here. Each tuple is (mean_MeV, sigma_MeV, weight) for the
# *per-alpha* aggregate distribution; the weighted mean is ~8.68/3 MeV so total
# released kinetic energy is conserved on average.
_ALPHA_SPECTRUM: tuple[tuple[float, float, float], ...] = (
    (3.76, 0.30, 0.90 / 3.0),    # alpha1 primary (the classic ~3.76 MeV peak)
    (2.46, 1.00, 0.90 * 2 / 3.0),  # alpha1 secondaries: broad 8Be*(3.03) breakup
    (5.70, 0.30, 0.10 / 3.0),    # alpha0 primary (more energetic)
    (1.43, 0.50, 0.10 * 2 / 3.0),  # alpha0 secondaries: 8Be(g.s.) breakup
)
_ALPHA_E_MIN_MEV: float = 0.05
_ALPHA_E_MAX_MEV: float = 9.0


def sample_alpha_energies_J(
    n: int, rng: np.random.Generator
) -> npt.NDArray[np.float64]:
    """Sample ``n`` born-alpha kinetic energies [J] from the p-11B spectrum.

    Draws from the four-component mixture of the alpha0/alpha1 sequential-decay
    branches (see :data:`_ALPHA_SPECTRUM`). Returns energies in Joules.
    """
    if n <= 0:
        return np.zeros(0, dtype=float)
    means = np.array([c[0] for c in _ALPHA_SPECTRUM])
    sigmas = np.array([c[1] for c in _ALPHA_SPECTRUM])
    weights = np.array([c[2] for c in _ALPHA_SPECTRUM])
    weights = weights / weights.sum()
    idx = rng.choice(len(means), size=n, p=weights)
    e_mev = rng.normal(means[idx], sigmas[idx])
    e_mev = np.clip(e_mev, _ALPHA_E_MIN_MEV, _ALPHA_E_MAX_MEV)
    return e_mev * C.MEV_TO_JOULE


def alpha_speeds_from_energies(
    energies_J: npt.NDArray[np.float64],
) -> npt.NDArray[np.float64]:
    """Convert alpha kinetic energies [J] to (non-relativistic) speeds [m/s]."""
    return np.sqrt(2.0 * np.asarray(energies_J, dtype=float) / C.ALPHA.mass)


# ---------------------------------------------------------------------------
# 4. Net gain
# ---------------------------------------------------------------------------
def q_net(
    p_fusion: ArrayLike,
    p_brems: ArrayLike,
    p_conduction: ArrayLike,
    *,
    floor: float = 1.0e-30,
) -> ArrayLike:
    r"""Plasma physics gain (fusion vs radiation + transport).

    .. math::

        Q_{plasma} = \frac{P_{fusion}}{P_{Br} + P_{cond}}

    A floor avoids division by zero before the plasma is energized.
    """
    losses = np.asarray(p_brems, dtype=float) + np.asarray(p_conduction, dtype=float)
    return np.asarray(p_fusion, dtype=float) / np.maximum(losses, floor)


def q_system(
    p_recovered: ArrayLike,
    p_input_and_losses: ArrayLike,
    *,
    floor: float = 1.0e-30,
) -> ArrayLike:
    r"""Engineering / plant gain (recovered fusion-product power vs all inputs).

    .. math::

        Q_{sys} = \frac{P_{recovered}}{P_{NBI} + P_{Br} + P_{cond}}

    For TAE FRC, ``P_recovered`` is the ICC direct-conversion harvest of charged
    p-11B alphas.
    """
    return np.asarray(p_recovered, dtype=float) / np.maximum(
        np.asarray(p_input_and_losses, dtype=float), floor
    )


def conduction_loss_density(
    n_e: ArrayLike,
    T_e_keV: ArrayLike,
    tau_energy_s: float,
) -> ArrayLike:
    r"""Simple energy-confinement conduction loss density [W/m^3].

    .. math::

        P_{cond} = \frac{3 n_e T_e}{\tau_E}

    Uses the combined electron+ion thermal energy density divided by the energy
    confinement time ``tau_energy_s``.
    """
    energy_density = 3.0 * np.asarray(n_e, dtype=float) * np.asarray(T_e_keV, dtype=float) * C.KEV_TO_JOULE
    return energy_density / max(tau_energy_s, 1.0e-12)


# ---------------------------------------------------------------------------
# 5. TAE FRC beam-driven & ICC power balance
# ---------------------------------------------------------------------------
def nbi_beam_energy_keV(i_nbi: float, *, i_ref: float = 120.0) -> float:
    """MeV-class NBI energy [keV] from the normalized beam-current slider."""
    frac = max(float(i_nbi), 0.0) / i_ref
    return 250.0 + 320.0 * frac**0.85


def nbi_fast_ion_fraction(i_nbi: float, *, i_ref: float = 120.0) -> float:
    """Fraction of protons in the fast beam population (vs thermal bulk)."""
    frac = max(float(i_nbi), 0.0) / i_ref
    return min(0.72, 0.10 + 0.62 * frac)


def beam_target_fusion_power_density(
    n_beam: ArrayLike,
    n_B: ArrayLike,
    e_beam_keV: ArrayLike,
    *,
    e_fusion_J: float = C.PB11_REACTION_ENERGY_J,
    beam_enhancement: float = 4.5,
) -> ArrayLike:
    r"""Beam-on-boron fusion power [W/m^3] using :math:`\langle\sigma v\rangle(E_{beam})`.

    TAE's path relies on fast injected protons reacting with boron before
    full thermalization — this channel uses the beam energy, not the bulk ``T_i``.
    ``beam_enhancement`` accounts for non-Maxwellian overlap of the beam with
    the boron target distribution in the FRC core.
    """
    sv = pb11_reactivity(e_beam_keV)
    return (
        beam_enhancement
        * np.asarray(n_beam, dtype=float)
        * np.asarray(n_B, dtype=float)
        * sv
        * e_fusion_J
    )


def nbi_input_power_density(
    i_nbi: float,
    e_beam_keV: float,
    volume_m3: float,
    *,
    i_ref: float = 120.0,
) -> float:
    """Electrical NBI power deposited per unit FRC plasma volume [W/m^3]."""
    frac = max(float(i_nbi), 0.0) / i_ref
    e_scale = max(float(e_beam_keV), 1.0) / 400.0
    p_total_w = 1.35e5 * (frac**1.35) * e_scale
    return p_total_w / max(volume_m3, 1.0e-3)


def icc_recovery_power_density(p_fusion: ArrayLike, eta_icc: float) -> ArrayLike:
    """ICC harvest of charged alpha kinetic energy [W/m^3] (p-11B → 3 alphas)."""
    return float(eta_icc) * np.asarray(p_fusion, dtype=float)


def frc_transport_loss_density(
    n_e: ArrayLike,
    T_e_keV: ArrayLike,
    n_i_thermal: ArrayLike,
    T_i_thermal_keV: ArrayLike,
    tau_energy_s: float,
    *,
    n_loss_cap: float = 4.0e19,
    sustainment: float = 1.0,
) -> ArrayLike:
    r"""FRC end/transport loss on the *thermal* populations only [W/m^3].

    Fast beam ions are excluded from the loss inventory — they fuse before
    equilibrating. ``n_loss_cap`` limits the loss-region density used in the
    flux estimate (core peak ``n_e`` can exceed the scrape-off value).

    When ``sustainment < 1`` (under-beamed FRC), end/convection losses rise as
    the reversed field decays — modeled as excess loss above the confined limit.
    """
    n_e_loss = np.minimum(np.asarray(n_e, dtype=float), n_loss_cap)
    n_i_loss = np.minimum(np.asarray(n_i_thermal, dtype=float), n_loss_cap * 0.55)
    energy_density = (
        1.5
        * C.KEV_TO_JOULE
        * (
            n_e_loss * np.asarray(T_e_keV, dtype=float)
            + n_i_loss * np.asarray(T_i_thermal_keV, dtype=float)
        )
    )
    base = energy_density / max(tau_energy_s, 1.0e-12)
    s = float(np.clip(sustainment, 0.0, 1.0))
    # End-loss blow-up as field reversal weakens (tilt / open field lines).
    end_loss_factor = 1.0 + 12.0 * (1.0 - s) ** 2
    return base * end_loss_factor


def _smooth01(x: float) -> float:
    """Clamp to [0, 1] with a smoothstep edge."""
    t = max(0.0, min(1.0, x))
    return t * t * (3.0 - 2.0 * t)


def frc_nbi_sustainment(
    i_nbi: float,
    b0: float,
    *,
    i_onset_a: float = 30.0,
    i_full_a: float = 55.0,
) -> float:
    r"""Beam-driven FRC sustainment fraction in ``[0, 1]``.

    TAE forms and holds the reversed field with **neutral-beam-driven current**.
    Below ``i_onset_a`` the FRC cannot maintain reversal; above ``i_full_a`` the
    beam fully sustains flat-top. External ``B0`` tightens confinement but does
    **not** replace beam sustainment (NBI-only formation physics).
    """
    span = max(i_full_a - i_onset_a, 1.0)
    beam_hold = _smooth01((float(i_nbi) - i_onset_a) / span)
    # B0 assists τ_E and density peaking once the FRC exists, not reversal itself.
    confinement_boost = 0.70 + 0.30 * min(float(b0) / 5.0, 1.0)
    return beam_hold * confinement_boost


def frc_beam_overlap(i_nbi: float, *, i_ref: float = 120.0) -> float:
    """Effective beam–plasma overlap for beam-target fusion (trapping + density)."""
    frac = max(float(i_nbi), 0.0) / i_ref
    return min(1.0, 0.15 + 0.85 * _smooth01((frac - 0.18) / 0.55))
