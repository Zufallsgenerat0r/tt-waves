# SPDX-FileCopyrightText: (c) 2026 Kilian
# SPDX-License-Identifier: Apache-2.0

import os
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge

# Variant F runs internal logic at 2× the VGA pixel rate. Clock period is
# ~19862 ps (≈50.347 MHz); two internal cycles per VGA pixel. Cocotb
# requires the period to be even (for symmetric half-cycles).
CLK_PERIOD_PS = 19862

# Internal clocks per VGA pixel (variant F = 2).
INT_PER_PIXEL = 2

# VGA 640x480 @ 60Hz timing constants (in VGA pixels)
H_DISPLAY = 640
H_FRONT = 16
H_SYNC = 96
H_BACK = 48
H_TOTAL = H_DISPLAY + H_FRONT + H_SYNC + H_BACK  # 800

V_DISPLAY = 480
V_BOTTOM = 10
V_SYNC = 2
V_TOP = 33
V_TOTAL = V_DISPLAY + V_BOTTOM + V_SYNC + V_TOP  # 525

# Internal-clock counts that correspond to VGA timings.
H_TOTAL_INT = H_TOTAL * INT_PER_PIXEL
H_SYNC_INT = H_SYNC * INT_PER_PIXEL
FRAME_INT = V_TOTAL * H_TOTAL * INT_PER_PIXEL

LATTICE = 16  # dot spacing in pixels
DOT_R = 2     # Chebyshev radius; 5x5 square dot

# uo_out bit positions (TinyVGA Pmod)
BIT_R1 = 0
BIT_G1 = 1
BIT_B1 = 2
BIT_VSYNC = 3
BIT_R0 = 4
BIT_G0 = 5
BIT_B0 = 6
BIT_HSYNC = 7


def decode_vga(uo_out):
    """Decode uo_out into VGA signals."""
    val = int(uo_out.value)
    hsync = (val >> BIT_HSYNC) & 1
    vsync = (val >> BIT_VSYNC) & 1
    r = ((val >> BIT_R1) & 1) << 1 | ((val >> BIT_R0) & 1)
    g = ((val >> BIT_G1) & 1) << 1 | ((val >> BIT_G0) & 1)
    b = ((val >> BIT_B1) & 1) << 1 | ((val >> BIT_B0) & 1)
    return hsync, vsync, r, g, b


async def reset_dut(dut):
    """Standard reset sequence."""
    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)


# --- VGA timing tests --------------------------------------------------------

@cocotb.test()
async def test_hsync_period(dut):
    """HSYNC rising edges must be 800 VGA pixels apart (= 1600 internal clocks)."""
    clock = Clock(dut.clk, CLK_PERIOD_PS, unit="ps")
    cocotb.start_soon(clock.start())
    await reset_dut(dut)

    prev_hsync = 0
    for _ in range(H_TOTAL_INT + 10):
        await RisingEdge(dut.clk)
        hsync, _, _, _, _ = decode_vga(dut.uo_out)
        if hsync and not prev_hsync:
            break
        prev_hsync = hsync

    count = 0
    prev_hsync = 1
    for _ in range(H_TOTAL_INT + 10):
        await RisingEdge(dut.clk)
        count += 1
        hsync, _, _, _, _ = decode_vga(dut.uo_out)
        if hsync and not prev_hsync:
            break
        prev_hsync = hsync

    assert count == H_TOTAL_INT, f"HSYNC period: expected {H_TOTAL_INT}, got {count}"
    dut._log.info(f"HSYNC period: {count} clocks (expected {H_TOTAL_INT})")


@cocotb.test()
async def test_hsync_pulse_width(dut):
    """HSYNC pulse must be 96 VGA pixels wide (= 192 internal clocks)."""
    clock = Clock(dut.clk, CLK_PERIOD_PS, unit="ps")
    cocotb.start_soon(clock.start())
    await reset_dut(dut)

    for _ in range(H_TOTAL_INT + 10):
        await RisingEdge(dut.clk)
        hsync, _, _, _, _ = decode_vga(dut.uo_out)
        if hsync:
            break

    width = 1
    for _ in range(H_TOTAL_INT):
        await RisingEdge(dut.clk)
        hsync, _, _, _, _ = decode_vga(dut.uo_out)
        if hsync:
            width += 1
        else:
            break

    assert width == H_SYNC_INT, f"HSYNC width: expected {H_SYNC_INT}, got {width}"
    dut._log.info(f"HSYNC pulse width: {width} clocks (expected {H_SYNC_INT})")


