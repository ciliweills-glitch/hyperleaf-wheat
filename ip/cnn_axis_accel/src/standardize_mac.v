// standardize_mac.v - uint16 pixel * signed Q28 coefficient + signed Q28 bias.
//
// Signed floor/remainder rounding implements ties-to-even, including negative
// ties. A sparse +/-1 correction applies AFTER symmetric saturation of the
// base result.
`timescale 1ns / 1ps

module standardize_mac (
    input  wire               clk,
    input  wire               rst,
    input  wire               s_valid,
    output wire               s_ready,
    input  wire [15:0]        pixel,
    input  wire signed [29:0] multiplier_q28,
    input  wire signed [47:0] offset_q28,
    input  wire signed [ 1:0] correction,
    input  wire               foreground,
    input  wire               is_mask,
    input  wire [ 7:0]        band,
    input  wire               s_last,
    output reg  signed [15:0] quantized,
    output wire               m_valid,
    input  wire               m_ready,
    output reg  [ 7:0]        m_band,
    output reg                m_last
);

    localparam signed [15:0] MASK_VALUE = 16'sd2709;

    reg [3:0] valid;

    wire advance = !valid[3] || m_ready;

    assign s_ready = advance && !rst;
    assign m_valid = valid[3];

    // Two signed small multipliers, both fitting a single DSP48E1 multiplier.
    (* use_dsp = "yes" *) reg signed [33:0] product_low;
    (* use_dsp = "yes" *) reg signed [31:0] product_high;
    reg signed [33:0] product_low_pipe;
    reg signed [31:0] product_high_pipe;
    reg signed [47:0] offset_reg, offset_pipe;
    (* use_dsp = "no" *) reg signed [47:0] total;
    reg signed [ 1:0] correction_pipe [0:2];
    reg [2:0]         foreground_pipe, mask_pipe, last_pipe;
    reg [7:0]         band_pipe [0:2];

    wire signed [47:0] low_term  = {{14{product_low_pipe[33]}}, product_low_pipe};
    wire signed [47:0] high_term = $signed({{16{product_high_pipe[31]}}, product_high_pipe}) <<< 16;

    reg signed [20:0] rounded;
    reg signed [16:0] saturated, corrected;

    always @* begin
        rounded = $signed(total[47:28]);
        if (total[27] && ((|total[26:0]) || total[28])) rounded = rounded + 1;
        if (rounded < -32767) saturated = -32767;
        else if (rounded > 32767) saturated = 32767;
        else saturated = rounded;
        corrected = saturated + $signed(correction_pipe[2]);
    end

    always @(posedge clk) begin
        if (rst) begin
            valid     <= 0;
            quantized <= 0;
            m_band    <= 0;
            m_last    <= 0;
        end else if (advance) begin
            valid          <= {valid[2:0], s_valid};
            product_low    <= $signed({1'b0, pixel}) * $signed({1'b0, multiplier_q28[15:0]});
            product_high   <= $signed({1'b0, pixel}) * $signed(multiplier_q28[29:16]);
            offset_reg     <= offset_q28;
            product_low_pipe <= product_low;
            product_high_pipe <= product_high;
            offset_pipe <= offset_reg;
            total          <= low_term + high_term + offset_pipe;
            correction_pipe[0] <= correction;
            correction_pipe[1] <= correction_pipe[0];
            correction_pipe[2] <= correction_pipe[1];
            foreground_pipe <= {foreground_pipe[1:0], foreground};
            mask_pipe       <= {mask_pipe[1:0], is_mask};
            last_pipe       <= {last_pipe[1:0], s_last};
            band_pipe[0]    <= band;
            band_pipe[1]    <= band_pipe[0];
            band_pipe[2]    <= band_pipe[1];
            if (valid[2]) begin
                if (!foreground_pipe[2]) quantized <= 0;
                else if (mask_pipe[2]) quantized <= MASK_VALUE;
                else quantized <= corrected[15:0];
                m_band <= band_pipe[2];
                m_last <= last_pipe[2];
            end
        end
    end

endmodule
