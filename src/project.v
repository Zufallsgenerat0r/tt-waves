/*
 * Copyright (c) 2026 Kilian
 * SPDX-License-Identifier: Apache-2.0
 *
 * Wave Lattice — a dot-grid port of https://taylor.town/waves (Taylor Troesh,
 * inspired by Zach Lieberman). Two interfering radial wave sources displace
 * a 40x30 dot lattice on a 640x480 VGA signal. Source A follows a virtual
 * pointer that slowly winds a spiral from screen centre outward and back;
 * source B is its point-mirror (640-x, 480-y). All logic is streaming and
 * stateless per-pixel; no frame buffer, no line buffer.
 */

`default_nettype none

module tt_um_kilian_waves (
    input  wire [7:0] ui_in,    // Dedicated inputs
    output wire [7:0] uo_out,   // Dedicated outputs
    input  wire [7:0] uio_in,   // IOs: Input path
    output wire [7:0] uio_out,  // IOs: Output path
    output wire [7:0] uio_oe,   // IOs: Enable path (active high: 0=input, 1=output)
    input  wire       ena,      // always 1 when the design is powered, so you can ignore it
    input  wire       clk,      // clock 25.175 MHz
    input  wire       rst_n     // reset_n - low to reset
);

  assign uio_out = 0;
  assign uio_oe  = 0;

  wire _unused = &{ena, ui_in[7:4], uio_in, 1'b0};

  wire hsync, vsync, display_on;
  wire [9:0] x, y;

  hvsync_generator hvsync_gen(
    .clk(clk),
    .reset(~rst_n),
    .hsync(hsync),
    .vsync(vsync),
    .display_on(display_on),
    .hpos(x),
    .vpos(y)
  );

  // --- Frame counter (increments once per frame on vsync rising edge) ---
  reg [11:0] frame_counter;
  reg vsync_prev;
  always @(posedge clk) begin
    if (~rst_n) begin
      frame_counter <= 0;
      vsync_prev <= 0;
    end else begin
      vsync_prev <= vsync;
      if (vsync && !vsync_prev)
        frame_counter <= frame_counter + 1;
    end
  end

  // --- Pointer counter: separate from frame_counter so ui_in[2] can freeze
  //     the spiral without stopping internal animation. ---
  reg [11:0] ptr_counter;
  always @(posedge clk) begin
    if (~rst_n)
      ptr_counter <= 0;
    else if (vsync && !vsync_prev && !ui_in[2])
      ptr_counter <= ptr_counter + 1;
  end

  // --- Block 0: spiral virtual pointer ---
  // theta rotates; amp grows then shrinks (triangle), giving a spiral that
  // winds outward and back. ui_in[3] halves theta speed.
  // Retraced spiral driver: a phase counter ramps 0→511→0, giving one full
  // out-and-back cycle. Both theta and radius derive from this phase, so
  // when the phase reverses, the pointer retraces exactly the same path
  // backwards. Fast: 17 s full cycle (8.5 s out, 8.5 s back), ~8.5 s per
  // rotation. Slow: 34 s cycle, ~17 s per rotation (per-frame motion is
  // bursty in slow mode — motion halves but can't be fractional).
  wire [9:0] phase_raw = ui_in[3] ? ptr_counter[10:1] : ptr_counter[9:0];
  wire [8:0] phase     = phase_raw[9] ? (9'd511 - phase_raw[8:0])
                                      : phase_raw[8:0];       // 0..511..0

  // 9-bit theta_full = phase, giving exactly one theta rotation per outward
  // ramp and one backward rotation during retrace.
  wire [8:0] theta_full   = phase;
  wire [7:0] theta_hi     = theta_full[8:1];
  wire       theta_lo     = theta_full[0];

  wire [8:0] theta90_full = theta_full + 9'd128;   // 90° of 512
  wire [7:0] theta90_hi   = theta90_full[8:1];
  wire       theta90_lo   = theta90_full[0];
  // 8-bit theta_hi triangle (amp ±126, steps of 2 per theta_hi). theta_lo
  // interpolates half-steps so cos_s moves by 1 each frame.
  wire [6:0] cos_mag = theta_hi[6]
      ? {theta_hi[5:0], 1'b0}
      : (7'd126 - {theta_hi[5:0], 1'b0});
  wire signed [8:0] cos_s_coarse = (theta_hi[7] ^ theta_hi[6])
      ? -$signed({2'b00, cos_mag})
      :  $signed({2'b00, cos_mag});
  wire cos_at_bnd = (theta_hi[5:0] == 6'd63);
  wire signed [8:0] cos_delta = cos_at_bnd ? 9'sd0
                              : (theta_hi[7] ? 9'sd2 : -9'sd2);
  wire signed [8:0] cos_s = theta_lo ? (cos_s_coarse + (cos_delta >>> 1))
                                     : cos_s_coarse;

  wire [6:0] sin_mag = theta90_hi[6]
      ? {theta90_hi[5:0], 1'b0}
      : (7'd126 - {theta90_hi[5:0], 1'b0});
  wire signed [8:0] sin_s_coarse = (theta90_hi[7] ^ theta90_hi[6])
      ? -$signed({2'b00, sin_mag})
      :  $signed({2'b00, sin_mag});
  wire sin_at_bnd = (theta90_hi[5:0] == 6'd63);
  wire signed [8:0] sin_delta = sin_at_bnd ? 9'sd0
                              : (theta90_hi[7] ? 9'sd2 : -9'sd2);
  wire signed [8:0] sin_s = theta90_lo ? (sin_s_coarse + (sin_delta >>> 1))
                                       : sin_s_coarse;

  // Radius shares the phase counter: as phase grows, radius grows; as phase
  // retreats, radius shrinks. Floor of 64 keeps per-frame motion ≥ 1 px so
  // the retrace is smooth even at the inner end of the path.
  wire [6:0] radius = 7'd64 + {3'b000, phase[8:5]};  // 64..79..64

  // cos_s (±126) × radius (64..79) → ±9954. >>> 6 gives ±155, inside
  // the clamp rails at ±158.
  wire signed [15:0] dx_full = cos_s * $signed({1'b0, radius});
  wire signed [15:0] dy_full = sin_s * $signed({1'b0, radius});
  wire signed [12:0] ptr_x_raw = 13'sd320 + (dx_full >>> 6);
  wire signed [12:0] ptr_y_raw = 13'sd240 + (dy_full >>> 6);
  // Clamps chosen so |offset_ax|, |offset_bx| ≤ 158 — fits inside the 159
  // available hblank cycles for the r2a/r2b fix-up below.
  wire signed [9:0]  ptr_x = (ptr_x_raw < 13'sd162) ? 10'sd162
                           : (ptr_x_raw > 13'sd478) ? 10'sd478
                           : ptr_x_raw[9:0];
  wire signed [9:0]  ptr_y = (ptr_y_raw < 13'sd32)  ? 10'sd32
                           : (ptr_y_raw > 13'sd448) ? 10'sd448
                           : ptr_y_raw[9:0];

  // --- Source centres: A = pointer, B = point-mirror ---
  wire signed [9:0] center_ax = ptr_x;
  wire signed [9:0] center_ay = ptr_y;
  wire signed [9:0] center_bx = 10'sd640 - ptr_x;
  wire signed [9:0] center_by = 10'sd480 - ptr_y;
  wire signed [9:0] p_ax = x - center_ax;
  wire signed [9:0] p_ay = y - center_ay;
  wire signed [9:0] p_bx = x - center_bx;
  wire signed [9:0] p_by = y - center_by;

  // --- Distance-squared accumulators (two sources) ---
  // Narrowed accumulators; phase-bit extraction is tolerant of modular wrap.
  reg [13:0] r1a, r1b;
  reg [13:0] r2a, r2b;
  wire [14:0] ra = {r1a, 1'b0} + {1'b0, r2a};
  wire [14:0] rb = {r1b, 1'b0} + {1'b0, r2b};

  // Offset relative to screen centre, used by the hblank fix-up branch.
  // |offset| bounded to ≤ 158 via the ptr_x clamp above.
  wire signed [9:0] offset_ax = center_ax - 10'sd320;
  wire signed [9:0] offset_bx = center_bx - 10'sd320;
  wire [9:0] abs_off_ax = offset_ax[9] ? (10'd0 - offset_ax) : offset_ax;
  wire [9:0] abs_off_bx = offset_bx[9] ? (10'd0 - offset_bx) : offset_bx;

  always @(posedge clk) begin
    if (~rst_n) begin
      r1a <= 0; r2a <= 0;
      r1b <= 0; r2b <= 0;
    end else begin
      if (vsync) begin
        r1a <= 0; r2a <= 0;
        r1b <= 0; r2b <= 0;
      end

      if (display_on & y == 0) begin
        if (x < center_ay) r1a <= r1a + center_ay;
        if (x < center_by) r1b <= r1b + center_by;
      end else if (x == 640) begin
        r2a <= 14'd4096;
        r2b <= 14'd4096;
      end else if (x > 640) begin
        // During hblank, walk r2a from 320² toward center_ax² with |offset|
        // increments. Needs full 10-bit magnitude to support the spiral's
        // ±158 range (original tt-interference only supported ±15).
        if (offset_ax[9] == 1'b0 && (x - 10'd641) < abs_off_ax)
          r2a <= r2a + 19'sd640 + offset_ax;
        else if (offset_ax[9] == 1'b1 && (x - 10'd641) < abs_off_ax)
          r2a <= r2a - (19'sd640 + offset_ax);
        if (offset_bx[9] == 1'b0 && (x - 10'd641) < abs_off_bx)
          r2b <= r2b + 19'sd640 + offset_bx;
        else if (offset_bx[9] == 1'b1 && (x - 10'd641) < abs_off_bx)
          r2b <= r2b - (19'sd640 + offset_bx);
      end else if (display_on & x == 0) begin
        // Delta uses 2*(Y-1-cy)+1 = 2*p_ay-1 because y has already
        // advanced to Y when this update fires at x==0.
        r1a <= r1a + 2*p_ay - 1;
        r1b <= r1b + 2*p_by - 1;
      end else if (display_on) begin
        r2a <= r2a + 2*p_ax + 1;
        r2b <= r2b + 2*p_bx + 1;
      end
    end
  end

  // "Far-from-axis" predicates: true when the dot is at least 16 px off the
  // source's x or y line. Used to damp on-axis displacement so dots sitting
  // at y=center_ay don't get full vertical push (silicon analog of the
  // JS normalized `dy/d` scaling — approximated as a binary gate here).
  wire p_ax_far = ~((&p_ax[9:4]) | (~|p_ax[9:4]));
  wire p_ay_far = ~((&p_ay[9:4]) | (~|p_ay[9:4]));
  wire p_bx_far = ~((&p_bx[9:4]) | (~|p_bx[9:4]));
  wire p_by_far = ~((&p_by[9:4]) | (~|p_by[9:4]));

  // --- Block A: lattice anchor latches ---
  // Fire on last pixel of each 16-wide cell (x[3:0]==4'hF), AND on x==639 so
  // column 0 of the next scanline isn't still carrying the previous line's
  // values after the y accumulator has stepped.
  reg [11:0] ra_lat, rb_lat;
  reg sgn_ax_lat, sgn_ay_lat, sgn_bx_lat, sgn_by_lat;
  reg far_ax_lat, far_ay_lat, far_bx_lat, far_by_lat;
  always @(posedge clk) begin
    if (~rst_n) begin
      ra_lat <= 0; rb_lat <= 0;
      sgn_ax_lat <= 0; sgn_ay_lat <= 0;
      sgn_bx_lat <= 0; sgn_by_lat <= 0;
      far_ax_lat <= 0; far_ay_lat <= 0;
      far_bx_lat <= 0; far_by_lat <= 0;
    end else if (display_on && (x[3:0] == 4'hF || x == 10'd639)) begin
      ra_lat <= ra[14:3];
      rb_lat <= rb[14:3];
      sgn_ax_lat <= p_ax[9];
      sgn_ay_lat <= p_ay[9];
      sgn_bx_lat <= p_bx[9];
      sgn_by_lat <= p_by[9];
      far_ax_lat <= p_ax_far;
      far_ay_lat <= p_ay_far;
      far_bx_lat <= p_bx_far;
      far_by_lat <= p_by_far;
    end
  end

  // --- Block B: signed displacement decode ---
  // ra_lat[10] is the sign (alternates across radial ridges — the
  // tanh(sharp·sin) behaviour in silicon form); ra_lat[9:8] is 0..3 magnitude.
  // Axis direction on top of that from the latched p_ax/p_ay sign.
  wire signed [2:0] disp_a = ra_lat[10] ? -{1'b0, ra_lat[9:8]} : {1'b0, ra_lat[9:8]};
  wire signed [2:0] disp_b = rb_lat[10] ? -{1'b0, rb_lat[9:8]} : {1'b0, rb_lat[9:8]};

  // Zero the axis component when the dot is within 16 px of that source axis.
  // This replaces the hard sign flip at y=center_ay with a 32-px-wide band of
  // undisplaced dots, hiding the seam without a divider.
  wire signed [3:0] dlx_a = far_ax_lat ? (sgn_ax_lat ? -{disp_a[2], disp_a} : {disp_a[2], disp_a}) : 4'sd0;
  wire signed [3:0] dly_a = far_ay_lat ? (sgn_ay_lat ? -{disp_a[2], disp_a} : {disp_a[2], disp_a}) : 4'sd0;
  wire signed [3:0] dlx_b = far_bx_lat ? (sgn_bx_lat ? -{disp_b[2], disp_b} : {disp_b[2], disp_b}) : 4'sd0;
  wire signed [3:0] dly_b = far_by_lat ? (sgn_by_lat ? -{disp_b[2], disp_b} : {disp_b[2], disp_b}) : 4'sd0;

  wire signed [4:0] dlx_sum = dlx_a + dlx_b;
  wire signed [4:0] dly_sum = dly_a + dly_b;

  // Saturate to ±6 so a dot cannot cross into a neighbour cell.
  wire signed [3:0] dlx = (dlx_sum >  5'sd6) ?  4'sd6
                        : (dlx_sum < -5'sd6) ? -4'sd6
                        : dlx_sum[3:0];
  wire signed [3:0] dly = (dly_sum >  5'sd6) ?  4'sd6
                        : (dly_sum < -5'sd6) ? -4'sd6
                        : dly_sum[3:0];

  // --- Block C: dot mask, pipelined ---
  // 16-pixel lattice spacing; dot centre at local (8,8); Chebyshev radius 2.
  wire signed [5:0] ex = $signed({2'b00, x[3:0]}) - 6'sd8 - {{2{dlx[3]}}, dlx};
  wire signed [5:0] ey = $signed({2'b00, y[3:0]}) - 6'sd8 - {{2{dly[3]}}, dly};
  wire dot_now = (ex >= -6'sd2 && ex <= 6'sd2 &&
                  ey >= -6'sd2 && ey <= 6'sd2);
  reg dot;
  always @(posedge clk) begin
    if (~rst_n) dot <= 0;
    else dot <= dot_now;
  end

  // --- Block D: output ---
  // Palette variants: 00=white, 01=no-red(cyan), 10=no-green(magenta), 11=no-blue(yellow).
  wire [1:0] palette = ui_in[1:0];
  wire dot_on = display_on & dot;
  wire [1:0] R = dot_on ? (palette == 2'b01 ? 2'b00 : 2'b11) : 2'b00;
  wire [1:0] G = dot_on ? (palette == 2'b10 ? 2'b00 : 2'b11) : 2'b00;
  wire [1:0] B = dot_on ? (palette == 2'b11 ? 2'b00 : 2'b11) : 2'b00;

  // TinyVGA Pmod: {hsync, B[0], G[0], R[0], vsync, B[1], G[1], R[1]}
  assign uo_out = {hsync, B[0], G[0], R[0], vsync, B[1], G[1], R[1]};

endmodule


// VGA 640x480 @ 60Hz sync generator
// Proven in silicon (tt08-vga-drop by ReJ/Renaldas Zioma)
module hvsync_generator(clk, reset, hsync, vsync, display_on, hpos, vpos);
    input clk;
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
      hsync <= (hpos>=H_SYNC_START && hpos<=H_SYNC_END);
      if(hmaxxed)
        hpos <= 0;
      else
        hpos <= hpos + 1;
    end

    always @(posedge clk)
    begin
      vsync <= (vpos>=V_SYNC_START && vpos<=V_SYNC_END);
      if(hmaxxed)
        if (vmaxxed)
          vpos <= 0;
        else
          vpos <= vpos + 1;
    end

    assign display_on = (hpos<H_DISPLAY) && (vpos<V_DISPLAY);
endmodule
