/*
 * OrangeCrab ECP5-85F wrapper for tt_um_kilian_waves (variant F).
 * PLL generates 51.2 MHz internal clock from 48 MHz oscillator.
 * Variant F runs internal logic at 2× the VGA pixel rate (50.35 MHz target;
 * 51.2 MHz is the closest achievable from 48 MHz via ECP5 PLL, and the VGA
 * output timing tolerates the 1.7% offset the same way 25.6→25.175 does).
 */

`default_nettype none

module top (
    input  wire clk48,
    input  wire usr_btn,
    output wire led_r,
    output wire led_g,
    output wire led_b,
    output wire [7:0] pmod
);

    wire clk_vga;
    wire pll_locked;

    // PLL: 48 MHz -> 51.2 MHz (2× VGA pixel rate for variant F time-mux)
    pll_50m pll_inst (
        .clkin(clk48),
        .clkout0(clk_vga),
        .locked(pll_locked)
    );

    // Power-on reset: hold reset until PLL locks
    reg [3:0] reset_cnt = 4'hF;
    wire rst_n = (reset_cnt == 0);
    always @(posedge clk_vga)
        if (!pll_locked)
            reset_cnt <= 4'hF;
        else if (reset_cnt != 0)
            reset_cnt <= reset_cnt - 1;

    wire [7:0] uo_out;

    tt_um_kilian_waves demo (
        .ui_in  (8'b0000_0000),
        .uo_out (uo_out),
        .uio_in (8'h00),
        .uio_out(),
        .uio_oe (),
        .ena    (1'b1),
        .clk    (clk_vga),
        .rst_n  (rst_n)
    );

    // Invert HSYNC[7] and VSYNC[3] for active-low VGA sync.
    assign pmod = uo_out ^ 8'b1000_1000;

    assign led_r = 1'b1;
    assign led_g = ~pll_locked;  // green when PLL locked
    assign led_b = 1'b1;

endmodule


// ECP5 PLL: 48 MHz -> 51.2 MHz (ecppll -i 48 -o 50.35)
// VCO = 48 * 16 / 15 = 51.2 * 12 = 614.4 MHz
module pll_50m (
    input  wire clkin,
    output wire clkout0,
    output wire locked
);

    (* FREQUENCY_PIN_CLKI="48" *)
    (* FREQUENCY_PIN_CLKOP="51.2" *)
    (* ICP_CURRENT="12" *)
    (* LPF_RESISTOR="8" *)
    (* MFG_ENABLE_FILTEROPAMP="1" *)
    (* MFG_GMCREF_SEL="2" *)
    EHXPLLL #(
        .PLLRST_ENA       ("DISABLED"),
        .INTFB_WAKE       ("DISABLED"),
        .STDBY_ENABLE     ("DISABLED"),
        .DPHASE_SOURCE    ("DISABLED"),
        .OUTDIVIDER_MUXA  ("DIVA"),
        .OUTDIVIDER_MUXB  ("DIVB"),
        .OUTDIVIDER_MUXC  ("DIVC"),
        .OUTDIVIDER_MUXD  ("DIVD"),
        .CLKI_DIV         (15),
        .CLKOP_ENABLE     ("ENABLED"),
        .CLKOP_DIV        (12),
        .CLKOP_CPHASE     (6),
        .CLKOP_FPHASE     (0),
        .FEEDBK_PATH      ("CLKOP"),
        .CLKFB_DIV        (16)
    ) pll_i (
        .RST       (1'b0),
        .STDBY     (1'b0),
        .CLKI      (clkin),
        .CLKOP     (clkout0),
        .CLKFB     (clkout0),
        .CLKINTFB  (),
        .PHASESEL0 (1'b0),
        .PHASESEL1 (1'b0),
        .PHASEDIR  (1'b1),
        .PHASESTEP (1'b1),
        .PHASELOADREG (1'b1),
        .PLLWAKESYNC (1'b0),
        .ENCLKOP   (1'b0),
        .LOCK      (locked)
    );

endmodule