@cocotb.test()
async def test_vsync_period(dut):
    """VSYNC must pulse every 525 lines (= 525 * 800 * 2 internal clocks)."""
    clock = Clock(dut.clk, CLK_PERIOD_PS, unit="ps")
    cocotb.start_soon(clock.start())
    await reset_dut(dut)

    expected_period = FRAME_INT

    prev_vsync = 0
    for _ in range(expected_period + 200):
        await RisingEdge(dut.clk)
        _, vsync, _, _, _ = decode_vga(dut.uo_out)
        if vsync and not prev_vsync:
            break
        prev_vsync = vsync

    count = 0
    prev_vsync = 1
    for _ in range(expected_period + 200):
        await RisingEdge(dut.clk)
        count += 1
        _, vsync, _, _, _ = decode_vga(dut.uo_out)
        if vsync and not prev_vsync:
            break
        prev_vsync = vsync

    assert count == expected_period, f"VSYNC period: expected {expected_period}, got {count}"
    dut._log.info(f"VSYNC period: {count} clocks (expected {expected_period})")


@cocotb.test()
async def test_total_line_count(dut):
    """Verify 525 total lines per frame (480 display + 45 blanking)."""
    clock = Clock(dut.clk, CLK_PERIOD_PS, unit="ps")
    cocotb.start_soon(clock.start())
    await reset_dut(dut)

    frame_clocks = FRAME_INT

    prev_vsync = 0
    for _ in range(frame_clocks + 200):
        await RisingEdge(dut.clk)
        _, vsync, _, _, _ = decode_vga(dut.uo_out)
        if vsync and not prev_vsync:
            break
        prev_vsync = vsync

    line_count = 0
    prev_hsync = 0
    prev_vsync = 1
    for _ in range(frame_clocks + 200):
        await RisingEdge(dut.clk)
        hsync, vsync, _, _, _ = decode_vga(dut.uo_out)
        if hsync and not prev_hsync:
            line_count += 1
        if vsync and not prev_vsync:
            break
        prev_hsync = hsync
        prev_vsync = vsync

    assert line_count == V_TOTAL, f"Total lines per frame: expected {V_TOTAL}, got {line_count}"
    dut._log.info(f"Total lines per frame: {line_count} (expected {V_TOTAL})")


# --- Frame capture helpers ---------------------------------------------------

async def capture_frame(dut):
    """Capture one full VGA frame as a 640x480 array of (r, g, b) tuples.

    Samples once per VGA pixel (every INT_PER_PIXEL internal clocks), on the
    rising edge of clk. The output signals from the DUT hold across both
    internal phases of a pixel, so either sample is valid.
    """
    frame_clocks = FRAME_INT

    prev_vsync = 0
    for _ in range(frame_clocks + 200):
        await RisingEdge(dut.clk)
        _, vsync, _, _, _ = decode_vga(dut.uo_out)
        if vsync and not prev_vsync:
            break
        prev_vsync = vsync

    await ClockCycles(dut.clk, (V_SYNC + V_TOP) * H_TOTAL * INT_PER_PIXEL)

    pixels = []
    for _line in range(V_DISPLAY):
        row = []
        for _px in range(H_DISPLAY):
            await ClockCycles(dut.clk, INT_PER_PIXEL)
            _, _, r, g, b = decode_vga(dut.uo_out)
            row.append((r, g, b))
        pixels.append(row)
        await ClockCycles(dut.clk, (H_TOTAL - H_DISPLAY) * INT_PER_PIXEL)

    return pixels


def save_frame_png(pixels, filename):
    """Save captured frame as PNG."""
    from PIL import Image
    h = len(pixels)
    w = len(pixels[0]) if h > 0 else 0
    img = Image.new("RGB", (w, h))
    for y in range(h):
        for x in range(w):
            r, g, b = pixels[y][x]
            img.putpixel((x, y), (r * 85, g * 85, b * 85))
    os.makedirs("output", exist_ok=True)
    img.save(f"output/{filename}")
    return img


def is_lit(rgb):
    r, g, b = rgb
    return r > 0 or g > 0 or b > 0


# --- Dot-lattice demo tests --------------------------------------------------

@cocotb.test()
async def test_frame_dump(dut):
    """Capture a frame and save as PNG for visual inspection."""
    clock = Clock(dut.clk, CLK_PERIOD_PS, unit="ps")
    cocotb.start_soon(clock.start())
    await reset_dut(dut)

    # Run past first frame (y=0 init happens here)
    await ClockCycles(dut.clk, FRAME_INT + 200)

    pixels = await capture_frame(dut)
    assert len(pixels) == V_DISPLAY, f"Expected {V_DISPLAY} rows, got {len(pixels)}"
    save_frame_png(pixels, "frame_waves.png")
    dut._log.info("Frame saved to output/frame_waves.png")


