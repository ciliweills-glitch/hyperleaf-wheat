// cnn_axis_accel.v - INT16 Conv1-4 tile IP top with AXI4-Lite control
//
// CTRL/words retain loopback offsets; other regs are new.
// See fpga/overlay/README.md for the DMA packet and tile contract.
`timescale 1ns / 1ps

module cnn_axis_accel #(parameter ROM_PREFIX = "") (
    (* X_INTERFACE_PARAMETER = "ASSOCIATED_BUSIF s_axi:s_axis:m_axis, ASSOCIATED_RESET aresetn" *)
    input  wire        aclk,
    (* X_INTERFACE_PARAMETER = "POLARITY ACTIVE_LOW" *)
    input  wire        aresetn,

    // AXI4-Lite slave: register control port.
    input  wire [ 7:0] s_axi_awaddr,
    input  wire        s_axi_awvalid,
    output wire        s_axi_awready,
    input  wire [31:0] s_axi_wdata,
    input  wire [ 3:0] s_axi_wstrb,
    input  wire        s_axi_wvalid,
    output wire        s_axi_wready,
    output wire [ 1:0] s_axi_bresp,
    output wire        s_axi_bvalid,
    input  wire        s_axi_bready,
    input  wire [ 7:0] s_axi_araddr,
    input  wire        s_axi_arvalid,
    output wire        s_axi_arready,
    output wire [31:0] s_axi_rdata,
    output wire [ 1:0] s_axi_rresp,
    output wire        s_axi_rvalid,
    input  wire        s_axi_rready,

    // AXI4-Stream slave: tile / weight input.
    input  wire [31:0] s_axis_tdata,
    input  wire [ 3:0] s_axis_tkeep,
    input  wire        s_axis_tvalid,
    output wire        s_axis_tready,
    input  wire        s_axis_tlast,

    // AXI4-Stream master: result output.
    output wire [31:0] m_axis_tdata,
    output wire [ 3:0] m_axis_tkeep,
    output wire        m_axis_tvalid,
    input  wire        m_axis_tready,
    output wire        m_axis_tlast
);

    wire rst = !aresetn;

    wire        wr_en, rd_en;
    wire [ 7:0] wr_addr, rd_addr;
    wire [31:0] wr_data;
    wire [ 3:0] wr_strb;
    reg  [31:0] rd_data;

    axil_regs regs (
        .clk        (aclk),
        .rst        (rst),
        .s_axi_awaddr(s_axi_awaddr),
        .s_axi_awvalid(s_axi_awvalid),
        .s_axi_awready(s_axi_awready),
        .s_axi_wdata(s_axi_wdata),
        .s_axi_wstrb(s_axi_wstrb),
        .s_axi_wvalid(s_axi_wvalid),
        .s_axi_wready(s_axi_wready),
        .s_axi_bresp(s_axi_bresp),
        .s_axi_bvalid(s_axi_bvalid),
        .s_axi_bready(s_axi_bready),
        .s_axi_araddr(s_axi_araddr),
        .s_axi_arvalid(s_axi_arvalid),
        .s_axi_arready(s_axi_arready),
        .s_axi_rdata(s_axi_rdata),
        .s_axi_rresp(s_axi_rresp),
        .s_axi_rvalid(s_axi_rvalid),
        .s_axi_rready(s_axi_rready),
        .wr_en      (wr_en),
        .wr_addr    (wr_addr),
        .wr_data    (wr_data),
        .wr_strb    (wr_strb),
        .rd_en      (rd_en),
        .rd_addr    (rd_addr),
        .rd_data    (rd_data)
    );

    reg [31:0] words, command, dims, groups, pool;
    reg [31:0] words_q, command_q, dims_q;
    reg        start, done;
    reg        start_q;
    reg [15:0] cmpl;

    wire busy, core_done, core_frame_error, raw_frame_error;
    wire frame_error = core_frame_error || raw_frame_error;
    reg  raw_active;

    wire [8:0] spatial_words = (dims[18:16] + 9'd2) * (dims[5:0] + 9'd2);
    wire [2:0] mask_words    = (spatial_words + 9'd31) >> 5;

    wire [31:0] raw_data;
    wire [ 3:0] raw_keep;
    wire        raw_valid, raw_ready, raw_last, core_ready;

    assign s_axis_tready = raw_active ? raw_ready : core_ready;

    raw_tile_preprocess #(.ROM_PREFIX(ROM_PREFIX)) raw_input (
        .clk        (aclk),
        .rst        (rst),
        .command_start(start),
        .raw_start  (start && command == 4),
        .words      (words),
        .spatial_words(spatial_words[7:0]),
        .mask_words (mask_words),
        .s_data     (s_axis_tdata),
        .s_keep     (s_axis_tkeep),
        .s_valid    (s_axis_tvalid && raw_active),
        .s_ready    (raw_ready),
        .s_last     (s_axis_tlast),
        .m_data     (raw_data),
        .m_keep     (raw_keep),
        .m_valid    (raw_valid),
        .m_ready    (core_ready && raw_active),
        .m_last     (raw_last),
        .frame_error(raw_frame_error)
    );

    wire idle = !busy && !start;

    integer byte_i;

    always @(posedge aclk) begin
        if (rst) begin
            words      <= 0;
            command    <= 0;
            dims       <= 0;
            groups     <= 0;
            pool       <= 0;
            start      <= 0;
            done       <= 0;
            words_q    <= 0;
            command_q  <= 0;
            dims_q     <= 0;
            start_q    <= 0;
            cmpl       <= 0;
            raw_active <= 0;
        end else begin
            start <= 0;
            if (start) raw_active <= command == 4;
            if (rd_en && rd_addr == 0) done <= 0;
            if (core_done) begin
                done <= 1;
                cmpl <= cmpl + 1;
                if (start_q) begin
                    start_q   <= 0;
                    words     <= words_q;
                    command   <= command_q;
                    dims      <= dims_q;
                    start     <= 1;
                end
            end
            if (wr_en) begin
                if (wr_addr == 0 && wr_strb[0] && wr_data[0]) begin
                    if (idle) begin
                        start <= 1;
                        done  <= 0;
                    end else begin
                        words_q   <= words;
                        command_q <= command;
                        dims_q    <= dims;
                        start_q   <= 1;
                    end
                end
                for (byte_i = 0; byte_i < 4; byte_i = byte_i + 1) begin
                    if (wr_strb[byte_i]) begin
                        case (wr_addr)
                            8'h10: words[byte_i * 8 +: 8]   <= wr_data[byte_i * 8 +: 8];
                            8'h14: command[byte_i * 8 +: 8] <= wr_data[byte_i * 8 +: 8];
                            8'h18: dims[byte_i * 8 +: 8]    <= wr_data[byte_i * 8 +: 8];
                            8'h1c: groups[byte_i * 8 +: 8]  <= wr_data[byte_i * 8 +: 8];
                            8'h20: pool[byte_i * 8 +: 8]    <= wr_data[byte_i * 8 +: 8];
                        endcase
                    end
                end
            end
        end
    end

    always @* begin
        rd_data = 0;
        case (rd_addr)
            8'h00: rd_data = {28'd0, idle, idle, done, start};
            8'h10: rd_data = words;
            8'h14: rd_data = command;
            8'h18: rd_data = dims;
            8'h1c: rd_data = groups;
            8'h20: rd_data = pool;
            8'h24: rd_data = {30'd0, frame_error, busy};
            8'h28: rd_data = 32'h00010004;
            8'h2c: rd_data = cmpl;
        endcase
    end

    cnn_tile_core core (
        .clk        (aclk),
        .rst        (rst),
        .start      (start),
        .command    (command == 4 ? 2'd1 : command[1:0]),
        .words      (command == 4 ? words - mask_words : words),
        .tile_w     (dims[5:0]),
        .tile_h     (dims[18:16]),
        .groups     (groups[6:0]),
        .pool_w     (pool[10:8]),
        .pool_h     (pool[1:0]),
        .pointwise  (pool[16]),
        .npairs     (pool[31:20]),
        .busy       (busy),
        .done       (core_done),
        .frame_error(core_frame_error),
        .s_axis_tdata (raw_active ? raw_data : s_axis_tdata),
        .s_axis_tkeep (raw_active ? raw_keep : s_axis_tkeep),
        .s_axis_tvalid(raw_active ? raw_valid : s_axis_tvalid),
        .s_axis_tready(core_ready),
        .s_axis_tlast (raw_active ? raw_last : s_axis_tlast),
        .m_axis_tdata (m_axis_tdata),
        .m_axis_tkeep (m_axis_tkeep),
        .m_axis_tvalid(m_axis_tvalid),
        .m_axis_tready(m_axis_tready),
        .m_axis_tlast (m_axis_tlast)
    );

endmodule
