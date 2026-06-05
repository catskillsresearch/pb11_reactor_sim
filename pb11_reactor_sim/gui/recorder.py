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
Capture GUI frames to MP4 for presentation export.

Composites the spatial canvas and the diagnostics panel (temperature, power, Q)
into each frame. Writes via ``imageio`` when installed, otherwise falls back
to ``ffmpeg`` or a numbered PNG sequence.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from collections.abc import Callable
from io import BytesIO
from pathlib import Path

from PySide6 import QtCore, QtWidgets

import numpy as np

from pb11_reactor_sim.gui.audio_synth import FPS, SAMPLE_RATE, FrameMeta, pad_audio, write_wav
from pb11_reactor_sim.gui.chattts_narration import narration_enabled
from pb11_reactor_sim.gui.export_timeline import BED_DUCK_FACTOR, BED_LEVEL, ExportTimeline, build_export_timeline
from pb11_reactor_sim.gui.session_cache import cache_root

FPS = 30.0


class FrameRecorder:
    """Accumulates PNG frames; exports on :meth:`stop`."""

    def __init__(self) -> None:
        self._frames: list[bytes] = []
        self._meta: list[FrameMeta] = []
        self._reactor_name: str = ""
        self._frame_size: tuple[int, int] | None = None
        self._compiled_mixed: np.ndarray | None = None
        self._export_canvas: list[bytes] | None = None
        self._export_diag: list[bytes] | None = None
        self._export_meta: list[FrameMeta] | None = None
        self._panel_size: tuple[int, int, int] | None = None  # left_w, right_w, h
        self.active = False

    def set_compiled_audio(self, mixed: np.ndarray) -> None:
        """Use pre-mixed compile audio for MP4 mux (matches Play/Step review)."""
        self._compiled_mixed = np.asarray(mixed, dtype=np.float32).reshape(-1)

    def export_panel_size(self) -> tuple[int, int, int] | None:
        """Fixed (left_w, right_w, h) for stable composed frames, if compile export is set."""
        return self._panel_size

    def set_compiled_export(
        self,
        *,
        canvas_frames: list[bytes],
        diag_frames: list[bytes],
        meta: list[FrameMeta],
        reactor_name: str,
    ) -> None:
        """Prefer compile PNGs for MP4 (compose once, subtitle once)."""
        self._export_canvas = list(canvas_frames)
        self._export_diag = list(diag_frames)
        self._export_meta = list(meta)
        self._reactor_name = reactor_name
        self._panel_size = _panel_size_for_export(canvas_frames, diag_frames)

    def start(self, *, reactor_name: str = "") -> None:
        self._frames.clear()
        self._meta.clear()
        self._reactor_name = reactor_name
        self._frame_size = None
        self._compiled_mixed = None
        self.active = True

    def has_frames(self) -> bool:
        return bool(self._frames)

    def captured_frames(self) -> tuple[list[bytes], list[FrameMeta]]:
        return self._frames, self._meta

    def add_frame(
        self,
        png: bytes | None,
        *,
        phase: str = "",
        fast_forward: bool = False,
        intensity: float = 0.0,
    ) -> None:
        if self.active and png:
            norm, size = _normalize_png_for_video(png, self._frame_size)
            if self._frame_size is None:
                self._frame_size = size
            self._frames.append(norm)
            self._meta.append(
                FrameMeta(phase=phase, fast_forward=fast_forward, intensity=intensity)
            )

    def add_png(self, png: bytes | None) -> None:
        """Legacy capture without phase metadata (silent/generic audio)."""
        self.add_frame(png)

    def stop(
        self,
        parent: QtWidgets.QWidget | None = None,
        *,
        default_name: str = "pb11_reactor_shot.mp4",
    ) -> tuple[str | None, str | None]:
        """Prompt for save path and write the movie.

        Uses compile export when set; otherwise captured GUI frames.

        Returns ``(saved_path, error_message)``.
        """
        self.active = False
        has_compile = bool(self._export_canvas and self._export_meta)
        if not self._frames and not has_compile:
            return None, None
        if not default_name.lower().endswith(".mp4"):
            default_name = f"{default_name}.mp4"
        out_dir = cache_root()
        out_dir.mkdir(parents=True, exist_ok=True)
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            parent,
            "Save simulation recording",
            str(out_dir / default_name),
            "MP4 video (*.mp4);;PNG sequence (*.png)",
        )
        if not path:
            return None, None
        out = Path(path)
        dlg = QtWidgets.QProgressDialog(
            "Preparing export…",
            None,
            0,
            4,
            parent,
        )
        dlg.setWindowTitle("Save MP4")
        dlg.setWindowModality(QtCore.Qt.WindowModality.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setValue(0)
        dlg.show()
        QtWidgets.QApplication.processEvents()

        def progress(step: int, label: str) -> None:
            dlg.setMaximum(4)
            dlg.setValue(min(step, 4))
            dlg.setLabelText(label)
            QtWidgets.QApplication.processEvents()

        try:
            if out.suffix.lower() == ".png":
                progress(1, "Writing PNG sequence…")
                return self._write_png_sequence(out), None
            return self._write_mp4(out, parent=parent, progress=progress)
        finally:
            dlg.close()

    def _write_mp4(
        self,
        path: Path,
        *,
        parent: QtWidgets.QWidget | None = None,
        progress: Callable[[int, str], None] | None = None,
    ) -> tuple[str | None, str | None]:
        def step(i: int, label: str) -> None:
            if progress is not None:
                progress(i, label)

        step(
            0,
            "Building narration timeline (voice, subtitles, holds)…"
            if narration_enabled()
            else "Building export timeline…",
        )
        timeline = self._build_export_timeline(parent)
        if timeline is not None:
            frames = timeline.frames
            meta = timeline.meta
        else:
            frames = self._frames
            meta = self._meta

        step(1, f"Encoding video ({len(frames)} frames)…")
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg:
            ff_err = self._write_mp4_ffmpeg(frames, path, ffmpeg)
            if ff_err is None:
                step(2, "Mixing reactor bed and narration…")
                saved = self._mux_export_audio(path, timeline, meta, parent=parent)
                step(3, "MP4 save complete.")
                return saved
            err = ff_err
        else:
            err = self._write_mp4_imageio(frames, path)
            if err is None:
                step(2, "Mixing reactor bed and narration…")
                saved = self._mux_export_audio(path, timeline, meta, parent=parent)
                step(3, "MP4 save complete.")
                return saved

        step(2, "Saving PNG fallback…")
        fallback = self._write_png_sequence(path.with_suffix(".png"))
        step(3, "Finished (fallback).")
        return fallback, (
            f"MP4 encode failed ({err}); saved PNG sequence instead:\n{fallback}"
        )

    def _build_export_timeline(self, parent: QtWidgets.QWidget | None):
        try:
            if self._export_canvas and self._export_diag and self._export_meta:
                composed = compose_export_frames(
                    self._export_canvas,
                    self._export_diag,
                    panel_size=self._panel_size,
                )
                return build_export_timeline(
                    composed,
                    self._export_meta,
                    reactor_name=self._reactor_name,
                    fps=FPS,
                    with_subtitles=True,
                )
            return build_export_timeline(
                self._frames,
                self._meta,
                reactor_name=self._reactor_name,
                fps=FPS,
                with_subtitles=True,
            )
        except Exception as exc:  # noqa: BLE001
            if parent is not None:
                win = parent if isinstance(parent, QtWidgets.QMainWindow) else parent.window()
                if isinstance(win, QtWidgets.QMainWindow):
                    win.statusBar().showMessage(f"Export timeline failed ({exc}); using raw frames.")
            return None

    def _mux_export_audio(
        self,
        path: Path,
        timeline: ExportTimeline | None,
        meta: list[FrameMeta],
        *,
        parent: QtWidgets.QWidget | None = None,
    ) -> tuple[str | None, str | None]:
        """Mix 2× reactor bed + narration; video length matches audio."""
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg or not meta:
            return str(path), None
        try:
            if parent is not None:
                win = parent if isinstance(parent, QtWidgets.QMainWindow) else parent.window()
                if isinstance(win, QtWidgets.QMainWindow):
                    win.statusBar().showMessage("Mixing reactor bed and narration…")
                QtWidgets.QApplication.processEvents()

            if self._compiled_mixed is not None and self._compiled_mixed.size:
                mixed = self._compiled_mixed
            elif timeline is not None:
                from pb11_reactor_sim.gui.shot_audio import mix_shot_audio

                mixed = mix_shot_audio(timeline.meta, timeline.segments, timeline.duration_s)
            else:
                from pb11_reactor_sim.gui.audio_synth import synthesize_shot_audio
                from pb11_reactor_sim.gui.export_timeline import BED_DUCK_FACTOR, BED_LEVEL, mix_tracks

                bed = synthesize_shot_audio(meta, fps=FPS)
                n_samples = bed.size
                mixed = mix_tracks(bed, None, bed_level=BED_LEVEL)

            if timeline is not None:
                n_samples = int(round(timeline.duration_s * SAMPLE_RATE))
            else:
                n_samples = mixed.size
            mixed = pad_audio(mixed, max(n_samples, mixed.size))

            with tempfile.TemporaryDirectory(prefix="pb11_mux_") as tmp:
                td = Path(tmp)
                wav = td / "shot.wav"
                out = td / "muxed.mp4"
                write_wav(wav, mixed)
                cmd = [
                    ffmpeg,
                    "-y",
                    "-i",
                    str(path),
                    "-i",
                    str(wav),
                    "-c:v",
                    "copy",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "192k",
                    str(out),
                ]
                proc = subprocess.run(cmd, capture_output=True, text=True)
                if proc.returncode != 0:
                    detail = (proc.stderr or proc.stdout or "").strip()[-400:]
                    return str(path), f"Audio mux skipped ({detail})"
                out.replace(path)
            return str(path), None
        except Exception as exc:  # noqa: BLE001
            return str(path), f"Audio mux skipped ({exc})"

    def _write_mp4_imageio(self, frames: list[bytes], path: Path) -> str | None:
        try:
            import imageio.v3 as iio  # type: ignore[import-untyped]
            import numpy as np
            from PIL import Image

            imgs = [np.asarray(Image.open(BytesIO(png))) for png in frames]
            iio.imwrite(path, imgs, fps=30, codec="libx264")
            return None
        except ImportError:
            return "imageio/Pillow not installed"
        except Exception as exc:  # noqa: BLE001
            return f"imageio: {exc}"

    def _write_mp4_ffmpeg(self, frames: list[bytes], path: Path, ffmpeg: str) -> str | None:
        with tempfile.TemporaryDirectory(prefix="pb11_frames_") as tmp:
            td = Path(tmp)
            for i, png in enumerate(frames):
                (td / f"frame_{i:05d}.png").write_bytes(png)
            cmd = [
                ffmpeg,
                "-y",
                "-framerate",
                "30",
                "-i",
                str(td / "frame_%05d.png"),
                # Qt widget grabs are often odd-sized; libx264/yuv420p needs even dims.
                "-vf",
                "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                "-pix_fmt",
                "yuv420p",
                str(path),
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode == 0:
                return None
            detail = (proc.stderr or proc.stdout or "").strip()
            tail = detail[-800:] if len(detail) > 800 else detail
            return f"ffmpeg exit {proc.returncode}: {tail}"

    def _write_png_sequence(self, path: Path) -> str:
        """Write ``stem_00000.png`` … next to ``path`` when MP4 encode fails."""
        stem = path.with_suffix("")
        stem.parent.mkdir(parents=True, exist_ok=True)
        for i, png in enumerate(self._frames):
            (stem.parent / f"{stem.name}_{i:05d}.png").write_bytes(png)
        return str(stem.parent / f"{stem.name}_00000.png")


def _png_dimensions(png: bytes) -> tuple[int, int]:
    try:
        from PIL import Image

        with Image.open(BytesIO(png)) as img:
            return img.size
    except Exception:
        return (0, 0)


def _panel_size_for_export(
    canvas_frames: list[bytes],
    diag_frames: list[bytes],
) -> tuple[int, int, int]:
    """Fixed (left_w, right_w, h) so every composed frame has identical layout."""
    lw = rh = h = 0
    for png in canvas_frames:
        w, ph = _png_dimensions(png)
        lw = max(lw, w)
        h = max(h, ph)
    for png in diag_frames:
        w, ph = _png_dimensions(png)
        rh = max(rh, w)
        h = max(h, ph)
    return (max(1, lw), max(1, rh), max(1, h))


def _fit_panel(png: bytes, box_w: int, box_h: int):
    from PIL import Image

    img = Image.open(BytesIO(png)).convert("RGB")
    scale = min(box_w / img.width, box_h / img.height)
    nw = max(1, int(round(img.width * scale)))
    nh = max(1, int(round(img.height * scale)))
    if (nw, nh) != img.size:
        img = img.resize((nw, nh), Image.Resampling.LANCZOS)
    panel = Image.new("RGB", (box_w, box_h), (0, 0, 0))
    panel.paste(img, ((box_w - nw) // 2, (box_h - nh) // 2))
    return panel


def compose_png_horizontal(
    left: bytes | None,
    right: bytes | None,
    *,
    panel_size: tuple[int, int, int] | None = None,
) -> bytes | None:
    """Stitch canvas (left) and diagnostics (right); optional fixed panel geometry."""
    if not left and not right:
        return None
    if not left:
        return right
    if not right:
        return left
    try:
        from PIL import Image

        if panel_size is not None:
            lw, rw, h = panel_size
            a = _fit_panel(left, lw, h)
            b = _fit_panel(right, rw, h)
            out = Image.new("RGB", (lw + rw, h), (0, 0, 0))
            out.paste(a, (0, 0))
            out.paste(b, (lw, 0))
        else:
            a = Image.open(BytesIO(left)).convert("RGB")
            b = Image.open(BytesIO(right)).convert("RGB")
            h = a.height
            bw = max(1, int(round(b.width * h / b.height)))
            b = b.resize((bw, h), Image.Resampling.LANCZOS)
            out = Image.new("RGB", (a.width + bw, h))
            out.paste(a, (0, 0))
            out.paste(b, (a.width, 0))
        buf = BytesIO()
        out.save(buf, format="PNG")
        return buf.getvalue()
    except ImportError:
        return left


def compose_export_frames(
    canvas_frames: list[bytes],
    diag_frames: list[bytes],
    *,
    panel_size: tuple[int, int, int] | None = None,
) -> list[bytes]:
    """Compose every canvas/diag pair with identical panel dimensions."""
    size = panel_size or _panel_size_for_export(canvas_frames, diag_frames)
    out: list[bytes] = []
    for left, right in zip(canvas_frames, diag_frames):
        png = compose_png_horizontal(left, right, panel_size=size)
        if png:
            out.append(png)
    return out


def _normalize_png_for_video(
    png: bytes,
    frame_size: tuple[int, int] | None = None,
) -> tuple[bytes, tuple[int, int]]:
    """Crop to even width/height so H.264 encoders accept the frames."""
    del frame_size  # reserved for future fixed-size capture
    try:
        from PIL import Image

        img = Image.open(BytesIO(png)).convert("RGB")
        w, h = img.size
        ew, eh = w - (w % 2), h - (h % 2)
        if ew < 2 or eh < 2:
            return png, (w, h)
        if (ew, eh) != (w, h):
            img = img.crop((0, 0, ew, eh))
        out = BytesIO()
        img.save(out, format="PNG")
        return out.getvalue(), (ew, eh)
    except ImportError:
        return png, (0, 0)
