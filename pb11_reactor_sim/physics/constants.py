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
Physical constants and per-species data for the p-11B reactor simulator.

All values are SI unless a name explicitly carries another unit suffix
(``_keV``, ``_MeV``, ``_kV``, ``_MV``, ...). Constants are pulled from
``scipy.constants`` where possible so the whole package shares one source of
truth.
"""
from __future__ import annotations

from dataclasses import dataclass

import scipy.constants as sc

# --- Fundamental constants (SI) ---------------------------------------------
ELEMENTARY_CHARGE: float = sc.elementary_charge          # C
ELECTRON_MASS: float = sc.electron_mass                  # kg
PROTON_MASS: float = sc.proton_mass                      # kg
ATOMIC_MASS: float = sc.atomic_mass                      # kg (unified amu)
SPEED_OF_LIGHT: float = sc.speed_of_light                # m / s
VACUUM_PERMITTIVITY: float = sc.epsilon_0                # F / m
VACUUM_PERMEABILITY: float = sc.mu_0                     # H / m
BOLTZMANN: float = sc.Boltzmann                          # J / K

# --- Convenient unit conversions --------------------------------------------
KEV_TO_JOULE: float = 1.0e3 * ELEMENTARY_CHARGE          # J per keV
MEV_TO_JOULE: float = 1.0e6 * ELEMENTARY_CHARGE          # J per MeV
JOULE_TO_KEV: float = 1.0 / KEV_TO_JOULE
KELVIN_TO_KEV: float = BOLTZMANN / KEV_TO_JOULE          # multiply T[K] -> keV

# Electron rest energy expressed in keV; used by the relativistic Bremsstrahlung
# correction term (T_e / (m_e c^2)).
ELECTRON_REST_ENERGY_KEV: float = (
    ELECTRON_MASS * SPEED_OF_LIGHT * SPEED_OF_LIGHT * JOULE_TO_KEV
)  # ~511 keV

# --- p-11B reaction energetics ----------------------------------------------
# p + 11B -> 3 alpha + 8.7 MeV (aneutronic headline channel).
PB11_REACTION_ENERGY_MEV: float = 8.7
PB11_REACTION_ENERGY_J: float = PB11_REACTION_ENERGY_MEV * MEV_TO_JOULE


@dataclass(frozen=True)
class Species:
    """Static data describing one simulated macro-species.

    Attributes
    ----------
    name:
        Human readable label (used in legends and diagnostics).
    symbol:
        Short key (``"p"``, ``"B"``, ``"alpha"``, ``"e"``).
    charge:
        Signed charge in Coulombs.
    mass:
        Particle mass in kilograms.
    color:
        RGB triple (0-255) used by the GUI scatter overlay.
    z_number:
        Charge number Z (used by ``Z_eff`` and Bremsstrahlung sums).
    """

    name: str
    symbol: str
    charge: float
    mass: float
    color: tuple[int, int, int]
    z_number: int


PROTON = Species(
    name="Proton",
    symbol="p",
    charge=+ELEMENTARY_CHARGE,
    mass=PROTON_MASS,
    color=(220, 50, 50),  # Red
    z_number=1,
)

BORON11 = Species(
    name="Boron-11",
    symbol="B",
    charge=+5.0 * ELEMENTARY_CHARGE,
    mass=11.009305 * ATOMIC_MASS,
    color=(60, 200, 90),  # Green
    z_number=5,
)

ALPHA = Species(
    name="Alpha",
    symbol="alpha",
    charge=+2.0 * ELEMENTARY_CHARGE,
    mass=4.002602 * ATOMIC_MASS,
    color=(240, 220, 60),  # Yellow
    z_number=2,
)

ELECTRON = Species(
    name="Electron",
    symbol="e",
    charge=-ELEMENTARY_CHARGE,
    mass=ELECTRON_MASS,
    color=(70, 130, 240),  # Blue
    z_number=-1,
)

#: Canonical lookup of all species by short symbol.
SPECIES_BY_SYMBOL: dict[str, Species] = {
    s.symbol: s for s in (PROTON, BORON11, ALPHA, ELECTRON)
}
