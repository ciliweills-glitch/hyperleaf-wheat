// pe_array.v - Parameterized conv MAC array (fabric adder tree, DSP multipliers).
//
// N_MAC = PIN*POUT*K*K signed 16x16 products.
// Pipeline: input reg -> mul M -> mul P -> 3-level adder tree (reg) -> acc. ACC_W=48.
`timescale 1ns / 1ps

(* use_dsp = "no" *)
module pe_array #(
    parameter integer PIN   = 2,
    parameter integer POUT  = 2,
    parameter integer K     = 3,
    parameter integer ACC_W = 48
) (
    input  wire                      clk,
    input  wire                      rst,
    input  wire                      clear_acc,
    input  wire                      in_valid,
    input  wire [PIN*K*K*16-1:0]     x_flat,
    input  wire [POUT*PIN*K*K*16-1:0] w_flat,
    output reg  [POUT*ACC_W-1:0]     acc_flat
);

    localparam integer XTAPS = PIN * K * K;
    localparam integer NMAC  = POUT * XTAPS;
    // 18 taps -> 9 -> 5 -> 1  (levels sized for XTAPS=18; generate-safe floors)
    localparam integer L1 = (XTAPS + 1) / 2;
    localparam integer L2 = (L1 + 1) / 2;
    localparam integer L3 = (L2 + 1) / 2;

    wire [31:0] prod [0:NMAC-1];
    reg         v0, vm, v1;

    genvar mac;
    generate
        for (mac = 0; mac < NMAC; mac = mac + 1) begin : mul
            pe_mul16 pipeline (
                .clk   (clk),
                .enable(in_valid),
                .a     (x_flat[(mac % XTAPS) * 16 +: 16]),
                .b     (w_flat[mac * 16 +: 16]),
                .p     (prod[mac])
            );
        end
    endgenerate

    (* use_dsp = "no", ram_style = "registers" *) reg signed [ACC_W-1:0] l1 [0:POUT*L1-1];
    (* use_dsp = "no", ram_style = "registers" *) reg signed [ACC_W-1:0] l2 [0:POUT*L2-1];
    (* use_dsp = "no", ram_style = "registers" *) reg signed [ACC_W-1:0] l3 [0:POUT*L3-1];
    reg v2, v3, v4;

    integer i, po, t;
    reg signed [ACC_W-1:0] final_sum [0:POUT-1];
    integer cp, ct;

    always @* begin
        for (cp = 0; cp < POUT; cp = cp + 1) begin
            final_sum[cp] = 0;
            for (ct = 0; ct < L3; ct = ct + 1) final_sum[cp] = final_sum[cp] + l3[cp * L3 + ct];
        end
    end

    always @(posedge clk) begin
        if (rst) begin
            v0 <= 1'b0;
            vm <= 1'b0;
            v1 <= 1'b0;
            v2 <= 1'b0;
            v3 <= 1'b0;
            v4 <= 1'b0;
        end else begin
            v0 <= in_valid;
            vm <= v0;
            v1 <= vm;
            v2 <= v1;
            v3 <= v2;
            v4 <= v3;
        end
    end

    always @(posedge clk) begin
        for (po = 0; po < POUT; po = po + 1) begin
            for (t = 0; t < L1; t = t + 1) begin
                if (2 * t + 1 < XTAPS)
                    l1[po * L1 + t] <= {{(ACC_W - 32){prod[po * XTAPS + 2 * t][31]}}, prod[po * XTAPS + 2 * t]} +
                                       {{(ACC_W - 32){prod[po * XTAPS + 2 * t + 1][31]}}, prod[po * XTAPS + 2 * t + 1]};
                else
                    l1[po * L1 + t] <= {{(ACC_W - 32){prod[po * XTAPS + 2 * t][31]}}, prod[po * XTAPS + 2 * t]};
            end
        end
    end

    always @(posedge clk) begin
        for (po = 0; po < POUT; po = po + 1) begin
            for (t = 0; t < L2; t = t + 1) begin
                if (2 * t + 1 < L1)
                    l2[po * L2 + t] <= l1[po * L1 + 2 * t] + l1[po * L1 + 2 * t + 1];
                else
                    l2[po * L2 + t] <= l1[po * L1 + 2 * t];
            end
        end
    end

    always @(posedge clk) begin
        for (po = 0; po < POUT; po = po + 1) begin
            for (t = 0; t < L3; t = t + 1) begin
                if (2 * t + 1 < L2)
                    l3[po * L3 + t] <= l2[po * L2 + 2 * t] + l2[po * L2 + 2 * t + 1];
                else
                    l3[po * L3 + t] <= l2[po * L2 + 2 * t];
            end
        end
    end

    always @(posedge clk) begin
        if (clear_acc) begin
            for (po = 0; po < POUT; po = po + 1) acc_flat[po * ACC_W +: ACC_W] <= {ACC_W{1'b0}};
        end else if (v4) begin
            for (po = 0; po < POUT; po = po + 1) begin
                acc_flat[po * ACC_W +: ACC_W] <= acc_flat[po * ACC_W +: ACC_W] + final_sum[po];
            end
        end
    end

endmodule

// Preserve this arithmetic boundary so the fabric tree cannot absorb DSP PREG.
(* keep_hierarchy = "yes", use_dsp = "yes" *)
module pe_mul16 (
    input  wire               clk,
    input  wire               enable,
    input  wire signed [15:0] a, b,
    output reg  signed [31:0] p
);

    reg signed [15:0] ar, br;
    reg signed [31:0] m;

    always @(posedge clk) begin
        if (enable) begin
            ar <= a;
            br <= b;
        end
        m <= ar * br;
        p <= m;
    end

endmodule
