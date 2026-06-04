# pb11_reactor_sim

An AI-assisted 2D simulation of various proton-boron fusion reactors described in open-source literature (company promo documents, patents, academic articles, etc.). 

*Note on Development: This codebase was generated using Large Language Model (LLM) AI tools. The code serves as a computational interpretation of public documentation and may contain AI-generated artifacts, structural anomalies, or mathematical hallucinations. It must be rigorously verified by the user before any application.*

## Installation notes

Requires [Poetry](https://python-poetry.org/) and Python 3.12–3.14 (see `pyproject.toml`).

```bash
# From the repository root (the directory that contains pyproject.toml):
poetry install --with simulator
./pb11_reactor_sim/run.sh
```

**Why `--with simulator`?** The GUI depends on **PySide6** (Qt). That package is in an optional Poetry group named `simulator`, not in the default install:

- `poetry install` — core numerics and export stack only (`numpy`, `scipy`, `pyqtgraph`, `pillow`, narration deps, etc.).
- `poetry install --with simulator` — also installs **PySide6**, which `python -m pb11_reactor_sim` needs for the dashboard.

Without `--with simulator`, imports such as `from PySide6 import QtWidgets` will fail.

Alternatively, after install:

```bash
poetry run python -m pb11_reactor_sim
```

Simulator architecture, controls, and physics are documented in [`pb11_reactor_sim/README.md`](pb11_reactor_sim/README.md).

**Voice callouts (ChatTTS)** need the `requests` package (pulled in via Poetry). Disk caches live under the repo **`.cache/`** tree: **`narration/`** (WAV callouts), **`compile/`** (compiled shot playback `.pkl` after **Compile**), **`optimize/`** (slider optimum JSON). Override the session tree with `PB11_SESSION_CACHE`; narration only with `PB11_NARRATION_CACHE`. Older builds may still have compile files in `~/.cache/pb11_reactor_sim/compile/` — those are read once, then rewritten under `.cache/compile/` on the next **Compile**. **Fire** opens a short progress dialog that compiles the full shot audio track (same 2× bed + 50% duck during callouts as the saved MP4); **Play** replays that track while the simulation runs. If you **Record MP4**, audio is recompiled from captured frames when the shot finishes so the export matches. Set `PB11_SKIP_NARRATION=1` for silent export/playback, or `PB11_SKIP_CACHE_WARM=1` to skip startup voice precompute.

## Contributions and Collaboration

This repository functions strictly as a unilateral broadcast of public code for educational and research purposes. 

* **Pull Requests and Issues:** This project does not accept external Pull Requests, code contributions, or modifications, and tracking features have been disabled. Any external collaboration vectors are closed.
* **Forks:** Users are entirely free and encouraged to fork or clone this repository to modify the code on their own profiles in accordance with the repository's Apache 2.0 License.

## Regulatory and Liability Disclaimer

* **Simulation and Physics Limitations:** The models provided herein (e.g., simulating proton-boron / p-B11 plasma and reactor environments) are for theoretical research and academic simulation purposes only. They do not constitute engineering specifications or operational blueprints for physical systems.
* **Liability Protection:** In accordance with Section 8 of the Apache 2.0 License, this software is provided "AS IS" without warranties of any kind. Catskills Research Company disclaims all liability for any direct, indirect, or consequential damages resulting from the use, misuse, or deployment of this simulation code.

