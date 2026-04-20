# SPDX-FileCopyrightText: (c) 2026 Kilian
# SPDX-License-Identifier: Apache-2.0
#
# Contact sheet of visual variants at a single ptr_counter. Lets us eyeball
# which knob produces the nicest interference pattern before committing to it.

from dataclasses import replace
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

from renderer import Params, render_frame, W, H


OUT = Path(__file__).parent / "output"

# Each variant: (label, overrides on Params). Baseline first.
VARIANTS = [
    ("baseline dots",              dict()),
    ("pixel plasma, bits[10:8]",   dict(mode="pixel", bright_lsb=8, bright_bits=3)),
    ("pixel plasma, bits[11:9]",   dict(mode="pixel", bright_lsb=9, bright_bits=3)),
    ("pixel plasma, bits[12:10]",  dict(mode="pixel", bright_lsb=10, bright_bits=3)),
    ("pixel plasma, bits[9:7]",    dict(mode="pixel", bright_lsb=7, bright_bits=3)),
    ("pixel plasma, bits[8:6]",    dict(mode="pixel", bright_lsb=6, bright_bits=3)),
    ("pixel plasma, 2-bit",        dict(mode="pixel", bright_lsb=9, bright_bits=2)),
    ("pixel plasma, 4-bit",        dict(mode="pixel", bright_lsb=8, bright_bits=4)),
    ("pixel+dot-gate",             dict(mode="pixeldots", bright_lsb=9, bright_bits=3)),
    ("pixel plasma + cyan",        dict(mode="pixel", palette=1, bright_lsb=9, bright_bits=3)),
    ("pixel plasma + magenta",     dict(mode="pixel", palette=2, bright_lsb=9, bright_bits=3)),
    ("pixel plasma + yellow",      dict(mode="pixel", palette=3, bright_lsb=9, bright_bits=3)),
]

PTR = 400
COLS = 4
LABEL_H = 22
SCALE = 2  # shrink for contact sheet (1 = full, 2 = half, ...)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    base = Params()
    cell_w, cell_h = W // SCALE, H // SCALE
    rows = (len(VARIANTS) + COLS - 1) // COLS
    sheet = Image.new("RGB", (cell_w * COLS, (cell_h + LABEL_H) * rows), (16, 16, 16))
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 14)
    except OSError:
        font = ImageFont.load_default()

    for i, (label, overrides) in enumerate(VARIANTS):
        p = replace(base, **overrides)
        frame = render_frame(PTR, p)
        img = Image.fromarray(frame, "RGB").resize((cell_w, cell_h), Image.NEAREST)
        r, c = divmod(i, COLS)
        x = c * cell_w
        y = r * (cell_h + LABEL_H)
        sheet.paste(img, (x, y + LABEL_H))
        draw = ImageDraw.Draw(sheet)
        draw.text((x + 4, y + 2), f"{i}: {label}", fill=(240, 240, 240), font=font)

    path = OUT / f"variants_pc{PTR}.png"
    sheet.save(path)
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
