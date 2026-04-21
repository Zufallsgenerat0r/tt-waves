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
from dataclasses import dataclass, field, replace
import numpy as np

W, H = 640, 480
LATTICE = 16
DOT_R = 2
MASK14 = 0x3FFF
MASK15 = 0x7FFF


@dataclass
class Params:
    palette: int = 0          # 0=white, 1=cyan, 2=magenta, 3=yellow (manual)
    palette_auto: bool = False  # derive palette from frame counter instead
    palette_shift: int = 6      # ptr_counter >> shift → palette index
    palette_dither: bool = True # 4x4 Bayer dither between adjacent palette entries
    _blend_phase: int = 0       # internal: current 0..15 Bayer phase for this frame

    # "Breathing" envelope — slow triangle wave on overall intensity.
    breath: bool = False
    breath_shift: int = 5       # ptr_counter bits driving the 4-bit phase
    breath_floor: int = 4       # 0..15 min envelope so trough doesn't go fully black
    _breath_env: int = 15       # internal: this frame's envelope 0..15
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


# 16-step hue cycle — each step changes exactly one channel by one level,
# so transitions never visibly jump. Pure anchor colours (white/cyan/blue/
# magenta) each get a one-step dwell for visibility. Per channel we carry
# 2-bit gain (0..3) that scales wave amplitude; in Verilog this is a 16-entry
# LUT plus one 2x2 multiplier per channel (~30 gates total).
HUE_PALETTE_2B: list[tuple[int, int, int]] = [
    (3, 3, 3),  # 0  white
    (3, 3, 3),  # 1  (dwell)
    (2, 3, 3),  # 2
    (1, 3, 3),  # 3
    (0, 3, 3),  # 4  cyan
    (0, 2, 3),  # 5
    (0, 1, 3),  # 6
    (0, 0, 3),  # 7  blue
    (0, 0, 3),  # 8  (dwell)
    (1, 0, 3),  # 9
    (2, 0, 3),  # 10
    (3, 0, 3),  # 11 magenta
    (3, 0, 3),  # 12 (dwell)
    (3, 1, 3),  # 13
    (3, 2, 3),  # 14
    (3, 3, 3),  # 15 back to white
]

# Legacy 4-way palette retained for dots/cells modes that want hard-coded tints.
def _palette_rgb(palette: int) -> tuple[int, int, int]:
    if palette == 1:
        return (0, 255, 255)
    if palette == 2:
        return (255, 0, 255)
    if palette == 3:
        return (255, 255, 0)
    return (255, 255, 255)


def _palette_gain_2b(idx: int) -> tuple[int, int, int]:
    return HUE_PALETTE_2B[idx % len(HUE_PALETTE_2B)]


# 4x4 Bayer matrix, values 0..15. Each pixel gets a spatial threshold; over
# a 16-step blend phase, the fraction of pixels on the "next" palette grows by
# one per phase step — the eye reads this as a continuous fade, not a flash.
_BAYER4 = np.array(
    [[ 0,  8,  2, 10],
     [12,  4, 14,  6],
     [ 3, 11,  1,  9],
     [15,  7, 13,  5]],
    dtype=np.int32,
)


def _bright_from_lat(lat: np.ndarray, p: Params) -> np.ndarray:
    """Map ra_lat bits → triangle-wave brightness in 0..3 (2-bit VGA levels)."""
    mask = (1 << p.bright_bits) - 1
    val = (lat >> p.bright_lsb) & mask
    # Unfold: use sign bit above to make a triangle — avoids the hard 7→0 jump
    # that would show as a sharp ring boundary.
    sign = (lat >> (p.bright_lsb + p.bright_bits)) & 1
    val = np.where(sign == 1, mask - val, val)
    return val.astype(np.int32)


def _effective_palette(ptr_counter: int, p: Params) -> int:
    if not p.palette_auto:
        return p.palette
    # 16-entry hue ring (4-bit index) when auto — narrow steps read as smooth.
    return (ptr_counter >> p.palette_shift) & 0xF


def _palette_blend_phase(ptr_counter: int, p: Params) -> int:
    """0..15 sub-step phase within the current palette dwell — drives Bayer dither."""
    if not p.palette_auto or not p.palette_dither:
        return 0
    # Lower 4 bits of the frame counter shifted by (palette_shift - 4).
    lo = max(0, p.palette_shift - 4)
    return (ptr_counter >> lo) & 0xF


def _breath_envelope(ptr_counter: int, p: Params) -> int:
    """Triangle-wave 'breath' envelope 0..15 (floored to breath_floor) — slow
    pulse on overall intensity. Verilog cost: triangle-fold a 5-bit counter."""
    if not p.breath:
        return 15
    raw = (ptr_counter >> p.breath_shift) & 0x1F  # 5 bits → 0..31
    env = raw if (raw & 0x10) == 0 else (31 - raw)  # triangle 0..15..0
    return max(env, p.breath_floor)


def render_frame(ptr_counter: int, p: Params | None = None) -> np.ndarray:
    if p is None:
        p = Params()
    # Resolve auto-cycled palette for this frame. Stash blend state on params so
    # the pixel-level renderers can apply the Bayer dither between entries.
    pal_idx = _effective_palette(ptr_counter, p)
    blend_phase = _palette_blend_phase(ptr_counter, p)
    auto = p.palette_auto
    p = replace(p, palette=pal_idx, palette_auto=auto)
    cax, cay = compute_pointer(ptr_counter, p)
    breath_env = _breath_envelope(ptr_counter, p)
    p = replace(p, _blend_phase=blend_phase, _breath_env=breath_env)

    if p.mode == "cells":
        return _render_cells(cax, cay, p)
    if p.mode == "bright":
        return _render_bright_dots(cax, cay, p)
    if p.mode == "pixel":
        return _render_pixel_phase(cax, cay, p)
    if p.mode == "pixeldots":
        return _render_pixel_phase_dots(cax, cay, p)
    if p.mode == "dotsfull":
        return _render_dots_full(cax, cay, p)

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