@cocotb.test()
async def test_dot_lattice_density(dut):
    """Foreground coverage should land in the expected dot-lattice range.

    40x30 dots * 5x5 px = 30000 lit pixels out of 307200 ≈ 9.8%.
    Allow 3%..20% — loose because displacement, clamp saturation, and
    edge-clipped cells perturb the count.
    """
    clock = Clock(dut.clk, CLK_PERIOD_PS, unit="ps")
    cocotb.start_soon(clock.start())
    await reset_dut(dut)

    await ClockCycles(dut.clk, FRAME_INT + 200)
    pixels = await capture_frame(dut)

    lit = 0
    total = V_DISPLAY * H_DISPLAY
    for row in pixels:
        for px in row:
            if is_lit(px):
                lit += 1

    pct = lit * 100 / total
    dut._log.info(f"Lit pixels: {lit}/{total} ({pct:.2f}%)")
    assert 3.0 < pct < 20.0, f"Dot-lattice density out of range: {pct:.2f}%"


@cocotb.test()
async def test_deep_background_black(dut):
    """Corner pixels of each 16x16 cell are almost always black.

    At local (0..1, 0..1), a dot would have to be displaced <= (-6,-6) to
    reach, which requires both sources to contribute max negative.
    Assert >= 98% black in the deep-corner 2x2 region of each cell.
    """
    clock = Clock(dut.clk, CLK_PERIOD_PS, unit="ps")
    cocotb.start_soon(clock.start())
    await reset_dut(dut)

    await ClockCycles(dut.clk, FRAME_INT + 200)
    pixels = await capture_frame(dut)

    corner_total = 0
    corner_lit = 0
    for y in range(V_DISPLAY):
        if y % LATTICE >= 2:
            continue
        for x in range(H_DISPLAY):
            if x % LATTICE >= 2:
                continue
            corner_total += 1
            if is_lit(pixels[y][x]):
                corner_lit += 1

    pct_lit = corner_lit * 100 / max(corner_total, 1)
    dut._log.info(f"Deep-corner lit: {corner_lit}/{corner_total} ({pct_lit:.2f}%)")
    assert pct_lit < 2.0, f"Too many lit corner pixels: {pct_lit:.2f}%"


@cocotb.test()
async def test_dots_displace_between_frames(dut):
    """Over a 16-frame span the displacement field should change
    non-trivially (the Lissajous pointer advances).

    Force ptr_counter to a high morph_env value first — at env=0 the morph
    envelope zeroes displacement, so early frames show an identical static
    lattice and the pointer-motion check would vacuously hold."""
    clock = Clock(dut.clk, CLK_PERIOD_PS, unit="ps")
    cocotb.start_soon(clock.start())
    await reset_dut(dut)

    await ClockCycles(dut.clk, 64)
    try:
        # pc=240 → morph_raw=15 → env=15 (full displacement).
        dut.user_project.ptr_counter.value = 240
    except AttributeError:
        dut._log.info("ptr_counter not accessible (GL sim?); skipping")
        return
    await ClockCycles(dut.clk, FRAME_INT + 200)
    pixels_a = await capture_frame(dut)

    # pc=256 → morph_raw=16 → env=31-16=15 still. Pointer has advanced.
    dut.user_project.ptr_counter.value = 256
    await ClockCycles(dut.clk, FRAME_INT)
    pixels_b = await capture_frame(dut)

    differences = 0
    samples = 0
    for y in range(100, V_DISPLAY, 3):
        for x in range(100, H_DISPLAY, 3):
            samples += 1
            if pixels_a[y][x] != pixels_b[y][x]:
                differences += 1

    pct = differences * 100 / max(samples, 1)
    dut._log.info(f"Frame-diff: {differences}/{samples} ({pct:.2f}%)")
    assert differences > 0, "Frames should differ — the Lissajous pointer should be moving"


