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
Control panel widget: reactor selector, dynamic sliders, transport buttons.

The panel rebuilds its slider stack whenever the active reactor changes (each
reactor declares its own :class:`~pb11_reactor_sim.engine.base.ControlSpec`
list). Slider movements are debounced into a single float dictionary and emitted
via the ``controlsChanged`` signal.
"""
from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from pb11_reactor_sim.engine.base import ControlSpec

_SLIDER_TICKS = 1000


class _LabeledSlider(QtWidgets.QWidget):
    """A horizontal slider with a name + live value readout, mapping to a float."""

    valueChanged = QtCore.Signal(str, float)

    def __init__(self, spec: ControlSpec, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.spec = spec
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(1)

        self._header = QtWidgets.QLabel()
        self._header.setStyleSheet("color: #ddd; font-weight: 600;")
        layout.addWidget(self._header)

        self._slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self._slider.setMinimum(0)
        self._slider.setMaximum(_SLIDER_TICKS)
        self._slider.setValue(self._to_tick(spec.default))
        self._slider.valueChanged.connect(self._on_change)
        layout.addWidget(self._slider)

        self._update_header(spec.default)

    def _to_tick(self, value: float) -> int:
        frac = (value - self.spec.minimum) / (self.spec.maximum - self.spec.minimum)
        return int(round(frac * _SLIDER_TICKS))

    def _to_value(self, tick: int) -> float:
        frac = tick / _SLIDER_TICKS
        return self.spec.minimum + frac * (self.spec.maximum - self.spec.minimum)

    def _update_header(self, value: float) -> None:
        unit = f" {self.spec.units}" if self.spec.units else ""
        self._header.setText(f"{self.spec.label}: {value:.3g}{unit}")

    def _on_change(self, tick: int) -> None:
        value = self._to_value(tick)
        self._update_header(value)
        self.valueChanged.emit(self.spec.key, value)

    def value(self) -> float:
        return self._to_value(self._slider.value())

    def set_value(self, value: float) -> None:
        """Move the slider without emitting ``valueChanged``."""
        self._slider.blockSignals(True)
        self._slider.setValue(self._to_tick(value))
        self._slider.blockSignals(False)
        self._update_header(value)


class ControlPanel(QtWidgets.QWidget):
    """Left-hand control column: reactor dropdown, sliders, transport buttons."""

    reactorChanged = QtCore.Signal(str)
    controlsChanged = QtCore.Signal(dict)
    playToggled = QtCore.Signal(bool)
    resetRequested = QtCore.Signal()
    armRequested = QtCore.Signal()
    fireRequested = QtCore.Signal()
    skipToDischargeRequested = QtCore.Signal()
    optimizeRequested = QtCore.Signal()
    recordToggled = QtCore.Signal(bool)

    def __init__(self, reactor_names: list[str], parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._values: dict[str, float] = {}
        self._sliders: list[_LabeledSlider] = []

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        title = QtWidgets.QLabel("p-11B Reactor Core Simulator")
        title.setStyleSheet("color: #fff; font-size: 15px; font-weight: 700;")
        title.setWordWrap(True)
        root.addWidget(title)

        # Reactor selector.
        root.addWidget(self._section_label("Reactor Model"))
        self.reactor_combo = QtWidgets.QComboBox()
        self.reactor_combo.addItems(reactor_names)
        self.reactor_combo.currentTextChanged.connect(self.reactorChanged.emit)
        root.addWidget(self.reactor_combo)

        # Dynamic slider container.
        root.addWidget(self._section_label("Control Inputs"))
        self._slider_box = QtWidgets.QVBoxLayout()
        self._slider_box.setSpacing(4)
        slider_holder = QtWidgets.QWidget()
        slider_holder.setLayout(self._slider_box)
        root.addWidget(slider_holder)

        # Transport buttons.
        root.addWidget(self._section_label("Simulation"))
        shot_row = QtWidgets.QHBoxLayout()
        self.arm_btn = QtWidgets.QPushButton("Arm shot")
        self.arm_btn.setStyleSheet("font-weight: 600;")
        self.arm_btn.clicked.connect(self.armRequested.emit)
        self.fire_btn = QtWidgets.QPushButton("Fire")
        self.fire_btn.setStyleSheet("font-weight: 700; color: #ffcccc;")
        self.fire_btn.clicked.connect(self.fireRequested.emit)
        shot_row.addWidget(self.arm_btn)
        shot_row.addWidget(self.fire_btn)
        root.addLayout(shot_row)

        self.skip_btn = QtWidgets.QPushButton("Skip to flat-top")
        self.skip_btn.setVisible(False)
        self.skip_btn.clicked.connect(self.skipToDischargeRequested.emit)
        root.addWidget(self.skip_btn)

        btn_row = QtWidgets.QHBoxLayout()
        self.play_btn = QtWidgets.QPushButton("Play")
        self.play_btn.setCheckable(True)
        self.play_btn.toggled.connect(self._on_play)
        self.reset_btn = QtWidgets.QPushButton("Reset")
        self.reset_btn.setToolTip(
            "Factory defaults and empty chamber. Does NOT keep optimized sliders — "
            "use Arm → Fire again instead to re-run with current settings."
        )
        self.reset_btn.clicked.connect(self.resetRequested.emit)
        btn_row.addWidget(self.play_btn)
        btn_row.addWidget(self.reset_btn)
        root.addLayout(btn_row)

        self.record_btn = QtWidgets.QPushButton("Record MP4")
        self.record_btn.setCheckable(True)
        self.record_btn.setToolTip(
            "Presentation capture (spatial view + graphs + facility audio + ChatTTS callouts). "
            "Recommended: Optimize → Record ON → Arm → Fire → "
            "Record OFF to save MP4. Idle frames before Fire are not recorded."
        )
        self.record_btn.toggled.connect(self._on_record)
        root.addWidget(self.record_btn)

        # Optimizer: search this reactor's control space for the best Q_net.
        self.optimize_btn = QtWidgets.QPushButton("Solve for optimal Q_net")
        self.optimize_btn.setStyleSheet("font-weight: 600;")
        self.optimize_btn.clicked.connect(self.optimizeRequested.emit)
        root.addWidget(self.optimize_btn)

        # Status / readout box.
        root.addWidget(self._section_label("Live Readout"))
        self.readout = QtWidgets.QLabel("--")
        self.readout.setStyleSheet("color: #9fe; font-family: monospace; font-size: 11px;")
        self.readout.setWordWrap(True)
        self.readout.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop)
        root.addWidget(self.readout, stretch=1)

        self.setMinimumWidth(280)
        self.setMaximumWidth(360)

    @staticmethod
    def _section_label(text: str) -> QtWidgets.QLabel:
        lab = QtWidgets.QLabel(text)
        lab.setStyleSheet("color: #8ab; font-size: 11px; font-weight: 600; margin-top: 4px;")
        return lab

    def _on_play(self, checked: bool) -> None:
        self.play_btn.setText("Pause" if checked else "Play")
        self.playToggled.emit(checked)

    def _on_record(self, checked: bool) -> None:
        self.record_btn.setText("Stop & save MP4" if checked else "Record MP4")
        self.recordToggled.emit(checked)

    def set_recording(self, active: bool) -> None:
        self.record_btn.blockSignals(True)
        self.record_btn.setChecked(active)
        self.record_btn.setText("Stop & save MP4" if active else "Record MP4")
        self.record_btn.blockSignals(False)

    def set_playing(self, playing: bool) -> None:
        self.play_btn.blockSignals(True)
        self.play_btn.setChecked(playing)
        self.play_btn.setText("Pause" if playing else "Play")
        self.play_btn.blockSignals(False)

    def rebuild_sliders(self, specs: list[ControlSpec]) -> None:
        """Replace the slider stack for a newly selected reactor."""
        while self._slider_box.count():
            item = self._slider_box.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._sliders.clear()
        self._values.clear()

        for spec in specs:
            slider = _LabeledSlider(spec)
            slider.valueChanged.connect(self._on_slider)
            self._slider_box.addWidget(slider)
            self._sliders.append(slider)
            self._values[spec.key] = spec.default

    def _on_slider(self, key: str, value: float) -> None:
        self._values[key] = value
        self.controlsChanged.emit(dict(self._values))

    def current_values(self) -> dict[str, float]:
        return dict(self._values)

    def set_values(self, values: dict[str, float]) -> None:
        """Programmatically move sliders to ``values`` (e.g. optimizer result)."""
        for slider in self._sliders:
            if slider.spec.key in values:
                slider.set_value(values[slider.spec.key])
                self._values[slider.spec.key] = slider.value()

    def set_optimizing(self, busy: bool) -> None:
        """Reflect optimizer activity in the button and disable it while busy."""
        self.optimize_btn.setEnabled(not busy)
        self.optimize_btn.setText("Optimizing..." if busy else "Solve for optimal Q_net")

    def update_readout(self, text: str) -> None:
        self.readout.setText(text)

    def set_fire_enabled(self, enabled: bool) -> None:
        self.fire_btn.setEnabled(enabled)

    def set_shot_status(self, phase: str, callout: str, can_fire: bool) -> None:
        self.arm_btn.setToolTip("Prepare vacuum, fuel, and power systems for the next shot.")
        self.fire_btn.setToolTip(
            "Run the automated discharge sequence. Pre-discharge countdown runs "
            "fast-forward; flat-top / pulse / pinch play at normal speed."
        )
        self.fire_btn.setText("Fire" if can_fire else "Fire (arm first)")
        self.set_fire_enabled(can_fire)

    def set_skip_to_discharge(self, visible: bool, label: str = "Skip to flat-top") -> None:
        self.skip_btn.setVisible(visible)
        self.skip_btn.setText(label)
        self.skip_btn.setToolTip(
            "Jump past the countdown straight to the main discharge phase "
            "(flat-top, laser pulse, or pinch)."
        )
