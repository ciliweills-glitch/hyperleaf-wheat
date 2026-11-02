// cnn_tile_core.v - Conv1-5 reusable 2-Cin x 2-Cout tile engine
//
// Input: group-major [ceil(Cin/2), H+2, W+2], packed {channel1, channel0}.
// Nine spatial banks supply all 3x3 taps; no window FIFO or unbounded stream.
// Commands: 1 = load activation tile, 2 = load weights/F48 coefficients,
//           3 = batched run.
// Batched RUN (v3): words = K*(18*groups+8) weight words streamed on s_axis
// while the core computes; K = pool[31:20]. Outputs K pairs back to back with
// one TLAST at the very end. Weight banks ping-pong so pair k+1 streams while
// pair k computes.
`timescale 1ns / 1ps

module cnn_tile_core (
    input  wire        clk,
    input  wire        rst,
    input  wire        start,
    input  wire [ 1:0] command,
    input  wire [31:0] words,
    input  wire [ 5:0] tile_w,
    input  wire [ 2:0] tile_h,
    input  wire [ 6:0] groups,
    input  wire [ 2:0] pool_w,
    input  wire [ 1:0] pool_h,
    input  wire        pointwise,
    input  wire [11:0] npairs,

    output wire        busy,
    output reg         done,
    output reg         frame_error,

    input  wire [31:0] s_axis_tdata,
    input  wire [ 3:0] s_axis_tkeep,
    input  wire        s_axis_tvalid,
    output wire        s_axis_tready,
    input  wire        s_axis_tlast,
    output reg  [31:0] m_axis_tdata,
    output wire [ 3:0] m_axis_tkeep,
    output wire        m_axis_tvalid,
    input  wire        m_axis_tready,
    output reg         m_axis_tlast
);

    localparam IDLE    =  0, LOAD_X  =  1, LOAD_W = 2, CLEAR = 3,
                 FETCH   =  4, ISSUE   =  5, DRAIN  = 6, REQUANT = 7,
                 POOL    =  8, SEND    =  9, NEXT   = 10, RWAIT  = 11;

    reg [3:0]  state;
    reg [31:0] left;
    reg [6:0]  lg, cg;
    reg [2:0]  ly, py;
    reg [5:0]  lx, px;
    reg [4:0]  wb;
    reg [3:0]  lane;
    reg [3:0]  coeff_index;
    reg [3:0]  drain_count;

    // Batched-run receiver / pair loop registers.
    reg        ractive, rdone, wrecv, wact, pair_end;
    reg        pair_ready0, pair_ready1;
    reg [31:0] rw, wtotal;
    reg [11:0] pair_idx, K;
    reg [31:0] coeff [0:1][0:7];

    wire input_fire = s_axis_tvalid && s_axis_tready;
    // FETCH primes group 0; ISSUE consumes q while BRAM prefetches group cg+1.
    wire fetch_group = (state == FETCH) || (state == ISSUE && cg != groups - 1);
    wire [6:0] read_group = (state == ISSUE) ? cg + 7'd1 : cg;

    assign busy = (state != IDLE) || ractive;
    assign s_axis_tready = (state == LOAD_X) || (state == LOAD_W) ||
                           (ractive && !(wrecv ? pair_ready1 : pair_ready0));
    assign m_axis_tvalid = state == SEND;
    assign m_axis_tkeep  = 4'hf;

    wire [3:0] load_bank = pointwise ? lane : (ly % 3) * 3 + (lx % 3);

    // Fixed strides for the maximum 4x32 output tile (6x34 with halo).
    wire [11:0] load_addr = pointwise ? lg * 128 + ly * 32 + lx :
                                        lg * 24 + (ly / 3) * 12 + (lx / 3);

    wire [31:0] bank_data [0:8];
    genvar b;
    generate
        for (b = 0; b < 9; b = b + 1) begin : xbank
            (* ram_style = "block" *) reg [31:0] mem [0:2471];
            reg  [31:0] q;
            wire [ 5:0] bx   = px + ((b % 3 + 3 - (px % 3)) % 3);
            wire [ 2:0] by   = py + ((b / 3 + 3 - (py % 3)) % 3);
            wire [11:0] addr = pointwise ? read_group * 128 + py * 32 + px :
                                           read_group * 24 + (by / 3) * 12 + (bx / 3);
            always @(posedge clk) begin
                if (state == LOAD_X && input_fire && load_bank == b) mem[load_addr] <= s_axis_tdata;
                if (fetch_group) q <= mem[addr];
            end
            assign bank_data[b] = q;
        end
    endgenerate

    // 36 weight banks = 2 ping-pong sets of 18 taps; set 0 also serves legacy LOAD_W.
    wire [575:0] w_flat [0:1];
    wire [31:0]  pair_words = 18 * groups + 8;
    wire         rcv_fire   = ractive && s_axis_tvalid && s_axis_tready;
    generate
        for (b = 0; b < 36; b = b + 1) begin : wbank
            (* ram_style = "block" *) reg [31:0] mem [0:102];
            reg  [31:0] q;
            localparam SET = b / 18, TAP = b % 18;
            always @(posedge clk) begin
                if (state == LOAD_W && input_fire && lg < groups && wb == TAP && SET == 0) mem[lg] <= s_axis_tdata;
                if (rcv_fire && rw < 18 * groups && wrecv == SET && wb == TAP) mem[lg] <= s_axis_tdata;
                if (fetch_group) q <= mem[read_group];
            end
            assign w_flat[SET][TAP * 32 +: 32] = q;
        end
    endgenerate

    reg [287:0] x_flat;
    integer     ky, kx, bank_sel;
    always @* begin
        x_flat = 0;
        for (ky = 0; ky < 3; ky = ky + 1) begin
            for (kx = 0; kx < 3; kx = kx + 1) begin
                bank_sel = pointwise ? ky * 3 + kx : ((py + ky) % 3) * 3 + ((px + kx) % 3);
                x_flat[(ky * 3 + kx) * 16 +: 16]     = bank_data[bank_sel][15:0];
                x_flat[(9 + ky * 3 + kx) * 16 +: 16] = bank_data[bank_sel][31:16];
            end
        end
    end

    wire [95:0] acc;
    pe_array pe (
        .clk       (clk),
        .rst       (rst),
        .clear_acc (state == CLEAR),
        .in_valid  (state == ISSUE),
        .x_flat    (x_flat),
        .w_flat    (w_flat[wact]),
        .acc_flat  (acc)
    );

    wire [15:0] y0, y1;
    wire        yv0, yv1;
    requant_pipe rq0 (
        .clk     (clk),
        .rst     (rst),
        .in_valid(state == REQUANT),
        .acc     (acc[47:0]),
        .scale_q ({coeff[wact][1], coeff[wact][0]}),
        .bias_q  ({coeff[wact][3], coeff[wact][2]}),
        .y       (y0),
        .out_valid(yv0)
    );
    requant_pipe rq1 (
        .clk     (clk),
        .rst     (rst),
        .in_valid(state == REQUANT),
        .acc     (acc[95:48]),
        .scale_q ({coeff[wact][5], coeff[wact][4]}),
        .bias_q  ({coeff[wact][7], coeff[wact][6]}),
        .y       (y1),
        .out_valid(yv1)
    );

    reg [31:0] pool_mem [0:31];

    // Pool widths/heights are powers of two: 1, 2, 4 and 1, 2.
    wire [5:0] pool_col      = pool_w == 4 ? (px >> 2) : pool_w == 2 ? (px >> 1) : px;
    wire       first_in_pool = ((py & (pool_h - 1)) == 0) && ((px & (pool_w - 1)) == 0);
    wire       last_in_pool  = ((py & (pool_h - 1)) == pool_h - 1) && ((px & (pool_w - 1)) == pool_w - 1);
    wire [31:0] old_pool     = pool_mem[pool_col];
    wire [15:0] max0 = first_in_pool || y0 > old_pool[15:0] ? y0 : old_pool[15:0];
    wire [15:0] max1 = first_in_pool || y1 > old_pool[31:16] ? y1 : old_pool[31:16];

    always @(posedge clk) begin
        if (rst) begin
            state        <= IDLE;
            done         <= 0;
            frame_error  <= 0;
            left         <= 0;
            lg           <= 0;
            cg           <= 0;
            ly           <= 0;
            lx           <= 0;
            py           <= 0;
            px           <= 0;
            wb           <= 0;
            lane         <= 0;
            coeff_index  <= 0;
            drain_count  <= 0;
            m_axis_tdata <= 0;
            m_axis_tlast <= 0;
            ractive      <= 0;
            rdone        <= 0;
            wrecv        <= 0;
            wact         <= 0;
            pair_end     <= 0;
            pair_ready0  <= 0;
            pair_ready1  <= 0;
            rw           <= 0;
            wtotal       <= 0;
            pair_idx     <= 0;
            K            <= 0;
        end else begin
            done <= 0;

            // Weight-stream receiver for batched RUN.
            if (rcv_fire) begin
                wtotal <= wtotal - 1;
                if (s_axis_tkeep != 4'hf) frame_error <= 1;
                if (rw < 18 * groups) begin
                    if (wb == 17) begin
                        wb <= 0;
                        lg <= lg + 1;
                    end else wb <= wb + 1;
                end else begin
                    coeff[wrecv][coeff_index] <= s_axis_tdata;
                    coeff_index               <= coeff_index + 1;
                end
                if (rw == pair_words - 1) begin
                    if (wrecv) pair_ready1 <= 1; else pair_ready0 <= 1;
                    wrecv       <= wrecv ^ 1;
                    rw          <= 0;
                    lg          <= 0;
                    wb          <= 0;
                    coeff_index <= 0;
                    if (wtotal == 1) begin
                        if (!s_axis_tlast) frame_error <= 1;
                        ractive <= 0;
                        rdone   <= 1;
                    end else if (s_axis_tlast) frame_error <= 1;
                end else begin
                    rw <= rw + 1;
                    if (s_axis_tlast) frame_error <= 1;
                end
            end

            case (state)
                IDLE: if (start) begin
                        frame_error <= 0;
                        left        <= words;
                        lg          <= 0;
                        ly          <= 0;
                        lx          <= 0;
                        wb          <= 0;
                        lane        <= 0;
                        coeff_index <= 0;
                        px          <= 0;
                        py          <= 0;
                        rdone       <= 0;
                        pair_end    <= 0;
                        pair_idx    <= 0;
                        case (command)
                            1: state <= LOAD_X;
                            2: state <= LOAD_W;
                            3: begin
                                K           <= npairs;
                                state       <= RWAIT;
                                ractive     <= 1;
                                rw          <= 0;
                                wtotal      <= words;
                                wrecv       <= 0;
                                wact        <= 0;
                                pair_ready0 <= 0;
                                pair_ready1 <= 0;
                            end
                        endcase
                    end

                LOAD_X, LOAD_W: if (input_fire) begin
                    left <= left - 1;
                    if (s_axis_tkeep != 4'hf || s_axis_tlast != (left == 1)) frame_error <= 1;
                    if (state == LOAD_X) begin
                        if (pointwise && lane != 8) lane <= lane + 1; else begin
                            lane <= 0;
                            if (lx == (pointwise ? tile_w - 1 : tile_w + 1)) begin
                                lx <= 0;
                                if (ly == (pointwise ? tile_h - 1 : tile_h + 1)) begin
                                    ly <= 0;
                                    lg <= lg + 1;
                                end else ly <= ly + 1;
                            end else lx <= lx + 1;
                        end
                    end else begin
                        if (lg < groups) begin
                            if (wb == 17) begin
                                wb <= 0;
                                lg <= lg + 1;
                            end else wb <= wb + 1;
                        end else begin
                            coeff[0][coeff_index] <= s_axis_tdata;
                            coeff_index           <= coeff_index + 1;
                        end
                    end
                    if (left == 1 || s_axis_tlast) begin
                        state <= IDLE;
                        done  <= 1;
                    end
                end

                RWAIT: if (wact ? pair_ready1 : pair_ready0) begin
                    // pair_ready stays set while the pair computes: it is the
                    // receiver's write-protection flag, cleared only at pair end.
                    px    <= 0;
                    py    <= 0;
                    cg    <= 0;
                    state <= CLEAR;
                end

                CLEAR: begin
                    cg    <= 0;
                    state <= FETCH;
                end

                FETCH: state <= ISSUE;

                ISSUE: begin
                    if (cg == groups - 1) begin
                        drain_count <= 0;
                        state       <= DRAIN;
                    end else begin
                        cg    <= cg + 1;
                        state <= ISSUE;
                    end
                end

                DRAIN: begin
                    if (drain_count == 6) state <= REQUANT; else drain_count <= drain_count + 1;
                end

                REQUANT: state <= POOL;

                POOL: if (yv0 && yv1) begin
                    pool_mem[pool_col] <= {max1, max0};
                    if (last_in_pool) begin
                        m_axis_tdata <= {max1, max0};
                        m_axis_tlast <= (px == tile_w - 1 && py == tile_h - 1 && pair_idx == K - 1);
                        pair_end     <= (px == tile_w - 1 && py == tile_h - 1 && pair_idx != K - 1);
                        state        <= SEND;
                    end else state <= NEXT;
                end

                SEND: if (m_axis_tready) begin
                    if (m_axis_tlast) begin
                        if (wact) pair_ready1 <= 0; else pair_ready0 <= 0;
                        state <= IDLE;
                        done  <= 1;
                    end else if (pair_end) begin
                        if (wact) pair_ready1 <= 0; else pair_ready0 <= 0;
                        pair_end <= 0;
                        pair_idx <= pair_idx + 1;
                        wact     <= wact ^ 1;
                        state    <= RWAIT;
                    end else state <= NEXT;
                end

                NEXT: begin
                    if (px == tile_w - 1) begin
                        px <= 0;
                        py <= py + 1;
                    end else px <= px + 1;
                    state <= CLEAR;
                end
            endcase
        end
    end

endmodule
