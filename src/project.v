/*
 * Copyright (c) 2026 Kilian
 * SPDX-License-Identifier: Apache-2.0
 *
 * Wave Lattice — a dot-grid port of https://taylor.town/waves (Taylor Troesh,
 * inspired by Zach Lieberman). Two interfering radial wave sources displace
 * a 40x30 dot lattice on a 640x480 VGA signal. Source A follows a virtual
 * Lissajous pointer; source B is its point-mirror (640-x, 480-y).
 *
 * Variant F: dual-source via 2× internal clock.
 *   The internal logic runs at 2× the VGA pixel rate (50.35 MHz). A 1-bit
 *   `phase` register splits each pixel into two internal cycles:
 *     phase=0 — "pixel cycle": VGA timing advances, A-side state updates,
 *               output (dot) is sampled.
 *     phase=1 — "free cycle":  B-side state updates.
 *   Per-pixel adders, subtractors, and predicates that used to exist
 *   twice (once for A, once for B) are now single resources whose
 *   operands are muxed on `phase`. Only the state registers (r1a/r1b,
 *   r2a/r2b, ra_lat/rb_lat, sgn/far latches) remain duplicated, because
 *   both values are read at display-time.
 */

`default_nettype none

module tt_um_kilian_waves (
    input  wire [7:0] ui_in,    // Dedicated inputs
    output wire [7:0] uo_out,   // Dedicated outputs
    input  wire [7:0] uio_in,   // IOs: Input path
    output wire [7:0] uio_out,  // IOs: Output path
    output wire [7:0] uio_oe,   // IOs: Enable path (active high: 0=input, 1=output)
    input  wire       ena,      // always 1 when the design is powered, so you can ignore it
    input  wire       clk,      // clock 50.35 MHz (2× VGA pixel rate)
    input  wire       rst_n     // reset_n - low to reset
);

  assign uio_out = 0;
  assign uio_oe  = 0;

  // All ui_in and uio_in bits are unused in variant F (palette hardwired white,
  // freeze/slow knobs dropped).
  wire _unused = &{ena, ui_in, uio_in, 1'b0};

  // --- Phase register: toggles every clock.
  //   phase == 0 → pixel cycle (VGA advances, A updates, output samples)
  //   phase == 1 → free cycle  (B updates)
  reg phase;
  always @(posedge clk) begin
    if (~rst_n) phase <= 1'b0;
    else        phase <= ~phase;
  end
  wire pixel_ce = (phase == 1'b0);

  wire hsync, vsync, display_on;
  wire [9:0] x, y;

  hvsync_generator hvsync_gen(
    .clk(clk),
    .clken(pixel_ce),
    .reset(~rst_n),
    .hsync(hsync),
    .vsync(vsync),
    .display_on(display_on),
    .hpos(x),
    .vpos(y)
  );

  // --- vsync rising-edge detector (only ticks on pixel cycles).
  reg vsync_prev;
  always @(posedge clk) begin
    if (~rst_n)           vsync_prev <= 0;
    else if (pixel_ce)    vsync_prev <= vsync;
  end

  // --- Pointer counter (advances once per VGA frame).
  reg [11:0] ptr_counter;
  always @(posedge clk) begin
    if (~rst_n)
      ptr_counter <= 0;
    else if (pixel_ce && vsync && !vsync_prev)
      ptr_counter <= ptr_counter + 1;
  end

  // --- Lissajous virtual pointer (multiplier-free, 3:5 coprime ratio).
  wire [13:0] pc3 = {ptr_counter, 1'b0} + {2'b00, ptr_counter};   // 3 × pc
  wire [14:0] pc5 = {ptr_counter, 2'b00} + {3'b000, ptr_counter}; // 5 × pc

  wire [9:0] px_raw = pc3[9:0];
  wire [9:0] py_raw = pc5[9:0];

  wire [8:0] px_tri = px_raw[9] ? (9'd511 - px_raw[8:0]) : px_raw[8:0];
  wire [8:0] py_tri = py_raw[9] ? (9'd511 - py_raw[8:0]) : py_raw[8:0];

  wire signed [9:0] offset_x = $signed({3'b000, px_tri[8:2]}) - 10'sd64;
  wire signed [9:0] offset_y = $signed({3'b000, py_tri[8:2]}) - 10'sd64;

  wire signed [9:0] ptr_x_raw = 10'sd320 + offset_x;  // [256, 383]
  wire signed [9:0] ptr_y_raw = 10'sd240 + offset_y;  // [176, 303]

  wire signed [9:0]  ptr_x = (ptr_x_raw < 10'sd256) ? 10'sd256
                           : (ptr_x_raw > 10'sd383) ? 10'sd383
                           : ptr_x_raw;
  wire signed [9:0]  ptr_y = (ptr_y_raw < 10'sd176) ? 10'sd176
                           : (ptr_y_raw > 10'sd303) ? 10'sd303
                           : ptr_y_raw;

  // --- Pattern mode: 2-bit auto-cycle that picks how source B relates to A.
  // 0 = point-mirror (F's original behaviour)
  // 1 = y-mirror only (sources share x, B above/below A)
  // 2 = x-mirror only (sources share y, B left/right of A)
  // 3 = B anchored at screen centre
  // PATTERN_SHIFT=6 → each mode holds for 64 frames (~1 s at 60 Hz).
  localparam PATTERN_SHIFT = 6;
  wire [1:0] pattern_mode = ptr_counter[PATTERN_SHIFT+1 : PATTERN_SHIFT];

  // --- Source centres: A = pointer, B = pattern-dependent.
  wire signed [9:0] center_ax = ptr_x;
  wire signed [9:0] center_ay = ptr_y;
  wire signed [9:0] mirror_x  = 10'sd640 - ptr_x;
  wire signed [9:0] mirror_y  = 10'sd480 - ptr_y;
  wire signed [9:0] center_bx =
      (pattern_mode == 2'd1) ? ptr_x       :   // y-mirror only (share x)
      (pattern_mode == 2'd3) ? 10'sd320    :   // centre
                               mirror_x;       // modes 0, 2 use mirror
  wire signed [9:0] center_by =
      (pattern_mode == 2'd2) ? ptr_y       :   // x-mirror only (share y)
      (pattern_mode == 2'd3) ? 10'sd240    :   // centre
                               mirror_y;       // modes 0, 1 use mirror

  // --- Time-muxed pixel-to-centre subtractors (p_?x, p_?y).
  // One shared x-subtractor, operand muxed by phase; same for y.
  wire signed [9:0] center_cur_x = phase ? center_bx : center_ax;
  wire signed [9:0] center_cur_y = phase ? center_by : center_ay;
  wire signed [9:0] p_cur_x = x - center_cur_x;  // = p_ax on phase=0, p_bx on phase=1
  wire signed [9:0] p_cur_y = y - center_cur_y;  // = p_ay on phase=0, p_by on phase=1

  // --- Distance-squared accumulators (dual state, still two of each).
  reg [13:0] r1a, r1b;
  reg [13:0] r2a, r2b;

  // --- Time-muxed r = 2·r1 + r2.
  // Selects A on phase=0, B on phase=1 — same adder, different operands.
  wire [13:0] r1_sel = phase ? r1b : r1a;
  wire [13:0] r2_sel = phase ? r2b : r2a;
  wire [14:0] r_sel  = {r1_sel, 1'b0} + {1'b0, r2_sel};

  // --- Time-muxed "far-from-axis" predicates.
  // One x-predicate, one y-predicate, each reading p_cur_* which is
  // itself muxed. On phase=0 produces far_ax/far_ay; on phase=1 far_bx/far_by.
  wire p_cur_x_far = ~((&p_cur_x[9:4]) | (~|p_cur_x[9:4]));
  wire p_cur_y_far = ~((&p_cur_y[9:4]) | (~|p_cur_y[9:4]));

  // --- Hblank fix-up operands. Compute once through the already-muxed
  // center_cur_x — saves a subtractor and an abs-er versus the per-source
  // (offset_ax, offset_bx) + muxing approach.
  wire signed [9:0] offset_cur  = center_cur_x - 10'sd320;
  wire        [9:0] abs_off_cur = offset_cur[9] ? (10'd0 - offset_cur) : offset_cur;

  // --- r1 update delta (single adder shared for x==0 case).
  // 2·p_cur_y - 1 on either phase (A on phase=0, B on phase=1).
  wire [13:0] r1_delta   = {{3{p_cur_y[9]}}, p_cur_y, 1'b0} - 14'd1;
  wire [13:0] r1_sel_new = r1_sel + r1_delta;

  // --- r2 update delta for display (1-step: 2·p + 1).
  wire [13:0] r2_delta_disp = {{3{p_cur_x[9]}}, p_cur_x, 1'b0} + 14'd1;
  wire [13:0] r2_sel_new    = r2_sel + r2_delta_disp;

  // --- r2 update delta for hblank walk (±640 + offset_cur).
  // Use full 14-bit arithmetic; operands are small enough to fit.
  wire [13:0] hblank_add = 14'd640 + {{4{offset_cur[9]}}, offset_cur};
  wire [13:0] hblank_sub = 14'd640 - {{4{offset_cur[9]}}, offset_cur};
  wire [13:0] r2_sel_new_hblank = offset_cur[9] ? (r2_sel - hblank_sub)
                                                : (r2_sel + hblank_add);
  wire hblank_walk_active = (x - 10'd641) < abs_off_cur;

  // --- r1 seed walk (y==0 line).
  // At y==0, r1 accumulates center_cur_y into r1_sel for each x < center_cur_y.
  wire [13:0] r1_seed_new    = r1_sel + {{4{1'b0}}, center_cur_y};
  wire        r1_seed_active = (x < center_cur_y);

  // --- Accumulator updates.
  // Per-phase write-back routes r?_sel_new into either r?a or r?b.
  // The if/else-if chain mirrors the single-source form; the only change
  // is that a single phase-muxed resource feeds whichever accumulator the
  // current phase selects (phase=0 → A, phase=1 → B).
  always @(posedge clk) begin
    if (~rst_n) begin
      r1a <= 0; r2a <= 0;
      r1b <= 0; r2b <= 0;
    end else begin
      if (vsync) begin
        r1a <= 0; r2a <= 0;
        r1b <= 0; r2b <= 0;
      end else if (display_on && y == 10'd0) begin
        // y==0 line: r1 accumulates center_y for columns x < center_y.
        if (r1_seed_active) begin
          if (phase == 1'b0) r1a <= r1_seed_new;
          else               r1b <= r1_seed_new;
        end
      end else if (x == 10'd640) begin
        // End of line: seed r2 = 320² mod 2¹⁴ = 4096. No A/B skew in this
        // variant (the display-time r2 update uses the 1-step delta, not
        // the 2-step trick), so both sources use the same clean seed.
        if (phase == 1'b0) r2a <= 14'd4096;
        else               r2b <= 14'd4096;
      end else if (x > 10'd640) begin
        // Hblank walk: advance r2_sel toward c?x² (the starting value for
        // the per-line x walk). Runs on both phases with muxed operands.
        if (hblank_walk_active) begin
          if (phase == 1'b0) r2a <= r2_sel_new_hblank;
          else               r2b <= r2_sel_new_hblank;
        end
      end else if (display_on && x == 10'd0) begin
        // r1 update at start of new visible line. Delta = 2·p_cur_y - 1
        // because y has already advanced to the new row.
        if (phase == 1'b0) r1a <= r1_sel_new;
        else               r1b <= r1_sel_new;
      end else if (display_on) begin
        // Display-time r2 update: standard 1-step delta 2·p_cur_x + 1.
        // Both sources fresh every pixel — no inter-source skew.
        if (phase == 1'b0) r2a <= r2_sel_new;
        else               r2b <= r2_sel_new;
      end
    end
  end

  // --- Block A: lattice anchor latches.
  // Both A and B latches update on the SAME VGA pixel; the phase splits
  // writes so each latch captures the freshly-computed r_sel for its source.
  reg [11:0] ra_lat, rb_lat;
  reg sgn_ax_lat, sgn_ay_lat, sgn_bx_lat, sgn_by_lat;
  reg far_ax_lat, far_ay_lat, far_bx_lat, far_by_lat;

  // Latch condition in VGA-pixel terms (last pixel of each 16-wide cell,
  // plus x==639). VGA signals only update on phase=0, so x is stable
  // across both phases of that pixel.
  wire latch_en = display_on && (x[3:0] == 4'hF || x == 10'd639);

  always @(posedge clk) begin
    if (~rst_n) begin
      ra_lat <= 0; rb_lat <= 0;
      sgn_ax_lat <= 0; sgn_ay_lat <= 0;
      sgn_bx_lat <= 0; sgn_by_lat <= 0;
      far_ax_lat <= 0; far_ay_lat <= 0;
      far_bx_lat <= 0; far_by_lat <= 0;
    end else if (latch_en) begin
      if (phase == 1'b0) begin
        // Source A cycle.
        ra_lat     <= r_sel[14:3];
        sgn_ax_lat <= p_cur_x[9];
        sgn_ay_lat <= p_cur_y[9];
        far_ax_lat <= p_cur_x_far;
        far_ay_lat <= p_cur_y_far;
      end else begin
        // Source B cycle.
        rb_lat     <= r_sel[14:3];
        sgn_bx_lat <= p_cur_x[9];
        sgn_by_lat <= p_cur_y[9];
        far_bx_lat <= p_cur_x_far;
        far_by_lat <= p_cur_y_far;
      end
    end
  end

  // --- Block B: signed displacement decode.
  // Kept combinational (mirrors the original) to avoid a 1-pixel lag
  // between latch and display. Two 3-bit negates (one per source) are
  // cheap; the big-ticket savings are the time-muxed adders above.
  wire signed [2:0] disp_a = ra_lat[10] ? -{1'b0, ra_lat[9:8]} : {1'b0, ra_lat[9:8]};
  wire signed [2:0] disp_b = rb_lat[10] ? -{1'b0, rb_lat[9:8]} : {1'b0, rb_lat[9:8]};

  wire signed [3:0] dlx_a = far_ax_lat ? (sgn_ax_lat ? -{disp_a[2], disp_a} : {disp_a[2], disp_a}) : 4'sd0;
  wire signed [3:0] dly_a = far_ay_lat ? (sgn_ay_lat ? -{disp_a[2], disp_a} : {disp_a[2], disp_a}) : 4'sd0;
  wire signed [3:0] dlx_b = far_bx_lat ? (sgn_bx_lat ? -{disp_b[2], disp_b} : {disp_b[2], disp_b}) : 4'sd0;
  wire signed [3:0] dly_b = far_by_lat ? (sgn_by_lat ? -{disp_b[2], disp_b} : {disp_b[2], disp_b}) : 4'sd0;

  wire signed [4:0] dlx_sum = dlx_a + dlx_b;
  wire signed [4:0] dly_sum = dly_a + dly_b;

  wire signed [3:0] dlx_sat = (dlx_sum >  5'sd6) ?  4'sd6
                            : (dlx_sum < -5'sd6) ? -4'sd6
                            : dlx_sum[3:0];
  wire signed [3:0] dly_sat = (dly_sum >  5'sd6) ?  4'sd6
                            : (dly_sum < -5'sd6) ? -4'sd6
                            : dly_sum[3:0];

  // --- Morph envelope: slow triangle 0..15..0 from ptr_counter[8:4] (5-bit
  // slice, 512-frame cycle at 60 Hz ≈ 8.5 s). Frame-stable — ptr_counter only
  // advances on vsync rising edge — so no dedicated flop needed.
  localparam MORPH_SHIFT = 4;
  wire [4:0] morph_raw = ptr_counter[MORPH_SHIFT+4 : MORPH_SHIFT];
  wire [3:0] morph_env = morph_raw[4] ? (5'd31 - morph_raw) : morph_raw[3:0];

  // Scale saturated displacement by morph_env ≈ /15 via >>> 4. At env=0 dots
  // sit at cell centres; at env=15 positive peaks attenuate one level (+6 →
  // +5 via floor(90/16)) while negative peaks hold (−6 → −6 via floor(−90/16))
  // — an imperceptible asymmetry for a morph endpoint. One multiplier time-
  // shared across x and y via phase: phase=0 → dlx, phase=1 → dly; results
  // latched so both axes are available combinationally downstream.
  wire signed [3:0] dl_in = phase ? dly_sat : dlx_sat;
  wire signed [8:0] dl_scaled = $signed({1'b0, morph_env}) * dl_in;
  wire signed [3:0] dl_morphed = dl_scaled >>> 4;

  reg signed [3:0] dlx_m, dly_m;
  always @(posedge clk) begin
    if (~rst_n) begin
      dlx_m <= 0;
      dly_m <= 0;
    end else if (phase == 1'b0) begin
      dlx_m <= dl_morphed;
    end else begin
      dly_m <= dl_morphed;
    end
  end

  wire signed [3:0] dlx = dlx_m;
  wire signed [3:0] dly = dly_m;

  // --- Block C: dot mask, pipelined.
  wire signed [5:0] ex = $signed({2'b00, x[3:0]}) - 6'sd8 - {{2{dlx[3]}}, dlx};
  wire signed [5:0] ey = $signed({2'b00, y[3:0]}) - 6'sd8 - {{2{dly[3]}}, dly};
  wire dot_now = (ex >= -6'sd2 && ex <= 6'sd2 &&
                  ey >= -6'sd2 && ey <= 6'sd2);
  reg dot;
  always @(posedge clk) begin
    if (~rst_n)        dot <= 0;
    else if (pixel_ce) dot <= dot_now;  // Sample output only on pixel cycle.
  end

  // --- Block D: per-pixel amplitude from r_sel top bits.
  // amp_a / amp_b latch r_sel[14:12] once per VGA pixel (A on phase=0, B on
  // phase=1). Numpy's triangle-fold bit (position 15 of r_sel) doesn't exist
  // in the 15-bit r_sel for this parameter set — phase_lat is 12-bit after
  // the >> 3, so sign bit 12 is always 0 and the fold is a no-op. Skip it.
  reg [2:0] amp_a, amp_b;
  always @(posedge clk) begin
    if (~rst_n) begin
      amp_a <= 0;
      amp_b <= 0;
    end else if (phase == 1'b0) begin
      amp_a <= r_sel[14:12];
    end else begin
      amp_b <= r_sel[14:12];
    end
  end

  // --- Per-source morph blend toward binary. Each source is lifted
  // independently so dual-colour can paint A-dominant and B-dominant regions
  // in their own palette tints. The same 3×4 multiplier handles both sides
  // via phase: phase=0 → source A delta, phase=1 → source B delta.
  wire [2:0] amp_in = phase ? amp_b : amp_a;
  wire [2:0] amp_delta = 3'd7 - amp_in;
  wire [6:0] amp_lift_full = amp_delta * morph_env;
  wire [2:0] amp_lift = amp_lift_full[6:4];
  wire [3:0] amp_lifted_raw = {1'b0, amp_in} + {1'b0, amp_lift};
  wire [2:0] amp_lifted_cur = amp_lifted_raw[3] ? 3'b111 : amp_lifted_raw[2:0];

  reg [2:0] amp_a_lifted, amp_b_lifted;
  always @(posedge clk) begin
    if (~rst_n) begin
      amp_a_lifted <= 0;
      amp_b_lifted <= 0;
    end else if (phase == 1'b0) begin
      amp_a_lifted <= amp_lifted_cur;
    end else begin
      amp_b_lifted <= amp_lifted_cur;
    end
  end

  // --- Breath envelope: 2-level strobe that halves every dot's brightness on
  // the triangle's low half. Trades a smooth 16-step ramp for ~6 cells of
  // logic; over ~68 s at BREATH_SHIFT=7 the pulse still reads as a breath
  // rather than a flicker. breath_full is true when the triangle-folded
  // 5-bit counter env ≥ 8, which is exactly raw[4] ⊕ raw[3].
  //
  // Shift 7 places the breath slice at ptr_counter[11:7] so it doesn't share
  // bits with morph_raw (pc[8:4]); otherwise breath would be a deterministic
  // function of morph_env and the two envelopes would look locked together.
  localparam BREATH_SHIFT = 7;
  wire [4:0] breath_raw = ptr_counter[BREATH_SHIFT+4 : BREATH_SHIFT];
  wire breath_full = breath_raw[4] ^ breath_raw[3];

  wire [2:0] amp_a_breathed = breath_full ? amp_a_lifted : {1'b0, amp_a_lifted[2:1]};
  wire [2:0] amp_b_breathed = breath_full ? amp_b_lifted : {1'b0, amp_b_lifted[2:1]};

  // Map per-source lifted+breathed amplitude (0..7) → VGA range (0..3).
  wire [1:0] amp_a_vga = amp_a_breathed[2:1];
  wire [1:0] amp_b_vga = amp_b_breathed[2:1];

  // --- Block E: palette lookup.
  // pal_idx cycles 0..15 every 2^PALETTE_SHIFT frames; full hue ring = 1024
  // frames at shift=6 (~17 s at 60 Hz). The 16-entry LUT walks through
  // white → cyan → blue → magenta → white; B stays at gain 3 the whole time
  // (the palette is a rotation around the blue corner of the VGA cube), so
  // hardwire pal_b = 3 and save a 2-bit mux per-entry and a scale stage.
  localparam PALETTE_SHIFT = 6;
  wire [3:0] pal_idx   = ptr_counter[PALETTE_SHIFT+3 : PALETTE_SHIFT];
  wire [3:0] pal_idx_b = pal_idx + 4'd8;  // +8 = complementary (180° hue)

  function [3:0] pal_lookup;  // returns {pal_r, pal_g} for a given index
    input [3:0] idx;
    begin
      case (idx)
        4'd2:         pal_lookup = {2'd2, 2'd3};
        4'd3:         pal_lookup = {2'd1, 2'd3};
        4'd4:         pal_lookup = {2'd0, 2'd3};
        4'd5:         pal_lookup = {2'd0, 2'd2};
        4'd6:         pal_lookup = {2'd0, 2'd1};
        4'd7, 4'd8:   pal_lookup = {2'd0, 2'd0};
        4'd9:         pal_lookup = {2'd1, 2'd0};
        4'd10:        pal_lookup = {2'd2, 2'd0};
        4'd11, 4'd12: pal_lookup = {2'd3, 2'd0};
        4'd13:        pal_lookup = {2'd3, 2'd1};
        4'd14:        pal_lookup = {2'd3, 2'd2};
        default:      pal_lookup = {2'd3, 2'd3};  // idx 0, 1, 15 = white
      endcase
    end
  endfunction

  wire [3:0] pal_a_raw = pal_lookup(pal_idx);
  wire [3:0] pal_b_raw = pal_lookup(pal_idx_b);
  wire [1:0] pal_r_a = pal_a_raw[3:2];
  wire [1:0] pal_g_a = pal_a_raw[1:0];
  wire [1:0] pal_r_b = pal_b_raw[3:2];
  wire [1:0] pal_g_b = pal_b_raw[1:0];

  // Back-compat aliases for the palette test that reads these wire names.
  wire [1:0] pal_r = pal_r_a;
  wire [1:0] pal_g = pal_g_a;

  // Channel scale ≈ (vga × gain) / 3. Enumerated per gain — the only lossy
  // case is gain=2 where (3 × 2 / 3) = 2 but a plain >>1 would give 1.
  function [1:0] scale_ch;
    input [1:0] vga;
    input [1:0] gain;
    begin
      case (gain)
        2'd0:    scale_ch = 2'd0;
        2'd1:    scale_ch = {1'b0, vga[1] & vga[0]};                 // only vga=3 → 1
        2'd2:    scale_ch = {vga[1] & vga[0], vga[1] & ~vga[0]};     // 0,0,1,2
        default: scale_ch = vga;                                      // gain=3 → pass-through
      endcase
    end
  endfunction

  // --- Block F: dual-colour output. A contributes through palette A, B
  // through palette B (at idx+8); channels sum with saturation to 3 so
  // overlap regions brighten to the channel-mix colour. B gain is 3 on
  // every palette entry so the B channel sums amplitudes directly.
  wire [1:0] R_a = scale_ch(amp_a_vga, pal_r_a);
  wire [1:0] R_b = scale_ch(amp_b_vga, pal_r_b);
  wire [1:0] G_a = scale_ch(amp_a_vga, pal_g_a);
  wire [1:0] G_b = scale_ch(amp_b_vga, pal_g_b);
  wire [2:0] R_sum = {1'b0, R_a} + {1'b0, R_b};
  wire [2:0] G_sum = {1'b0, G_a} + {1'b0, G_b};
  wire [2:0] B_sum = {1'b0, amp_a_vga} + {1'b0, amp_b_vga};
  wire [1:0] R_sat = R_sum[2] ? 2'd3 : R_sum[1:0];
  wire [1:0] G_sat = G_sum[2] ? 2'd3 : G_sum[1:0];
  wire [1:0] B_sat = B_sum[2] ? 2'd3 : B_sum[1:0];

  wire dot_on = display_on & dot;
  wire [1:0] R = dot_on ? R_sat : 2'b00;
  wire [1:0] G = dot_on ? G_sat : 2'b00;
  wire [1:0] B = dot_on ? B_sat : 2'b00;

  // TinyVGA Pmod: {hsync, B[0], G[0], R[0], vsync, B[1], G[1], R[1]}
  assign uo_out = {hsync, B[0], G[0], R[0], vsync, B[1], G[1], R[1]};

endmodule


// VGA 640x480 @ 60Hz sync generator, clock-enable variant.
// Internal logic runs at 2× pixel rate; hvsync advances only when clken=1.
module hvsync_generator(clk, clken, reset, hsync, vsync, display_on, hpos, vpos);
    input clk;
    input clken;
    input reset;
    output reg hsync, vsync;
    output display_on;
    output reg [9:0] hpos;
    output reg [9:0] vpos;

    parameter H_DISPLAY       = 640;
    parameter H_BACK          =  48;
    parameter H_FRONT         =  16;
    parameter H_SYNC          =  96;
    parameter V_DISPLAY       = 480;
    parameter V_TOP           =  33;
    parameter V_BOTTOM        =  10;
    parameter V_SYNC          =   2;

    parameter H_SYNC_START    = H_DISPLAY + H_FRONT;
    parameter H_SYNC_END      = H_DISPLAY + H_FRONT + H_SYNC - 1;
    parameter H_MAX           = H_DISPLAY + H_BACK + H_FRONT + H_SYNC - 1;
    parameter V_SYNC_START    = V_DISPLAY + V_BOTTOM;
    parameter V_SYNC_END      = V_DISPLAY + V_BOTTOM + V_SYNC - 1;
    parameter V_MAX           = V_DISPLAY + V_TOP + V_BOTTOM + V_SYNC - 1;

    wire hmaxxed = (hpos == H_MAX) || reset;
    wire vmaxxed = (vpos == V_MAX) || reset;

    always @(posedge clk)
    begin
      if (clken) begin
        hsync <= (hpos>=H_SYNC_START && hpos<=H_SYNC_END);
        if(hmaxxed)
          hpos <= 0;
        else
          hpos <= hpos + 1;
      end
    end

    always @(posedge clk)
    begin
      if (clken) begin
        vsync <= (vpos>=V_SYNC_START && vpos<=V_SYNC_END);
        if(hmaxxed)
          if (vmaxxed)
            vpos <= 0;
          else
            vpos <= vpos + 1;
      end
    end

    assign display_on = (hpos<H_DISPLAY) && (vpos<V_DISPLAY);
endmodule
