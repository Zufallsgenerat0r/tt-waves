# SPDX-FileCopyrightText: (c) 2026 Kilian
# SPDX-License-Identifier: Apache-2.0
#
# Render a short MP4 of the selected variant. Motion is the real test — a still
# frame can't tell you whether the pattern feels alive or stuttered.

import argparse
import subprocess
from dataclasses import replace
from pathlib import Path
import numpy as np
from PIL import Image

from renderer import Params, render_frame


OUT = Path(__file__).parent / "output"

# Named presets — keep the promising ones from the variant grid addressable.
PRESETS: dict[str, dict] = {
    "baseline":   dict(),
    "plasma9":    dict(mode="pixel", bright_lsb=9, bright_bits=3),
    "plasma10":   dict(mode="pixel", bright_lsb=10, bright_bits=3),
    "plasma9-c":  dict(mode="pixel", bright_lsb=9, bright_bits=3, palette=1),
    "plasma9-m":  dict(mode="pixel", bright_lsb=9, bright_bits=3, palette=2),
    "pixeldots":       dict(mode="pixeldots", bright_lsb=9, bright_bits=3, bright_floor=0),
    "pixeldots-floor": dict(mode="pixeldots", bright_lsb=9, bright_bits=3, bright_floor=1),
    "pixeldots-cycle": dict(mode="pixeldots", bright_lsb=9, bright_bits=3, bright_floor=0,
                             palette_auto=True, palette_shift=6),
    "pixeldots-breath": dict(mode="pixeldots", bright_lsb=9, bright_bits=3, bright_floor=0,
                              palette_auto=True, palette_shift=6,
                              breath=True, breath_shift=3, breath_floor=4),
    "dotsfull":        dict(mode="dotsfull",
                             palette_auto=True, palette_shift=6,
                             breath=True, breath_shift=3, breath_floor=4),
    "pixeldots-dual":  dict(mode="pixeldots", bright_lsb=9, bright_bits=3, bright_floor=0,
                             palette_auto=True, palette_shift=6,
                             breath=True, breath_shift=3, breath_floor=4,
                             dual_color=True, palette_b_offset=8),
    "dotsfull-dual":   dict(mode="dotsfull",
                             palette_auto=True, palette_shift=6,
                             breath=True, breath_shift=3, breath_floor=4,
                             dual_color=True, palette_b_offset=8),
    "cells11":    dict(mode="cells", bright_lsb=9, bright_bits=3),
}


def render_animation(preset: str, frames: int, start: int, step: int, fps: int) -> Path:
    if preset not in PRESETS:
        raise SystemExit(f"Unknown preset {preset!r}; choose: {', '.join(PRESETS)}")
    p = replace(Params(), **PRESETS[preset])

    tmp = OUT / f"_anim_{preset}"
    tmp.mkdir(parents=True, exist_ok=True)
    for old in tmp.glob("*.png"):
        old.unlink()

    for i in range(frames):
        pc = start + i * step
        frame = render_frame(pc, p)
        Image.fromarray(frame, "RGB").save(tmp / f"f{i:04d}.png")

    mp4 = OUT / f"anim_{preset}_{frames}f.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-framerate", str(fps),
        "-i", str(tmp / "f%04d.png"),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18",
        str(mp4),
    ], check=True)
    return mp4


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("preset", nargs="?", default="plasma9")
    ap.add_argument("--frames", type=int, default=240)   # ~4s @ 60fps
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--step", type=int, default=1)       # Verilog ticks ptr+=1 per frame
    ap.add_argument("--fps", type=int, default=60)
    args = ap.parse_args()
    mp4 = render_animation(args.preset, args.frames, args.start, args.step, args.fps)
    print(f"wrote {mp4}")


if __name__ == "__main__":
    main()
