// tb_standardize_mac.v - Directed standardize_mac: ties, saturation, sign extension, correction, mask, reset.
`timescale 1ns / 1ps

module tb_standardize_mac;

    reg clk = 0;
    always #5 clk = ~clk;

    reg rst = 1, s_valid = 0, m_ready = 1;
    reg [15:0] pixel = 0;
    reg signed [29:0] multiplier_q28 = 0;
    reg signed [47:0] offset_q28 = 0;
    reg signed [ 1:0] correction = 0;
    reg foreground = 1, is_mask = 0, s_last = 0;
    reg [7:0] band = 0;

    wire s_ready, m_valid, m_last;
    wire signed [15:0] quantized;
    wire [7:0]         m_band;

    standardize_mac dut (.*);

    reg signed [15:0] oracle [0:4095];
    integer sent = 0, received = 0, floor, distance, delta;
    integer floors [0:11];
    reg signed [63:0] total;
    integer rounded, clipped, expected;

    task send(input [15:0] x, input signed [29:0] a, input signed [47:0] bias,
              input signed [1:0] corr, input fg, input mask, input integer exp);
        begin
            @(negedge clk);
            s_valid = 1;
            pixel = x;
            multiplier_q28 = a;
            offset_q28 = bias;
            correction = corr;
            foreground = fg;
            is_mask = mask;
            band = sent % 205;
            s_last = sent % 3 == 0;
            oracle[sent] = exp;
            @(posedge clk);
            while (!s_ready) @(posedge clk);
            sent = sent + 1;
        end
    endtask

    always @(posedge clk) if (!rst && m_valid && m_ready) begin
        if (quantized !== oracle[received] || m_band !== received % 205 || m_last !== (received % 3 == 0))
            $fatal(1, "directed word=%0d got=%0d expected=%0d band=%0d last=%b", received,
                   quantized, oracle[received], m_band, m_last);
        received = received + 1;
    end

    initial begin
        floors[0] = -32769; floors[1] = -32768; floors[2] = -32767;
        floors[3] = -3; floors[4] = -2; floors[5] = -1; floors[6] = 0;
        floors[7] = 1; floors[8] = 2; floors[9] = 32766; floors[10] = 32767; floors[11] = 32768;
        repeat (5) @(negedge clk);
        rst = 0;
        for (floor = 0; floor < 12; floor = floor + 1) begin
            for (distance = -1; distance <= 1; distance = distance + 1) begin
                total = $signed(floors[floor]);
                total = (total <<< 28) + 64'sd134217728 + distance;
                rounded = floors[floor] + (distance > 0 || (distance == 0 && (floors[floor] & 1)));
                clipped = rounded < -32767 ? -32767 : rounded > 32767 ? 32767 : rounded;
                send(0, 0, total, 0, 1, 0, clipped);
            end
        end
        // Full unsigned pixel and coefficient top-bit/sign-extension corner.
        send(65535, 30'sd536870911, 48'sd0, 0, 1, 0, 32767);
        send(65535, -30'sd536870912, 48'sd0, 0, 1, 0, -32767);
        send(1, 30'sd268435456, -48'sd536870912, 0, 1, 0, -1);
        send(1, 30'sd268435456, 48'sd0, -1, 1, 0, 0);
        send(1, 30'sd268435456, 48'sd0, 1, 1, 0, 2);
        send(65535, 30'sd536870911, 48'sd0, 0, 0, 0, 0);
        send(65535, 30'sd536870911, 48'sd0, 0, 1, 1, 2709);
        send(65535, 30'sd536870911, 48'sd0, 0, 0, 1, 0);
        @(negedge clk);
        s_valid = 0;
        wait (received == sent);
        repeat (5) @(negedge clk);
        // Reset discards an in-flight transaction rather than emitting stale valid.
        send(1, 30'sd268435456, 0, 0, 1, 0, 1);
        @(negedge clk);
        rst = 1;
        s_valid = 0;
        repeat (2) @(negedge clk);
        rst = 0;
        sent = received;
        repeat (5) @(negedge clk);
        if (m_valid) $fatal(1, "Reset left stale valid");
        $display("TEST_PASS standardize_mac directed=%0d negative/positive ties half+/-1 saturation signed multiply correction mask reset", received);
        $finish;
    end

    initial begin
        #20000;
        $fatal(1, "Watchdog");
    end

endmodule
