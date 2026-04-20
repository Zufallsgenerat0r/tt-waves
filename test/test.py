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

