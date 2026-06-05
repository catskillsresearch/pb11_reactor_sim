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
from pb11_reactor_sim.gui.recorder import FrameRecorder, compose_png_horizontal
from pb11_reactor_sim.gui.narration_scripts import PHASE_NARRATION
from pb11_reactor_sim.gui.shot_audio import build_recorded_shot_audio
from pb11_reactor_sim.gui.shot_compiler import CompiledPlayback, compile_shot_playback
from pb11_reactor_sim.gui.shot_playback import ShotAudioPlayer, shot_audio_enabled
from pb11_reactor_sim.gui.session_cache import (
    controls_fingerprint,
    controls_near,
    get_optimize,
    lookup_compile,
    put_compile,
    put_optimize,
)
from pb11_reactor_sim.gui.shot_review import ReviewSegment, build_review_segments
from pb11_reactor_sim.gui.sim_stepping import hud_speed_mode, is_fast_gui_frame, substeps_per_frame
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


class _NarrationCacheWorker(QtCore.QObject):
    """Precompute ChatTTS clips off the GUI thread (used when startup warm was skipped)."""

    finished = QtCore.Signal(int, int)

    def __init__(self, reactor_name: str) -> None:
        super().__init__()
        self._reactor_name = reactor_name

    @QtCore.Slot()
    def run(self) -> None:
        from pb11_reactor_sim.gui.narration_cache import ensure_narration_cache, lines_for_reactor

        cached, synthesized = ensure_narration_cache(lines_for_reactor(self._reactor_name))
        self.finished.emit(cached, synthesized)


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
        self._shot_player = ShotAudioPlayer(self)
        self._compiled_playback: CompiledPlayback | None = None
        self._review_segments: list[ReviewSegment] = []
        self._review_seg_ix = -1
        self._review_auto = False
        self._review_segment_done = False
        self._segment_voice_done = False
        self._full_timeline_playback = False
        self._playback_ix = 0
        self._playback_stop_ix = 0
        self._playback_mode: str | None = None  # "review"

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
        self.controls.playRequested.connect(self._on_play)
        self.controls.stepRequested.connect(self._on_step)
        self.controls.backRequested.connect(self._on_back)
        self.controls.compileRequested.connect(self._on_compile)
        self.controls.skipToDischargeRequested.connect(self._on_skip_to_discharge)
        self.controls.optimizeRequested.connect(self._on_optimize)
        self.controls.recordStartRequested.connect(self._on_record_start)
        self.controls.recordSaveRequested.connect(self._on_record_save)

        # Optimizer worker thread handles (kept alive while running).
        self._opt_thread: QtCore.QThread | None = None
        self._opt_worker: _OptimizeWorker | None = None
        self._narr_thread: QtCore.QThread | None = None
        self._narr_worker: _NarrationCacheWorker | None = None

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
        self._shot_player.stop()
        self._clear_compiled_playback()
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
        from pb11_reactor_sim.gui.session_cache import (
            cache_root,
            migrate_legacy_session_cache,
            optimize_cache_dir,
        )

        migrated = migrate_legacy_session_cache()
        restored = self._apply_session_optimize(name)
        if self._try_restore_compile_cache():
            migrate_note = (
                f" Migrated {migrated} file(s) from ~/.cache/pb11_reactor_sim."
                if migrated
                else ""
            )
            self.statusBar().showMessage(
                f"{name}: restored cached Optimize + Compile.{migrate_note} "
                f"Disk: {cache_root()}/optimize, {cache_root()}/compile."
            )
        elif restored:
            self.statusBar().showMessage(
                f"{name}: restored cached Optimize ({optimize_cache_dir()})."
            )
        elif migrated:
            self.statusBar().showMessage(
                f"Migrated {migrated} cache file(s) into {cache_root()} "
                f"(compile/, optimize/)."
            )
        self._warm_reactor_narration_async(name)

    def _apply_session_optimize(self, reactor_name: str) -> bool:
        """Restore last optimize for this reactor (memory or disk) and tick the checklist."""
        result = get_optimize(reactor_name)
        if result is None:
            return False
        self.controls.set_values(result.controls)
        if self._reactor is not None:
            self._reactor.apply_controls(self.controls.current_values())
            self._update_readout()
        if not self.controls.is_step_done("optimize"):
            self.controls.mark_step_done("optimize")
        return True

    def _install_compiled_playback(
        self,
        compiled: CompiledPlayback,
        *,
        from_cache: bool = False,
    ) -> None:
        self._compiled_playback = compiled
        self._review_segments = build_review_segments(
            compiled,
            self._recorder_reactor_name(),
            reactor_cls=type(self._reactor) if self._reactor else None,
        )
        self._review_seg_ix = -1
        self._review_segment_done = True
        self._shot_player.load(compiled.mixed_audio)
        self.canvas.cache_playback_frames(compiled.canvas_frames)
        self.diagnostics.cache_playback_frames(compiled.diag_frames)
        self._recorder.set_compiled_export(
            canvas_frames=compiled.canvas_frames,
            diag_frames=compiled.diag_frames,
            meta=list(compiled.meta),
            reactor_name=self._recorder_reactor_name(),
        )
        self.controls.set_review_enabled(True)
        self._attach_recorder_compiled_audio(compiled.mixed_audio)
        self.controls.mark_step_done("compile")
        self.controls.unmark_steps("review", "rec_save")
        n_seg = len(self._review_segments)
        n = len(compiled.meta)
        from pb11_reactor_sim.gui.session_cache import compile_cache_dir

        prefix = "Restored cached compile" if from_cache else "Compiled"
        self.statusBar().showMessage(
            f"{prefix}: {n} frames ({compiled.duration_s:.1f} s), "
            f"{n_seg} numbered steps. Cache: {compile_cache_dir()}. "
            "Rec Save exports MP4; Play/Step are optional preview."
        )

    def _try_restore_compile_cache(self) -> bool:
        if self._reactor is None:
            return False
        from_quiescent = self._reactor.shot_phase == ShotPhase.QUIESCENT
        cached, _fp = lookup_compile(
            self._recorder_reactor_name(),
            self.controls.current_values(),
            from_quiescent=from_quiescent,
        )
        if cached is None:
            return False
        self._install_compiled_playback(cached, from_cache=True)
        return True

    def _warm_reactor_narration_async(self, reactor_name: str) -> None:
        from pb11_reactor_sim.gui.narration_cache import cache_ready_for_reactor

        if not narration_enabled() or cache_ready_for_reactor(reactor_name):
            return
        self._teardown_narr_thread()
        self.statusBar().showMessage(f"Finishing voice cache for {reactor_name}…")
        thread = QtCore.QThread(self)
        worker = _NarrationCacheWorker(reactor_name)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_narration_cache_finished)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_narr_thread)
        self._narr_thread = thread
        self._narr_worker = worker
        thread.start()

    def _teardown_narr_thread(self) -> None:
        if self._narr_thread is not None and self._narr_thread.isRunning():
            self._narr_thread.quit()
            self._narr_thread.wait(2000)
        self._narr_thread = None
        self._narr_worker = None

    @QtCore.Slot(int, int)
    def _on_narration_cache_finished(self, cached: int, synthesized: int) -> None:
        if synthesized:
            self.statusBar().showMessage(
                f"Voice ready for this reactor ({synthesized} new, {cached} cached)."
            )
        else:
            self.statusBar().showMessage(f"Voice cache ready ({cached} clip(s)).")

    @QtCore.Slot()
    def _clear_narr_thread(self) -> None:
        self._narr_thread = None
        self._narr_worker = None

    def _clear_compiled_playback(self) -> None:
        self._compiled_playback = None
        self._review_segments = []
        self._review_seg_ix = -1
        self._review_auto = False
        self._review_segment_done = False
        self._playback_mode = None
        self._full_timeline_playback = False
        self._playback_ix = 0
        self._playback_stop_ix = 0
        self.canvas.end_playback()
        self.diagnostics.end_playback()
        self.diagnostics.unlock_axis_limits()
        self._shot_player.cleanup()
        self.controls.set_review_enabled(False)
        self.controls.unmark_steps("compile", "review")

    def _require_compiled(self) -> bool:
        if self._compiled_playback is None or not self._review_segments:
            self.statusBar().showMessage(
                "Compile simulation first (current sliders), then Play or Step."
            )
            return False
        return True

    def _stop_review_playback(self, *, hold_frame: bool = False) -> None:
        if self._playback_mode is None:
            return
        self._playback_mode = None
        self._review_auto = False
        self._set_run_timer(False)
        self._shot_player.stop()
        if hold_frame:
            return

    def _recorder_reactor_name(self) -> str:
        return type(self._reactor).display_name if self._reactor is not None else ""

    def _attach_recorder_compiled_audio(self, mixed) -> None:
        """Attach pre-mixed compile audio for MP4 export (tolerates stale recorder builds)."""
        import numpy as np

        arr = np.asarray(mixed, dtype=np.float32).reshape(-1)
        setter = getattr(self._recorder, "set_compiled_audio", None)
        if callable(setter):
            setter(arr)
        else:
            self._recorder._compiled_mixed = arr

    def _on_compile(self) -> None:
        if self._reactor is None:
            return
        ok, reason = self.controls.can_run_step("compile")
        if not ok:
            self.statusBar().showMessage(reason)
            return
        from_quiescent = self._reactor.shot_phase == ShotPhase.QUIESCENT
        cached, fp = lookup_compile(
            self._recorder_reactor_name(),
            self.controls.current_values(),
            from_quiescent=from_quiescent,
        )
        if cached is not None:
            self._set_run_timer(False)
            self._install_compiled_playback(cached, from_cache=True)
            self._reactor.arm_shot()
            self.canvas.attach(self._reactor, self.backend.label)
            self._sync_shot_ui()
            self._update_readout()
            return

        self._set_run_timer(False)
        self.controls.unmark_steps("compile", "review")
        self.controls.set_compiling(True)
        dlg = QtWidgets.QProgressDialog(
            "Compiling full shot (physics + voice + facility audio)…",
            None,
            0,
            5,
            self,
        )
        dlg.setWindowTitle("p-11B Reactor Simulator")
        dlg.setMinimumDuration(0)
        dlg.show()
        QtWidgets.QApplication.processEvents()

        def progress(done: int, total: int, label: str) -> None:
            dlg.setMaximum(max(total, 1))
            dlg.setValue(min(done, total))
            dlg.setLabelText(f"({done}/{total}) {label}")
            QtWidgets.QApplication.processEvents()

        if fp is None:
            fp = controls_fingerprint(
                self._recorder_reactor_name(),
                self.controls.current_values(),
                from_quiescent=from_quiescent,
            )

        def _apply_axis_peaks(peaks) -> None:
            self.diagnostics.set_limits_from_peaks(
                t_us=peaks.t_us,
                ti_kev=peaks.ti_kev,
                te_kev=peaks.te_kev,
                p_w_m3=peaks.p_w_m3,
                q_max=peaks.q_max,
            )

        def _refresh_compile_view() -> None:
            r = self._reactor
            if r is None:
                return
            self.canvas.refresh()
            self.canvas.update_hud(
                gui_frame=r.step_index,
                substeps=substeps_per_frame(r),
                speed_mode=hud_speed_mode(r),
                sim_time_us=r.time * 1.0e6,
                ops=r.shot_phase.value,
                idle=False,
            )
            self.diagnostics.update_from(r.diagnostics)

        try:
            compiled = compile_shot_playback(
                self._reactor,
                from_quiescent=from_quiescent,
                reactor_name=self._recorder_reactor_name(),
                grab_canvas=self.canvas.grab_frame_png,
                grab_diag=self.diagnostics.grab_frame_png,
                tick_ui=QtWidgets.QApplication.processEvents,
                refresh_canvas=_refresh_compile_view,
                refresh_diag=_refresh_compile_view,
                on_axis_peaks=_apply_axis_peaks,
                progress=progress,
            )
            if fp:
                put_compile(fp, compiled)
            self._install_compiled_playback(compiled, from_cache=False)
        except Exception as exc:  # noqa: BLE001
            self._clear_compiled_playback()
            self.statusBar().showMessage(f"Compile failed ({exc}).")
        finally:
            dlg.close()
            self.controls.set_compiling(False)
            self._reactor.arm_shot()
            self.canvas.attach(self._reactor, self.backend.label)
            self._sync_shot_ui()
            self._update_readout()

    def _compile_recorded_audio_for_mp4(self) -> None:
        """After a recorded shot, rebuild audio from captured frames (MP4-accurate)."""
        if not shot_audio_enabled() or not self._recorder.active or not self._recorder.has_frames():
            return
        dlg = QtWidgets.QProgressDialog("Compiling recorded shot audio for MP4…", None, 0, 3, self)
        dlg.setMinimumDuration(0)
        dlg.show()
        QtWidgets.QApplication.processEvents()

        def progress(done: int, total: int, label: str) -> None:
            dlg.setMaximum(max(total, 1))
            dlg.setValue(done)
            dlg.setLabelText(label)
            QtWidgets.QApplication.processEvents()

        try:
            frames, meta = self._recorder.captured_frames()
            compiled = build_recorded_shot_audio(
                frames,
                meta,
                reactor_name=self._recorder_reactor_name(),
                progress=progress,
            )
            self._shot_player.load(compiled.mixed)
            self._attach_recorder_compiled_audio(compiled.mixed)
        except Exception as exc:  # noqa: BLE001
            self.statusBar().showMessage(f"Recorded audio compile failed ({exc}).")
        finally:
            dlg.close()

    def _on_play(self) -> None:
        if self._reactor is None or not self._require_compiled():
            return
        pb = self._compiled_playback
        if pb is None:
            return
        self._stop_review_playback()
        self._frame = 0
        self._review_auto = True
        self._full_timeline_playback = True
        self._playback_mode = "review"
        self._review_seg_ix = 0
        self._review_segment_done = False
        self._playback_ix = 0
        self._playback_stop_ix = len(pb.meta)
        seg = self._segment_for_frame(0)
        self._show_playback_frame(0, seg=seg)
        if shot_audio_enabled():
            self._shot_player.play(start_ms=0)
        self._set_run_timer(True)

    def _on_step(self) -> None:
        if self._reactor is None or not self._require_compiled():
            return
        if self._playback_mode is not None:
            self._stop_review_playback(hold_frame=True)
            self._review_segment_done = True
            self.statusBar().showMessage(
                f"{self._review_segments[self._review_seg_ix].subtitle}  —  "
                "Stopped. Press Step for the next segment."
            )
            return
        next_ix = 0 if self._review_seg_ix < 0 else self._review_seg_ix + 1
        if not self._review_segment_done:
            next_ix = max(0, self._review_seg_ix)
        if next_ix >= len(self._review_segments):
            self.statusBar().showMessage(
                f"Shot review complete ({self._review_segments[-1].seq}/"
                f"{self._review_segments[-1].total}). Press Back to revisit a step."
            )
            self._finish_review_session()
            return
        self._frame = 0
        self._review_auto = False
        self._review_segment_done = False
        self._play_review_segment(next_ix, auto_continue=False)

    def _on_back(self) -> None:
        if self._reactor is None or not self._require_compiled():
            return
        self._stop_review_playback()
        if self._review_seg_ix <= 0:
            self._review_seg_ix = 0
            self._show_review_segment(0, paused=True)
            self.statusBar().showMessage(
                f"{self._review_segments[0].subtitle}  —  At first step (paused)."
            )
            return
        prev_ix = self._review_seg_ix - 1
        self._review_seg_ix = prev_ix
        self._show_review_segment(prev_ix, paused=True)
        seg = self._review_segments[prev_ix]
        self.statusBar().showMessage(f"{seg.subtitle}  —  Paused at step start.")

    def _sync_shot_ui(self) -> None:
        r = self._reactor
        if r is None:
            return
        self.controls.set_shot_status(r.shot_phase.value, r.shot_callout, r.can_fire())
        self.controls.set_skip_to_discharge(r.can_skip_to_discharge(), r.skip_to_discharge_label())

    def _callout_for_phase(self, phase: str) -> str:
        r = self._reactor
        if r is None:
            return phase
        scripts = PHASE_NARRATION.get(self._recorder_reactor_name(), {})
        if phase in scripts:
            return scripts[phase].split(".")[0]
        if phase == "armed":
            return r.shot_ops().arm_callout
        if phase == "quiescent":
            return r.shot_ops().quiescent_callout
        for p in r.shot_ops().fire_phases:
            if p.key == phase:
                return p.callout
        return phase

    def _segment_for_frame(self, ix: int):
        for seg in self._review_segments:
            if seg.start_ix <= ix < seg.end_ix:
                return seg
        if self._review_segments:
            return self._review_segments[-1]
        return None

    def _play_review_segment(self, seg_ix: int, *, auto_continue: bool) -> None:
        if self._reactor is None or seg_ix < 0 or seg_ix >= len(self._review_segments):
            return
        self._full_timeline_playback = False
        self._review_seg_ix = seg_ix
        self._review_auto = auto_continue
        self._review_segment_done = False
        self._segment_voice_done = False
        seg = self._review_segments[seg_ix]
        self._playback_mode = "review"
        self._playback_ix = seg.start_ix
        self._playback_stop_ix = seg.end_ix
        self._show_playback_frame(self._playback_ix, seg=seg)
        if shot_audio_enabled():
            self._shot_player.play(start_ms=seg.start_ms)
        self._set_run_timer(True)

    def _show_review_segment(self, seg_ix: int, *, paused: bool) -> None:
        if seg_ix < 0 or seg_ix >= len(self._review_segments):
            return
        seg = self._review_segments[seg_ix]
        self._review_seg_ix = seg_ix
        self._playback_ix = seg.start_ix
        self._show_playback_frame(seg.start_ix, seg=seg)
        if paused and shot_audio_enabled():
            self._shot_player.stop()

    def _show_playback_frame(self, ix: int, *, seg: ReviewSegment | None = None) -> None:
        pb = self._compiled_playback
        if pb is None or self._reactor is None or ix < 0 or ix >= len(pb.meta):
            return
        self.canvas.show_playback_png(ix=ix)
        self.diagnostics.show_playback_png(ix=ix)
        if self._playback_mode == "review" and seg is not None:
            phase = seg.phase
        else:
            phase = pb.meta[ix].phase
        if seg is None and self._review_segments:
            for s in self._review_segments:
                if s.start_ix <= ix < s.end_ix:
                    seg = s
                    break
        if seg is not None:
            self._reactor.shot_callout = seg.callout
        else:
            self._reactor.shot_callout = self._callout_for_phase(phase)
        if phase == "quiescent":
            self._reactor.shot_phase = ShotPhase.QUIESCENT
        elif phase == "armed":
            self._reactor.shot_phase = ShotPhase.ARMED
        else:
            self._reactor.shot_phase = ShotPhase.FIRING
        self._sync_shot_ui()
        msg = (
            seg.subtitle
            if seg is not None
            else self._reactor.shot_callout
        )
        self.statusBar().showMessage(msg)

    def _advance_playback(self) -> None:
        pb = self._compiled_playback
        if pb is None:
            return
        seg = (
            self._review_segments[self._review_seg_ix]
            if 0 <= self._review_seg_ix < len(self._review_segments)
            else None
        )
        self._playback_ix += 1

        if self._playback_ix >= self._playback_stop_ix:
            self._playback_ix = max(0, self._playback_stop_ix - 1)
            if self._full_timeline_playback:
                self._finish_full_timeline_playback()
            else:
                self._end_playback_segment()
            return

        if self._full_timeline_playback:
            seg = self._segment_for_frame(self._playback_ix)
            if seg is not None:
                self._review_seg_ix = seg.seq - 1
        self._show_playback_frame(self._playback_ix, seg=seg)

    def _finish_full_timeline_playback(self) -> None:
        self._playback_mode = None
        self._full_timeline_playback = False
        self._set_run_timer(False)
        if shot_audio_enabled():
            self._shot_player.stop()
        if self._reactor is not None:
            self.controls.mark_step_done("review")
            self._finish_review_session()

    def _end_playback_segment(self) -> None:
        finished_ix = self._review_seg_ix
        auto = self._review_auto

        self._playback_mode = None
        self._set_run_timer(False)
        if shot_audio_enabled():
            self._shot_player.stop()

        if self._reactor is None:
            return

        seg = (
            self._review_segments[finished_ix]
            if 0 <= finished_ix < len(self._review_segments)
            else None
        )
        if seg is not None and seg.phase == "quiescent":
            self._finish_review_session()
            return

        self._review_segment_done = not auto
        if seg is not None:
            self._show_playback_frame(max(seg.start_ix, seg.end_ix - 1), seg=seg)

        if seg is not None:
            tail = "Press Step for next segment." if not auto else ""
            if auto and finished_ix + 1 >= len(self._review_segments):
                tail = "Full shot review complete."
                self.controls.mark_step_done("review")
            if self._recorder.active:
                tail = (tail + "  |  Rec Save when done.").strip()
            msg = f"{seg.subtitle}  —  {tail}".strip(" —")
            self.statusBar().showMessage(msg)

    def _finish_review_session(self) -> None:
        if self._reactor is None:
            return
        self.controls.mark_step_done("review")
        self._reactor.enter_quiescent()
        self._reactor.shot_phase = ShotPhase.QUIESCENT
        if self._review_segments:
            self._reactor.shot_callout = self._review_segments[-1].callout
        else:
            self._reactor.shot_callout = self._reactor.shot_ops().quiescent_callout
        self._auto_paused_after_shot = True
        self.canvas.end_playback()
        self.diagnostics.end_playback()
        self.canvas.attach(self._reactor, self.backend.label)
        self._sync_shot_ui()
        self._update_readout()

    def _on_skip_to_discharge(self) -> None:
        if self._playback_mode is not None:
            return
        if self._reactor is None or not self._reactor.skip_to_discharge():
            return
        self.canvas.attach(self._reactor, self.backend.label)
        self._sync_shot_ui()
        self.statusBar().showMessage(self._reactor.shot_callout)

    def _on_controls_changed(self, values: dict) -> None:
        if self._reactor is None:
            return
        cached_opt = get_optimize(self._recorder_reactor_name())
        if cached_opt is not None and not controls_near(values, cached_opt.controls):
            self.controls.unmark_steps("optimize")
        self._clear_compiled_playback()
        self.controls.unmark_steps("compile", "review", "rec_start", "rec_save")
        self._reactor.apply_controls(values)
        # HB11/LPP rebuild conductor masks when controls change; TAE only updates B_z.
        if self._reactor_needs_geometry_refresh():
            self.canvas.attach(self._reactor, self.backend.label)
        else:
            self.canvas.refresh()
        self._update_readout()
        self._try_restore_compile_cache()

    def _set_run_timer(self, running: bool) -> None:
        self._playing = running
        if not running:
            self._timer.stop()
            if self._playback_mode is None:
                self._shot_player.stop()
        else:
            self._timer.start()

    # -- optimizer ----------------------------------------------------------
    def _on_optimize(self) -> None:
        if self._reactor is None or self._opt_thread is not None:
            return
        reactor_name = self._recorder_reactor_name()
        cached = get_optimize(reactor_name)
        if cached is not None:
            self.controls.set_values(cached.controls)
            self._reactor.apply_controls(self.controls.current_values())
            self.controls.mark_step_done("optimize")
            self._update_readout()
            if self._try_restore_compile_cache():
                note = "  |  Compile restored from cache."
            else:
                note = "  |  Press Compile (cached after first full compile)."
            self.statusBar().showMessage(
                f"Restored cached optimize — Q_net = {cached.q_net:.3e}  "
                f"({cached.n_evaluations} evaluations){note}"
            )
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
        self.controls.mark_step_done("optimize")
        # Apply the optimum to the sliders (quantized); cache those values for stable keys.
        self.controls.set_values(result.controls)
        if self._reactor is not None:
            self._reactor.apply_controls(self.controls.current_values())
            self._update_readout()
            stored = OptimizeResult(
                controls=self.controls.current_values(),
                q_net=result.q_net,
                n_evaluations=result.n_evaluations,
            )
            put_optimize(self._recorder_reactor_name(), stored)
            compile_note = ""
            if self._try_restore_compile_cache():
                compile_note = "  |  Compile restored from cache."
            pretty = ", ".join(
                f"{k}={v:.3g}" for k, v in self.controls.current_values().items()
            )
            self.statusBar().showMessage(
                f"Optimal Q_net = {result.q_net:.3e}  at  {pretty}   "
                f"({result.n_evaluations} evaluations){compile_note}  |  "
                f"Engine: {self.backend.label}"
            )
            return
        put_optimize(self._recorder_reactor_name(), result)

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

    def _on_record_start(self) -> None:
        if not self.controls.can_run_step("rec_start")[0]:
            self.statusBar().showMessage(self.controls.can_run_step("rec_start")[1])
            return
        self._recorder.start(reactor_name=self._recorder_reactor_name())
        if self._compiled_playback is not None:
            self._attach_recorder_compiled_audio(self._compiled_playback.mixed_audio)
        self.controls.mark_step_done("rec_start")
        self.controls.set_recording_active(True)
        self.statusBar().showMessage(
            "Recording… Compile, then Play or Step through the shot, then Rec Save."
        )

    def _on_record_save(self) -> None:
        ok, reason = self.controls.can_run_step("rec_save")
        if not ok:
            self.statusBar().showMessage(reason)
            return
        if self._compiled_playback is not None:
            compiled = self._compiled_playback
            self._recorder.set_compiled_export(
                canvas_frames=compiled.canvas_frames,
                diag_frames=compiled.diag_frames,
                meta=list(compiled.meta),
                reactor_name=self._recorder_reactor_name(),
            )
            self._attach_recorder_compiled_audio(compiled.mixed_audio)
        elif self._recorder.has_frames() and shot_audio_enabled():
            self._compile_recorded_audio_for_mp4()
        else:
            self.statusBar().showMessage("Compile first — nothing to export.")
            return
        saved = self._recorder.stop(self, default_name=self._recording_default_name())
        self.controls.set_recording_active(False)
        if saved[0]:
            self.controls.mark_step_done("rec_save")
        if saved[0] and not saved[1]:
            self.statusBar().showMessage(f"MP4 saved (voice + subtitles): {saved[0]}")
        elif saved[0] and saved[1]:
            QtWidgets.QMessageBox.warning(self, "MP4 encode", saved[1])
            self.statusBar().showMessage(f"Saved fallback: {saved[0]}")
        else:
            self.statusBar().showMessage("Export cancelled.")

    # -- main loop ----------------------------------------------------------
    def _on_tick(self) -> None:
        if self._reactor is None:
            return
        if self._playback_mode is not None:
            self._advance_playback()
            if self._recorder.active and self._compiled_playback is not None:
                ix = max(0, min(self._playback_ix, len(self._compiled_playback.meta) - 1))
                pb = self._compiled_playback
                png = compose_png_horizontal(
                    pb.canvas_frames[ix],
                    pb.diag_frames[ix],
                    panel_size=self._recorder.export_panel_size(),
                )
                self._recorder.add_frame(
                    png,
                    phase=pb.meta[ix].phase,
                    fast_forward=pb.meta[ix].fast_forward,
                    intensity=pb.meta[ix].intensity,
                )
            self._frame += 1
            if self._frame % 3 == 0:
                self._update_readout()
            return

        r = self._reactor
        substeps = substeps_per_frame(r)

        # Compiled review holds the last frame; do not overwrite with idle HUD.
        if (
            self._compiled_playback is not None
            and self._review_seg_ix >= 0
            and self.canvas.is_playback_visible()
        ):
            return

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
                self._set_run_timer(False)
                self.statusBar().showMessage(
                    f"{r.shot_callout}  —  Compile, then Play or Step."
                )
            return

        prev_phase = r.shot_phase
        speed_mode = hud_speed_mode(r)
        ff = is_fast_gui_frame(r)
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
            if self._recorder.active and self._recorder.has_frames():
                self._compile_recorded_audio_for_mp4()
            self._set_run_timer(False)
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
        lines = self.controls.readout_control_lines()
        if lines:
            lines.append("")
        lines += [
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
    """Precompute all fixed callout strings before the dashboard opens."""
    import os

    from pb11_reactor_sim.gui.narration_cache import all_narration_lines, ensure_cache_dir

    ensure_cache_dir()
    if not narration_enabled():
        return
    if os.environ.get("PB11_SKIP_CACHE_WARM", "").strip().lower() in ("1", "true", "yes"):
        return

    from pb11_reactor_sim.gui.narration_cache import ensure_narration_cache

    n_lines = len(all_narration_lines())
    dlg = QtWidgets.QProgressDialog(
        f"Preparing {n_lines} voice callouts (first run may take a few minutes)…",
        None,
        0,
        max(n_lines, 1),
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

    from pb11_reactor_sim.gui.narration_cache import ensure_cache_dir

    ensure_cache_dir()
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
