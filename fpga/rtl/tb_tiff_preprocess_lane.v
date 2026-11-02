// tb_tiff_preprocess_lane.v - Exhaustive per-band, mask, background and mixed-stream lane TB.
`timescale 1ns / 1ps

module tb_tiff_preprocess_lane;

    reg clk = 0;
    always #5 clk = ~clk;

    reg rst = 1, s_valid = 0, foreground = 1, s_last = 0;
    reg [15:0] pixel = 0;
    reg [ 7:0] band = 0;
    wire s_ready, m_valid, m_last;
    reg  m_ready = 0;
    wire signed [15:0] quantized;
    wire [7:0]         m_band;

    tiff_preprocess_lane dut (.*);

    reg [15:0] expected [0:65535];
    reg [41:0] mixed [0:65535];
    integer current_band = 0, sent = 0, received = 0, cycles = 0, total_received = 0;
    integer b, fd, count, scanned, last_mixed_cycle = 0;
    reg checking = 0, stalled = 0, input_stalled = 0, continuous = 0;
    reg [24:0] held;
    string     expected_file;

    always @(negedge clk) m_ready = !rst && (continuous || cycles % 17 < 13);

    always @(posedge clk) begin
        cycles = cycles + 1;
        if (!rst) begin
            if (stalled && (!m_valid || {m_band, m_last, quantized} !== held))
                $fatal(1, "Output changed under backpressure cycle=%0d", cycles);
            stalled = m_valid && !m_ready;
            held = {m_band, m_last, quantized};
            if (checking && m_valid && m_ready) begin
                if (current_band == -1) begin
                    if (received > 0 && cycles != last_mixed_cycle + 1)
                        $fatal(1, "Mixed continuous stream has output bubble");
                    last_mixed_cycle = cycles;
                end
                if (quantized !== expected[received] ||
                    m_band !== (current_band == -1 ? mixed[received][23:16] : current_band) ||
                    m_last !== (current_band == -1 ? mixed[received][24] : (received == 65535)))
                    $fatal(1, "band=%0d pixel=%0d got=%0d exp=%0d tag=%0d last=%b", current_band,
                           received, quantized, $signed(expected[received]), m_band, m_last);
                received = received + 1;
                total_received = total_received + 1;
            end
        end else stalled = 0;
    end

    initial begin
        repeat (5) @(negedge clk);
        rst = 0;
        for (b = 0; b < 204; b = b + 1) begin
            expected_file = $sformatf("vectors/preprocess_fixed/expected_%03d.hex", b);
            $readmemh(expected_file, expected);
            current_band = b;
            band = b;
            sent = 0;
            received = 0;
            checking = 1;
            input_stalled = 0;
            while (sent < 65536) begin
                @(negedge clk);
                if (!input_stalled) begin
                    s_valid = (cycles % 29 != 0);
                    pixel = sent;
                    s_last = (sent == 65535);
                    foreground = 1;
                end
                @(posedge clk);
                input_stalled = s_valid && !s_ready;
                if (s_valid && s_ready) sent = sent + 1;
            end
            @(negedge clk);
            s_valid = 0;
            wait (received == 65536);
            @(negedge clk);
            if (b % 16 == 0 || b == 203) $display("EXHAUSTIVE band=%0d words=%0d", b, total_received);
        end
        // 再扫描 band 204：交替前景/背景，保证 mask 量化不是 raw 0/1。
        current_band = 204;
        band = 204;
        sent = 0;
        received = 0;
        input_stalled = 0;
        for (b = 0; b < 65536; b = b + 1) expected[b] = (b % 2 == 0) ? 16'd2709 : 16'd0;
        while (sent < 65536) begin
            @(negedge clk);
            if (!input_stalled) begin
                s_valid = 1;
                pixel = sent;
                foreground = (sent % 2 == 0);
                s_last = (sent == 65535);
            end
            @(posedge clk);
            input_stalled = s_valid && !s_ready;
            if (s_valid && s_ready) sent = sent + 1;
        end
        @(negedge clk);
        s_valid = 0;
        wait (received == 65536);
        @(negedge clk);
        // Background override must suppress even nonzero/65535 spectral inputs.
        current_band = 0;
        band = 0;
        sent = 0;
        received = 0;
        input_stalled = 0;
        for (b = 0; b < 65536; b = b + 1) expected[b] = 0;
        while (sent < 65536) begin
            @(negedge clk);
            if (!input_stalled) begin
                s_valid = 1;
                pixel = sent;
                foreground = 0;
                s_last = (sent == 65535);
            end
            @(posedge clk);
            input_stalled = s_valid && !s_ready;
            if (s_valid && s_ready) sent = sent + 1;
        end
        @(negedge clk);
        s_valid = 0;
        wait (received == 65536);
        @(negedge clk);
        fd = $fopen("vectors/preprocess_fixed/mixed.txt", "r");
        scanned = $fscanf(fd, "%d\n", count);
        if (scanned != 1 || count > 65536) $fatal(1, "Mixed stimulus count");
        for (b = 0; b < count; b = b + 1) begin
            scanned = $fscanf(fd, "%h\n", mixed[b]);
            if (scanned != 1) $fatal(1, "Mixed stimulus word %0d", b);
            expected[b] = mixed[b][41:26];
        end
        $fclose(fd);
        current_band = -1;
        sent = 0;
        received = 0;
        input_stalled = 0;
        continuous = 1;
        while (sent < count) begin
            @(negedge clk);
            if (!input_stalled) begin
                s_valid = 1;
                pixel = mixed[sent][15:0];
                band = mixed[sent][23:16];
                s_last = mixed[sent][24];
                foreground = mixed[sent][25];
            end
            @(posedge clk);
            input_stalled = s_valid && !s_ready;
            if (s_valid && s_ready) sent = sent + 1;
        end
        @(negedge clk);
        s_valid = 0;
        wait (received == count);
        @(negedge clk);
        checking = 0;
        repeat (10) @(negedge clk);
        if (m_valid) $fatal(1, "Unexpected extra output");
        $display("TEST_PASS tiff_preprocess_lane spectral=%0d mask=%0d background=%0d mixed=%0d total=%0d cycles=%0d",
                 204 * 65536, 65536, 65536, count, total_received, cycles);
        $finish;
    end

    initial begin
        #300000000;
        $fatal(1, "Watchdog band=%0d sent=%0d received=%0d", current_band, sent, received);
    end

endmodule