@cocotb.test()
async def test_morph_env_zero_static_lattice(dut):
    """At morph_env=0 (ptr_counter slice 0 or 31 in bits [8:4]) dots sit on
    the static 16-px lattice centres — no source displaces them."""
    clock = Clock(dut.clk, CLK_PERIOD_PS, unit="ps")
    cocotb.start_soon(clock.start())
    await reset_dut(dut)

    await ClockCycles(dut.clk, 64)
    try:
        dut.user_project.ptr_counter.value = 0  # morph_raw=0 → env=0
    except AttributeError:
        dut._log.info("ptr_counter not accessible (GL sim?); skipping")
        return
    await ClockCycles(dut.clk, FRAME_INT + 200)
    pixels = await capture_frame(dut)

    # With zero displacement, every dot sits centred at (cx*16+8, cy*16+8) with
    # Chebyshev radius 2. The `dot` register adds a one-pixel x-pipeline lag
    # (hpos and dot both update on phase=0, but dot is sampled pre-update),
    # so lit columns are x%16 ∈ [5..9]; y has no lag → y%16 ∈ [6..10].
    # Check that OUTSIDE this centred region every pixel is black.
    stray = 0
    for y in range(V_DISPLAY):
        for x in range(H_DISPLAY):
            lx, ly = x % LATTICE, y % LATTICE
            in_centre = 5 <= lx <= 9 and 6 <= ly <= 10
            if not in_centre and is_lit(pixels[y][x]):
                stray += 1
    assert stray == 0, f"{stray} pixels lit outside cell centres at env=0"


@cocotb.test()
async def test_morph_env_full_binary(dut):
    """At morph_env=15 every lit pixel is at max blue (VGA level 3).
    The hue palette rotates around the blue corner of the VGA cube, so
    the B channel is always at full gain — at env=15 the amplitude is
    also binary-full, so B must be 3 on every lit pixel regardless of
    which palette entry is active."""
    clock = Clock(dut.clk, CLK_PERIOD_PS, unit="ps")
    cocotb.start_soon(clock.start())
    await reset_dut(dut)

    await ClockCycles(dut.clk, 64)
    try:
        dut.user_project.ptr_counter.value = 240  # env=15
    except AttributeError:
        dut._log.info("ptr_counter not accessible (GL sim?); skipping")
        return
    await ClockCycles(dut.clk, FRAME_INT + 200)
    pixels = await capture_frame(dut)

    partial = 0
    lit_any = 0
    for row in pixels:
        for (r, g, b) in row:
            if r or g or b:
                lit_any += 1
                if b != 3:
                    partial += 1
    assert lit_any > 0, "no lit pixels captured"
    assert partial == 0, f"{partial}/{lit_any} lit pixels not at full B at env=15"


@cocotb.test()
async def test_morph_env_zero_amp_variation(dut):
    """At morph_env=0 the amplitude path modulates dot brightness, so the
    frame must contain at least two distinct non-black VGA levels. Check B
    specifically — it's always driven by amp regardless of palette tint."""
    clock = Clock(dut.clk, CLK_PERIOD_PS, unit="ps")
    cocotb.start_soon(clock.start())
    await reset_dut(dut)

    await ClockCycles(dut.clk, 64)
    try:
        dut.user_project.ptr_counter.value = 0  # env=0
    except AttributeError:
        dut._log.info("ptr_counter not accessible (GL sim?); skipping")
        return
    await ClockCycles(dut.clk, FRAME_INT + 200)
    pixels = await capture_frame(dut)

    levels = set()
    for row in pixels:
        for (r, g, b) in row:
            if b:
                levels.add(b)
    assert len(levels) >= 2, f"expected varying B brightness at env=0; got levels {sorted(levels)}"


@cocotb.test()
async def test_pattern_mode_centres(dut):
    """Pattern mode mux: verify each of the 4 B-source formulas is actually
    used. Force ptr_counter so pattern_mode takes each value and read
    center_bx/center_by directly."""
    clock = Clock(dut.clk, CLK_PERIOD_PS, unit="ps")
    cocotb.start_soon(clock.start())
    await reset_dut(dut)

    await ClockCycles(dut.clk, 64)
    try:
        _ = dut.user_project.pattern_mode.value
    except AttributeError:
        dut._log.info("pattern_mode not accessible (GL sim?); skipping")
        return

    seen_centres = set()
    for mode in range(4):
        # PATTERN_SHIFT=6 places mode bits at ptr_counter[7:6]. Base 8 keeps
        # bits[7:6]=0 so OR-in cleanly sets the mode, while the Lissajous
        # drive still puts ptr_x / ptr_y off-centre for distinguishable B.
        pc = 8 | (mode << 6)
        dut.user_project.ptr_counter.value = pc
        await ClockCycles(dut.clk, 8)
        pm = int(dut.user_project.pattern_mode.value)
        bx = int(dut.user_project.center_bx.value.signed_integer)
        by = int(dut.user_project.center_by.value.signed_integer)
        assert pm == mode, f"expected pattern_mode={mode}, got {pm}"
        seen_centres.add((bx, by))
        if mode == 3:
            assert (bx, by) == (320, 240), \
                f"mode=3 should anchor B at (320,240); got ({bx},{by})"

    assert len(seen_centres) == 4, \
        f"all 4 pattern modes should produce distinct centres; saw {seen_centres}"


