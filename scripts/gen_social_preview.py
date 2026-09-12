#!/usr/bin/env python
"""Generate the repository social preview card (1280x640, GitHub's size).

Uploaded manually via Settings -> Social preview, because there is no API
scope for it here. Re-run whenever the tagline changes so the card and the
README never disagree.

Requires Pillow and Windows' bundled Consolas fonts:
    python scripts/gen_social_preview.py
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1280, 640
BACKGROUND = "#0F172A"
ACCENT = "#5DCAA5"
HUB_LINE = "#1E3A5F"
TEXT_TITLE = "#F8FAFC"
TEXT_SUB = "#94A3B8"
TEXT_DIM = "#64748B"
TEXT_FAINT = "#475569"
DEVICE_SOLID = "#185FA5"
DEVICE_OUTLINE = "#378ADD"

FONTS = Path("C:/Windows/Fonts")


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / name), size)


def draw_device_pool(image: ImageDraw.ImageDraw, cx: int, cy: int) -> None:
    """A hub with five devices around it, one actively leased."""
    radius = 150
    for index, angle in enumerate((200, 270, 340, 40, 110)):
        x = cx + radius * math.cos(math.radians(angle))
        y = cy + radius * math.sin(math.radians(angle))
        image.line((cx, cy, x, y), fill=HUB_LINE, width=2)
        box = (x - 18, y - 18, x + 18, y + 18)
        if index == 1:
            image.ellipse(box, fill=DEVICE_SOLID)
        else:
            image.ellipse(box, outline=DEVICE_OUTLINE, width=3)

    image.ellipse((cx - 32, cy - 32, cx + 32, cy + 32), outline=ACCENT, width=4)
    image.ellipse((cx - 8, cy - 8, cx + 8, cy + 8), fill=ACCENT)


def main() -> int:
    out = Path(__file__).resolve().parent.parent / "docs" / "social-preview.png"

    image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(image)

    draw_device_pool(draw, cx=1060, cy=280)

    draw.text((80, 84), "BUILT ON MAESTRO OFFICIAL MCP", font=font("consolab.ttf", 22), fill=ACCENT)
    draw.text((72, 126), "maestro-plus", font=font("consolab.ttf", 88), fill=TEXT_TITLE)
    draw.text(
        (80, 258),
        "Multi-device pools · Assertions · Failure diagnosis",
        font=font("consola.ttf", 24),
        fill=TEXT_SUB,
    )
    draw.text(
        (80, 340),
        "health_check   list_device_pool   run_parallel   run_and_assert",
        font=font("consola.ttf", 20),
        fill=TEXT_DIM,
    )
    draw.text(
        (80, 376),
        "assert_visual   debug_failure   explore_and_record",
        font=font("consola.ttf", 20),
        fill=TEXT_DIM,
    )
    draw.text(
        (80, 540), "github.com/Fwmouomu/maestro-plus", font=font("consola.ttf", 24), fill=TEXT_FAINT
    )

    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(out)
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
