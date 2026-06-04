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


def _subtitle_block_height(draw, lines: list[str], font, *, margin: int) -> int:
    total = 0
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        total += bbox[3] - bbox[1] + 6
    return total + 2 * margin


def burn_subtitle(png: bytes, text: str | None) -> bytes:
    """Return ``png`` with centered white subtitle bar when ``text`` is non-empty."""
    if not text or not text.strip():
        return png
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return png

    img = Image.open(BytesIO(png)).convert("RGBA")
    w, h = img.size
    margin = max(12, w // 80)
    font_size = max(18, w // 52)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
    except OSError:
        font = ImageFont.load_default()

    lines = wrap_subtitle_lines(text, max_chars=54)
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    block_h = _subtitle_block_height(probe, lines, font, margin=margin)
    # Extra strip below the plot so descenders (e.g. "y") are never clipped.
    pad_bottom = block_h + margin
    canvas = Image.new("RGBA", (w, h + pad_bottom), (0, 0, 0, 255))
    canvas.paste(img, (0, 0))
    draw = ImageDraw.Draw(canvas)
    h_total = h + pad_bottom
    y0 = h_total - block_h - margin // 2
    draw.rectangle((margin, y0, w - margin, h_total - margin // 2), fill=(0, 0, 0, 150))
    y = y0 + margin
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        draw.text(((w - tw) // 2, y), line, fill=(255, 255, 255, 255), font=font)
        y += th + 6

    out = canvas.convert("RGB")
    buf = BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue()
