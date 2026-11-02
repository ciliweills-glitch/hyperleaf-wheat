// tb_cnn_axis_accel.v - Real Conv1-4 transactions; independent AXI-Lite channels and AXIS stalls.
`timescale 1ns / 1ps

module tb_cnn_axis_accel;

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

    cnn_axis_accel #(.ROM_PREFIX("preprocess_rom/")) dut (
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

    time run_started;
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

    reg  [31:0]  payload [0:163839];
    reg  [127:0] mac_expected [0:16383];
    reg  [31:0]  normalized_expected [0:21011];
    reg  [2047:0] raw_path;
    integer      raw_fd = 0, normalized_count = 0, normalized_total = 0;
    reg  [2047:0] path, mac_path, out_path;
    integer      out_fd = 0;
    integer      fd, mac_fd, scan, cmd, nwords, dims, groups, pool, nout, tag;
    integer      i, k, kk, count = 0, total_out = 0, commands = 0, stallcycles;
    integer      mac_points = 0, mac_index;
    reg  [31:0]  value;

    always @(posedge clk) begin
        if (rstn && dut.raw_active && dut.core.state == 1 && dut.core.input_fire) begin
            if (dut.core.s_axis_tdata !== normalized_expected[normalized_count])
                $fatal(1, "normalized tile=%0d word=%0d got=%h expected=%h", tag, normalized_count,
                       dut.core.s_axis_tdata, normalized_expected[normalized_count]);
            normalized_count = normalized_count + 1;
            normalized_total = normalized_total + 1;
        end
        if (rstn && dut.core.state == 8 && dut.core.yv0 && dut.core.yv1) begin
            mac_index = (dut.core.pair_idx * (dims >> 16) + dut.core.py) * (dims & 65535) + dut.core.px;
            if ({dut.core.acc, dut.core.y1, dut.core.y0} !== mac_expected[mac_index])
                $fatal(1, "prepool tile=%0d pixel=%0d acc/y=%h expected=%h", tag, mac_index,
                       {dut.core.acc, dut.core.y1, dut.core.y0}, mac_expected[mac_index]);
            mac_points = mac_points + 1;
        end
    end

    initial begin
        repeat (5) @(negedge clk);
        rstn = 1;
        rd(8'h28, value);
        if (value !== 32'h00010004) $fatal(1, "version");
        wr(8'h10, 32'h12345678);
        wr(8'h10, 32'habcd0000, 4'hc);
        rd(8'h10, value);
        if (value !== 32'habcd5678) $fatal(1, "WSTRB");
        begin : read_cfg
            integer cfg;
            cfg = $fopen("vectors/run.cfg", "r");
            if (cfg) begin
                scan = $fscanf(cfg, "%s\n", path);
                scan = $fscanf(cfg, "%s\n", mac_path);
                scan = $fscanf(cfg, "%s\n", out_path);
                $fclose(cfg);
                out_fd = $fopen(out_path, "w");
            end else begin
                path     = "vectors/overlay/00001_smoke.txt";
                mac_path = "vectors/overlay/00001_smoke_mac.txt";
            end
        end
        scan = $value$plusargs("VECTORS=%s", path);
        scan = $value$plusargs("MACFILE=%s", mac_path);
        begin
            raw_path = "normalized.txt";
            raw_fd = $fopen(raw_path, "r");
            if (!raw_fd) $fatal(1, "Cannot open normalized oracle");
        end
        fd = $fopen(path, "r");
        if (!fd) $fatal(1, "Cannot open vectors %0s", path);
        mac_fd = $fopen(mac_path, "r");
        if (!mac_fd) $fatal(1, "Cannot open MAC vectors %0s", mac_path);
        while (1) begin
            scan = $fscanf(fd, "%h %h %h %h %h %h %h\n", cmd, nwords, dims, groups, pool, nout, tag);
            if (scan != 7) $fatal(1, "truncated command stream");
            if (cmd == 0) begin
                $display("TEST_PASS overlay commands=%0d tiles=%0d output_words=%0d prepool_values=%0d normalized_words=%0d; exact acc/F48, AW/W skew, B/R stalls, WSTRB, input gaps, output backpressure, TLAST",
                         commands, count, total_out, mac_points * 2, normalized_total);
                if (out_fd) $fclose(out_fd);
                $finish;
            end
            if (cmd == 4) begin
                if (!raw_fd) $fatal(1, "RAWFILE required for command 4");
                normalized_count = 0;
                for (i = 0; i < groups * ((dims >> 16) + 2) * ((dims & 65535) + 2); i = i + 1) begin
                    scan = $fscanf(raw_fd, "%h\n", normalized_expected[i]);
                    if (scan != 1) $fatal(1, "Truncated normalized oracle");
                end
            end
            for (i = 0; i < nwords + nout; i = i + 1) begin
                scan = $fscanf(fd, "%h\n", payload[i]);
                if (scan != 1) $fatal(1, "truncated payload");
            end
            if (cmd == 3) begin
                kk = (pool >> 20) & 12'hfff;
                for (i = 0; i < kk * (dims >> 16) * (dims & 65535); i = i + 1) begin
                    scan = $fscanf(mac_fd, "%h\n", mac_expected[i]);
                    if (scan != 1) $fatal(1, "truncated MAC payload");
                end
            end
            wr(8'h18, dims);
            wr(8'h1c, groups);
            wr(8'h20, pool);
            wr(8'h10, nwords);
            wr(8'h14, cmd);
            wr(8'h00, 1);
            if (cmd == 3) run_started = $time;
            if (cmd != 3) begin
                for (i = 0; i < nwords; i = i + 1) begin
                    @(negedge clk);
                    if (i % 17 == 3) begin
                        sv = 0;
                        repeat (2) @(negedge clk);
                    end
                    sd = payload[i];
                    sl = (i == nwords - 1);
                    sv = 1;
                    do @(posedge clk); while (!sr);
                end
                @(negedge clk);
                sv = 0;
                sl = 0;
                wait (dut.done);
                @(negedge clk);
                if (cmd == 4 && normalized_count != groups * ((dims >> 16) + 2) * ((dims & 65535) + 2))
                    $fatal(1, "Normalized word count %0d", normalized_count);
            end else begin
                // Batched RUN: nwords weight words stream in while nout results stream out.
                fork
                    begin : stream_blk
                        integer jj;
                        for (jj = 0; jj < nwords; jj = jj + 1) begin
                            @(negedge clk);
                            if (jj % 17 == 3) begin
                                sv = 0;
                                repeat (2) @(negedge clk);
                            end
                            sd = payload[jj];
                            sl = (jj == nwords - 1);
                            sv = 1;
                            do @(posedge clk); while (!sr);
                        end
                        @(negedge clk);
                        sv = 0;
                        sl = 0;
                    end
                    begin : collect_blk
                        integer jj;
                        for (jj = 0; jj < nout; jj = jj + 1) begin
                            mr = 0;
                            wait (mv);
                            repeat ((jj % 5) + 2) @(negedge clk);
                            if (md !== payload[nwords + jj] || mk != 15 || ml !== (jj == nout - 1))
                                $fatal(1, "tile=%0d word=%0d got=%h expected=%h last=%b px=%0d py=%0d acc=%h",
                                       tag, jj, md, payload[nwords + jj], ml, dut.core.px, dut.core.py, dut.core.acc);
                            if (out_fd) $fwrite(out_fd, "%08h\n", md);
                            mr = 1;
                            @(posedge clk);
                            @(negedge clk);
                            mr = 0;
                        end
                    end
                join
                $display("RUN_PERF tag=%0d elapsed_ns=%0d", tag, $time-run_started);
                total_out = total_out + nout;
                count     = count + 1;
                if (count % 32 == 0) $display("progress tiles=%0d output_words=%0d tag=%0d", count, total_out, tag);
            end
            rd(8'h00, value);
            if ((value & 14) != 14) $fatal(1, "completion CTRL=%h tag=%0d", value, tag);
            rd(8'h00, value);
            if (value[1]) $fatal(1, "ap_done not clear-on-read");
            rd(8'h24, value);
            if (value != 0) $fatal(1, "framing status=%h", value);
            commands = commands + 1;
        end
    end

    initial begin
        #2000000000;
        $fatal(1, "timeout cmd=%0d tag=%0d state=%0d output=%0d", cmd, tag, dut.core.state, total_out);
    end

endmodule
