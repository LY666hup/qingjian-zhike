// preproc4_top.sv -- R46/P2+P4.  The whole preprocessing chain at FOUR PIXELS PER CLOCK.
//
//   rgb 4px/clk (128 bit, vendor VID008 ISP packing) -> gray4 -> gauss4 -> map4 -> mapped gray
//                                                                  ^        ^
//                                                        hist4 ----+        |
//                                                          |               |
//                                                          +--> lut_build --+ (2 banks x 4 copies)
//
// Control law, copied from the frozen preproc_pipe (R36/R44) so that a consumer sees the same
// contract:
//   * frame k's histogram builds the LUT used by frame k+1 (one frame of latency);
//   * frame 0 (and every frame before the first LUT exists) passes through unchanged, gen = 0;
//   * the switch happens ON the first group of a frame, and that group is already mapped with the
//     new table (map4 does this internally from sw_pend/pub_bank);
//   * a build that sees its histogram generation reused (rd_stale) is DISCARDED, never published.
//
// Differences from the frozen chain, all forced by the four-pixel width and stated openly:
//   * one histogram buffer instead of two banks: a build must finish before the next frame's
//     first group.  It takes ~300 clocks and the blanking is thousands of groups, so overlapping
//     is impossible in a legal stream; if it ever happens the task is dropped and counted
//     (c_hist_drop / c_stale) instead of publishing a table built from two frames.
//   * no output FIFO: this block never back-pressures (P0 contract), the consumer must be able to
//     accept one group per clock.  Anything else must be absorbed downstream.
//   * there is no frame-end signal in the vendor stream (only per-line tlast), so the frame end is
//     derived from a line counter and every anomaly is counted (c_len_err), never assumed.
//
// Reset contract (R46_P4_RESET_CONTRACT.md, closes KI-13 for this chain):
//   rst        : full reset, the LUT is invalidated (gen back to 0, pass-through until rebuilt).
//   i_flush    : abandon the frame in progress (row/column/accumulator state cleared) but KEEP the
//                LUT banks and their readiness, so the next frame still gets equalisation.
//   Both are synchronous; hold for at least 2 clocks.
`default_nettype none

module qj_preproc4_views #(
    parameter integer W     = 1920,       // W % 4 == 0
    parameter integer H     = 1080,
    parameter integer MAXW  = 2048,
    parameter integer CW    = 24,
    parameter integer HBITS = 12
) (
    input  wire          clk,
    input  wire          rst,
    input  wire          i_flush,
    // ---- pixel input: 4 pixels/clk, vendor packing {8'hff,R,G,B} per 32-bit lane, lane0 first
    input  wire          i_valid,
    input  wire          i_sof,           // first group of a frame (vertical sync derived)
    input  wire          i_eol,           // last group of a LINE (vendor tlast)
    input  wire [127:0]  i_pix,
    // ---- mapped gray output: 4 pixels/clk, no back-pressure
    // Read-only outputs: consumers must buffer, never backpressure this core.
    output wire o_gray_valid, o_gray_sof, o_gray_eol,
    output wire [31:0] o_gray_y,
    output wire o_gauss_valid, o_gauss_sof, o_gauss_eol,
    output wire [31:0] o_gauss_y,
    output wire          o_valid,
    output wire          o_sof,
    output wire          o_eol,
    output wire [31:0]   o_y,
    output wire [15:0]   o_gen,
    // ---- audit
    output reg  [31:0]   c_frames,
    output reg  [31:0]   c_identity,
    output reg  [31:0]   c_switched,
    output reg  [31:0]   c_pub,
    output reg  [31:0]   c_stale,
    output reg  [31:0]   c_hist_drop,
    output reg  [31:0]   c_len_err,
    output reg  [31:0]   c_gauss_drop,
    output reg  [31:0]   o_lut_src_frame,
    output reg  [7:0]    o_lut_src_epoch,
    output reg           o_lut_rdy
);

    localparam integer NG = W / 4;

    // an unaligned or short frame must never silently produce a wrong table: both the row counter
    // and the group counter are checked against the parameters
    wire grst = rst | i_flush;

    // ------------------------------------------------------------------ RGB -> gray (4 px/clk)
    wire        g4_valid, g4_sof, g4_last;
    wire [31:0] g4_y;

    gray4 u_gray (
        .clk(clk), .rst(grst),
        .i_valid(i_valid), .i_sof(i_sof), .i_last(i_eol), .i_data(i_pix),
        .o_valid(g4_valid), .o_sof(g4_sof), .o_last(g4_last), .o_y(g4_y)
    );

    // ------------------------------------------------------------------ frame-end derivation
    // The vendor stream has no frame-end flag.  A frame ends on the line whose index is H-1; a
    // frame that ends early or late is counted, never corrected silently.
    reg [HBITS-1:0] row_in;
    // eof MUST be asserted in the same clock as the last group, not one clock later: the gaussian
    // only recognises its end-of-frame on the group that carries i_eof (a registered pulse would
    // never coincide with i_valid and the chain would never flush -- caught by this testbench).
    wire last_line_in = g4_valid && g4_last && (row_in == (H - 1));
    wire eof_in       = last_line_in;

    always @(posedge clk) begin
        if (grst) begin
            row_in <= {HBITS{1'b0}};
        end else if (g4_valid) begin
            if (g4_sof)             row_in <= {HBITS{1'b0}};
            else if (g4_last)       row_in <= last_line_in ? {HBITS{1'b0}}
                                                          : row_in + {{(HBITS-1){1'b0}}, 1'b1};
        end
    end

    // ------------------------------------------------------------------ 3x3 gaussian
    wire        g_valid, g_sof, g_eol;
    wire [31:0] g_y;
    wire [15:0] g_drop;
    assign o_gray_valid=g4_valid, o_gray_sof=g4_sof, o_gray_eol=g4_last;
    assign o_gray_y=g4_y;
    assign o_gauss_valid=g_valid, o_gauss_sof=g_sof, o_gauss_eol=g_eol;
    assign o_gauss_y=g_y;


    gauss4 #(.W(W), .MAXW(MAXW), .HBITS(HBITS)) u_gauss (
        .clk(clk), .rst(grst),
        .i_valid(g4_valid), .i_sof(g4_sof), .i_eol(g4_last), .i_eof(eof_in), .i_y(g4_y),
        .o_valid(g_valid), .o_sof(g_sof), .o_eol(g_eol), .o_y(g_y), .o_drop(g_drop)
    );

    // frame end on the OUTPUT side: the last line of the frame is the H-th o_eol
    reg [15:0]      prev_drop;              // must be reset: an X here poisons c_gauss_drop
    reg [HBITS-1:0] row_out;
    localparam integer GGW = (NG * H <= 1) ? 1 : $clog2(NG * H + 1);
    reg [GGW-1:0]   o_grp;                 // group index inside the output frame (0-based);
                                           // 16 bits OVERFLOWED at 1080p (NG*H = 518400) and
                                           // flagged every frame as a length error
    wire last_line_out = g_valid && g_eol && (row_out == (H - 1));
    wire o_eof         = last_line_out;    // same clock as the last group, as above

    // Geometry / drop EVENTS are detected here but COUNTED in the ownership block below: a register
    // may only have one driving always block.  TD 2026.1 rejected the earlier split
    // ("net 'c_len_err[31]' is constantly driven from multiple places") even though ModelSim ran it
    // happily -- the reset lived in one block and the increment in another.
    // gauss4.o_drop is a running 16-bit total, so take its DELTA (mod 2^16 arithmetic survives the
    // wrap); copying the total would only ever record the last 16 bits.
    wire        len_err_evt = last_line_out && (o_grp != (NG * H - 1)) && !i_flush;
    wire [15:0] drop_delta  = g_drop - prev_drop;

    always @(posedge clk) begin
        if (grst) begin
            row_out <= {HBITS{1'b0}};
            o_grp   <= {GGW{1'b0}};
            prev_drop <= 16'd0;
        end else begin
            if (g_valid) begin
                o_grp <= (g_sof) ? {{(GGW-1){1'b0}}, 1'b1} : (o_grp + {{(GGW-1){1'b0}}, 1'b1});
                if (g_sof)          row_out <= {HBITS{1'b0}};
                else if (g_eol)     row_out <= last_line_out ? {HBITS{1'b0}}
                                                           : row_out + {{(HBITS-1){1'b0}}, 1'b1};
                if (last_line_out) o_grp <= {GGW{1'b0}};
            end
            prev_drop <= g_drop;
        end
    end

    // ------------------------------------------------------------------ histogram
    wire          h_done;
    wire [7:0]    h_epoch;
    wire [31:0]   h_fid;
    wire [CW-1:0] h_total, h_minpx, h_rdata;
    wire [7:0]    h_minv;
    wire          h_const;
    wire          h_ren;
    wire [7:0]    h_raddr;

    reg  [7:0]    rd_exp_epoch;
    wire          h_stale;

    hist4 #(.CW(CW)) u_hist (
        // rst clears the histogram memory and the bank phase; a flush only abandons the frame, so the
        // bank this block writes stays the one the mapper is not using (F41).
        .clk(clk), .rst(rst), .i_flush(i_flush),
        .i_valid(g_valid), .i_sof(g_sof), .i_eof(o_eof), .i_d(g_y),
        .frame_done(h_done), .done_epoch(h_epoch), .done_frame_id(h_fid),
        .total_pixels(h_total), .min_value(h_minv), .min_pixels(h_minpx), .const_frame(h_const),
        .rd_en(h_ren), .rd_addr(h_raddr), .rd_data(h_rdata),
        .rd_exp_epoch(rd_exp_epoch), .rd_stale(h_stale),
        .cur_epoch(), .frame_cnt()
    );

    // ------------------------------------------------------------------ LUT build control
    reg        busy, sw_pend, lut_act_top;
    reg        start;
    reg        wr_bank;
    reg [31:0] run_frame;
    reg [7:0]  run_epoch;
    reg [CW-1:0] run_total, run_minpx;
    reg [7:0]  run_min;
    reg        run_const;
    reg        pub_bank;
    reg [31:0] pub_frame;
    reg [7:0]  pub_epoch;

    wire        b_we, b_bank, b_done, b_start_busy;
    wire [7:0]  b_addr, b_wdata;

    lut_build #(.CW(CW), .READ_LAT(2)) u_lut (
        .clk(clk), .rst(grst),
        .start(start), .bank(wr_bank),
        .total(run_total), .min_value(run_min), .const_frame(run_const),
        .rd_en(h_ren), .rd_addr(h_raddr), .rd_data(h_rdata),
        .we(b_we), .wbank(b_bank), .waddr(b_addr), .wdata(b_wdata), .done(b_done)
    );

    assign b_start_busy = busy;

    // ------------------------------------------------------------------ mapper
    wire        m_valid, m_sof, m_eol, m_switch;
    wire [31:0] m_y;
    wire [15:0] m_gen;

    map4 #(.NC(4)) u_map (
        .clk(clk), .rst(rst),                     // NOT grst: a flush keeps the LUT banks
        .i_valid(g_valid), .i_sof(g_sof), .i_eof(g_eol), .i_y(g_y),
        .i_sw_pend(sw_pend), .i_pub_bank(pub_bank), .i_lut_rdy(o_lut_rdy),
        .we(b_we), .wbank(b_bank), .waddr(b_addr), .wdata(b_wdata),
        .o_valid(m_valid), .o_sof(m_sof), .o_eol(m_eol), .o_y(m_y),
        .o_gen(m_gen), .o_switch(m_switch)
    );

    assign o_valid = m_valid;
    assign o_sof   = m_sof;
    assign o_eol   = m_eol;
    assign o_y     = m_y;
    assign o_gen   = m_gen;

    // ------------------------------------------------------------------ task / ownership control
    always @(posedge clk) begin
        if (rst) begin
            busy <= 1'b0; sw_pend <= 1'b0; lut_act_top <= 1'b0; start <= 1'b0;
            wr_bank <= 1'b0; run_frame <= 32'd0; run_epoch <= 8'd0;
            run_total <= {CW{1'b0}}; run_minpx <= {CW{1'b0}}; run_min <= 8'd0; run_const <= 1'b0;
            pub_bank <= 1'b0; pub_frame <= 32'd0; pub_epoch <= 8'd0;
            rd_exp_epoch <= 8'd0;
            c_frames <= 32'd0; c_identity <= 32'd0; c_switched <= 32'd0; c_pub <= 32'd0;
            c_stale <= 32'd0; c_hist_drop <= 32'd0; c_len_err <= 32'd0; c_gauss_drop <= 32'd0;
            o_lut_src_frame <= 32'hFFFF_FFFF; o_lut_src_epoch <= 8'd0; o_lut_rdy <= 1'b0;
        end else begin
            start <= 1'b0;

            // single owner for the audit counters (see the comment above the grst block)
            if (len_err_evt) c_len_err <= c_len_err + 32'd1;
            if (!i_flush)    c_gauss_drop <= c_gauss_drop + {16'd0, drop_delta};

            if (i_flush) begin
                // the frame in flight is abandoned; the table survives
                busy <= 1'b0; start <= 1'b0;
            end else begin

                // ---- a finished histogram wants a table
                if (h_done) begin
                    c_frames <= c_frames + 32'd1;
                    if (!busy && !sw_pend) begin
                        // a task may only start when no switch is waiting: while sw_pend is set the
                        // other bank is about to become active and must not be rewritten
                        // F39: the source identity must NOT come from the histogram block.  hist4 is
                        // reset by a flush (it must forget the partial frame), and its frame-id counter
                        // goes with it, so h_fid restarts at 1 after any flush.  The published table
                        // then claimed to come from frame 1 while it came from frame N: the reported
                        // identity collapsed, which is exactly what software uses to attribute a table.
                        // c_frames only counts COMPLETED histograms and is cleared only by rst, which
                        // also clears the tables, so c_frames + 1 is a monotonic 1-based ordinal.
                        run_frame <= c_frames + 32'd1; run_epoch <= h_epoch;
                        run_total <= h_total; run_min <= h_minv; run_minpx <= h_minpx;
                        run_const <= h_const;
                        wr_bank   <= o_lut_rdy ? ~lut_act_top : 1'b0;
                        rd_exp_epoch <= h_epoch;
                        busy  <= 1'b1;
                        start <= 1'b1;
                    end else begin
                        c_hist_drop <= c_hist_drop + 32'd1;
                    end
                end

                // ---- build finished: publish or discard
                if (b_done) begin
                    if (h_stale) begin
                        // the histogram was reused under the build: never publish a mixed table
                        c_stale <= c_stale + 32'd1;
                        busy <= 1'b0;
                    end else begin
                        pub_bank  <= wr_bank;
                        pub_frame <= run_frame;
                        pub_epoch <= run_epoch;
                        sw_pend   <= 1'b1;
                        c_pub     <= c_pub + 32'd1;
                        busy      <= 1'b0;
                    end
                end

                // ---- frame boundary: apply the pending switch (map4 does the same at this edge)
                if (g_sof) begin
                    if (sw_pend) begin
                        sw_pend     <= 1'b0;
                        lut_act_top <= pub_bank;
                        o_lut_rdy   <= 1'b1;
                        c_switched  <= c_switched + 32'd1;
                        o_lut_src_frame <= pub_frame;
                        o_lut_src_epoch <= pub_epoch;
                    end else if (!o_lut_rdy) begin
                        c_identity <= c_identity + 32'd1;
                    end
                end
            end
        end
    end

endmodule

`default_nettype wire