// tb_requant_pipe.v - 256 full 112-bit products: ties-even, negative ReLU, saturation, 48-bit extremes, back-to-back coefficients.
`timescale 1ns / 1ps

module tb_requant_pipe;

    reg clk = 0, rst = 1, iv = 0;
    always #5 clk = ~clk;

    reg [47:0] a = 0;
    reg [63:0] sc = 0, b = 0;

`ifdef INT8
    wire [ 7:0] y;
`else
    wire [15:0] y;
`endif
    wire        ov;

    reg  [191:0] vec [0:255];
    integer      sent = 0, received = 0, products = 0;
    reg signed [111:0] product_expected;
    reg [15:0]         y_expected;

`ifdef INT8
    requant_pipe_int8 dut (
`else
    requant_pipe dut (
`endif
        .clk      (clk),
        .rst      (rst),
        .in_valid (iv),
        .acc      (a),
        .scale_q  (sc),
        .bias_q   (b),
        .y        (y),
        .out_valid(ov)
    );

    always @(posedge clk) if (!rst && dut.valid[6]) begin
        product_expected = $signed(vec[products][191:144]) * $signed(vec[products][143:80]);
        if (dut.product !== product_expected)
            $fatal(1, "product case=%0d got=%h expected=%h", products, dut.product, product_expected);
        products = products + 1;
    end

    always @(posedge clk) if (!rst && ov) begin
        y_expected = vec[received][15:0];
`ifdef INT8
        if (y_expected > 127) y_expected = 127;
`endif
        if (y !== y_expected)
            $fatal(1, "case=%0d y=%0d expected=%0d", received, y, vec[received][15:0]);
        received = received + 1;
    end

    initial begin
        $readmemh("vectors/requant_pipe.hex", vec);
        repeat (4) @(negedge clk);
        rst = 0;
        for (sent = 0; sent < 256; sent = sent + 1) begin
            @(negedge clk);
            if (sent % 7 == 3) begin
                iv = 0;
                repeat (2) @(negedge clk);
            end
            {a, sc, b} = vec[sent][191:16];
            iv = 1;
        end
        @(negedge clk);
        iv = 0;
        wait (received == 256);
        $display("PASS requant_pipe 256 full 112-bit products and outputs: ties-even, negative ReLU, saturation, 48-bit extremes, back-to-back coefficients");
        $finish;
    end

    initial begin
        #10000;
        $fatal(1, "timeout received=%0d", received);
    end

endmodule
