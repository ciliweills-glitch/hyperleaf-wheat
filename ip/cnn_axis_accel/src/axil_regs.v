// axil_regs.v - One outstanding AXI4-Lite transaction; independent AW/W capture.
`timescale 1ns / 1ps

module axil_regs (
    input  wire        clk,
    input  wire        rst,

    // AXI4-Lite write address channel.
    input  wire [ 7:0] s_axi_awaddr,
    input  wire        s_axi_awvalid,
    output wire        s_axi_awready,

    // AXI4-Lite write data channel.
    input  wire [31:0] s_axi_wdata,
    input  wire [ 3:0] s_axi_wstrb,
    input  wire        s_axi_wvalid,
    output wire        s_axi_wready,

    // AXI4-Lite write response channel.
    output wire [ 1:0] s_axi_bresp,
    output reg         s_axi_bvalid,
    input  wire        s_axi_bready,

    // AXI4-Lite read address channel.
    input  wire [ 7:0] s_axi_araddr,
    input  wire        s_axi_arvalid,
    output wire        s_axi_arready,

    // AXI4-Lite read data channel.
    output reg  [31:0] s_axi_rdata,
    output wire [ 1:0] s_axi_rresp,
    output reg         s_axi_rvalid,
    input  wire        s_axi_rready,

    // Decoded register interface.
    output wire        wr_en,
    output wire [ 7:0] wr_addr,
    output wire [31:0] wr_data,
    output wire [ 3:0] wr_strb,
    output wire        rd_en,
    output wire [ 7:0] rd_addr,
    input  wire [31:0] rd_data
);

    reg        aw_full, w_full;
    reg [ 7:0] aw_hold;
    reg [31:0] w_hold;
    reg [ 3:0] strb_hold;

    assign s_axi_awready = !aw_full && !s_axi_bvalid;
    assign s_axi_wready  = !w_full && !s_axi_bvalid;
    assign s_axi_bresp   = 2'b00;
    assign s_axi_rresp   = 2'b00;
    assign s_axi_arready = !s_axi_rvalid;
    assign wr_en         = aw_full && w_full && !s_axi_bvalid;
    assign wr_addr       = aw_hold;
    assign wr_data       = w_hold;
    assign wr_strb       = strb_hold;
    assign rd_en         = s_axi_arvalid && s_axi_arready;
    assign rd_addr       = s_axi_araddr;

    always @(posedge clk) begin
        if (rst) begin
            aw_full      <= 0;
            w_full       <= 0;
            s_axi_bvalid <= 0;
            s_axi_rvalid <= 0;
            aw_hold      <= 0;
            w_hold       <= 0;
            strb_hold    <= 0;
            s_axi_rdata  <= 0;
        end else begin
            if (s_axi_awvalid && s_axi_awready) begin
                aw_hold <= s_axi_awaddr;
                aw_full <= 1;
            end
            if (s_axi_wvalid && s_axi_wready) begin
                w_hold    <= s_axi_wdata;
                strb_hold <= s_axi_wstrb;
                w_full    <= 1;
            end
            if (wr_en) begin
                aw_full      <= 0;
                w_full       <= 0;
                s_axi_bvalid <= 1;
            end else if (s_axi_bvalid && s_axi_bready) s_axi_bvalid <= 0;
            if (rd_en) begin
                s_axi_rdata  <= rd_data;
                s_axi_rvalid <= 1;
            end else if (s_axi_rvalid && s_axi_rready) s_axi_rvalid <= 0;
        end
    end

endmodule
