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
from pb11_reactor_sim.gui.workflow import WORKFLOW_STEPS

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
    playRequested = QtCore.Signal()
    stepRequested = QtCore.Signal()
    backRequested = QtCore.Signal()
    compileRequested = QtCore.Signal()
    skipToDischargeRequested = QtCore.Signal()
    optimizeRequested = QtCore.Signal()
    recordStartRequested = QtCore.Signal()
    recordSaveRequested = QtCore.Signal()

    def __init__(self, reactor_names: list[str], parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._values: dict[str, float] = {}
        self._sliders: list[_LabeledSlider] = []
        self._completed: set[str] = set()
        self._step_checks: dict[str, QtWidgets.QCheckBox] = {}
        self._step_buttons: dict[str, QtWidgets.QPushButton] = {}

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        title = QtWidgets.QLabel("p-11B Reactor Core Simulator")
        title.setStyleSheet("color: #fff; font-size: 15px; font-weight: 700;")
        title.setWordWrap(True)
        root.addWidget(title)

        root.addWidget(self._section_label("Reactor Model"))
        self.reactor_combo = QtWidgets.QComboBox()
        self.reactor_combo.addItems(reactor_names)
        self.reactor_combo.currentTextChanged.connect(self._on_reactor_changed)
        root.addWidget(self.reactor_combo)

        root.addWidget(self._section_label("Control Inputs"))
        self._slider_box = QtWidgets.QVBoxLayout()
        self._slider_box.setSpacing(4)
        slider_holder = QtWidgets.QWidget()
        slider_holder.setLayout(self._slider_box)
        root.addWidget(slider_holder)

        root.addWidget(self._section_label("Shot checklist"))
        checklist = QtWidgets.QVBoxLayout()
        checklist.setSpacing(4)
        self.play_btn = QtWidgets.QPushButton("Play")
        self.step_btn = QtWidgets.QPushButton("Step")
        self.back_btn = QtWidgets.QPushButton("Back")
        for step in WORKFLOW_STEPS:
            row = QtWidgets.QHBoxLayout()
            row.setSpacing(6)
            cb = QtWidgets.QCheckBox()
            cb.setEnabled(False)
            cb.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
            cb.setStyleSheet("color: #9cf;")
            row.addWidget(cb, 0)
            self._step_checks[step.step_id] = cb

            if step.step_id == "review":
                review_row = QtWidgets.QHBoxLayout()
                review_row.setSpacing(4)
                self.play_btn.setToolTip(
                    "Play the full compiled shot from step 1 through the end, with voice each segment."
                )
                self.step_btn.setToolTip(
                    "Play one numbered segment per press (Arm first, then each callout). "
                    "Waits at the end — press Step again for the next. Step while playing only stops."
                )
                self.back_btn.setToolTip(
                    "Jump to the previous segment and pause on its first frame."
                )
                for btn in (self.play_btn, self.step_btn, self.back_btn):
                    btn.setStyleSheet("font-weight: 600;")
                    review_row.addWidget(btn)
                holder = QtWidgets.QWidget()
                holder.setLayout(review_row)
                row.addWidget(holder, 1)
            else:
                btn = QtWidgets.QPushButton(step.button_label)
                btn.setToolTip(step.callout)
                if step.step_id in ("optimize", "compile"):
                    btn.setStyleSheet("font-weight: 600;")
                row.addWidget(btn, 1)
                self._step_buttons[step.step_id] = btn

            checklist.addLayout(row)

        self.play_btn.clicked.connect(self.playRequested.emit)
        self.step_btn.clicked.connect(self.stepRequested.emit)
        self.back_btn.clicked.connect(self.backRequested.emit)
        self._step_buttons["optimize"].clicked.connect(self.optimizeRequested.emit)
        self._step_buttons["compile"].clicked.connect(self.compileRequested.emit)
        self._step_buttons["rec_start"].clicked.connect(self.recordStartRequested.emit)
        self._step_buttons["rec_save"].clicked.connect(self.recordSaveRequested.emit)

        checklist_holder = QtWidgets.QWidget()
        checklist_holder.setLayout(checklist)
        root.addWidget(checklist_holder)

        self.skip_btn = QtWidgets.QPushButton("Skip to flat-top")
        self.skip_btn.setVisible(False)
        self.skip_btn.clicked.connect(self.skipToDischargeRequested.emit)
        root.addWidget(self.skip_btn)

        root.addWidget(self._section_label("Live Readout"))
        self.readout = QtWidgets.QLabel("--")
        self.readout.setStyleSheet(
            "color: #0f172a; background-color: #f1f5f9; font-family: monospace;"
            " font-size: 11px; padding: 6px; border: 1px solid #94a3b8; border-radius: 3px;"
        )
        self.readout.setWordWrap(True)
        self.readout.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop)
        root.addWidget(self.readout, stretch=1)

        self.setMinimumWidth(280)
        self.setMaximumWidth(360)
        self._wire_step_buttons()
        self.set_review_enabled(False)

    @staticmethod
    def _section_label(text: str) -> QtWidgets.QLabel:
        lab = QtWidgets.QLabel(text)
        lab.setStyleSheet("color: #8ab; font-size: 11px; font-weight: 600; margin-top: 4px;")
        return lab

    def _on_reactor_changed(self, name: str) -> None:
        self.reset_workflow()
        self.reactorChanged.emit(name)

    def reset_workflow(self) -> None:
        """Clear checklist (reactor change, new shot, etc.)."""
        self._completed.clear()
        for cb in self._step_checks.values():
            cb.setChecked(False)
        self._wire_step_buttons()

    def is_step_done(self, step_id: str) -> bool:
        return step_id in self._completed

    def mark_step_done(self, step_id: str) -> None:
        self._completed.add(step_id)
        if step_id in self._step_checks:
            self._step_checks[step_id].setChecked(True)
        self._wire_step_buttons()

    def unmark_steps(self, *step_ids: str) -> None:
        """Clear checklist items (e.g. after slider change invalidates compile)."""
        for step_id in step_ids:
            self._completed.discard(step_id)
            if step_id in self._step_checks:
                self._step_checks[step_id].setChecked(False)
        self._wire_step_buttons()

    def can_run_step(self, step_id: str) -> tuple[bool, str]:
        from pb11_reactor_sim.gui.workflow import can_run_step

        return can_run_step(step_id, self._completed)

    def _wire_step_buttons(self) -> None:
        for step in WORKFLOW_STEPS:
            if step.step_id == "review":
                continue
            ok, _ = self.can_run_step(step.step_id)
            self._step_buttons[step.step_id].setEnabled(ok)

    def set_recording_active(self, active: bool) -> None:
        if active:
            self._step_buttons["rec_start"].setEnabled(False)
            self._step_buttons["rec_save"].setEnabled("rec_save" not in self._completed)
        else:
            self._wire_step_buttons()

    def set_review_enabled(self, enabled: bool) -> None:
        """Enable Play / Step / Back after a successful compile."""
        for btn in (self.play_btn, self.step_btn, self.back_btn):
            btn.setEnabled(enabled)

    def rebuild_sliders(self, specs: list[ControlSpec]) -> None:
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
        for slider in self._sliders:
            if slider.spec.key in values:
                slider.set_value(values[slider.spec.key])
                self._values[slider.spec.key] = slider.value()

    def set_optimizing(self, busy: bool) -> None:
        btn = self._step_buttons["optimize"]
        if busy:
            btn.setEnabled(False)
            btn.setText("Optimizing…")
        else:
            btn.setText("Optimize")
            self._wire_step_buttons()

    def set_compiling(self, busy: bool) -> None:
        btn = self._step_buttons["compile"]
        if busy:
            btn.setEnabled(False)
            btn.setText("Compiling…")
        else:
            btn.setText("Compile")
            self._wire_step_buttons()

    def readout_control_lines(self) -> list[str]:
        lines: list[str] = []
        for slider in self._sliders:
            spec = slider.spec
            unit = f" {spec.units}" if spec.units else ""
            lines.append(f"{spec.label:12s} = {slider.value():10.3g}{unit}")
        return lines

    def update_readout(self, text: str) -> None:
        self.readout.setText(text)

    def set_shot_status(self, phase: str, callout: str, can_fire: bool) -> None:
        del can_fire  # live fire removed; review uses compiled playback

    def set_skip_to_discharge(self, visible: bool, label: str = "Skip to flat-top") -> None:
        self.skip_btn.setVisible(visible)
        self.skip_btn.setText(label)
