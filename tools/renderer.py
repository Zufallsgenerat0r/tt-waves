# SPDX-FileCopyrightText: (c) 2026 Kilian
# SPDX-License-Identifier: Apache-2.0
#
# Numpy reference renderer for the tt-waves dot-lattice demo.
#
# Produces frames that visually match src/project.v (Lissajous pointer + two
# radial sources, per-cell displacement from (2*r1 + r2) mod 2^14, 5x5 dots on
# a 16-px lattice). The renderer is the iteration surface: try new knobs here,
# port the winners back to Verilog. Parity with test/output/frame_waves_*.png
# is verified in verify_parity.py (not bit-exact at 1-2 px because the Verilog
# pipeline shifts the per-cell latch; visual parity is the contract).

from __future__ import annotations
from dataclasses import dataclass, field
import numpy as np

W, H = 640, 480
LATTICE = 16
DOT_R = 2
MASK14 = 0x3FFF
MASK15 = 0x7FFF


@dataclass
class Params:
    palette: int = 0          # 0=white, 1=cyan, 2=magenta, 3=yellow
    slow: bool = False        # ui_in[3]: halves Lissajous rate
    clip_to_cell: bool = True # Verilog clips dots at 16-px cell boundary

    # Variant knobs (set == Verilog by default)
    freq_x: int = 3                    # Lissajous x freq (Verilog: 3)
    freq_y: int = 5                    # Lissajous y freq (Verilog: 5)
    disp_sign_bit: int = 10            # ra_lat bit used as disp sign
    disp_mag_lsb: int = 8              # ra_lat bit for disp magnitude LSB
    disp_mag_bits: int = 2             # # magnitude bits (0..3 → 2 bits)
    sat: int = 6                       # dot displacement saturation (±sat)
    r1_weight: int = 2                 # ra = r1_weight*r1 + r2
    accum_bits: int = 14               # r1, r2 accumulator width
    shift_right: int = 3               # ra_lat = ra >> shift_right
    far_threshold: int = 15            # axis-damp: |p| > threshold → far
    ampl_range: int = 64               # pointer amplitude from screen centre
    dot_r: int = 2                     # Chebyshev radius of each dot

    # Render mode: "dots" (displaced 5x5), "cells" (solid per-cell), "bright" (dot brightness).
    mode: str = "dots"
    # For "cells" / "bright": which ra_lat bits map to brightness.
    bright_lsb: int = 8
    bright_bits: int = 2
    bright_floor: int = 0  # pixeldots: min VGA level (1..3) so troughs still glow


def _tri(v: int) -> int:
    """Triangle fold of a 10-bit unsigned into 9-bit 0..511..0."""
    if (v >> 9) & 1:
        return 511 - (v & 0x1FF)
    return v & 0x1FF


def compute_pointer(ptr_counter: int, p: Params) -> tuple[int, int]:
    """Lissajous pointer → (center_ax, center_ay), clamped to ±ampl_range of screen centre."""
    # fx×pc and fy×pc — matches Verilog's {pc<<1} + pc style for 3× and 5×.
    pc3 = (p.freq_x * ptr_counter) & 0x3FFF
    pc5 = (p.freq_y * ptr_counter) & 0x7FFF
    if p.slow:
        px_raw = (pc3 >> 1) & 0x3FF
        py_raw = (pc5 >> 1) & 0x3FF
    else:
        px_raw = pc3 & 0x3FF
        py_raw = pc5 & 0x3FF
    px_tri = _tri(px_raw)
    py_tri = _tri(py_raw)
    offset_x = (px_tri >> 2) - p.ampl_range
    offset_y = (py_tri >> 2) - p.ampl_range
    cax = 320 + offset_x
    cay = 240 + offset_y
    cax = max(320 - p.ampl_range, min(320 + p.ampl_range - 1, cax))
    cay = max(240 - p.ampl_range, min(240 + p.ampl_range - 1, cay))
    return cax, cay


