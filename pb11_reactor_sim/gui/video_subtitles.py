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

"""
White subtitle overlay for exported MP4 frames.
"""
from __future__ import annotations

from io import BytesIO

from pb11_reactor_sim.gui.chattts_narration import wrap_subtitle_lines


def burn_subtitle(png: bytes, text: str | None) -> bytes:
    """Return ``png`` with subtitles in a dedicated strip below the plot (no crop)."""
    if not text or not text.strip():
        return png
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return png

    img = Image.open(BytesIO(png)).convert("RGBA")
    w, h = img.size
    margin = max(14, w // 72)
    font_size = max(18, w // 52)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
    except OSError:
        font = ImageFont.load_default()

    lines = wrap_subtitle_lines(text, max_chars=54)
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    _, descent = font.getmetrics()
    line_gap = 10
    y_probe = 0
    for line in lines:
        bbox = probe.textbbox((0, y_probe), line, font=font)
        y_probe = bbox[3] + line_gap
    strip_h = (y_probe - (probe.textbbox((0, 0), lines[0], font=font)[1] if lines else 0)) + 2 * margin + descent + 12

    canvas = Image.new("RGBA", (w, h + strip_h), (0, 0, 0, 255))
    canvas.paste(img, (0, 0))
    draw = ImageDraw.Draw(canvas)
    bar_top = h + margin // 2
    bar_bottom = h + strip_h - margin // 2
    draw.rectangle((margin, bar_top, w - margin, bar_bottom), fill=(0, 0, 0, 150))
    y = h + margin
    for line in lines:
        bbox = draw.textbbox((0, y), line, font=font)
        tw = bbox[2] - bbox[0]
        draw.text(((w - tw) // 2, y), line, fill=(255, 255, 255, 255), font=font)
        y = bbox[3] + line_gap

    out = canvas.convert("RGB")
    buf = BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue()
