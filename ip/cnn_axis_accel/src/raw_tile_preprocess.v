// raw_tile_preprocess.v - Command 4 adapter: packed mask prefix, then raw uint16 pixels.
//
// One Q28 lane processes the two halves of each packed word; normalized words
// feed the tile core's unchanged LOAD_X path.
`timescale 1ns / 1ps

module raw_tile_preprocess #(parameter ROM_PREFIX = "") (
    input  wire        clk,
    input  wire        rst,
    input  wire        command_start,
    input  wire        raw_start,
    input  wire [31:0] words,
    input  wire [ 7:0] spatial_words,
    input  wire [ 2:0] mask_words,

    // AXI4-Stream slave: packed raw pixels / mask words.
    input  wire [31:0] s_data,
    input  wire [ 3:0] s_keep,
    input  wire        s_valid,
    output wire        s_ready,
    input  wire        s_last,

    // AXI4-Stream master: standardized tile words.
    output reg  [31:0] m_data,
    output wire [ 3:0] m_keep,
    output reg         m_valid,
    input  wire        m_ready,
    output reg         m_last,
    output reg         frame_error
);

    localparam IDLE = 0, MASK = 1, DATA = 2, DRAIN = 3;

    reg [1:0] state;
    reg [31:0] left;
    reg [31:0] mask [0:6];
    reg [2:0]  mask_index;
    reg [7:0]  position;
    reg [7:0]  spatial_count;
    reg [2:0]  mask_count;
    reg [6:0]  group_index;
    reg        held, half;
    reg [31:0] held_word;
    reg [6:0]  held_group;
    reg        held_foreground, held_last;

    wire              lane_ready, lane_valid;
    wire signed [15:0] lane_data;
    wire [7:0]        lane_band;
    wire              lane_last;
    wire              lane_output_ready = !m_valid || m_ready;
    wire [7:0]        input_band = {held_group, half};
    wire              input_fire = s_valid && s_ready;
    wire              pixel_fire = held && lane_ready;

    assign s_ready = !rst && (state == MASK || (state == DATA && (!held || (half && lane_ready))));
    assign m_keep  = 4'hf;

    tiff_preprocess_lane #(
        .COEFFICIENT_FILE({ROM_PREFIX, "coefficients.mem"}),
        .DIRECTORY_FILE({ROM_PREFIX, "correction_directory.mem"}),
        .BANK_PREFIX({ROM_PREFIX, "correction_bank_"}),
        .BANK_SUFFIX(".mem")
    ) lane (
        .clk       (clk),
        .rst       (rst),
        .s_valid   (held),
        .s_ready   (lane_ready),
        .pixel     (half ? held_word[31:16] : held_word[15:0]),
        .band      (input_band),
        .foreground(held_foreground && input_band != 205),
        .s_last    (held_last && half),
        .quantized (lane_data),
        .m_valid   (lane_valid),
        .m_ready   (lane_output_ready),
        .m_band    (lane_band),
        .m_last    (lane_last)
    );

    reg [15:0] low_result;

    always @(posedge clk) begin
        if (rst) begin
            state         <= IDLE;
            left          <= 0;
            mask_index    <= 0;
            position      <= 0;
            group_index   <= 0;
            spatial_count <= 0;
            mask_count    <= 0;
            held          <= 0;
            half          <= 0;
            m_valid       <= 0;
            m_data        <= 0;
            m_last        <= 0;
            frame_error   <= 0;
        end else begin
            if (command_start) frame_error <= 0;
            if (raw_start) begin
                state         <= MASK;
                left          <= words;
                mask_index    <= 0;
                position      <= 0;
                group_index   <= 0;
                spatial_count <= spatial_words;
                mask_count    <= mask_words;
                held          <= 0;
                half          <= 0;
                m_valid       <= 0;
            end
            if (m_valid && m_ready) begin
                m_valid <= 0;
                if (m_last) state <= IDLE;
            end
            if (lane_valid && lane_output_ready) begin
                if (!lane_band[0]) low_result <= lane_data;
                else begin
                    m_data  <= {lane_data, low_result};
                    m_last  <= lane_last;
                    m_valid <= 1;
                end
            end
            if (pixel_fire) begin
                if (half) begin
                    held <= 0;
                    half <= 0;
                end else half <= 1;
            end
            if (input_fire) begin
                left <= left - 1;
                if (s_keep != 4'hf || s_last != (left == 1)) frame_error <= 1;
                if (state == MASK) begin
                    mask[mask_index] <= s_data;
                    if (mask_index == mask_count - 1) state <= DATA;
                    else mask_index <= mask_index + 1;
                end else begin
                    held_word       <= s_data;
                    held            <= 1;
                    half            <= 0;
                    held_group      <= group_index;
                    held_foreground <= mask[position[7:5]][position[4:0]];
                    held_last       <= left == 1;
                    if (position == spatial_count - 1) begin
                        position    <= 0;
                        group_index <= group_index + 1;
                    end else position <= position + 1;
                    if (left == 1) state <= DRAIN;
                end
            end
        end
    end

endmodule
