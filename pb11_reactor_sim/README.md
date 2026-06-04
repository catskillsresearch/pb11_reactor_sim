# p-11B Reactor Core Simulator

Interactive 2D core-slice simulator and visualizer for three proton-boron-11
(p-11B) aneutronic fusion reactor concepts:

| Model | Concept | Controls |
|-------|---------|----------|
| **TAE FRC** | TAE Technologies Field-Reversed Configuration | NBI current, background `B0` |
| **HB11 Laser** | HB11 Energy laser-driven block ignition | Laser intensity, collector grid voltage (to 3 MV) |
| **LPP DPF** | LPPFusion Dense Plasma Focus | Capacitor-bank voltage, gas pressure |

A PySide6 + pyqtgraph dashboard renders a live 2D core slice (field colormap,
solid structures, labeled boundaries, species-colored macroparticles) alongside
real-time 1D diagnostics (`T_i`/`T_e`, power balance, net gain `Q`).

## Running

```bash
# From the repository root (activates the Poetry env):
./pb11_reactor_sim/run.sh
```

Or, with the environment already configured:

```bash
poetry install --with simulator
poetry run python -m pb11_reactor_sim
```

### Physics engine

The interactive engine is a self-consistent, fully vectorized scipy PIC field
solve: cloud-in-cell deposition, sparse 5-point Poisson with Dirichlet conductor
masks, and Boris / RK4 pushers.

## Architecture

```
pb11_reactor_sim/
  physics/
    constants.py     # scipy.constants, per-species data
    processes.py     # Bremsstrahlung, ion-electron relaxation, p-11B reactivity,
                     # fusion power, Q_net, magnetic Brems suppression
  engine/
    poisson.py       # scipy.sparse 5-point Poisson w/ Dirichlet conductor masks
    particles.py     # vectorized macroparticles: CIC deposit/gather, Boris/RK4
    base.py          # Grid, ControlSpec, Diagnostics, ReactorSimulation (ABC)
    pic_backend.py   # FieldSolveBackend + scipy Poisson engine
  reactors/
    tae.py           # TAEReactor   (FRC profiles, ICC collector)
    hb11.py          # HB11Reactor  (ponderomotive drive, grid deceleration)
    lpp.py           # LPPReactor   (snowplow sheath, B-field Brems suppression)
  gui/
    canvas.py        # 2D spatial canvas (field + conductors + particles + labels)
    diagnostics.py   # linked 1D real-time plots
    controls.py      # reactor dropdown, dynamic sliders, transport buttons
  app.py             # PlasmaSimApp(QMainWindow) + QTimer simulation loop
  __main__.py        # python -m pb11_reactor_sim
  run.sh             # Poetry launcher
```

### Core process equations (per timestep)

* **Relativistic Bremsstrahlung:**
  `P_Br = 1.57e-40 Z_eff^2 n_e^2 sqrt(T_e) (1 + 1.71 T_e/(m_e c^2))`
* **Ion-electron relaxation:** `P_ie = (3/2) n_e (T_i - T_e)/tau_ie`,
  `tau_ie ~ T_e^1.5 / (n_i Z_i^2 lnLambda)`
* **p-11B fusion power:** `P_f = n_p n_B <sigma v> E_f`, `E_f = 8.7 MeV`,
  with a log-parabola `<sigma v>` fit peaking near `T_i ~ 300 keV`
* **Net gain:** `Q = P_f / (P_Br + P_cond)`

Temperatures are in keV, densities in m^-3, power densities in W/m^3.

## Dependencies

Provided by the repository Poetry environment: `PySide6`, `pyqtgraph`, `numpy`,
`scipy`.