def _render_dots_full(cax: int, cay: int, p: Params) -> np.ndarray:
    """Displaced-dot lattice (Variant F's algorithm: per-cell axis-damped
    displacement from ra_lat / rb_lat bit patterns) coloured by the same
    palette-cycle + Bayer-dither + breath pipeline as pixeldots. Dots are
    drawn at full amplitude (binary mask); the colour pipeline handles all
    intensity variation."""
    dlx, dly = compute_displacements(cax, cay, p)

    # 5x5 per-cell dot mask at (8+dlx, 8+dly) within each 16-px cell.
    n_rows, n_cols = dlx.shape
    dot_mask = np.zeros((H, W), dtype=bool)
    dr = p.dot_r
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
                dot_mask[y0:y1, x0:x1] = True

    # Full-amplitude (vga_level=3) where lit, else 0 — dots are binary, colour
    # pipeline below scales each channel.
    vga_level = np.where(dot_mask, 3, 0).astype(np.int32)

    # Palette cycle + Bayer dither (identical to pixeldots' pipeline).
    if p.palette_auto:
        rg_a, gg_a, bg_a = _palette_gain_2b(p.palette)
        rg_b, gg_b, bg_b = _palette_gain_2b(p.palette + 1)
        if p.palette_dither:
            tile_y = np.arange(H)[:, None] & 3
            tile_x = np.arange(W)[None, :] & 3
            threshold = _BAYER4[tile_y, tile_x]
            pick_b = (threshold < p._blend_phase)
            rg = np.where(pick_b, rg_b, rg_a).astype(np.int32)
            gg = np.where(pick_b, gg_b, gg_a).astype(np.int32)
            bg = np.where(pick_b, bg_b, bg_a).astype(np.int32)
        else:
            rg, gg, bg = rg_a, gg_a, bg_a
    else:
        legacy = {0: (3, 3, 3), 1: (0, 3, 3), 2: (3, 0, 3), 3: (3, 3, 0)}
        rg, gg, bg = legacy.get(p.palette, (3, 3, 3))

    # Breath envelope scales vga_level 50..100%.
    if p.breath:
        scale_num = p._breath_env + 15
        scale_den = 30
        vga_level = ((vga_level * scale_num + scale_den // 2) // scale_den).astype(np.int32)

    r_out = (vga_level * rg) // 3
    g_out = (vga_level * gg) // 3
    b_out = (vga_level * bg) // 3
    frame = np.stack([(r_out * 85).astype(np.uint8),
                      (g_out * 85).astype(np.uint8),
                      (b_out * 85).astype(np.uint8)], axis=-1)
    return frame


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

    # Brightness = level directly, quantised to the 4 VGA levels (0..3) so the
    # preview matches what 6-bit TinyVGA will actually emit.
    vga_level = (level * 3) // mask if mask > 0 else 3 * dot_mask.astype(np.int32)
    if p.bright_floor > 0:
        vga_level = np.maximum(vga_level, p.bright_floor)
    vga_level = np.where(dot_mask, vga_level, 0)

    # Palette mixes via per-channel 2-bit gain (0..3) × amplitude (0..3) >> 2.
    # Auto-cycle picks a 16-entry hue ring; Bayer dither blends into the *next*
    # entry over 16 phase sub-steps so the fade is continuous per-pixel.
    if p.palette_auto:
        rg_a, gg_a, bg_a = _palette_gain_2b(p.palette)
        rg_b, gg_b, bg_b = _palette_gain_2b(p.palette + 1)
        if p.palette_dither:
            tile_y = np.arange(H)[:, None] & 3
            tile_x = np.arange(W)[None, :] & 3
            threshold = _BAYER4[tile_y, tile_x]  # (H, W), 0..15
            pick_b = (threshold < p._blend_phase)
            rg = np.where(pick_b, rg_b, rg_a).astype(np.int32)
            gg = np.where(pick_b, gg_b, gg_a).astype(np.int32)
            bg = np.where(pick_b, bg_b, bg_a).astype(np.int32)
        else:
            rg, gg, bg = rg_a, gg_a, bg_a
    else:
        legacy = {0: (3, 3, 3), 1: (0, 3, 3), 2: (3, 0, 3), 3: (3, 3, 0)}
        rg, gg, bg = legacy.get(p.palette, (3, 3, 3))

    # Breath modulates the wave amplitude *before* channel gain so it dims the
    # whole pattern instead of individual channels. env maps linearly to a
    # scale in [breath_min..1.0] via (env + breath_min*N) / ((1+breath_min)*N),
    # with rounding so the dim state never collapses to 0 at peak amplitude.
    if p.breath:
        # breath_env 0..15 → scale factor 0.5..1.0 (bias 15 under, 15 over).
        scale_num = p._breath_env + 15
        scale_den = 30
        vga_level = ((vga_level * scale_num + scale_den // 2) // scale_den).astype(np.int32)

    # Channel output = (vga_level * channel_gain) / 3 → still 0..3, then *85 → 0..255.
    r_out = (vga_level * rg) // 3
    g_out = (vga_level * gg) // 3
    b_out = (vga_level * bg) // 3
    frame = np.stack([(r_out * 85).astype(np.uint8),
                      (g_out * 85).astype(np.uint8),
                      (b_out * 85).astype(np.uint8)], axis=-1)
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
