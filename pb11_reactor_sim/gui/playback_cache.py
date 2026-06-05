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

"""Decode compiled PNG frames once; fast indexed playback."""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets


class PlaybackFrameCache:
    """Hold decoded (and optionally scaled) pixmaps for compile review."""

    def __init__(self) -> None:
        self._raw: list[QtGui.QPixmap] = []
        self._scaled: list[QtGui.QPixmap] = []
        self._scaled_size = QtCore.QSize()

    def clear(self) -> None:
        self._raw.clear()
        self._scaled.clear()
        self._scaled_size = QtCore.QSize()

    def load(self, frames: list[bytes]) -> None:
        self.clear()
        for png in frames:
            pix = QtGui.QPixmap()
            if pix.loadFromData(png):
                self._raw.append(pix)

    def __len__(self) -> int:
        return len(self._raw)

    def _ensure_scaled(self, size: QtCore.QSize) -> None:
        if size.isEmpty() or not self._raw:
            return
        if size == self._scaled_size and len(self._scaled) == len(self._raw):
            return
        self._scaled = [
            pix.scaled(
                size,
                QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                QtCore.Qt.TransformationMode.FastTransformation,
            )
            for pix in self._raw
        ]
        self._scaled_size = size

    def pixmap(self, ix: int, *, target: QtCore.QSize) -> QtGui.QPixmap | None:
        if ix < 0 or ix >= len(self._raw):
            return None
        self._ensure_scaled(target)
        if ix < len(self._scaled):
            return self._scaled[ix]
        return self._raw[ix]


def show_cached_png(
    label: QtWidgets.QLabel,
    cache: PlaybackFrameCache,
    ix: int,
    *,
    target: QtCore.QSize,
) -> None:
    pix = cache.pixmap(ix, target=target)
    if pix is None or pix.isNull():
        label.hide()
        return
    label.setPixmap(pix)
    label.resize(target)
    label.raise_()
    label.show()
