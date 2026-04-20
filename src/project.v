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

  // uio[7] drives the 1-bit sigma-delta audio output; all others inputs.
  // Bit 7 is the TT audio-PMOD convention — vga-playground and on-chip
  // audio taps both read from that position.
  wire audio_out;
  assign uio_out = {audio_out, 7'b0};
  assign uio_oe  = 8'b1000_0000;

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

  // --- Frame divider: advance ptr_counter once every 10 vsyncs for
  //   ~10x slower Lissajous motion (≈ 170 s full cycle vs. ≈ 17 s).
  reg [3:0] frame_div;
  always @(posedge clk) begin
    if (~rst_n) frame_div <= 0;
    else if (pixel_ce && vsync && !vsync_prev)
      frame_div <= (frame_div == 4'd9) ? 4'd0 : frame_div + 4'd1;
  end
  wire ptr_tick = pixel_ce && vsync && !vsync_prev && (frame_div == 4'd9);

  // --- Pointer counter (advances once every 10 VGA frames).
  reg [11:0] ptr_counter;
  always @(posedge clk) begin
    if (~rst_n)
      ptr_counter <= 0;
    else if (ptr_tick)
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

  // --- Source centres: A = pointer, B = point-mirror ---
  wire signed [9:0] center_ax = ptr_x;
  wire signed [9:0] center_ay = ptr_y;
  wire signed [9:0] center_bx = 10'sd640 - ptr_x;
  wire signed [9:0] center_by = 10'sd480 - ptr_y;

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

  // --- Hblank fix-up operands (per-source, derived combinationally).
  wire signed [9:0] offset_ax = center_ax - 10'sd320;
  wire signed [9:0] offset_bx = center_bx - 10'sd320;
  wire [9:0] abs_off_ax = offset_ax[9] ? (10'd0 - offset_ax) : offset_ax;
  wire [9:0] abs_off_bx = offset_bx[9] ? (10'd0 - offset_bx) : offset_bx;

  // Time-muxed hblank walk operands.
  wire signed [9:0] offset_cur   = phase ? offset_bx   : offset_ax;
  wire        [9:0] abs_off_cur  = phase ? abs_off_bx  : abs_off_ax;

  // --- r1 update delta (single adder shared for x==0 case).
  // 2·p_cur_y - 1 on either phase (A on phase=0, B on phase=1).
  wire [13:0] r1_delta   = {{3{p_cur_y[9]}}, p_cur_y, 1'b0} - 14'd1;
  wire [13:0] r1_sel_new = r1_sel + r1_delta;

  // --- r2 update delta for display (1-step: 2·p + 1).
  wire [13:0] r2_delta_disp = {{3{p_cur_x[9]}}, p_cur_x, 1'b0} + 14'd1;
  wire [13:0] r2_sel_new    = r2_sel + r2_delta_disp;

  // --- r2 update delta for hblank walk: always (640 + offset_cur).
  // For offset >= 0: r2 += (640 + |offset|). For offset < 0:
  // r2 -= (640 − |offset|) (same as subtracting (640 + offset_cur)).
  // The previous hblank_sub = 640 − offset_cur was wrong: for negative
  // offset that evaluates to (640 + |offset|), subtracting too much and
  // flipping bit 13 of r2 at full Lissajous amplitude (|offset|=64 →
  // cumulative error 2·64² = 8192 = half of 2¹⁴).
  wire [13:0] hblank_add = 14'd640 + {{4{offset_cur[9]}}, offset_cur};
  wire [13:0] r2_sel_new_hblank = offset_cur[9] ? (r2_sel - hblank_add)
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

  wire signed [3:0] dlx = (dlx_sum >  5'sd6) ?  4'sd6
                        : (dlx_sum < -5'sd6) ? -4'sd6
                        : dlx_sum[3:0];
  wire signed [3:0] dly = (dly_sum >  5'sd6) ?  4'sd6
                        : (dly_sum < -5'sd6) ? -4'sd6
                        : dly_sum[3:0];

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

  // --- Block D: output with Bayer-dithered haze + chromatic fringe.
  //   Toned-down single-layer variant: only the primary interference band
  //   shows, and only every 4th pixel (x[0] & y[0]), so dots dominate and
  //   the wave field reads as a subtle ambient glow.
  //   ra_lat[8] (= d² bit 11) fringes ~4x faster than ra_lat[10] — the
  //   slow bit banded visibly. `warm` XORs with a 16-px-periodic bit so
  //   the chromatic tint mottles rather than drawing big contiguous
  //   patches.
  wire bayer    = x[0] & y[0];             // 25% sparse
  wire wave_lo  = ra_lat[8] ^ rb_lat[8];
  wire warm     = ra_lat[8] ^ x[4];

  wire dot_on = display_on & dot;
  wire haze   = display_on & ~dot & wave_lo & bayer;

  wire [1:0] R = dot_on ? 2'b11
               : haze   ? (warm ? 2'b01 : 2'b00)
               : 2'b00;
  wire [1:0] G = dot_on ? 2'b11
               : haze   ? 2'b01
               : 2'b00;
  wire [1:0] B = dot_on ? 2'b11
               : haze   ? (warm ? 2'b00 : 2'b01)
               : 2'b00;

  // TinyVGA Pmod: {hsync, B[0], G[0], R[0], vsync, B[1], G[1], R[1]}
  assign uo_out = {hsync, B[0], G[0], R[0], vsync, B[1], G[1], R[1]};

  // =========================================================================
  // Audio synth — 1-bit sigma-delta on uio[0].
  //   Two voices (square-wave melody + triangle-wave bass), exponential decay
  //   envelope on melody, bass constant-volume. Tempo and song position tick
  //   off vsync rising edges (60 Hz base). Shares the visual's vsync cadence
  //   so the audio and the Lissajous pointer breathe together.
  // =========================================================================

  // Song position: 8-bit counter, ticks once per vsync (60 Hz).
  reg [7:0] song_pos;
  always @(posedge clk) begin
    if (~rst_n) song_pos <= 0;
    else if (pixel_ce && vsync && !vsync_prev)
      song_pos <= song_pos + 1;
  end

  // Note frequency table (A-minor scale, 8 notes). inc = freq × 2^16 / 50.35 MHz
  // so that a 16-bit phase accumulator yields the right output frequency.
  //   0: A3 220 Hz  → 286
  //   1: C4 262 Hz  → 341
  //   2: D4 294 Hz  → 382
  //   3: E4 330 Hz  → 429
  //   4: F4 349 Hz  → 454
  //   5: G4 392 Hz  → 510
  //   6: A4 440 Hz  → 573
  //   7: C5 523 Hz  → 681
  function [9:0] note_inc;
    input [2:0] idx;
    case (idx)
      3'd0: note_inc = 10'd286;
      3'd1: note_inc = 10'd341;
      3'd2: note_inc = 10'd382;
      3'd3: note_inc = 10'd429;
      3'd4: note_inc = 10'd454;
      3'd5: note_inc = 10'd510;
      3'd6: note_inc = 10'd573;
      3'd7: note_inc = 10'd681;
    endcase
  endfunction

  // Melody steps every 8 vsyncs (≈ 133 ms) → ~1 s loop of 8 notes.
  wire [2:0] melody_idx = song_pos[5:3];
  // Bass steps every 64 vsyncs (≈ 1 s) → ~8.5 s loop of 8 notes, octave down.
  wire [2:0] bass_idx   = song_pos[7:5];
  wire [9:0] melody_inc   = note_inc(melody_idx);
  wire [9:0] bass_inc_raw = note_inc(bass_idx);
  wire [9:0] bass_inc     = {1'b0, bass_inc_raw[9:1]};  // >> 1 = octave down

  // Phase accumulators (advance every clock @ 50.35 MHz).
  reg [15:0] melody_phase;
  reg [15:0] bass_phase;
  always @(posedge clk) begin
    if (~rst_n) begin
      melody_phase <= 0;
      bass_phase   <= 0;
    end else begin
      melody_phase <= melody_phase + {6'b0, melody_inc};
      bass_phase   <= bass_phase   + {6'b0, bass_inc};
    end
  end

  // Melody envelope: 6-bit, triggered on every 4-vsync beat, exp-decays via >>3.
  wire beat_trigger = pixel_ce && vsync && !vsync_prev && (song_pos[1:0] == 2'b00);
  reg [5:0] melody_env;
  always @(posedge clk) begin
    if (~rst_n) melody_env <= 0;
    else if (beat_trigger) melody_env <= 6'd63;
    else if (pixel_ce && vsync && !vsync_prev)
      melody_env <= melody_env - (melody_env >> 3);
  end

  // Square-wave melody × envelope, signed.
  wire signed [7:0] melody_samp = melody_phase[15]
      ?  $signed({1'b0, melody_env, 1'b0})
      : -$signed({1'b0, melody_env, 1'b0});

  // Triangle-wave bass (constant ~64-amplitude).
  wire [6:0] tri_up   =  bass_phase[14:8];
  wire [6:0] tri_down = ~bass_phase[14:8];
  wire [6:0] tri_val  = bass_phase[15] ? tri_down : tri_up;
  wire signed [7:0] bass_samp = $signed({1'b0, tri_val}) - 8'sd64;

  // Mix, shift down, shift to unsigned for the sigma-delta accumulator.
  wire signed [8:0] mix          = melody_samp + bass_samp;
  wire        [7:0] audio_unsign = mix[8:1] + 8'd128;

  // 1-bit sigma-delta modulator (a1k0n's TT08 pattern).
  reg        audio_out_reg;
  reg  [7:0] sd_accum;
  wire [8:0] sd_next = {1'b0, sd_accum} + {1'b0, audio_unsign};
  always @(posedge clk) begin
    if (~rst_n) begin
      sd_accum      <= 0;
      audio_out_reg <= 0;
    end else begin
      sd_accum      <= sd_next[7:0];
      audio_out_reg <= sd_next[8];
    end
  end
  assign audio_out = audio_out_reg;

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
