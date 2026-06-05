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
Primary 2D spatial canvas.

Renders the selected reactor core as layered pyqtgraph items, bottom to top:

1. a dark gas/vacuum background,
2. a live colormap of the plasma field (potential ``Phi`` or magnetic ``B``),
3. a high-contrast overlay marking solid conductor structures,
4. macroparticle scatter overlays colored by species, and
5. persistent text labels naming each structural element.

The view uses data (metre) coordinates with a locked 1:1 aspect ratio so the
geometry is never distorted.
"""
from __future__ import annotations

import numpy as np
import numpy.typing as npt
import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

from pb11_reactor_sim.engine.base import ReactorSimulation
from pb11_reactor_sim.physics.constants import ALPHA, BORON11, ELECTRON, PROTON

pg.setConfigOption("imageAxisOrder", "row-major")
pg.setConfigOption("background", "#0a0a12")
pg.setConfigOption("foreground", "#cccccc")

FloatArray = npt.NDArray[np.float64]

#: Maximum macroparticles drawn per species (subsampled for render speed).
_MAX_DRAW = 1200

#: Species listed in the lower-left macroparticle legend.
_LEGEND_SPECIES = (PROTON, BORON11, ELECTRON, ALPHA)


class ReactorCanvas(QtWidgets.QWidget):
    """Layered 2D visualization of the active reactor core."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._glw = pg.GraphicsLayoutWidget()
        layout.addWidget(self._glw, stretch=1)

        self._subtitle_bar = QtWidgets.QLabel()
        self._subtitle_bar.setWordWrap(True)
        self._subtitle_bar.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._subtitle_bar.setStyleSheet(
            "background-color: rgba(0, 0, 0, 0.72); color: #ffffff; "
            "padding: 8px 12px; font-weight: bold;"
        )
        self._subtitle_bar.hide()
        layout.addWidget(self._subtitle_bar)

        from pb11_reactor_sim.gui.playback_cache import PlaybackFrameCache

        self._playback_cache = PlaybackFrameCache()
        self._playback_label = QtWidgets.QLabel(self._glw)
        self._playback_label.setScaledContents(False)
        self._playback_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._playback_label.hide()

        self._plot: pg.PlotItem = self._glw.addPlot()
        self._plot.setAspectLocked(True)
        self._plot.showGrid(x=True, y=True, alpha=0.15)
        self._plot.setLabel("bottom", "x", units="m")
        self._plot.setLabel("left", "y", units="m")

        # Field image (plasma colormap). Keep an explicit LUT so the very first
        # (paused) frame is colored deterministically, independent of the
        # ColorBarItem update timing.
        self._cmap = pg.colormap.get("inferno")
        self._lut = np.asarray(self._cmap.getLookupTable(0.0, 1.0, 256), dtype=np.ubyte)
        self._field_img = pg.ImageItem()
        self._field_img.setAutoLevels(False)
        self._field_img.setZValue(0)
        self._plot.addItem(self._field_img)

        # Conductor overlay (RGBA, transparent except solid cells).
        self._conductor_img = pg.ImageItem()
        self._conductor_img.setZValue(1)
        self._plot.addItem(self._conductor_img)

        # Color bar is *not* linked to the ImageItem (linking made the bar push
        # default (0, 1) levels / its own colormap onto the field and produced
        # the flat light-blue first frame on some Qt backends).
        self._cbar = pg.ColorBarItem(
            colorMap=self._cmap, values=(-1.0, 1.0), width=12, interactive=False,
        )
        self._cbar_placed = False

        # Dashed boundary outlines (created per reactor).
        self._boundary_items: list[pg.PlotDataItem] = []

        # One scatter item per species (created lazily).
        self._scatters: dict[str, pg.ScatterPlotItem] = {}

        # Persistent structure labels + macroparticle legend.
        self._label_items: list[pg.TextItem] = []
        self._legend_items: list[pg.TextItem] = []

        self._backend_text = pg.TextItem(anchor=(0, 0), color=(150, 255, 200))
        self._backend_text.setZValue(6)
        self._plot.addItem(self._backend_text)

        self._hud_text = pg.TextItem(anchor=(0, 0), color=(0, 0, 0))
        self._hud_text.setZValue(7)
        self._plot.addItem(self._hud_text)
        _hud_font = QtGui.QFont()
        _hud_font.setPointSize(12)
        _hud_font.setBold(True)
        self._hud_text.setFont(_hud_font)

        self._current_reactor: ReactorSimulation | None = None

    # -- lifecycle ----------------------------------------------------------
    def attach(self, reactor: ReactorSimulation, backend_label: str) -> None:
        """Bind a reactor: rebuild static layers (geometry, labels, scatters)."""
        self._current_reactor = reactor
        g = reactor.grid
        x0, x1, y0, y1 = g.extent

        self._draw_conductors(reactor)
        self._rebuild_boundaries(reactor)
        self._rebuild_scatters(reactor)
        self._rebuild_labels(reactor, backend_label)
        self._rebuild_legend(reactor)

        # Fit the *entire* domain. Setting the full rect (rather than X/Y
        # ranges separately) lets the aspect-locked view letterbox instead of
        # clipping, so edge structures (side walls, collectors) stay visible.
        vb = self._plot.getViewBox()
        vb.setRange(QtCore.QRectF(x0, y0, g.Lx, g.Ly), padding=0.04)
        # setImage must run before setRect (pyqtgraph requires an image first).
        self.refresh()
        self._sync_image_rect(reactor)

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        """Re-sync the field after the GL view is first realized (live GUI only)."""
        super().showEvent(event)
        QtCore.QTimer.singleShot(0, self._prime_field_display)
        QtCore.QTimer.singleShot(50, self._prime_field_display)

    def _prime_field_display(self) -> None:
        """Extra refresh passes so the first visible frame is never flat blue."""
        if self._current_reactor is None:
            return
        self.refresh()
        self._sync_image_rect(self._current_reactor)

    def _sync_image_rect(self, reactor: ReactorSimulation) -> None:
        """Map image pixel arrays onto the reactor's physical extent [m]."""
        if self._field_img.image is None:
            return
        g = reactor.grid
        rect = QtCore.QRectF(g.x0, g.y0, g.Lx, g.Ly)
        self._field_img.setRect(rect)
        self._conductor_img.setRect(rect)

    def _draw_conductors(self, reactor: ReactorSimulation) -> None:
        """Render the conductor mask as a high-contrast cyan-white overlay."""
        mask = reactor.conductor_mask
        ny, nx = mask.shape
        rgba = np.zeros((ny, nx, 4), dtype=np.ubyte)
        # Solid structures drawn as bright cyan-white, fully opaque.
        rgba[mask] = (180, 230, 255, 235)
        self._conductor_img.setImage(rgba, autoLevels=False)

    def _rebuild_boundaries(self, reactor: ReactorSimulation) -> None:
        """Draw each physical boundary as a dashed outline."""
        for item in self._boundary_items:
            self._plot.removeItem(item)
        self._boundary_items.clear()
        for b in reactor.boundaries:
            x, y = self._boundary_points(b)
            pen = pg.mkPen(color=b.color, width=2.0, style=QtCore.Qt.PenStyle.DashLine)
            item = pg.PlotDataItem(x, y, pen=pen, connect="all", antialias=True)
            item.setZValue(2)
            self._plot.addItem(item)
            self._boundary_items.append(item)

    @staticmethod
    def _boundary_points(b) -> tuple[FloatArray, FloatArray]:
        if b.shape == "circle":
            cx, cy, r = b.coords
            t = np.linspace(0.0, 2.0 * np.pi, 240)
            return cx + r * np.cos(t), cy + r * np.sin(t)
        if b.shape == "rect":
            x0, y0, x1, y1 = b.coords
            return (
                np.array([x0, x1, x1, x0, x0]),
                np.array([y0, y0, y1, y1, y0]),
            )
        x1, y1, x2, y2 = b.coords  # line
        return np.array([x1, x2]), np.array([y1, y2])

    def _rebuild_labels(self, reactor: ReactorSimulation, backend_label: str) -> None:
        for item in self._label_items:
            self._plot.removeItem(item)
        self._label_items.clear()

        font = QtGui.QFont()
        font.setPointSize(10)
        font.setBold(True)
        for lab in reactor.labels:
            # A dark semi-opaque fill + colored border gives a high-contrast
            # "halo" so labels stay readable over bright fields and dense dots.
            ti = pg.TextItem(
                lab.text,
                color=lab.color,
                anchor=lab.anchor,
                fill=pg.mkBrush(0, 0, 0, 175),
                border=pg.mkPen(lab.color, width=1),
            )
            ti.setFont(font)
            ti.setPos(lab.x, lab.y)
            ti.setAngle(lab.angle)
            ti.setZValue(5)
            self._plot.addItem(ti)
            self._label_items.append(ti)

        g = reactor.grid
        self._backend_text.setText(f"Engine: {backend_label}")
        self._backend_text.setPos(g.x0 + 0.012 * g.Lx, g.y0 + 0.018 * g.Ly)

    def _rebuild_legend(self, reactor: ReactorSimulation) -> None:
        """Lower-left key for macroparticle colors."""
        for item in self._legend_items:
            self._plot.removeItem(item)
        self._legend_items.clear()

        g = reactor.grid
        x = g.x0 + 0.012 * g.Lx
        y0 = g.y0 + 0.065 * g.Ly
        line_h = 0.028 * g.Ly

        font = QtGui.QFont()
        font.setPointSize(9)
        font.setBold(True)

        header = pg.TextItem(
            "Macroparticles",
            color=(230, 230, 230),
            anchor=(0, 0),
            fill=pg.mkBrush(0, 0, 0, 190),
            border=pg.mkPen(120, 160, 200, width=1),
        )
        header.setFont(font)
        header.setPos(x, y0 + len(_LEGEND_SPECIES) * line_h + 0.006 * g.Ly)
        header.setZValue(6)
        self._plot.addItem(header)
        self._legend_items.append(header)

        for i, sp in enumerate(_LEGEND_SPECIES):
            ti = pg.TextItem(
                f"●  {sp.name} ({sp.symbol})",
                color=sp.color,
                anchor=(0, 0),
                fill=pg.mkBrush(0, 0, 0, 175),
                border=pg.mkPen(*sp.color, width=1),
            )
            ti.setFont(font)
            ti.setPos(x, y0 + (len(_LEGEND_SPECIES) - 1 - i) * line_h)
            ti.setZValue(6)
            self._plot.addItem(ti)
            self._legend_items.append(ti)

    def _rebuild_scatters(self, reactor: ReactorSimulation) -> None:
        for s in self._scatters.values():
            self._plot.removeItem(s)
        self._scatters.clear()
        for sym, sp in reactor.species.items():
            color = sp.species.color
            scatter = pg.ScatterPlotItem(
                size=3.0,
                pen=None,
                brush=pg.mkBrush(*color, 200),
                name=sp.species.name,
            )
            scatter.setZValue(3)
            self._plot.addItem(scatter)
            self._scatters[sym] = scatter

    # -- per-frame refresh --------------------------------------------------
    def refresh(self) -> None:
        """Update the field image and particle scatters from the reactor state."""
        reactor = self._current_reactor
        if reactor is None:
            return

        field, label = reactor.display_field()
        levels = reactor.display_field_levels()
        finite = np.isfinite(field)
        if levels is None and np.any(finite):
            lo = float(np.min(field[finite]))
            hi = float(np.max(field[finite]))
            if hi <= lo:
                hi = lo + 1.0e-12
            levels = (lo, hi)
        if levels is not None and np.any(finite):
            lo, hi = levels
            self._field_img.setImage(
                field,
                autoLevels=False,
                levels=(lo, hi),
                lut=self._lut,
            )
            self._field_img.updateImage()
            if not self._cbar_placed:
                self._plot.layout.addItem(self._cbar, 2, 5)
                self._plot.layout.setColumnFixedWidth(4, 5)
                self._cbar_placed = True
            self._cbar.setLevels((lo, hi), update_items=False)
            self._cbar.setLabel("right", label)
            self._sync_image_rect(reactor)

        # Species are often empty at Arm and populated mid-shot (formation phase).
        if set(reactor.species.keys()) != set(self._scatters.keys()):
            self._rebuild_scatters(reactor)

        for sym, scatter in self._scatters.items():
            sp = reactor.species.get(sym)
            if sp is None or sp.count == 0:
                scatter.setData([], [])
                continue
            x, y = sp.x, sp.y
            if x.size > _MAX_DRAW:
                idx = np.random.default_rng((sp.count, reactor.step_index)).choice(
                    x.size, _MAX_DRAW, replace=False,
                )
                x, y = x[idx], y[idx]
            scatter.setData(x, y)

    def update_hud(
        self,
        *,
        gui_frame: int,
        substeps: int,
        speed_mode: str = "1×",
        sim_time_us: float,
        ops: str = "",
        idle: bool = False,
    ) -> None:
        """Bold overlay: GUI frame index and playback speed (1× or FF×N)."""
        reactor = self._current_reactor
        if reactor is None:
            return
        g = reactor.grid
        if idle:
            line2 = f"Ops: {ops}  —  Compile, then Play or Step"
        else:
            line2 = f"t = {sim_time_us:.3f} µs"
        self._hud_text.setText(
            f"Frame {gui_frame}   [{speed_mode}  {substeps} substeps/frame]\n{line2}"
        )
        self._hud_text.setPos(g.x0 + 0.012 * g.Lx, g.y0 + g.Ly - 0.11 * g.Ly)
        self._hud_text.setColor((0, 0, 0))
        self._hud_text.fill = pg.mkBrush(255, 255, 255, 210)
        self._hud_text.border = pg.mkPen(0, 0, 0, width=2)

    def cache_playback_frames(self, frames: list[bytes]) -> None:
        self._playback_cache.load(frames)

    def set_playback_subtitle(self, text: str | None) -> None:
        if text and text.strip():
            self._subtitle_bar.setText(text.strip())
            self._subtitle_bar.show()
        else:
            self._subtitle_bar.hide()

    def show_playback_png(self, png: bytes | None = None, *, ix: int | None = None) -> None:
        """Display a pre-rendered PNG over the live plot (compile playback)."""
        from pb11_reactor_sim.gui.playback_cache import show_cached_png

        if ix is not None and len(self._playback_cache):
            self._playback_ix = ix
            show_cached_png(
                self._playback_label,
                self._playback_cache,
                ix,
                target=self._glw.size(),
            )
            return
        if not png:
            self.end_playback()
            return
        pix = QtGui.QPixmap()
        if not pix.loadFromData(png):
            return
        scaled = pix.scaled(
            self._glw.size(),
            QtCore.Qt.AspectRatioMode.KeepAspectRatio,
            QtCore.Qt.TransformationMode.FastTransformation,
        )
        self._playback_label.setPixmap(scaled)
        self._playback_label.resize(self._glw.size())
        self._playback_label.raise_()
        self._playback_label.show()

    def is_playback_visible(self) -> bool:
        return self._playback_label.isVisible()

    def end_playback(self) -> None:
        self._playback_cache.clear()
        self._playback_label.hide()
        self._subtitle_bar.hide()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        if self._playback_label.isVisible() and len(self._playback_cache):
            from pb11_reactor_sim.gui.playback_cache import show_cached_png

            # Re-scale cached frame on resize (ix from last show — use label tag).
            ix = getattr(self, "_playback_ix", 0)
            show_cached_png(
                self._playback_label,
                self._playback_cache,
                ix,
                target=self._glw.size(),
            )

    def grab_frame_png(self) -> bytes | None:
        """Return a PNG snapshot of the plot widget (for MP4 export)."""
        pix = self._glw.grab()
        if pix.isNull():
            return None
        from PySide6 import QtCore
        buf = QtCore.QBuffer()
        buf.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
        pix.save(buf, "PNG")
        return bytes(buf.data())
