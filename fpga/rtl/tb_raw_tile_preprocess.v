// tb_raw_tile_preprocess.v - raw_tile_preprocess adapter TB: mask prefix, stalls, framing, reset.
`timescale 1ns / 1ps

module tb_raw_tile_preprocess;

    reg clk = 0, rst = 1, command_start = 0, raw_start = 0;
    always #5 clk = ~clk;

    reg [31:0] words = 0, s_data = 0;
    reg [ 7:0] spatial_words = 0;
    reg [ 2:0] mask_words = 0;
    reg [ 3:0] s_keep = 15;
    reg s_valid = 0, s_last = 0, m_ready = 0;
    wire s_ready, m_valid, m_last, frame_error;
    wire [31:0] m_data;
    wire [ 3:0] m_keep;

    raw_tile_preprocess #(.ROM_PREFIX("preprocess_rom/")) dut (.*);

    reg [31:0] input_words [0:21018], expected [0:21011];
    integer fd, scan, spatial, prefix, n, bad, i, received = 0, cases = 0, total = 0, cycles = 0;
    reg held_valid = 0;
    reg [36:0] held_output;

    always @(posedge clk) begin
        cycles <= cycles + 1;
        if (rst) held_valid <= 0;
        else begin
            if (held_valid && (!m_valid || {m_keep, m_last, m_data} !== held_output))
                $fatal(1, "adapter changed output under backpressure");
            held_valid <= m_valid && !m_ready;
            held_output <= {m_keep, m_last, m_data};
            if (m_valid && m_ready) begin
                if (m_data !== expected[received] || m_keep !== 15 || m_last !== (received == 103 * spatial - 1))
                    $fatal(1, "adapter case=%0d word=%0d got=%h expected=%h last=%b", cases, received, m_data, expected[received], m_last);
                received = received + 1;
                total = total + 1;
            end
        end
    end

    initial begin
        repeat (5) @(negedge clk);
        rst = 0;
        fd = $fopen("vectors/overlay_raw/adapter.txt", "r");
        if (!fd) $fatal(1, "missing adapter vectors");
        while (1) begin
            scan = $fscanf(fd, "%h %h %h %h\n", spatial, prefix, n, bad);
            if (scan != 4) $fatal(1, "truncated adapter header");
            if (spatial == 0) begin
                $display("TEST_PASS adapter cases=%0d normalized_words=%0d cycles=%0d; mask 1..7 words, unsigned input, rounding exceptions, tail, stalls, framing, reset", cases, total, cycles);
                $finish;
            end
            for (i = 0; i < n; i = i + 1) begin
                scan = $fscanf(fd, "%h\n", input_words[i]);
                if (scan != 1) $fatal(1, "truncated input");
            end
            for (i = 0; i < 103 * spatial; i = i + 1) begin
                scan = $fscanf(fd, "%h\n", expected[i]);
                if (scan != 1) $fatal(1, "truncated oracle");
            end
            if (cases == 0) begin
                // Flush a partly consumed packet and pipeline; no stale outputs may escape.
                @(negedge clk);
                words = n;
                spatial_words = spatial;
                mask_words = prefix;
                command_start = 1;
                raw_start = 1;
                @(negedge clk);
                command_start = 0;
                raw_start = 0;
                for (i = 0; i < 3; i = i + 1) begin
                    s_data = input_words[i];
                    s_valid = 1;
                    do @(posedge clk); while (!s_ready);
                    @(negedge clk);
                end
                rst = 1;
                s_valid = 0;
                repeat (3) @(negedge clk);
                rst = 0;
                repeat (12) begin
                    @(negedge clk);
                    if (m_valid) $fatal(1, "stale output after reset");
                end
            end
            received = 0;
            @(negedge clk);
            words = n;
            spatial_words = spatial;
            mask_words = prefix;
            command_start = 1;
            raw_start = 1;
            @(negedge clk);
            command_start = 0;
            raw_start = 0;
            if (frame_error) $fatal(1, "frame error not cleared by command start");
            // Changing config after start must not alter the active packet geometry.
            spatial_words = 1;
            mask_words = 1;
            words = 1;
            fork
                begin : source
                    integer j;
                    for (j = 0; j < n; j = j + 1) begin
                        @(negedge clk);
                        if (j % 13 == 4) begin
                            s_valid = 0;
                            repeat (3) @(negedge clk);
                        end
                        s_valid = 1;
                        s_data = input_words[j];
                        s_last = (j == n - 1) || (bad && j == prefix + 5);
                        s_keep = (bad && j == 0) ? 4'h1 : 4'hf;
                        do @(posedge clk); while (!s_ready);
                    end
                    @(negedge clk);
                    s_valid = 0;
                    s_last = 0;
                    s_keep = 15;
                end
                begin : sink
                    integer j = 0;
                    while (received < 103 * spatial) begin
                        @(negedge clk);
                        m_ready = (j % 19 < 12);
                        j = j + 1;
                        if (m_valid && m_last && j % 19 == 12) repeat (9) @(negedge clk);
                    end
                    @(negedge clk);
                    m_ready = 0;
                end
            join
            if (frame_error !== bad[0]) $fatal(1, "frame error got=%b expected=%b", frame_error, bad[0]);
            cases = cases + 1;
        end
    end

    initial begin
        #10000000;
        $fatal(1, "adapter timeout case=%0d count=%0d", cases, received);
    end

endmodule
