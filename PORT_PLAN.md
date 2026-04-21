# Port plan — morph + dual-colour + palette/dither/breath/pattern-swap

This branch ports the numpy-renderer preview (branch `numpy-renderer`,
preset `morph-full`) onto variant F's 2× clock time-muxed datapath.
`numpy-renderer`'s `tools/animate.py` + `tools/renderer.py` are the
bit-exact specification — this port is bit-exact against those, not
against the current project.v.

## F baseline (2026-04-21)

Measured from GHA run [24678586114][1]:
- Total cells: **1455**
- Design area: 12469.46 µm²
- Effective utilization: 62.4 % of 1×1 tile (die 161 × 111.52 µm²)
- Breakdown: 858 multi-input combinational, 103 sequential (flops),
  26 clock buffers, 132 timing-repair buffers, 25 inverters, 8 buffers,
  78 fill, 225 tap.
- **Headroom before uncomfortable (~80 %) utilization: ≈ 400 cells.**

Re-measure after every step that changes `src/project.v`. Record the new
total in the commit message.

[1]: https://github.com/Zufallsgenerat0r/tt-waves/actions/runs/24678586114

## Design decisions fixed before writing any Verilog

### /15 approximation
numpy uses `* env / 15` in two places (morph amplitude blend, morph
displacement scale). Verilog cheap substitute: `>> 4` (i.e. divide by
16). Error: at env=15, `15 * 15 / 16 = 14` → peak is 1 level short of
maximum (6.25 % error). Accept this — the judges can't distinguish a
6 % intensity cut at peak in a dark-background demo.

Do NOT attempt `((x << 4) - x) >> 4` — saves one level of peak error
but costs a subtractor per axis, and amplitude has two of these.
Not worth the cells.

### 2× clock constraint (inherited from F)
F's internal state advances twice per VGA pixel: phase=0 updates source
A, phase=1 updates source B. Both `ra` and `rb` must be available at
the *output sampling* point (phase=0) for the palette and dot mask to
read both sources in the same cycle.

Implication for dual-colour: the palette read for source B must happen
on phase=1 and its output latched into a register that survives the
phase=0→1 transition. One extra 6-bit flop per channel (= 18 flops).
This differs from the numpy spec (which is "pure combinational") and
should be added to the port before any feature that reads both palettes.

### Output-path widening
F's current output is binary: `R = dot_on ? 2'b11 : 2'b00` (similarly
G/B). The port needs 4-level output driven by amplitude × palette gain.
Change order:
1. Widen each channel to 2-bit (they already are 2-bit, but bit[1] is
   tied to bit[0]). Separate the bits.
2. Drive each bit from the appropriate slice of (amp × gain).

### Amplitude path
Per-pixel amplitude = triangle-fold of `(2·r1 + r2)` top bits.
- Extract 3 bits at position `bright_lsb = 9`, `bright_bits = 3`.
- Sign bit (`bit 12`) drives triangle fold: `triangle = bit12 ? (7 - val) : val`.
- Combine sources: `amp = (amp_a + amp_b) & 7` (single-colour mode) OR
  per-source retained (dual-colour mode).

### Palette LUT
16-entry × 6-bit LUT (2 bits per channel gain). Index = `ptr_counter[palette_shift+3:palette_shift]`. In auto mode, no ui_in dependency (F freed those). Implement as a `case` statement — yosys
will synthesize to ~40 cells.

## Port order

Each step commits independently, measures cells via GHA, and records
the delta in the commit message.

1. **Morph envelope only.** Add `morph_env` flop (4 bits from
   `ptr_counter[8:4]` triangle-fold of 5-bit counter slice). Scale F's
   existing `dlx`, `dly` by `>> (4 - morph_env_bits)` — i.e., shift by
   (15 - env) so env=15 gives full displacement, env=0 gives zero.
   Keep binary output. Expected delta: +15–25 cells. Smoke-test: at
   env=0 dots are at cell centres; at env=15 dots match current F.

2. **Amplitude path.** Add triangle-fold on top bits of ra and rb.
   Morph-blend amplitude toward binary by env. Widen output to 4 VGA
   levels. Expected delta: +30–50 cells.

3. **16-entry hue LUT + per-channel multiply.** Auto-cycle palette index
   from ptr_counter high bits. Expected delta: +40–60 cells.

4. **Dual-colour.** Second palette read on phase=1, retained over
   phase. Per-channel saturating add. Expected delta: +30–40 cells.

5. **Pattern-swap.** 2-bit mode counter muxing source B's formula.
   Expected delta: +20 cells.

6. **4×4 Bayer dither.** LUT of thresholds + compare for palette blend.
   Expected delta: +25 cells.

7. **Triangle breath.** 5-bit triangle fold on amplitude scale.
   Expected delta: +15 cells.

**Running total estimate: +175–235 cells (F base 1455 → 1630–1690).**
Target: ≤ 1750 cells (77 % utilization). If we blow that, cut in order:
7 → 6 → 5 → 4 → 2 → 1. Protected: morph base (step 1) + dual-colour
(step 4) were user-selected as the visual identity.

## Testing

The current `test/test.py` asserts:
- HSYNC period / pulse-width, VSYNC period, total line count → **keep**
- `test_dot_lattice_density` → **update**: at morph=0, density is
  3–15 % (amplitude-modulated, not uniform). At morph=15, density
  matches F's ~9.8 %.
- `test_deep_background_black` → **remove**. Amplitude path lights cells
  everywhere during pulses.
- `test_dots_displace_between_frames` → **keep** (tests pointer motion).
- `test_multi_frame_dump`, `test_spiral_pointer_moves`,
  `test_freeze_holds_pointer` → **update**: freeze/slow knobs removed
  in F; delete those tests. Multi-frame dump is still useful for visual
  regression.

New tests to add per step:
- Step 1: morph_env triangle completes one cycle in 2^(morph_shift+5)
  frames; at env=0 r_dot_mask matches static lattice; at env=15 matches F.
- Step 3: palette index advances on schedule; hue cycle reaches all 16
  entries in 2^(palette_shift+4) frames.
- Step 5: pattern mode transitions at 2^pattern_shift boundary.
- Step 6: dither mask pattern matches 4×4 Bayer.

Numpy renderer (on `numpy-renderer` branch) is the golden reference for
per-pixel output — dump a frame at a known ptr_counter, compare against
`tools/render_frames.py` output.
