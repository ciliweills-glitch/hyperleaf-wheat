// tiff_preprocess_lane.v - Standalone pixel arithmetic lane.
//
// TIFF parsing and the complete 204-band mask stay in PS. Band 204 produces
// the quantized mask; spectral lanes accept foreground from PS. ROMs are
// frozen-model coefficients / sparse rounding corrections, not a full LUT.
`timescale 1ns / 1ps

module tiff_preprocess_lane #(
    parameter COEFFICIENT_FILE = "vectors/preprocess_fixed/coefficients.hex",
    parameter DIRECTORY_FILE   = "vectors/preprocess_fixed/correction_directory.hex",
    parameter BANK_PREFIX      = "vectors/preprocess_fixed/correction_bank_",
    parameter BANK_SUFFIX      = ".hex"
) (
    input  wire               clk,
    input  wire               rst,
    input  wire               s_valid,
    output wire               s_ready,
    input  wire [15:0]        pixel,
    input  wire [ 7:0]        band,
    input  wire               foreground,
    input  wire               s_last,
    output wire signed [15:0] quantized,
    output wire               m_valid,
    input  wire               m_ready,
    output wire [ 7:0]        m_band,
    output wire               m_last
);

    (* rom_style = "block" *) reg [77:0] coefficients [0:203];
    (* rom_style = "block" *) reg [17:0] directory [0:13055];

    initial begin
        $readmemh(COEFFICIENT_FILE, coefficients);
        $readmemh(DIRECTORY_FILE, directory);
    end

    reg [77:0] coefficient, coefficient_reg;
    reg [17:0] row;
    reg [15:0] pixel_rom, pixel_reg;
    reg [ 7:0] band_rom, band_reg;
    reg        foreground_rom, foreground_reg, last_rom, last_reg, valid_rom, valid_reg;

    wire advance;

    assign s_ready = advance;

    // Bands 204/205 are mask/tail, not spectral coefficient addresses.
    wire [ 7:0] coefficient_address = band >= 204 ? 8'd0 : band;
    wire [13:0] correction_address  = band >= 204 ? 14'd0 : {band, pixel[15:10]};

    reg signed [1:0] correction;

    wire [11:0] entry [0:8];
    wire [ 8:0] active;

    genvar g;
    generate
        for (g = 0; g < 9; g = g + 1) begin : correction_bank
            localparam [7:0] DIGIT = 8'h30 + g;
            localparam FILE_NAME   = {BANK_PREFIX, DIGIT, BANK_SUFFIX};
            (* rom_style = "block" *) reg [11:0] memory [0:1023];
            reg [11:0]  read_reg;
            reg         active_reg;
            wire [ 9:0] address       = row[9:0] + (g < row[13:10]);
            wire [ 4:0] relative_slot = g >= row[13:10] ? g - row[13:10] : g + 9 - row[13:10];
            initial $readmemh(FILE_NAME, memory);
            always @(posedge clk) if (advance) begin
                read_reg   <= memory[address];
                active_reg <= relative_slot < row[17:14];
            end
            assign entry[g]  = read_reg;
            assign active[g] = active_reg;
        end
    endgenerate

    integer slot;

    always @* begin
        correction = 0;
        for (slot = 0; slot < 9; slot = slot + 1)
            if (active[slot] && entry[slot][9:0] == pixel_reg[9:0]) correction = $signed(entry[slot][11:10]);
    end

    always @(posedge clk) begin
        if (rst) begin
            valid_rom <= 0;
            valid_reg <= 0;
        end else if (advance) begin
            valid_rom       <= s_valid;
            valid_reg       <= valid_rom;
            coefficient     <= coefficients[coefficient_address];
            row             <= directory[correction_address];
            pixel_rom       <= pixel;
            band_rom        <= band;
            foreground_rom  <= foreground;
            last_rom        <= s_last;
            coefficient_reg <= coefficient;
            pixel_reg       <= pixel_rom;
            band_reg        <= band_rom;
            foreground_reg  <= foreground_rom;
            last_reg        <= last_rom;
        end
    end

    standardize_mac mac (
        .clk           (clk),
        .rst           (rst),
        .s_valid       (valid_reg),
        .s_ready       (advance),
        .pixel         (pixel_reg),
        .multiplier_q28(coefficient_reg[29:0]),
        .offset_q28    (coefficient_reg[77:30]),
        .correction    (correction),
        .foreground    (foreground_reg),
        .is_mask       (band_reg == 204),
        .band          (band_reg),
        .s_last        (last_reg),
        .quantized     (quantized),
        .m_valid       (m_valid),
        .m_ready       (m_ready),
        .m_band        (m_band),
        .m_last        (m_last)
    );

endmodule
