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
1D diagnostic panel: three linked real-time line charts.

* Temperatures: ``T_i`` and ``T_e`` versus time.
* Core power balance: ``P_fusion`` vs ``P_Bremsstrahlung`` vs ``P_conduction``
  (log-scaled, W/m^3).
* Net gain: real-time ``Q_net`` ratio (log-scaled).

All three share the time axis. The panel reads directly from a reactor's
:class:`~pb11_reactor_sim.engine.base.Diagnostics` buffers on each refresh.
"""
from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

from pb11_reactor_sim.engine.base import Diagnostics


class DiagnosticsPanel(QtWidgets.QWidget):
    """Stack of three linked pyqtgraph plots fed from reactor diagnostics."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(4)

        pg.setConfigOptions(antialias=True)

        # --- Temperatures ---
        self.temp_plot = pg.PlotWidget(title="Ion / Electron Temperature")
        self.temp_plot.setLabel("left", "T", units="keV")
        self.temp_plot.setLabel("bottom", "time", units="us")
        self.temp_plot.addLegend(offset=(10, 5))
        self.temp_plot.showGrid(x=True, y=True, alpha=0.2)
        self.curve_ti = self.temp_plot.plot(pen=pg.mkPen("#ff6464", width=2), name="T_i")
        self.curve_te = self.temp_plot.plot(pen=pg.mkPen("#6496ff", width=2), name="T_e")
        layout.addWidget(self.temp_plot)

        # --- Power balance ---
        self.power_plot = pg.PlotWidget(title="Core Power Balance")
        self.power_plot.setLabel("left", "P", units="W/m^3")
        self.power_plot.setLabel("bottom", "time", units="us")
        self.power_plot.setLogMode(x=False, y=True)
        self.power_plot.addLegend(offset=(10, 5))
        self.power_plot.showGrid(x=True, y=True, alpha=0.2)
        self.curve_pf = self.power_plot.plot(pen=pg.mkPen("#ffd23c", width=2), name="P_fusion")
        self.curve_pb = self.power_plot.plot(pen=pg.mkPen("#ff7be0", width=2), name="P_Brems")
        self.curve_pc = self.power_plot.plot(pen=pg.mkPen("#7bffb0", width=2), name="P_cond")
        layout.addWidget(self.power_plot)

        # --- Q_net ---
        self.q_plot = pg.PlotWidget(title="Net Gain  Q_sys / Q_plasma")
        self.q_plot.setLabel("left", "Q")
        self.q_plot.setLabel("bottom", "time", units="us")
        self.q_plot.setLogMode(x=False, y=True)
        self.q_plot.addLegend(offset=(10, 5))
        self.q_plot.showGrid(x=True, y=True, alpha=0.2)
        self.curve_q = self.q_plot.plot(pen=pg.mkPen("#ffffff", width=2), name="Q_sys")
        self.curve_q_plasma = self.q_plot.plot(
            pen=pg.mkPen("#ffd23c", width=1.5, style=pg.QtCore.Qt.PenStyle.DashLine),
            name="Q_plasma",
        )
        self.q_line = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen("#888", style=pg.QtCore.Qt.PenStyle.DashLine))
        self.q_line.setPos(0.0)  # log10(Q)=0 -> Q=1 breakeven
        self.q_plot.addItem(self.q_line)
        layout.addWidget(self.q_plot)

        self.setMinimumWidth(360)
        self._axis_locked = False

    def set_limits_from_peaks(
        self,
        *,
        t_us: float,
        ti_kev: float,
        te_kev: float,
        p_w_m3: float,
        q_max: float,
    ) -> None:
        """Lock plot axes so compile/playback frames do not rescale between grabs."""
        t_hi = max(5.0, t_us * 1.12)
        t_y = max(0.3, ti_kev, te_kev) * 1.15
        p_lo = max(1.0e-30, p_w_m3 * 1.0e-6)
        p_hi = max(1.0e-20, p_w_m3 * 12.0)
        q_lo = max(1.0e-30, 1.0e-3)
        q_hi = max(1.0, q_max * 12.0)

        for plot in (self.temp_plot, self.power_plot, self.q_plot):
            plot.enableAutoRange(enable=False)

        self.temp_plot.setXRange(0.0, t_hi, padding=0.02)
        self.temp_plot.setYRange(0.0, t_y, padding=0.02)
        self.power_plot.setXRange(0.0, t_hi, padding=0.02)
        self.power_plot.setYRange(p_lo, p_hi, padding=0.02)
        self.q_plot.setXRange(0.0, t_hi, padding=0.02)
        self.q_plot.setYRange(q_lo, q_hi, padding=0.02)
        self._axis_locked = True

    def unlock_axis_limits(self) -> None:
        self._axis_locked = False
        for plot in (self.temp_plot, self.power_plot, self.q_plot):
            plot.enableAutoRange(enable=True)

    def clear(self) -> None:
        """Remove all curves (e.g. after Reset)."""
        for curve in (
            self.curve_ti,
            self.curve_te,
            self.curve_pf,
            self.curve_pb,
            self.curve_pc,
            self.curve_q,
            self.curve_q_plasma,
        ):
            curve.setData([], [])

    def update_from(self, diag: Diagnostics) -> None:
        """Refresh all three plots from the reactor's diagnostic buffers."""
        if not diag.time:
            self.clear()
            return
        t = np.asarray(diag.time)
        self.curve_ti.setData(t, np.asarray(diag.T_i))
        self.curve_te.setData(t, np.asarray(diag.T_e))

        # Power balance (guard against log of zero).
        eps = 1.0e-30
        self.curve_pf.setData(t, np.maximum(np.asarray(diag.p_fusion), eps))
        self.curve_pb.setData(t, np.maximum(np.asarray(diag.p_brems), eps))
        self.curve_pc.setData(t, np.maximum(np.asarray(diag.p_cond), eps))

        self.curve_q.setData(t, np.maximum(np.asarray(diag.q_net), eps))
        self.curve_q_plasma.setData(t, np.maximum(np.asarray(diag.q_plasma), eps))

    def __init_playback_overlay(self) -> None:
        if hasattr(self, "_playback_label"):
            return
        from pb11_reactor_sim.gui.playback_cache import PlaybackFrameCache

        self._playback_cache = PlaybackFrameCache()
        self._playback_label = QtWidgets.QLabel(self)
        self._playback_label.setScaledContents(False)
        self._playback_label.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignHCenter | QtCore.Qt.AlignmentFlag.AlignBottom
        )

    def cache_playback_frames(self, frames: list[bytes]) -> None:
        self.__init_playback_overlay()
        self._playback_cache.load(frames)

    def show_playback_png(self, png: bytes | None = None, *, ix: int | None = None) -> None:
        from pb11_reactor_sim.gui.playback_cache import show_cached_png

        self.__init_playback_overlay()
        if ix is not None and len(self._playback_cache):
            show_cached_png(self._playback_label, self._playback_cache, ix, target=self.size())
            return
        if not png:
            self.end_playback()
            return
        pix = QtGui.QPixmap()
        if not pix.loadFromData(png):
            return
        scaled = pix.scaled(
            self.size(),
            QtCore.Qt.AspectRatioMode.KeepAspectRatio,
            QtCore.Qt.TransformationMode.FastTransformation,
        )
        self._playback_label.setPixmap(scaled)
        self._playback_label.resize(self.size())
        self._playback_label.raise_()
        self._playback_label.show()

    def end_playback(self) -> None:
        if hasattr(self, "_playback_cache"):
            self._playback_cache.clear()
        if hasattr(self, "_playback_label"):
            self._playback_label.hide()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        if hasattr(self, "_playback_label") and self._playback_label.isVisible():
            self._playback_label.resize(self.size())

    def grab_frame_png(self) -> bytes | None:
        """Return a PNG snapshot of the diagnostic charts (for MP4 export)."""
        from PySide6 import QtCore

        pix = self.grab()
        if pix.isNull():
            return None
        buf = QtCore.QBuffer()
        buf.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
        pix.save(buf, "PNG")
        return bytes(buf.data())
