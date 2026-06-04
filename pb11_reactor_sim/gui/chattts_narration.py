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
ChatTTS voice synthesis for reactor shot MP4 export.

Narration timing is owned by :mod:`export_timeline` (synthesize first, then
extend video). This module handles text cleanup and speech generation.
"""
from __future__ import annotations

import logging
import os
import re

import numpy as np

from pb11_reactor_sim.gui.audio_synth import SAMPLE_RATE

logger = logging.getLogger(__name__)

CHAT_SAMPLE_RATE = 24_000
CHAT_VOICE_SEED = 1983
CHAT_SPEED_LEVEL = 1
#: Mandatory pause after each phase callout [s] (8 segments → +12 s minimum).
POST_PAUSE_S = 1.5

_CHAT_STATE: dict[str, object] = {}

_DIGIT_WORDS = {
    "0": "zero",
    "1": "one",
    "2": "two",
    "3": "three",
    "4": "four",
    "5": "five",
    "6": "six",
    "7": "seven",
    "8": "eight",
    "9": "nine",
}


def narration_enabled() -> bool:
    return os.environ.get("PB11_SKIP_NARRATION", "").strip().lower() not in ("1", "true", "yes")


def phase_segments(meta) -> list[tuple[str, int, int]]:
    """Return ``(phase_key, start_frame, end_frame_exclusive)`` runs."""
    if not meta:
        return []
    out: list[tuple[str, int, int]] = []
    cur = meta[0].phase
    start = 0
    for i, m in enumerate(meta[1:], 1):
        if m.phase != cur:
            out.append((cur, start, i))
            cur = m.phase
            start = i
    out.append((cur, start, len(meta)))
    return out


def sanitize_narration_line(text: str) -> str:
    """Display/TTS safe line: no hyphens, collapsed whitespace."""
    t = text.replace("-", " ").replace("_", " ")
    t = re.sub(r"\s+", " ", t).strip()
    return t


def wrap_subtitle_lines(text: str, *, max_chars: int = 54) -> list[str]:
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    cur: list[str] = []
    n = 0
    for w in words:
        add = len(w) + (1 if cur else 0)
        if cur and n + add > max_chars:
            lines.append(" ".join(cur))
            cur = [w]
            n = len(w)
        else:
            cur.append(w)
            n += add
    if cur:
        lines.append(" ".join(cur))
    return lines


def synthesize_speech(text: str) -> np.ndarray:
    """Return mono float32 speech at :data:`SAMPLE_RATE` (empty if narration disabled)."""
    if not narration_enabled():
        return np.zeros(0, dtype=np.float32)
    normalized = _normalize_narration_text(text)
    if not normalized:
        return np.zeros(0, dtype=np.float32)
    try:
        from pb11_reactor_sim.gui.narration_cache import synthesize_and_cache

        return synthesize_and_cache(normalized)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ChatTTS skipped for %r: %s", text[:60], exc)
        return np.zeros(0, dtype=np.float32)


def _chattts_state() -> dict[str, object]:
    state = _CHAT_STATE.get("state")
    if isinstance(state, dict):
        return state

    import ChatTTS  # type: ignore[import-untyped]
    import torch

    chat = ChatTTS.Chat()
    if not chat.load(source="huggingface"):
        raise RuntimeError("ChatTTS model load failed")
    torch.manual_seed(CHAT_VOICE_SEED)
    spk_emb = chat.sample_random_speaker()
    infer = chat.InferCodeParams(spk_emb=spk_emb, prompt=f"[speed_{CHAT_SPEED_LEVEL}]")
    state = {"chat": chat, "infer": infer}
    _CHAT_STATE["state"] = state
    return state


def _int_to_words(n: int) -> str:
    units = (
        "zero",
        "one",
        "two",
        "three",
        "four",
        "five",
        "six",
        "seven",
        "eight",
        "nine",
        "ten",
        "eleven",
        "twelve",
        "thirteen",
        "fourteen",
        "fifteen",
        "sixteen",
        "seventeen",
        "eighteen",
        "nineteen",
    )
    tens = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")
    if n < 20:
        return units[n]
    if n < 100:
        t, u = divmod(n, 10)
        return tens[t] if u == 0 else f"{tens[t]} {units[u]}"
    if n < 1000:
        h, r = divmod(n, 100)
        return f"{units[h]} hundred" if r == 0 else f"{units[h]} hundred {_int_to_words(r)}"
    if n < 10000:
        th, r = divmod(n, 1000)
        return f"{units[th]} thousand" if r == 0 else f"{units[th]} thousand {_int_to_words(r)}"
    return " ".join(_DIGIT_WORDS[d] for d in str(n))


def _normalize_narration_text(note: str) -> str:
    txt = sanitize_narration_line(note)

    def repl(m: re.Match[str]) -> str:
        token = m.group(0)
        if len(token) > 1 and token.startswith("0"):
            return " ".join(_DIGIT_WORDS[d] for d in token)
        try:
            val = int(token)
        except ValueError:
            return token
        if 0 <= val < 10000:
            return _int_to_words(val)
        return " ".join(_DIGIT_WORDS[d] for d in token)

    txt = re.sub(r"\d+", repl, txt)
    txt = re.sub(r"\s+", " ", txt).strip()
    txt = re.sub(r"([.!?])\s*", r"\1 [uv_break] ", txt).strip()
    return re.sub(r"\s+", " ", txt)


def _synthesize_chattts(note: str) -> np.ndarray:
    state = _chattts_state()
    chat = state["chat"]
    infer = state["infer"]
    normalized = _normalize_narration_text(note)
    wavs = chat.infer([normalized], params_infer_code=infer)
    wav = np.asarray(wavs[0], dtype=np.float32).squeeze()
    if wav.ndim != 1:
        wav = wav.reshape(-1)
    return wav


def _resample(wav: np.ndarray, from_rate: int, to_rate: int) -> np.ndarray:
    if from_rate == to_rate or wav.size == 0:
        return wav.astype(np.float32, copy=False)
    try:
        from scipy import signal

        n_out = int(round(wav.size * to_rate / from_rate))
        return signal.resample(wav, n_out).astype(np.float32)
    except ImportError:
        x_old = np.linspace(0.0, 1.0, wav.size, dtype=np.float64)
        n_out = int(round(wav.size * to_rate / from_rate))
        x_new = np.linspace(0.0, 1.0, n_out, dtype=np.float64)
        return np.interp(x_new, x_old, wav.astype(np.float64)).astype(np.float32)