def compute_phase(cax: int, cay: int, p: Params) -> tuple[np.ndarray, np.ndarray]:
    """Per-cell ra_lat, rb_lat grids (shape 30, 40). These are the raw
    modular-r² values; downstream passes map them to dots, color, or brightness."""
    cbx = W - cax
    cby = H - cay
    accum_mask = (1 << p.accum_bits) - 1
    ra_mask = (1 << (p.accum_bits + 1)) - 1
    lat_mask = (1 << (p.accum_bits + 1 - p.shift_right)) - 1
    n_cols = W // LATTICE
    n_rows = H // LATTICE
    cell_xs = np.arange(n_cols) * LATTICE + (LATTICE - 1)
    cell_ys = np.arange(n_rows) * LATTICE + (LATTICE - 1)
    dy_a = cell_ys - cay
    dy_b = cell_ys - cby
    dx_a = cell_xs - cax
    dx_b = cell_xs - cbx
    r1a = (dy_a * dy_a) & accum_mask
    r1b = (dy_b * dy_b) & accum_mask
    r2a = (dx_a * dx_a) & accum_mask
    r2b = (dx_b * dx_b) & accum_mask
    ra = (p.r1_weight * r1a[:, None] + r2a[None, :]) & ra_mask
    rb = (p.r1_weight * r1b[:, None] + r2b[None, :]) & ra_mask
    ra_lat = (ra >> p.shift_right) & lat_mask
    rb_lat = (rb >> p.shift_right) & lat_mask
    return ra_lat, rb_lat, (dx_a, dy_a, dx_b, dy_b)


def compute_displacements(cax: int, cay: int, p: Params) -> tuple[np.ndarray, np.ndarray]:
    """Per-cell (dlx, dly) displacement grid, shape (30, 40)."""
    ra_lat, rb_lat, (dx_a, dy_a, dx_b, dy_b) = compute_phase(cax, cay, p)

    # Signed displacement magnitude from ra_lat.
    mag_mask = (1 << p.disp_mag_bits) - 1
    disp_a_sign = (ra_lat >> p.disp_sign_bit) & 1
    disp_a_mag = (ra_lat >> p.disp_mag_lsb) & mag_mask
    disp_a = np.where(disp_a_sign == 1, -disp_a_mag, disp_a_mag).astype(np.int32)
    disp_b_sign = (rb_lat >> p.disp_sign_bit) & 1
    disp_b_mag = (rb_lat >> p.disp_mag_lsb) & mag_mask
    disp_b = np.where(disp_b_sign == 1, -disp_b_mag, disp_b_mag).astype(np.int32)

    # Axis gating: on-axis (|p| <= far_threshold) → don't push perpendicular.
    # Hides the hard sign flip that would otherwise run along each source's axis.
    sgn_ax = (dx_a < 0).astype(np.int32)
    sgn_bx = (dx_b < 0).astype(np.int32)
    sgn_ay = (dy_a < 0).astype(np.int32)
    sgn_by = (dy_b < 0).astype(np.int32)
    far_ax = (np.abs(dx_a) > p.far_threshold).astype(np.int32)
    far_bx = (np.abs(dx_b) > p.far_threshold).astype(np.int32)
    far_ay = (np.abs(dy_a) > p.far_threshold).astype(np.int32)
    far_by = (np.abs(dy_b) > p.far_threshold).astype(np.int32)

    dlx_a = np.where(far_ax[None, :] == 1,
                     np.where(sgn_ax[None, :] == 1, -disp_a, disp_a), 0)
    dly_a = np.where(far_ay[:, None] == 1,
                     np.where(sgn_ay[:, None] == 1, -disp_a, disp_a), 0)
    dlx_b = np.where(far_bx[None, :] == 1,
                     np.where(sgn_bx[None, :] == 1, -disp_b, disp_b), 0)
    dly_b = np.where(far_by[:, None] == 1,
                     np.where(sgn_by[:, None] == 1, -disp_b, disp_b), 0)

    dlx = np.clip(dlx_a + dlx_b, -p.sat, p.sat).astype(np.int32)
    dly = np.clip(dly_a + dly_b, -p.sat, p.sat).astype(np.int32)
    return dlx, dly


def _palette_rgb(palette: int) -> tuple[int, int, int]:
    if palette == 1:
        return (0, 255, 255)
    if palette == 2:
        return (255, 0, 255)
    if palette == 3:
        return (255, 255, 0)
    return (255, 255, 255)


def _bright_from_lat(lat: np.ndarray, p: Params) -> np.ndarray:
    """Map ra_lat bits → triangle-wave brightness in 0..3 (2-bit VGA levels)."""
    mask = (1 << p.bright_bits) - 1
    val = (lat >> p.bright_lsb) & mask
    # Unfold: use sign bit above to make a triangle — avoids the hard 7→0 jump
    # that would show as a sharp ring boundary.
    sign = (lat >> (p.bright_lsb + p.bright_bits)) & 1
    val = np.where(sign == 1, mask - val, val)
    return val.astype(np.int32)


