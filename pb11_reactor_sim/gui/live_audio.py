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
Live reactor bed + narration during interactive Play.

Uses cached ChatTTS clips and procedural per-frame bed audio from :mod:`audio_synth`.
Requires ``PySide6.QtMultimedia`` (included with the simulator extra).
"""
from __future__ import annotations

import logging
import os

import numpy as np

from pb11_reactor_sim.gui.audio_synth import (
    FPS,
    SAMPLE_RATE,
    FrameMeta,
    mix_tracks,
    pad_audio,
)
from pb11_reactor_sim.gui.chattts_narration import POST_PAUSE_S, narration_enabled
from pb11_reactor_sim.gui.export_timeline import BED_DUCK_FACTOR, BED_LEVEL
from pb11_reactor_sim.gui.narration_cache import load_cached_speech
from pb11_reactor_sim.gui.narration_scripts import PHASE_NARRATION

logger = logging.getLogger(__name__)


def live_audio_enabled() -> bool:
    if os.environ.get("PB11_SKIP_LIVE_AUDIO", "").strip().lower() in ("1", "true", "yes"):
        return False
    return narration_enabled()


class LiveShotAudio:
    """Stream mixed bed + voice to the default output device while the shot runs."""

    def __init__(self, *, samples_per_tick: int) -> None:
        self._n = max(256, samples_per_tick)
        self._sink = None
        self._io = None
        self._active = False
        self._reactor_name = ""
        self._last_phase = ""
        self._voice: np.ndarray = np.zeros(0, dtype=np.float32)
        self._voice_pos = 0
        self._duck_until = 0
        self._stream_samples = 0
        self._frame_index = 0

    @property
    def active(self) -> bool:
        return self._active

    def set_reactor(self, name: str) -> None:
        self._reactor_name = name
        self._reset_voice()

    def play_phase_callout(self, phase: str) -> bool:
        """Play one cached callout immediately (e.g. ``armed`` on Arm shot)."""
        if not live_audio_enabled() or not self._ensure_sink():
            return False
        scripts = PHASE_NARRATION.get(self._reactor_name, {})
        raw = scripts.get(phase)
        if not raw:
            return False
        from pb11_reactor_sim.gui.chattts_narration import _normalize_narration_text

        speech = load_cached_speech(_normalize_narration_text(raw))
        if speech is None or speech.size == 0:
            logger.info("Callout not cached for phase %s", phase)
            return False
        self._push_pcm(speech)
        return True

    def start(self) -> None:
        if not live_audio_enabled():
            return
        if not self._ensure_sink():
            return
        self._active = True
        self._reset_voice()

    def stop(self) -> None:
        self._active = False
        self._reset_voice()
        if self._sink is not None:
            self._sink.stop()
        self._io = None

    def feed(
        self,
        *,
        phase: str,
        fast_forward: bool,
        intensity: float,
    ) -> None:
        if not self._active or self._io is None:
            return

        from pb11_reactor_sim.gui.audio_synth import synthesize_frame_chunk

        if phase != self._last_phase:
            self._on_phase_change(phase)
            self._last_phase = phase

        meta = FrameMeta(phase=phase, fast_forward=fast_forward, intensity=intensity)
        bed = synthesize_frame_chunk(meta, frame_index=self._frame_index)
        self._frame_index += 1

        voice = self._voice_slice(len(bed))
        duck = self._stream_samples < self._duck_until
        mask = np.full(len(bed), 1.0 if duck else 0.0, dtype=np.float32)
        mixed = mix_tracks(
            bed,
            voice,
            bed_level=BED_LEVEL,
            duck_with_voice=BED_DUCK_FACTOR,
            voice_mask=mask,
        )
        self._push_pcm(mixed)
        self._stream_samples += len(bed)

    def _on_phase_change(self, phase: str) -> None:
        scripts = PHASE_NARRATION.get(self._reactor_name, {})
        raw = scripts.get(phase)
        if not raw:
            return
        from pb11_reactor_sim.gui.chattts_narration import _normalize_narration_text

        text = _normalize_narration_text(raw)
        speech = load_cached_speech(text)
        if speech is None:
            logger.info(
                "Live callout not ready for phase %s (run startup voice prep or wait for cache)",
                phase,
            )
            return
        self._voice = speech
        self._voice_pos = 0
        pause_samples = int(round(POST_PAUSE_S * SAMPLE_RATE))
        self._duck_until = self._stream_samples + speech.size + pause_samples

    def _voice_slice(self, n: int) -> np.ndarray:
        if self._voice_pos >= self._voice.size:
            return np.zeros(n, dtype=np.float32)
        end = min(self._voice.size, self._voice_pos + n)
        chunk = self._voice[self._voice_pos : end]
        self._voice_pos = end
        return pad_audio(chunk, n)

    def _reset_voice(self) -> None:
        self._last_phase = ""
        self._voice = np.zeros(0, dtype=np.float32)
        self._voice_pos = 0
        self._duck_until = 0
        self._stream_samples = 0
        self._frame_index = 0

    def _ensure_sink(self) -> bool:
        if self._sink is not None:
            return True
        try:
            from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices
        except ImportError:
            logger.warning("QtMultimedia not available; live audio disabled")
            return False

        device = QMediaDevices.defaultAudioOutput()
        if device.isNull():
            logger.warning("No audio output device; live audio disabled")
            return False

        fmt = QAudioFormat()
        fmt.setSampleRate(SAMPLE_RATE)
        fmt.setChannelCount(1)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        self._sink = QAudioSink(device, fmt)
        self._io = self._sink.start()
        return self._io is not None

    def _push_pcm(self, samples: np.ndarray) -> None:
        if self._io is None:
            return
        from PySide6 import QtCore

        pcm = np.clip(samples, -1.0, 1.0)
        pcm_i16 = (pcm * 32767.0).astype(np.int16)
        data = pcm_i16.tobytes()
        offset = 0
        while offset < len(data):
            n = self._io.write(data[offset:])
            if n < 0:
                break
            if n == 0:
                QtCore.QCoreApplication.processEvents()
                continue
            offset += n
