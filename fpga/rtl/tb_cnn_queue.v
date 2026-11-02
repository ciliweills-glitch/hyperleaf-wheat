// tb_cnn_queue.v - Pipelined queue test: issue LOAD_W(i+1) while RUN(i) is executing.
//
// Same vectors/expected values as tb_cnn_axis_accel; only issue timing differs.
`timescale 1ns / 1ps

module tb_cnn_queue;

    reg         clk = 0, rstn = 0;
    always #5 clk = ~clk;
    reg  [ 7:0] awaddr = 0, araddr = 0;
    reg         awvalid = 0, wvalid = 0, bready = 0, arvalid = 0, rready = 0;
    reg  [31:0] wdata = 0;
    reg  [ 3:0] wstrb = 15;
    wire        awready, wready, bvalid, arready, rvalid;
    wire [31:0] rdata;
    wire [ 1:0] bresp, rresp;
    reg  [31:0] sd = 0;
    reg         sv = 0, sl = 0, mr = 0;
    wire        sr, mv, ml;
    wire [31:0] md;
    wire [ 3:0] mk;

    cnn_axis_accel dut (
        .aclk (clk), .aresetn(rstn),
        .s_axi_awaddr (awaddr),  .s_axi_awvalid(awvalid), .s_axi_awready(awready),
        .s_axi_wdata  (wdata),   .s_axi_wstrb  (wstrb),   .s_axi_wvalid (wvalid),
        .s_axi_wready (wready),
        .s_axi_bresp  (bresp),   .s_axi_bvalid (bvalid),  .s_axi_bready (bready),
        .s_axi_araddr (araddr),  .s_axi_arvalid(arvalid), .s_axi_arready(arready),
        .s_axi_rdata  (rdata),   .s_axi_rresp  (rresp),   .s_axi_rvalid (rvalid),
        .s_axi_rready (rready),
        .s_axis_tdata (sd),      .s_axis_tkeep (4'hf),    .s_axis_tvalid(sv),
        .s_axis_tready(sr),      .s_axis_tlast (sl),
        .m_axis_tdata (md),      .m_axis_tkeep (mk),      .m_axis_tvalid(mv),
        .m_axis_tready(mr),      .m_axis_tlast (ml)
    );

    integer writes = 0;

    task automatic wr(input [7:0] addr, input [31:0] data, input [3:0] strb = 15);
        integer delay_aw, delay_w;
        begin
            delay_aw = writes % 3;
            delay_w  = (writes + 1) % 3;
            writes   = writes + 1;
            fork
                begin
                    repeat (delay_aw + 1) @(negedge clk);
                    awaddr = addr;
                    awvalid = 1;
                    do @(posedge clk); while (!awready);
                    @(negedge clk);
                    awvalid = 0;
                end
                begin
                    repeat (delay_w + 1) @(negedge clk);
                    wdata = data;
                    wstrb = strb;
                    wvalid = 1;
                    do @(posedge clk); while (!wready);
                    @(negedge clk);
                    wvalid = 0;
                end
            join
            wait (bvalid);
            repeat (3) begin
                @(negedge clk);
                if (!bvalid || bresp != 0) $fatal(1, "B response not held");
            end
            bready = 1;
            @(negedge clk);
            bready = 0;
        end
    endtask

    task automatic rd(input [7:0] addr, output [31:0] data);
        begin
            @(negedge clk);
            araddr = addr;
            arvalid = 1;
            do @(posedge clk); while (!arready);
            @(negedge clk);
            arvalid = 0;
            wait (rvalid);
            data = rdata;
            repeat (3) begin
                @(negedge clk);
                if (!rvalid || rdata !== data || rresp != 0) $fatal(1, "R response not held");
            end
            rready = 1;
            @(negedge clk);
            rready = 0;
        end
    endtask

    reg        stalled = 0;
    reg [36:0] held;

    always @(posedge clk) begin
        if (rstn) begin
            if (stalled && (!mv || {mk, ml, md} !== held)) $fatal(1, "AXIS changed while stalled");
            stalled <= mv && !mr;
            held    <= {mk, ml, md};
            if (mv && ml && !mr && dut.done) $fatal(1, "ap_done before final output handshake");
        end else stalled <= 0;
    end

    reg  [31:0]  payload [0:22000];
    reg  [31:0]  wpay [0:22000];
    reg  [127:0] mac_expected [0:127];
    reg  [2047:0] path, mac_path;
    integer      fd, mac_fd, scan, cmd, nwords, dims, groups, pool, nout, tag;
    integer      ncmd, nnwords, ndims, ngroups, npool, nnout, ntag;
    integer      i, count = 0, total_out = 0, commands = 0;
    integer      mac_points = 0, mac_index;
    reg  [31:0]  value;

    always @(posedge clk) begin
        if (rstn && dut.core.state == 8 && dut.core.yv0 && dut.core.yv1) begin
            mac_index = dut.core.py * (dims & 65535) + dut.core.px;
            if ({dut.core.acc, dut.core.y1, dut.core.y0} !== mac_expected[mac_index])
                $fatal(1, "prepool tile=%0d pixel=%0d acc/y=%h expected=%h", tag, mac_index,
                       {dut.core.acc, dut.core.y1, dut.core.y0}, mac_expected[mac_index]);
            mac_points = mac_points + 1;
        end
    end

    task automatic stream_words(input integer n, ref [31:0] words_ [0:22000]);
        integer j;
        begin
            for (j = 0; j < n; j = j + 1) begin
                @(negedge clk);
                if (j % 17 == 3) begin
                    sv = 0;
                    repeat (2) @(negedge clk);
                end
                sd = words_[j];
                sl = (j == n - 1);
                sv = 1;
                do @(posedge clk); while (!sr);
            end
            @(negedge clk);
            sv = 0;
            sl = 0;
        end
    endtask

    task automatic collect_outputs(input integer n);
        integer j;
        begin
            for (j = 0; j < n; j = j + 1) begin
                mr = 0;
                wait (mv);
                repeat ((j % 5) + 2) @(negedge clk);
                if (md !== payload[j] || mk != 15 || ml !== (j == n - 1))
                    $fatal(1, "tile=%0d word=%0d got=%h expected=%h last=%b", tag, j, md, payload[j], ml);
                mr = 1;
                @(posedge clk);
                @(negedge clk);
                mr = 0;
            end
        end
    endtask

    task automatic blocking_load(input integer c, input integer n);
        begin
            wr(8'h10, n);
            wr(8'h14, c);
            wr(8'h00, 1);
            stream_words(n, payload);
            wait (dut.done);
            @(negedge clk);
        end
    endtask

    initial begin
        repeat (5) @(negedge clk);
        rstn = 1;
        rd(8'h28, value);
        if (value !== 32'h00010001) $fatal(1, "version");
        if (!$value$plusargs("VECTORS=%s", path)) path = "vectors/overlay/00001_smoke.txt";
        if (!$value$plusargs("MACFILE=%s", mac_path)) mac_path = "vectors/overlay/00001_smoke_mac.txt";
        fd = $fopen(path, "r");
        if (!fd) $fatal(1, "Cannot open vectors %0s", path);
        mac_fd = $fopen(mac_path, "r");
        if (!mac_fd) $fatal(1, "Cannot open MAC vectors %0s", mac_path);
        while (1) begin
            scan = $fscanf(fd, "%h %h %h %h %h %h %h\n", cmd, nwords, dims, groups, pool, nout, tag);
            if (scan != 7) $fatal(1, "truncated command stream");
            if (cmd == 0) begin
                $display("PASS queue commands=%0d tiles=%0d output_words=%0d prepool_values=%0d; W-issued-during-RUN exact",
                         commands, count, total_out, mac_points * 2);
                $finish;
            end
            for (i = 0; i < nwords + nout; i = i + 1) begin
                scan = $fscanf(fd, "%h\n", payload[i]);
                if (scan != 1) $fatal(1, "truncated payload");
            end
            if (cmd == 1) begin
                // Tile boundary: config regs are written only while idle.
                wr(8'h18, dims);
                wr(8'h1c, groups);
                wr(8'h20, pool);
                blocking_load(cmd, nwords);
            end else if (cmd == 2) begin
                // A LOAD_W the lookahead did not consume (first pair of a tile).
                blocking_load(cmd, nwords);
            end else begin
                for (i = 0; i < (dims >> 16) * (dims & 65535); i = i + 1) begin
                    scan = $fscanf(mac_fd, "%h\n", mac_expected[i]);
                    if (scan != 1) $fatal(1, "truncated MAC payload");
                end
                // Peek the next record: if it is this tile's next LOAD_W, queue it mid-RUN.
                ncmd = -1;
                scan = $fscanf(fd, "%h %h %h %h %h %h %h\n", ncmd, nnwords, ndims, ngroups, npool, nnout, ntag);
                if (scan == 7 && ncmd == 2) begin
                    for (i = 0; i < nnwords + nnout; i = i + 1) begin
                        scan = $fscanf(fd, "%h\n", wpay[i]);
                        if (scan != 1) $fatal(1, "truncated queued W payload");
                    end
                    wr(8'h10, 0);    wr(8'h14, 3); wr(8'h00, 1);      // RUN starts (idle)
                    wr(8'h10, nnwords); wr(8'h14, 2); wr(8'h00, 1);   // queued while busy
                    fork
                        stream_words(nnwords, wpay);
                        collect_outputs(nout);
                    join
                    repeat (4) @(negedge clk);
                    rd(8'h00, value);
                    if ((value & 14) != 14) $fatal(1, "completion CTRL=%h tag=%0d", value, tag);
                    rd(8'h00, value);
                    if (value[1]) $fatal(1, "ap_done not clear-on-read");
                    rd(8'h24, value);
                    if (value != 0) $fatal(1, "framing status=%h", value);
                end else begin
                    wr(8'h10, 0);
                    wr(8'h14, 3);
                    wr(8'h00, 1);
                    collect_outputs(nout);
                    rd(8'h00, value);
                    if ((value & 14) != 14) $fatal(1, "completion CTRL=%h tag=%0d", value, tag);
                    rd(8'h00, value);
                    if (value[1]) $fatal(1, "ap_done not clear-on-read");
                    rd(8'h24, value);
                    if (value != 0) $fatal(1, "framing status=%h", value);
                    if (scan == 7) begin
                        if (ncmd == 0) begin
                            $display("PASS queue commands=%0d tiles=%0d output_words=%0d prepool_values=%0d; W-issued-during-RUN exact",
                                     commands + 1, count, total_out, mac_points * 2);
                            $finish;
                        end
                        cmd    = ncmd;
                        nwords = nnwords;
                        dims   = ndims;
                        groups = ngroups;
                        pool   = npool;
                        nout   = nnout;
                        tag    = ntag;
                        for (i = 0; i < nwords + nout; i = i + 1) begin
                            scan = $fscanf(fd, "%h\n", payload[i]);
                            if (scan != 1) $fatal(1, "truncated payload");
                        end
                        if (cmd == 1) begin
                            wr(8'h18, dims);
                            wr(8'h1c, groups);
                            wr(8'h20, pool);
                            blocking_load(cmd, nwords);
                        end else if (cmd == 2) begin
                            blocking_load(cmd, nwords);
                        end else $fatal(1, "unexpected cmd after RUN: %0d", cmd);
                    end
                end
                total_out = total_out + nout;
                count     = count + 1;
            end
            commands = commands + 1;
        end
    end

    initial begin
        #2000000000;
        $fatal(1, "timeout cmd=%0d tag=%0d state=%0d output=%0d", cmd, tag, dut.core.state, total_out);
    end

endmodule
