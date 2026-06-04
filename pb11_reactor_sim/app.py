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
Main application window.

:class:`PlasmaSimApp` assembles the control panel, the 2D spatial canvas, and
the 1D diagnostic panel into a single dashboard, owns the active
:class:`~pb11_reactor_sim.engine.base.ReactorSimulation`, and drives the
simulation loop with a :class:`QtCore.QTimer`. Multiple physics substeps are run
per GUI frame so the visualization stays smooth while the simulation advances at
its native (sub-nanosecond) timestep.
"""
from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from pb11_reactor_sim.engine.base import ReactorSimulation
from pb11_reactor_sim.engine.shot_sequence import ShotPhase
from pb11_reactor_sim.engine.optimizer import OptimizeResult, optimize_qnet
from pb11_reactor_sim.engine.pic_backend import FieldSolveBackend, make_backend
from pb11_reactor_sim.gui.canvas import ReactorCanvas
from pb11_reactor_sim.gui.controls import ControlPanel
from pb11_reactor_sim.gui.diagnostics import DiagnosticsPanel
from pb11_reactor_sim.gui.chattts_narration import narration_enabled
from pb11_reactor_sim.gui.live_audio import LiveShotAudio, live_audio_enabled
from pb11_reactor_sim.gui.recorder import FrameRecorder, compose_png_horizontal
from pb11_reactor_sim.reactors import REACTOR_REGISTRY

#: Physics substeps advanced per GUI frame.
_SUBSTEPS_PER_FRAME = 4
#: Extra substeps while fast-forwarding pre-discharge countdown after Fire.
_STARTUP_SUBSTEP_MULT = 35
#: Extra substeps during the long flat-top / main-pulse hold (sim physics unchanged).
_PLATEAU_SUBSTEP_MULT = 10
#: Extra substeps during ramp-down / recovery (shorter than plateau).
_TAIL_SUBSTEP_MULT = 4
#: GUI refresh interval [ms].
_FRAME_INTERVAL_MS = 33
#: Live/export audio samples per GUI tick (~33 ms at 48 kHz).
_SAMPLES_PER_TICK = int(round(48_000 * _FRAME_INTERVAL_MS / 1000))


class _OptimizeWorker(QtCore.QObject):
    """Runs the Q_net control-space search off the GUI thread."""

    finished = QtCore.Signal(object)  # OptimizeResult
    failed = QtCore.Signal(str)

    def __init__(self, reactor_cls: type[ReactorSimulation], backend: FieldSolveBackend) -> None:
        super().__init__()
        self._reactor_cls = reactor_cls
        self._backend = backend

    @QtCore.Slot()
    def run(self) -> None:
        try:
            result = optimize_qnet(self._reactor_cls, self._backend)
            self.finished.emit(result)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class PlasmaSimApp(QtWidgets.QMainWindow):
    """Top-level dashboard window for the p-11B reactor simulator."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("p-11B Reactor Core Simulator -- TAE FRC / HB11 Laser / LPP DPF")
        self.resize(1500, 900)

        self.backend = make_backend()

        self._reactor: ReactorSimulation | None = None
        self._playing = False
        self._frame = 0
        self._auto_paused_after_shot = False
        self._recorder = FrameRecorder()
        self._live_audio = LiveShotAudio(samples_per_tick=_SAMPLES_PER_TICK)

        # --- widgets ---
        self.controls = ControlPanel(list(REACTOR_REGISTRY.keys()))
        self.canvas = ReactorCanvas()
        self.diagnostics = DiagnosticsPanel()

        # --- layout: controls | canvas | diagnostics ---
        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        splitter.addWidget(self.controls)
        splitter.addWidget(self.canvas)
        splitter.addWidget(self.diagnostics)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([320, 880, 380])
        self.setCentralWidget(splitter)

        self.statusBar().showMessage(f"Field-solve engine: {self.backend.label}")

        # --- signals ---
        self.controls.reactorChanged.connect(self._on_reactor_changed)
        self.controls.controlsChanged.connect(self._on_controls_changed)
        self.controls.playToggled.connect(self._on_play_toggled)
        self.controls.resetRequested.connect(self._on_reset)
        self.controls.armRequested.connect(self._on_arm)
        self.controls.fireRequested.connect(self._on_fire)
        self.controls.skipToDischargeRequested.connect(self._on_skip_to_discharge)
        self.controls.optimizeRequested.connect(self._on_optimize)
        self.controls.recordToggled.connect(self._on_record_toggled)

        # Optimizer worker thread handles (kept alive while running).
        self._opt_thread: QtCore.QThread | None = None
        self._opt_worker: _OptimizeWorker | None = None

        # --- simulation timer ---
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(_FRAME_INTERVAL_MS)
        self._timer.timeout.connect(self._on_tick)

        # Boot the first reactor.
        self._on_reactor_changed(self.controls.reactor_combo.currentText())

    def _reactor_needs_geometry_refresh(self) -> bool:
        """True when control changes rebuild masks (HB11/LPP), not just fields (TAE)."""
        from pb11_reactor_sim.reactors.hb11 import HB11Reactor
        from pb11_reactor_sim.reactors.lpp import LPPReactor

        return isinstance(self._reactor, (HB11Reactor, LPPReactor))

    # -- reactor management -------------------------------------------------
    def _on_reactor_changed(self, name: str) -> None:
        self._live_audio.stop()
        self._live_audio.set_reactor(name)
        cls = REACTOR_REGISTRY[name]
        self.controls.rebuild_sliders(cls.control_specs())
        self._reactor = cls(field_solver=self.backend)
        self._reactor.apply_controls(self.controls.current_values())
        self.diagnostics.clear()
        self._frame = 0
        self.canvas.attach(self._reactor, self.backend.label)
        self.canvas.update_hud(
            gui_frame=0,
            substeps=_SUBSTEPS_PER_FRAME,
            speed_mode="idle",
            sim_time_us=0.0,
            ops=self._reactor.shot_phase.value,
            idle=True,
        )
        self._sync_shot_ui()
        self._update_readout()

    def _on_arm(self) -> None:
        if self._reactor is None:
            return
        self._on_play_toggled(False)
        self.controls.set_playing(False)
        self._reactor.apply_controls(self.controls.current_values())
        self._reactor.arm_shot()
        self.diagnostics.clear()
        self._frame = 0
        self.canvas.attach(self._reactor, self.backend.label)
        self.canvas.update_hud(
            gui_frame=0,
            substeps=_SUBSTEPS_PER_FRAME,
            speed_mode="idle",
            sim_time_us=0.0,
            ops=self._reactor.shot_phase.value,
            idle=True,
        )
        self._sync_shot_ui()
        self._update_readout()
        self.statusBar().showMessage(self._reactor.shot_callout)

    def _recorder_reactor_name(self) -> str:
        return type(self._reactor).display_name if self._reactor is not None else ""

    def _on_fire(self) -> None:
        if self._reactor is None or not self._reactor.fire_shot():
            return
        self._frame = 0
        if self._recorder.active:
            # Fresh buffer from discharge start (skip any idle pre-Fire frames).
            self._recorder.start(reactor_name=self._recorder_reactor_name())
        self._auto_paused_after_shot = False
        self.controls.set_playing(True)
        self._on_play_toggled(True)
        self._sync_shot_ui()
        msg = self._reactor.shot_callout
        if self._recorder.active:
            msg += "  |  Recording shot… toggle Record off to save MP4."
        self.statusBar().showMessage(msg)

    def _sync_shot_ui(self) -> None:
        r = self._reactor
        if r is None:
            return
        self.controls.set_shot_status(r.shot_phase.value, r.shot_callout, r.can_fire())
        self.controls.set_skip_to_discharge(r.can_skip_to_discharge(), r.skip_to_discharge_label())

    def _substeps_per_frame(self) -> int:
        r = self._reactor
        if r is None:
            return _SUBSTEPS_PER_FRAME
        if r.is_startup_countdown():
            return _SUBSTEPS_PER_FRAME * _STARTUP_SUBSTEP_MULT
        if r.is_plateau_fast_forward():
            return _SUBSTEPS_PER_FRAME * _PLATEAU_SUBSTEP_MULT
        if r.is_tail_fast_forward():
            return _SUBSTEPS_PER_FRAME * _TAIL_SUBSTEP_MULT
        return _SUBSTEPS_PER_FRAME

    def _hud_speed_mode(self) -> str:
        r = self._reactor
        if r is None:
            return "1×"
        if r.is_startup_countdown():
            return f"FF×{_STARTUP_SUBSTEP_MULT}"
        if r.is_plateau_fast_forward():
            return f"FF×{_PLATEAU_SUBSTEP_MULT}"
        if r.is_tail_fast_forward():
            return f"FF×{_TAIL_SUBSTEP_MULT}"
        return "1×"

    def _is_fast_gui_frame(self) -> bool:
        r = self._reactor
        if r is None:
            return False
        return (
            r.is_startup_countdown()
            or r.is_plateau_fast_forward()
            or r.is_tail_fast_forward()
        )

    def _on_skip_to_discharge(self) -> None:
        if self._reactor is None or not self._reactor.skip_to_discharge():
            return
        self.canvas.attach(self._reactor, self.backend.label)
        self._sync_shot_ui()
        self.statusBar().showMessage(self._reactor.shot_callout)

    def _on_controls_changed(self, values: dict) -> None:
        if self._reactor is None:
            return
        self._reactor.apply_controls(values)
        # HB11/LPP rebuild conductor masks when controls change; TAE only updates B_z.
        if self._reactor_needs_geometry_refresh():
            self.canvas.attach(self._reactor, self.backend.label)
        else:
            self.canvas.refresh()

    def _on_play_toggled(self, playing: bool) -> None:
        self._playing = playing
        if playing and live_audio_enabled():
            self._live_audio.set_reactor(self._recorder_reactor_name())
            self._live_audio.start()
        else:
            self._live_audio.stop()
        if playing:
            self._timer.start()
        else:
            self._timer.stop()

    def _on_reset(self) -> None:
        if self._reactor is None:
            return
        self._on_play_toggled(False)
        self.controls.set_playing(False)

        defaults = {s.key: s.default for s in type(self._reactor).control_specs()}
        self.controls.set_values(defaults)
        self._reactor.apply_controls(self.controls.current_values())
        self._reactor.reset()
        self._frame = 0
        self.canvas.attach(self._reactor, self.backend.label)
        self.diagnostics.clear()
        self._sync_shot_ui()
        self._update_readout()
        self.statusBar().showMessage("Reset — unarmed idle.")

    # -- optimizer ----------------------------------------------------------
    def _on_optimize(self) -> None:
        if self._reactor is None or self._opt_thread is not None:
            return
        reactor_cls = type(self._reactor)
        self.controls.set_optimizing(True)
        self.statusBar().showMessage(f"Optimizing Q_net over {reactor_cls.display_name} controls...")

        self._opt_thread = QtCore.QThread(self)
        self._opt_worker = _OptimizeWorker(reactor_cls, self.backend)
        self._opt_worker.moveToThread(self._opt_thread)
        self._opt_thread.started.connect(self._opt_worker.run)
        self._opt_worker.finished.connect(self._on_optimize_done)
        self._opt_worker.failed.connect(self._on_optimize_failed)
        self._opt_thread.start()

    def _teardown_opt_thread(self) -> None:
        if self._opt_thread is not None:
            self._opt_thread.quit()
            self._opt_thread.wait()
            self._opt_thread = None
            self._opt_worker = None
        self.controls.set_optimizing(False)

    @QtCore.Slot(object)
    def _on_optimize_done(self, result: OptimizeResult) -> None:
        self._teardown_opt_thread()
        # Apply the optimum to the sliders (which propagates to the live reactor).
        self.controls.set_values(result.controls)
        pretty = ", ".join(f"{k}={v:.3g}" for k, v in result.controls.items())
        self.statusBar().showMessage(
            f"Optimal Q_net = {result.q_net:.3e}  at  {pretty}   "
            f"({result.n_evaluations} evaluations)  |  "
            f"Next: Record → Arm → Fire → Record off  |  Engine: {self.backend.label}"
        )

    @QtCore.Slot(str)
    def _on_optimize_failed(self, message: str) -> None:
        self._teardown_opt_thread()
        self.statusBar().showMessage(f"Optimization failed: {message}")

    def _grab_recording_png(self) -> bytes | None:
        """Canvas + right-hand diagnostic charts (excludes control panel)."""
        return compose_png_horizontal(
            self.canvas.grab_frame_png(),
            self.diagnostics.grab_frame_png(),
        )

    def _recording_default_name(self) -> str:
        r = self._reactor
        if r is None:
            return "pb11_reactor_shot.mp4"
        slug = type(r).display_name.replace(" ", "_")
        return f"pb11_reactor_shot_{slug}.mp4"

    def _on_record_toggled(self, recording: bool) -> None:
        if recording:
            self._recorder.start(reactor_name=self._recorder_reactor_name())
            if self._reactor and self._reactor.shot_phase in (ShotPhase.UNARMED, ShotPhase.ARMED):
                self.statusBar().showMessage(
                    "Recording… Arm → Fire first, or you will capture idle frames (t stays 0)."
                )
            else:
                self.statusBar().showMessage(
                    "Recording canvas + diagnostics + phase audio… toggle again to save MP4."
                )
            return
        saved = self._recorder.stop(self, default_name=self._recording_default_name())
        self.controls.set_recording(False)
        if saved[0] and not saved[1]:
            self.statusBar().showMessage(f"Recording saved (audio + narration): {saved[0]}")
        elif saved[0] and saved[1]:
            QtWidgets.QMessageBox.warning(self, "MP4 encode", saved[1])
            self.statusBar().showMessage(f"Saved fallback: {saved[0]}")
        else:
            self.statusBar().showMessage("Recording cancelled (no frames captured).")

    # -- main loop ----------------------------------------------------------
    def _on_tick(self) -> None:
        if self._reactor is None:
            return
        r = self._reactor
        substeps = self._substeps_per_frame()

        # Play does not advance physics while unarmed / armed (by design).
        if r.shot_phase in (ShotPhase.UNARMED, ShotPhase.ARMED):
            self.canvas.refresh()
            self.canvas.update_hud(
                gui_frame=self._frame,
                substeps=substeps,
                speed_mode="idle",
                sim_time_us=r.time * 1.0e6,
                ops=r.shot_phase.value,
                idle=True,
            )
            if self._playing:
                self._on_play_toggled(False)
                self.controls.set_playing(False)
                self.statusBar().showMessage(
                    f"{r.shot_callout}  —  Press Fire to run the shot."
                )
            return

        prev_phase = r.shot_phase
        speed_mode = self._hud_speed_mode()
        ff = self._is_fast_gui_frame()
        for _ in range(substeps):
            r.step()
        self.canvas.refresh()
        self.canvas.update_hud(
            gui_frame=self._frame,
            substeps=substeps,
            speed_mode=speed_mode,
            sim_time_us=r.time * 1.0e6,
            ops=r.shot_phase.value,
        )
        self.diagnostics.update_from(r.diagnostics)
        phase = r._fire_phase_key if r.shot_phase == ShotPhase.FIRING else r.shot_phase.value
        nbi = float(getattr(r, "_nbi_scale", 0.0))
        intensity = nbi if nbi > 0 else min(1.0, max(0.0, r.last_q_net / 1.8))
        if self._playing and live_audio_enabled():
            self._live_audio.feed(phase=phase, fast_forward=ff, intensity=intensity)
        if self._recorder.active:
            self._recorder.add_frame(
                self._grab_recording_png(),
                phase=phase,
                fast_forward=ff,
                intensity=intensity,
            )
        if r.shot_phase == ShotPhase.FIRING:
            self.statusBar().showMessage(r.shot_callout)
        if prev_phase == ShotPhase.FIRING and r.shot_phase == ShotPhase.QUIESCENT:
            self._on_play_toggled(False)
            self.controls.set_playing(False)
            self._auto_paused_after_shot = True
            self.statusBar().showMessage(self._reactor.shot_callout)
        self._sync_shot_ui()
        self._frame += 1
        if self._frame % 3 == 0:
            self._update_readout()

    def _update_readout(self) -> None:
        from pb11_reactor_sim.reactors.tae import TAEReactor

        r = self._reactor
        if r is None:
            return
        lines = [
            f"Ops      = {r.shot_phase.value}",
            f"Status   = {r.shot_callout}",
            f"t        = {r.time * 1e6:10.4f} us",
            f"step     = {r.step_index}",
            f"T_i      = {r.T_i_keV:10.2f} keV",
            f"T_e      = {r.T_e_keV:10.2f} keV",
            f"n_e      = {r.n_e:10.3e} m^-3",
            f"P_fusion = {r.last_p_fusion:10.3e} W/m^3",
            f"P_Brems  = {r.last_p_brems:10.3e} W/m^3",
            f"P_cond   = {r.last_p_cond:10.3e} W/m^3",
            f"Q_net    = {r.last_q_net:10.3e}",
        ]
        if isinstance(r, TAEReactor):
            lines += [
                f"Sustain  = {getattr(r, 'sustainment', 0.0):10.3f}  (beam-driven FRC hold)",
                f"P_NBI    = {r.last_p_nbi:10.3e} W/m^3",
                f"P_ICC    = {r.last_p_icc:10.3e} W/m^3",
                f"Q_plasma = {r.last_q_plasma:10.3e}",
                f"Q_sys    = {r.last_q_net:10.3e}  (ICC / NBI+losses)",
            ]
        lines += self._reactor_specific_readout(r)
        self.controls.update_readout("\n".join(lines))

    @staticmethod
    def _reactor_specific_readout(r: ReactorSimulation) -> list[str]:
        out: list[str] = []
        icc = getattr(r, "icc_signal", None)
        if icc is not None:
            out.append(f"ICC sig  = {icc:10.3e} a.u.")
        coll = getattr(r, "collected_charge", None)
        if coll is not None:
            out.append(f"Collected= {coll:10.3e} C")
        cur = getattr(r, "current", None)
        if cur is not None:
            out.append(f"I(t)     = {cur:10.3e} A")
        bp = getattr(r, "b_pinch", None)
        if bp is not None:
            out.append(f"B_pinch  = {bp:10.3e} T")
        return out