@cocotb.test()
async def test_palette_cycles_through_hues(dut):
    """Sweep ptr_counter across all 16 palette entries and verify each entry
    produces the expected R/G gain pattern (the hue LUT from PORT_PLAN)."""
    clock = Clock(dut.clk, CLK_PERIOD_PS, unit="ps")
    cocotb.start_soon(clock.start())
    await reset_dut(dut)

    await ClockCycles(dut.clk, 64)
    try:
        _ = dut.user_project.pal_r.value
    except AttributeError:
        dut._log.info("pal_r not accessible (GL sim?); skipping")
        return

    expected = [  # (R_gain, G_gain) per palette index
        (3, 3), (3, 3), (2, 3), (1, 3), (0, 3), (0, 2), (0, 1), (0, 0),
        (0, 0), (1, 0), (2, 0), (3, 0), (3, 0), (3, 1), (3, 2), (3, 3),
    ]
    # palette_shift=6, so each index occupies pc[9:6]=idx with lower bits zero.
    for idx in range(16):
        dut.user_project.ptr_counter.value = idx << 6
        await ClockCycles(dut.clk, 4)
        r = int(dut.user_project.pal_r.value)
        g = int(dut.user_project.pal_g.value)
        assert (r, g) == expected[idx], \
            f"palette {idx}: got ({r},{g}), expected {expected[idx]}"


@cocotb.test()
async def test_morph_env_cycle(dut):
    """morph_env must triangle-fold cleanly: at pc[8:4]=0 and pc[8:4]=31 → env=0;
    at pc[8:4]=15 and pc[8:4]=16 → env=15."""
    clock = Clock(dut.clk, CLK_PERIOD_PS, unit="ps")
    cocotb.start_soon(clock.start())
    await reset_dut(dut)

    await ClockCycles(dut.clk, 64)
    try:
        _ = dut.user_project.morph_env.value
    except AttributeError:
        dut._log.info("morph_env not accessible (GL sim?); skipping")
        return

    # pc = raw << 4 so that morph_raw = raw. Sweep raw 0..31.
    for raw in range(32):
        dut.user_project.ptr_counter.value = raw << 4
        await ClockCycles(dut.clk, 4)
        env = int(dut.user_project.morph_env.value)
        expected = raw if raw < 16 else 31 - raw
        assert env == expected, f"raw={raw}: morph_env={env}, expected {expected}"


@cocotb.test()
async def test_multi_frame_dump(dut):
    """Capture frames at several pointer positions by force-setting
    ptr_counter. Sim-time cheap — forcing lets us jump straight to late
    Lissajous states without simulating thousands of idle frames."""
    clock = Clock(dut.clk, CLK_PERIOD_PS, unit="ps")
    cocotb.start_soon(clock.start())
    await reset_dut(dut)

    await ClockCycles(dut.clk, 64)

    # ptr_counter values sampling one half of the Lissajous cycle.
    for i, pc in enumerate([2, 64, 200, 400, 800, 1024]):
        try:
            dut.user_project.ptr_counter.value = pc
        except AttributeError:
            dut._log.info("ptr_counter not accessible (GL sim?); skipping")
            return
        await ClockCycles(dut.clk, 32)
        pixels = await capture_frame(dut)
        save_frame_png(pixels, f"frame_waves_{i}_pc{pc}.png")
        dut._log.info(f"Saved frame {i} at ptr_counter={pc}")

    dut._log.info("Multi-frame dump complete — check output/frame_waves_*_pc*.png")


@cocotb.test()
async def test_spiral_pointer_moves(dut):
    """Probe internal center_ax/ay over time; expect non-trivial motion."""
    clock = Clock(dut.clk, CLK_PERIOD_PS, unit="ps")
    cocotb.start_soon(clock.start())
    await reset_dut(dut)

    def read_centre():
        try:
            ax = int(dut.user_project.center_ax.value.signed_integer)
            ay = int(dut.user_project.center_ay.value.signed_integer)
        except AttributeError:
            return None, None
        return ax, ay

    samples = []
    for _ in range(32):
        ax, ay = read_centre()
        if ax is None:
            dut._log.info("center_ax not accessible (likely GL sim); skipping")
            return
        samples.append((ax, ay))
        await ClockCycles(dut.clk, FRAME_INT)

    distinct = len(set(samples))
    dut._log.info(f"Distinct centre positions sampled: {distinct}/{len(samples)}")
    assert distinct >= 4, f"Expected >= 4 distinct centre positions; got {distinct}"

    displaced = [s for s in samples if s != (320, 240)]
    assert len(displaced) > 0, "Pointer never left screen centre"
