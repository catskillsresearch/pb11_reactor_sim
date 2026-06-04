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
Disk cache for ChatTTS narration clips (all reactor phase callouts).

All callout lines are fixed in :mod:`narration_scripts` (19 unique strings).
They are precomputed once at startup; live playback and export read from disk only.
"""
from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import Callable, Iterable
from pathlib import Path

import numpy as np

from pb11_reactor_sim.gui.audio_synth import SAMPLE_RATE
from pb11_reactor_sim.gui.chattts_narration import (
    CHAT_SPEED_LEVEL,
    CHAT_VOICE_SEED,
    _normalize_narration_text,
    _synthesize_chattts,
    narration_enabled,
)
from pb11_reactor_sim.gui.narration_scripts import PHASE_NARRATION

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int, str], None]


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / "pyproject.toml").is_file():
            return parent
    return here.parents[2]


def cache_dir() -> Path:
    override = os.environ.get("PB11_NARRATION_CACHE", "").strip()
    if override:
        root = Path(override).expanduser()
    else:
        root = _repo_root() / ".cache" / "narration"
    root.mkdir(parents=True, exist_ok=True)
    return root


def ensure_cache_dir() -> Path:
    """Create ``.cache/narration`` under the repository root (idempotent)."""
    base = _repo_root() / ".cache"
    base.mkdir(parents=True, exist_ok=True)
    return cache_dir()


def lines_for_reactor(reactor_name: str) -> list[str]:
    scripts = PHASE_NARRATION.get(reactor_name, {})
    seen: set[str] = set()
    out: list[str] = []
    for raw in scripts.values():
        text = _normalize_narration_text(raw)
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def all_narration_lines() -> list[str]:
    """Every unique callout string across all three reactors."""
    seen: set[str] = set()
    out: list[str] = []
    for name in PHASE_NARRATION:
        for text in lines_for_reactor(name):
            if text not in seen:
                seen.add(text)
                out.append(text)
    return out


def cache_path_for_text(text: str) -> Path:
    ensure_cache_dir()
    key = _cache_key(text)
    return cache_dir() / f"{key}.npz"


def _cache_key(text: str) -> str:
    payload = f"v1|seed={CHAT_VOICE_SEED}|speed={CHAT_SPEED_LEVEL}|sr={SAMPLE_RATE}|{text}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def load_cached_speech(text: str) -> np.ndarray | None:
    path = cache_path_for_text(text)
    if not path.is_file():
        return None
    try:
        data = np.load(path)
        samples = np.asarray(data["samples"], dtype=np.float32).reshape(-1)
        if samples.size == 0:
            return None
        return samples
    except Exception as exc:  # noqa: BLE001
        logger.warning("Narration cache read failed %s: %s", path.name, exc)
        return None


def save_cached_speech(text: str, samples: np.ndarray) -> None:
    path = cache_path_for_text(text)
    path.parent.mkdir(parents=True, exist_ok=True)
    arr = np.asarray(samples, dtype=np.float32).reshape(-1)
    staging = path.parent / f".{path.stem}.writing"
    np.savez_compressed(str(staging), samples=arr, text=text)
    written = Path(f"{staging}.npz")
    if not written.is_file():
        raise OSError(f"narration cache write failed: {written}")
    os.replace(written, path)


def synthesize_and_cache(text: str) -> np.ndarray:
    """Return speech at export sample rate; synthesize on cache miss."""
    if not text.strip():
        return np.zeros(0, dtype=np.float32)
    hit = load_cached_speech(text)
    if hit is not None:
        return hit
    from pb11_reactor_sim.gui.chattts_narration import CHAT_SAMPLE_RATE, _resample

    raw = _synthesize_chattts(text, already_normalized=True)
    speech = _resample(raw, CHAT_SAMPLE_RATE, SAMPLE_RATE)
    if speech.size:
        save_cached_speech(text, speech)
    return speech


def cache_ready_for_reactor(reactor_name: str) -> bool:
    return all(load_cached_speech(text) is not None for text in lines_for_reactor(reactor_name))


def ensure_narration_cache(
    lines: Iterable[str] | None = None,
    *,
    progress: ProgressCallback | None = None,
) -> tuple[int, int]:
    """Precompute missing clips. Returns ``(already_cached, newly_synthesized)``."""
    if not narration_enabled():
        return 0, 0

    os.environ.setdefault("TQDM_DISABLE", "1")
    ensure_cache_dir()

    todo = list(lines) if lines is not None else all_narration_lines()
    if not todo:
        return 0, 0

    cached = 0
    synthesized = 0
    total = len(todo)
    for i, text in enumerate(todo):
        if progress is not None:
            preview = text[:72].replace("[", "(").replace("]", ")")
            progress(i, total, f"({i + 1}/{total}) {preview}")
        if load_cached_speech(text) is not None:
            cached += 1
            continue
        try:
            synthesize_and_cache(text)
            synthesized += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("Narration cache miss for %r: %s", text[:60], exc)
    if progress is not None:
        progress(total, total, "done")
    return cached, synthesized