def render_frame(ptr_counter: int, p: Params | None = None) -> np.ndarray:
    if p is None:
        p = Params()
    cax, cay = compute_pointer(ptr_counter, p)

    if p.mode == "cells":
        return _render_cells(cax, cay, p)
    if p.mode == "bright":
        return _render_bright_dots(cax, cay, p)
    if p.mode == "pixel":
        return _render_pixel_phase(cax, cay, p)
    if p.mode == "pixeldots":
        return _render_pixel_phase_dots(cax, cay, p)

    dlx, dly = compute_displacements(cax, cay, p)
    return _render_dots(dlx, dly, p)


def _render_dots(dlx: np.ndarray, dly: np.ndarray, p: Params) -> np.ndarray:
    frame = np.zeros((H, W, 3), dtype=np.uint8)
    r, g, b = _palette_rgb(p.palette)
    dr = p.dot_r
    n_rows, n_cols = dlx.shape
    for cy in range(n_rows):
        for cx in range(n_cols):
            dot_cx = cx * LATTICE + 8 + int(dlx[cy, cx])
            dot_cy = cy * LATTICE + 8 + int(dly[cy, cx])
            x0 = dot_cx - dr; x1 = dot_cx + dr + 1
            y0 = dot_cy - dr; y1 = dot_cy + dr + 1
            if p.clip_to_cell:
                x0 = max(x0, cx * LATTICE); x1 = min(x1, (cx + 1) * LATTICE)
                y0 = max(y0, cy * LATTICE); y1 = min(y1, (cy + 1) * LATTICE)
            x0 = max(x0, 0); x1 = min(x1, W)
            y0 = max(y0, 0); y1 = min(y1, H)
            if x1 > x0 and y1 > y0:
                frame[y0:y1, x0:x1] = (r, g, b)
    return frame


