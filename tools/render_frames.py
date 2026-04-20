# SPDX-FileCopyrightText: (c) 2026 Kilian
# SPDX-License-Identifier: Apache-2.0
#
# Render the same ptr_counter values the cocotb test dumps, so visuals can be
# compared 1:1 against test/output/frame_waves_*_pc*.png.

from pathlib import Path
from PIL import Image

from renderer import Params, render_frame


OUT = Path(__file__).parent / "output"
PTR_VALUES = [2, 64, 200, 400, 800, 1024]


def main() -> None:
    OUT.mkdir(exist_ok=True)
    p = Params()
    for i, pc in enumerate(PTR_VALUES):
        frame = render_frame(pc, p)
        path = OUT / f"numpy_waves_{i}_pc{pc}.png"
        Image.fromarray(frame, "RGB").save(path)
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
