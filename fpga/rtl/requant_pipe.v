// requant_pipe.v - Exact signed 48x64 -> 112-bit product; F48 ties-even and ReLU.
//
// 16-bit limbs give twelve single-DSP multipliers, each with A/B, M and P stages.
`timescale 1ns / 1ps

module requant_pipe (
    input  wire               clk,
    input  wire               rst,
    input  wire               in_valid,
    input  wire signed [47:0] acc,
    input  wire signed [63:0] scale_q,
    input  wire signed [63:0] bias_q,
    output reg        [16-1:0] y,
    output reg                out_valid
);

    wire signed [111:0] terms [0:11];
    reg signed [16:0] a [0:2], b [0:3];
    integer limb;

    always @(posedge clk) begin
        for (limb = 0; limb < 3; limb = limb + 1) a[limb] <= {limb == 2 ? acc[47] : 1'b0, acc[16 * limb +: 16]};
        for (limb = 0; limb < 4; limb = limb + 1) b[limb] <= {limb == 3 ? scale_q[63] : 1'b0, scale_q[16 * limb +: 16]};
    end

    genvar i, j;
    generate
        for (i = 0; i < 3; i = i + 1) begin : a_limb
            for (j = 0; j < 4; j = j + 1) begin : b_limb
                // The top signed 16x16 limb occupies exactly the top 32 product bits.
                localparam PW = (i == 2 && j == 3) ? 32 : 34;
                (* use_dsp = "yes" *) reg signed [PW-1:0] m, p;
                always @(posedge clk) begin
                    m <= a[i] * b[j];
                    p <= m;
                end
                assign terms[4 * i + j] = $signed({{(112 - PW){p[PW-1]}}, p}) <<< (16 * (i + j));
            end
        end
    endgenerate

    // Keep the wide addition tree in fabric; it is separate from DSP P registers.
    (* use_dsp = "no" *) reg signed [111:0] s1 [0:5], s2 [0:2], s3 [0:1];
    (* use_dsp = "no" *) reg signed [111:0] product, sum;
    reg signed [63:0] bias_delay [0:6];
    reg [7:0]         valid;
    reg signed [64:0] rounded;
    integer k;

    always @* begin
        rounded = $signed(sum[111:48]);
        if (sum[47] && ((|sum[46:0]) || sum[48])) rounded = rounded + 1;
    end

    always @(posedge clk) begin
        for (k = 0; k < 6; k = k + 1) s1[k] <= terms[2 * k] + terms[2 * k + 1];
        for (k = 0; k < 3; k = k + 1) s2[k] <= s1[2 * k] + s1[2 * k + 1];
        s3[0]     <= s2[0] + s2[1];
        s3[1]     <= s2[2];
        product   <= s3[0] + s3[1];
        bias_delay[0] <= bias_q;
        for (k = 1; k < 7; k = k + 1) bias_delay[k] <= bias_delay[k - 1];
        sum <= product + {{48{bias_delay[6][63]}}, bias_delay[6]};
        if (rst) begin
            valid     <= 0;
            out_valid <= 0;
            y         <= 0;
        end else begin
            valid     <= {valid[6:0], in_valid};
            out_valid <= valid[7];
            if (valid[7]) begin
                if (rounded < 0) y <= 0;
                else if (rounded > 32767) y <= 32767;
                else y <= rounded[16 - 1:0];
            end
        end
    end

endmodule