def _render_cells(cax: int, cay: int, p: Params) -> np.ndarray:
    """Paint each 16×16 cell as a solid colour from the combined phase of both sources.
    No dots — direct plasma-style ripple. Brightness = bright_a ⊕ bright_b (XOR for
    interference). Cheap in silicon: no dot mask, just a per-cell colour register."""
    ra_lat, rb_lat, _ = compute_phase(cax, cay, p)
    ba = _bright_from_lat(ra_lat, p)
    bb = _bright_from_lat(rb_lat, p)
    mask = (1 << p.bright_bits) - 1
    level = (ba + bb) & mask  # modular sum → interference fringes
    # Map 0..mask to 0..255
    step = 255 // mask if mask > 0 else 255
    val = (level * step).astype(np.int32)
    r, g, b = _palette_rgb(p.palette)
    rgb = np.stack([(val * r // 255).astype(np.uint8),
                    (val * g // 255).astype(np.uint8),
                    (val * b // 255).astype(np.uint8)], axis=-1)
    # Expand per-cell to per-pixel.
    frame = np.repeat(np.repeat(rgb, LATTICE, axis=0), LATTICE, axis=1)
    return frame[:H, :W]


def _pixel_phase(cax: int, cay: int, p: Params) -> tuple[np.ndarray, np.ndarray]:
    """Full-resolution per-pixel ra, rb phase arrays (shape H, W).
    In silicon this equals the per-pixel accumulator state — no latch."""
    cbx = W - cax
    cby = H - cay
    accum_mask = (1 << p.accum_bits) - 1
    ra_mask = (1 << (p.accum_bits + 1)) - 1
    ys = np.arange(H)[:, None]
    xs = np.arange(W)[None, :]
    dy_a = ys - cay
    dy_b = ys - cby
    dx_a = xs - cax
    dx_b = xs - cbx
    r1a = (dy_a * dy_a) & accum_mask
    r1b = (dy_b * dy_b) & accum_mask
    r2a = (dx_a * dx_a) & accum_mask
    r2b = (dx_b * dx_b) & accum_mask
    ra = (p.r1_weight * r1a + r2a) & ra_mask
    rb = (p.r1_weight * r1b + r2b) & ra_mask
    return ra, rb


def _phase_to_brightness(phase: np.ndarray, p: Params) -> np.ndarray:
    """Pick bits, triangle-unfold → 0..mask intensity."""
    phase_lat = phase >> p.shift_right
    mask = (1 << p.bright_bits) - 1
    val = (phase_lat >> p.bright_lsb) & mask
    sign = (phase_lat >> (p.bright_lsb + p.bright_bits)) & 1
    return np.where(sign == 1, mask - val, val).astype(np.int32)


def _render_pixel_phase(cax: int, cay: int, p: Params) -> np.ndarray:
    """Full-resolution plasma — every pixel gets coloured by (2*r1 + r2) mod 2^N."""
    ra, rb = _pixel_phase(cax, cay, p)
    ba = _phase_to_brightness(ra, p)
    bb = _phase_to_brightness(rb, p)
    mask = (1 << p.bright_bits) - 1
    level = (ba + bb) & mask
    step = 255 // mask if mask > 0 else 255
    val = (level * step).astype(np.int32)
    r, g, b = _palette_rgb(p.palette)
    frame = np.stack([(val * r // 255).astype(np.uint8),
                      (val * g // 255).astype(np.uint8),
                      (val * b // 255).astype(np.uint8)], axis=-1)
    return frame


def _render_pixel_phase_dots(cax: int, cay: int, p: Params) -> np.ndarray:
    """Per-pixel phase *modulates the brightness* of a dot-lattice mask.
    Dots are always in the lattice but dim in wave troughs and bright in peaks
    — the 4 VGA levels per channel give a soft pulse instead of a hard on/off."""
    ra, rb = _pixel_phase(cax, cay, p)
    ba = _phase_to_brightness(ra, p)
    bb = _phase_to_brightness(rb, p)
    mask = (1 << p.bright_bits) - 1
    level = (ba + bb) & mask

    # 5x5 dot mask, identical in every cell.
    ys = np.arange(H)[:, None]
    xs = np.arange(W)[None, :]
    local_x = xs & (LATTICE - 1)
    local_y = ys & (LATTICE - 1)
    ex = local_x - 8
    ey = local_y - 8
    dot_mask = (np.abs(ex) <= p.dot_r) & (np.abs(ey) <= p.dot_r)

    # Brightness = level directly, with a minimum floor so the lattice is always
    # visible even in wave troughs. Quantised to the 4-level VGA palette so the
    # preview matches what 6-bit TinyVGA will actually emit.
    vga_level = (level * 3) // mask if mask > 0 else 3 * dot_mask.astype(np.int32)
    if p.bright_floor > 0:
        vga_level = np.maximum(vga_level, p.bright_floor)
    vga_level = np.where(dot_mask, vga_level, 0)
    vga_intensity = (vga_level * 85).astype(np.int32)  # 0, 85, 170, 255

    r, g, b = _palette_rgb(p.palette)
    frame = np.stack([(vga_intensity * r // 255).astype(np.uint8),
                      (vga_intensity * g // 255).astype(np.uint8),
                      (vga_intensity * b // 255).astype(np.uint8)], axis=-1)
    return frame


def _render_bright_dots(cax: int, cay: int, p: Params) -> np.ndarray:
    """Displaced dots, but brightness of each dot encodes the wave amplitude.
    Keeps the dot-lattice aesthetic but adds a smoother phase signal."""
    ra_lat, rb_lat, _ = compute_phase(cax, cay, p)
    dlx, dly = compute_displacements(cax, cay, p)
    ba = _bright_from_lat(ra_lat, p)
    bb = _bright_from_lat(rb_lat, p)
    mask = (1 << p.bright_bits) - 1
    level = (ba + bb) & mask
    step = 255 // mask if mask > 0 else 255
    intensity = (level * step).astype(np.uint8)

    frame = np.zeros((H, W, 3), dtype=np.uint8)
    r, g, b = _palette_rgb(p.palette)
    dr = p.dot_r
    n_rows, n_cols = dlx.shape
    for cy in range(n_rows):
        for cx in range(n_cols):
            lvl = int(intensity[cy, cx])
            if lvl == 0:
                continue
            cr = (r * lvl) // 255
            cg = (g * lvl) // 255
            cb = (b * lvl) // 255
            dot_cx = cx * LATTICE + 8 + int(dlx[cy, cx])
            dot_cy = cy * LATTICE + 8 + int(dly[cy, cx])
            x0 = dot_cx - dr; x1 = dot_cx + dr + 1
            y0 = dot_cy - dr; y1 = dot_cy + dr + 1
            if p.clip_to_cell:
                x0 = max(x0, cx * LATTICE); x1 = min(x1, (cx + 1) * LATTICE)
                y0 = max(y0, cy * LATTICE); y1 = min(y1, (cy + 1) * LATTICE)
            x0 = max(x0, 0); x1 = min(x1, W)
            y0 = max(y0, 0); y1 = min(y1, H)
            if x1 > x0 and y1 > y0:
                frame[y0:y1, x0:x1] = (cr, cg, cb)
    return frame