def _warm_narration_cache(app: QtWidgets.QApplication) -> None:
    """Precompute ChatTTS clips before the dashboard opens (first run is slow)."""
    import os

    if not narration_enabled():
        return
    if os.environ.get("PB11_SKIP_CACHE_WARM", "").strip().lower() in ("1", "true", "yes"):
        return

    from pb11_reactor_sim.gui.narration_cache import ensure_narration_cache

    dlg = QtWidgets.QProgressDialog(
        "Preparing voice callouts (first run may take a few minutes)…",
        None,
        0,
        1,
        None,
    )
    dlg.setWindowTitle("p-11B Reactor Simulator")
    dlg.setMinimumDuration(0)
    dlg.setValue(0)
    dlg.show()
    app.processEvents()

    def progress(done: int, total: int, label: str) -> None:
        dlg.setMaximum(max(total, 1))
        dlg.setValue(min(done, total))
        dlg.setLabelText(label)
        app.processEvents()

    cached, synthesized = ensure_narration_cache(progress=progress)
    dlg.close()
    if synthesized:
        msg = f"Voice cache: {synthesized} new clip(s), {cached} already on disk."
    else:
        msg = f"Voice cache ready ({cached} clip(s) on disk)."
    # Shown again on the main window status bar after it opens.
    app.setProperty("pb11_cache_status", msg)


def main() -> int:
    """Application entry point."""
    import sys

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    _warm_narration_cache(app)
    window = PlasmaSimApp()
    cache_msg = app.property("pb11_cache_status")
    if cache_msg:
        window.statusBar().showMessage(str(cache_msg))
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
