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

"""Play pre-compiled shot WAV through Qt Multimedia."""
from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
from PySide6 import QtCore, QtMultimedia

from pb11_reactor_sim.gui.audio_synth import write_wav


def shot_audio_enabled() -> bool:
    from pb11_reactor_sim.gui.chattts_narration import narration_enabled

    return narration_enabled()


class ShotAudioPlayer(QtCore.QObject):
    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._output = QtMultimedia.QAudioOutput(self)
        self._player = QtMultimedia.QMediaPlayer(self)
        self._player.setAudioOutput(self._output)
        self._wav_path: Path | None = None
        self._samples: np.ndarray | None = None

    def load(self, mixed: np.ndarray) -> None:
        self.stop()
        self._samples = np.asarray(mixed, dtype=np.float32).reshape(-1)
        fd, raw = tempfile.mkstemp(prefix="pb11_shot_", suffix=".wav")
        path = Path(raw)
        import os

        os.close(fd)
        write_wav(path, self._samples)
        self._wav_path = path
        self._player.setSource(QtCore.QUrl.fromLocalFile(str(path)))

    def play(self, *, start_ms: int = 0, end_ms: int | None = None) -> None:
        if self._player.source().isEmpty():
            return
        self._segment_end_ms = end_ms
        self._player.setPosition(max(0, start_ms))
        self._player.play()

    def segment_end_ms(self) -> int | None:
        return getattr(self, "_segment_end_ms", None)

    def set_position_ms(self, ms: int) -> None:
        if self._player.source().isEmpty():
            return
        self._player.setPosition(max(0, ms))

    def position_ms(self) -> int:
        return int(self._player.position())

    def replay(self) -> None:
        self.play()

    def pause(self) -> None:
        """Pause without resetting position (end of core speech, frames continue)."""
        self._player.pause()

    def stop(self) -> None:
        """Stop playback but keep the loaded WAV for segment seeks."""
        self._segment_end_ms = None
        self._player.stop()
        self._player.setPosition(0)

    def cleanup(self) -> None:
        self._player.stop()
        self._player.setSource(QtCore.QUrl())
        if self._wav_path is not None and self._wav_path.is_file():
            self._wav_path.unlink(missing_ok=True)
        self._wav_path = None
        self._samples = None
