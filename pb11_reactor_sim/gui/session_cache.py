# ==============================================================================
# Copyright 2026 Catskills Research Company
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================

"""Session + on-disk cache for optimize results and compiled shot playback."""
from __future__ import annotations

import hashlib
import json
import logging
import pickle
import re
from pathlib import Path

from pb11_reactor_sim.engine.optimizer import OptimizeResult
from pb11_reactor_sim.gui.shot_compiler import CompiledPlayback

logger = logging.getLogger(__name__)

_OPTIMIZE: dict[str, OptimizeResult] = {}
_COMPILE: dict[str, CompiledPlayback] = {}

_CACHE_ROOT = Path.home() / ".cache" / "pb11_reactor_sim"
_OPTIMIZE_DIR = _CACHE_ROOT / "optimize"
_COMPILE_DIR = _CACHE_ROOT / "compile"


def controls_fingerprint(
    reactor_name: str,
    controls: dict[str, float],
    *,
    from_quiescent: bool,
) -> str:
    """Stable hash of reactor + slider values (+ quiescent re-fire flag)."""
    payload = {
        "reactor": reactor_name,
        "from_quiescent": from_quiescent,
        "controls": {k: round(float(v), 6) for k, v in sorted(controls.items())},
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def controls_near(a: dict[str, float], b: dict[str, float], *, rtol: float = 1e-3) -> bool:
    keys = set(a) | set(b)
    for k in keys:
        if k not in a or k not in b:
            return False
        if abs(float(a[k]) - float(b[k])) > rtol * max(1.0, abs(float(b[k]))):
            return False
    return True


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_") or "reactor"


def _load_optimize_disk(reactor_name: str) -> OptimizeResult | None:
    path = _OPTIMIZE_DIR / f"{_slug(reactor_name)}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return OptimizeResult(
            controls={k: float(v) for k, v in data["controls"].items()},
            q_net=float(data["q_net"]),
            n_evaluations=int(data["n_evaluations"]),
        )
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        logger.warning("Could not load optimize cache %s: %s", path, exc)
        return None


def _save_optimize_disk(reactor_name: str, result: OptimizeResult) -> None:
    path = _OPTIMIZE_DIR / f"{_slug(reactor_name)}.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "controls": {k: round(float(v), 6) for k, v in sorted(result.controls.items())},
            "q_net": result.q_net,
            "n_evaluations": result.n_evaluations,
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not write optimize cache %s: %s", path, exc)


def get_optimize(reactor_name: str) -> OptimizeResult | None:
    hit = _OPTIMIZE.get(reactor_name)
    if hit is not None:
        return hit
    disk = _load_optimize_disk(reactor_name)
    if disk is not None:
        _OPTIMIZE[reactor_name] = disk
    return disk


def put_optimize(reactor_name: str, result: OptimizeResult) -> None:
    _OPTIMIZE[reactor_name] = result
    _save_optimize_disk(reactor_name, result)


def _load_compile_disk(fingerprint: str) -> CompiledPlayback | None:
    path = _COMPILE_DIR / f"{fingerprint}.pkl"
    if not path.is_file():
        return None
    try:
        with path.open("rb") as fh:
            obj = pickle.load(fh)
        if isinstance(obj, CompiledPlayback):
            return obj
    except (OSError, pickle.PickleError, TypeError) as exc:
        logger.warning("Could not load compile cache %s: %s", path, exc)
    return None


def _save_compile_disk(fingerprint: str, playback: CompiledPlayback) -> None:
    path = _COMPILE_DIR / f"{fingerprint}.pkl"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as fh:
            pickle.dump(playback, fh, protocol=pickle.HIGHEST_PROTOCOL)
    except OSError as exc:
        logger.warning("Could not write compile cache %s: %s", path, exc)


def get_compile(fingerprint: str) -> CompiledPlayback | None:
    hit = _COMPILE.get(fingerprint)
    if hit is not None:
        return hit
    disk = _load_compile_disk(fingerprint)
    if disk is not None:
        _COMPILE[fingerprint] = disk
    return disk


def put_compile(fingerprint: str, playback: CompiledPlayback) -> None:
    _COMPILE[fingerprint] = playback
    _save_compile_disk(fingerprint, playback)


def lookup_compile(
    reactor_name: str,
    controls: dict[str, float],
    *,
    from_quiescent: bool,
) -> tuple[CompiledPlayback | None, str | None]:
    """Return cached compile for current controls, trying both shot entry modes."""
    for fq in (from_quiescent, not from_quiescent):
        fp = controls_fingerprint(reactor_name, controls, from_quiescent=fq)
        hit = get_compile(fp)
        if hit is not None:
            return hit, fp
    return None, None
