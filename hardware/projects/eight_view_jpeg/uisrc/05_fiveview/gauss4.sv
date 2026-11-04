// gauss4.sv -- R46/P2.  Four-pixel-per-clock 3x3 gaussian, bit-exact with gauss3x3.sv.
//
// Frozen reference (R44 core) contract, reproduced exactly:
//   h(x,y)   = p(x-1,y) + 2*p(x,y) + p(x+1,y)              clamped edges
//   out(x,y) = ( h(x,y-1) + 2*h(x,y) + h(x,y+1) + 8 ) >> 4
//   top edge   -> h(x,-1) := h(x,0)     bottom edge -> h(x,H) := h(x,H-1)
//   left edge  -> p(-1,y) := p(0,y)     right edge  -> p(W,y) := p(W-1,y)
//
// Why a quad variant: the vendor VID008 ISP output is 128-bit = 4 px/clock and has NO
// back-pressure (R46_P0_INTERFACE_CONTRACT.md), so a 1 px/clk block cannot sit there.
//
// Group geometry: one clock carries columns 4c..4c+3 of one row.  Their 3x3 windows need
// columns 4c-1 .. 4c+4 (six pixels) = { last pixel of group c-1, group c-1, first pixel of
// group c }, so the horizontal sums of group c-1 are produced on the clock group c arrives:
// a one-group pipeline delay, not a 4x clock.
//
// Vertical combine: three rotating banks of horizontal sums (4 lanes x 10 bit in 40 bits).
//   write bank row%3 ; read (row-1)%3 as mid, (row-2)%3 as top ; emit output row = row-1
// The write bank is disjoint from both read banks, so each bank needs one write port and one
// read port only.
//
// NOTE on the read port (deliberate, documented):
//   the combine stage reads the banks combinationally from a registered address.  With a
//   registered RAM output the flags and the read data are one clock apart; the first version of
//   this file silently paired the flags with the previous address and the differential test
//   against gauss3x3 caught it.  A synchronous-read version needs one more pipeline stage, which
//   changes only latency, not values; converting it is an open item for the integrated build
//   (it also decides LUTRAM vs BRAM).
//
// End of frame: one extra clock produces the horizontal sums of the final group with the right
// edge clamped, then W/4 clocks emit the last row with the bottom edge clamped.  Every row and
// every pixel is emitted exactly once; the flush needs W/4 + 2 clocks, which must fit inside the
// frame blanking (the adapter guarantees that; violations are counted in o_drop).
//
// Requirements: W % 4 == 0.  Latency: one row + 4 clocks (not a contract).
`default_nettype none

module gauss4 #(
    parameter integer W     = 1920,       // must be a multiple of 4
    parameter integer MAXW  = 2048,       // buffer capacity, multiple of 4
    parameter integer HBITS = 12          // row counter width
) (
    input  wire            clk,
    input  wire            rst,           // synchronous, active high
    input  wire            i_valid,
    input  wire            i_sof,         // first group of a frame
    input  wire            i_eol,         // last group of a LINE   (vendor tlast semantics)
    input  wire            i_eof,         // last group of a FRAME
    input  wire [4*8-1:0]  i_y,           // four gray pixels, lane 0 = first in time
    output reg             o_valid,
    output reg             o_sof,
    output reg             o_eol,
    output reg  [4*8-1:0]  o_y,
    output reg  [15:0]     o_drop         // groups presented while not accepting (must stay 0)
);

    localparam integer NG      = W / 4;
    localparam integer NGMAX   = (MAXW / 4 < 8) ? 8 : (MAXW / 4);
    localparam integer NG_LAST = NG - 1;
    // NG is 480 at W=1920, so 8-bit counters/addresses would silently wrap: size them.
    localparam integer CGW     = (NG    > 1) ? $clog2(NG)    : 1;
    localparam integer AW      = (NGMAX > 1) ? $clog2(NGMAX) : 1;

    localparam [1:0] S_RUN = 2'd0, S_LASTH = 2'd1, S_FLUSH = 2'd2;

    reg [1:0] st;

    // ---- group pipeline: every field travels with its group, so no index arithmetic can drift
    reg [7:0]       p0, p1, p2, p3;
    reg [7:0]       p_pl;         // pixel just left of p0
    reg [CGW-1:0]   p_cg;         // column-group index of that group
    reg [HBITS-1:0] p_row;        // row index of that group
    reg             p_last;       // that group is the last of its line
    reg             p_ok;         // p* holds a valid group

    // ---- index of the group currently being presented
    reg [CGW-1:0]   cg;
    reg [HBITS-1:0] row;

    // ---- three banks of horizontal sums
    reg [39:0] bank0 [0:NGMAX-1];
    reg [39:0] bank1 [0:NGMAX-1];
    reg [39:0] bank2 [0:NGMAX-1];

    reg        wr_en;
    reg [1:0]  wr_sel;
    reg [AW-1:0] wr_addr;
    reg [39:0] wr_data;

    reg        rd_en;
    reg [1:0]  rd_sel;            // bank of the row being written
    reg [AW-1:0] rd_addr;

    // ---- horizontal sums for the group in p*
    wire [9:0]  h_left = (p_cg == {CGW{1'b0}}) ? {2'd0, p0} : {2'd0, p_pl};
    wire [9:0]  h0 = h_left     + {1'd0, p0, 1'd0} + {2'd0, p1};
    wire [9:0]  h1 = {2'd0, p0} + {1'd0, p1, 1'd0} + {2'd0, p2};
    wire [9:0]  h2 = {2'd0, p1} + {1'd0, p2, 1'd0} + {2'd0, p3};
    wire [9:0]  h3_rc = {2'd0, p2} + {1'd0, p3, 1'd0} + {2'd0, p3};          // right clamped
    wire [9:0]  h3_nc = {2'd0, p2} + {1'd0, p3, 1'd0} + {2'd0, i_y[7:0]};    // right open
    wire [9:0]  h3 = p_last ? h3_rc : h3_nc;
    wire [39:0] hsum_pack = {h3, h2, h1, h0};

    // ---- buffer: one synchronous write port per bank, one combinational read mux
    always @(posedge clk) begin
        if (wr_en) begin
            case (wr_sel)
                2'd0:    bank0[wr_addr] <= wr_data;
                2'd1:    bank1[wr_addr] <= wr_data;
                default: bank2[wr_addr] <= wr_data;
            endcase
        end
    end

    reg  [39:0] rd_mid;
    reg  [39:0] rd_top;
    // The read address must be delayed along with the flags: at stage C the live rd_sel/rd_addr
    // already point at the NEXT group, so using them here would fetch the neighbouring column.
    reg [1:0]   rd_sel_c;
    reg [AW-1:0] rd_addr_c;
    always @(posedge clk) begin
        rd_sel_c  <= rd_sel;
        rd_addr_c <= rd_addr;
    end
    always @(*) begin
        case (rd_sel_c)                     // rd_sel_c = p_row%3 (the row being written)
            2'd0:    begin rd_top = bank1[rd_addr_c]; rd_mid = bank2[rd_addr_c]; end
            2'd1:    begin rd_top = bank2[rd_addr_c]; rd_mid = bank0[rd_addr_c]; end
            default: begin rd_top = bank0[rd_addr_c]; rd_mid = bank1[rd_addr_c]; end
        endcase
    end

    reg [CGW-1:0]   flush_c;
    reg [HBITS-1:0] flush_row;

    wire accept = (st == S_RUN);

    // ---- stage B flags: issued together with the write and the read address
    reg             b_valid, b_sof, b_eol, b_topclamp, b_botclamp;
    reg [39:0]      b_hsum;

    // ---- stage C: same flags one clock later, when rd_mid/rd_top are valid
    reg             c_valid, c_sof, c_eol, c_topclamp, c_botclamp;
    reg [39:0]      c_hsum;

    always @(posedge clk) begin
        if (rst) begin
            c_valid <= 1'b0; c_sof <= 1'b0; c_eol <= 1'b0;
            c_topclamp <= 1'b0; c_botclamp <= 1'b0; c_hsum <= 40'd0;
        end else begin
            c_valid    <= b_valid;
            c_sof      <= b_sof;
            c_eol      <= b_eol;
            c_topclamp <= b_topclamp;
            c_botclamp <= b_botclamp;
            c_hsum     <= b_hsum;
        end
    end

    always @(posedge clk) begin
        if (rst) begin
            st <= S_RUN;
            p0 <= 8'd0; p1 <= 8'd0; p2 <= 8'd0; p3 <= 8'd0; p_pl <= 8'd0;
            p_cg <= 8'd0; p_row <= {HBITS{1'b0}}; p_last <= 1'b0; p_ok <= 1'b0;
            cg <= 8'd0; row <= {HBITS{1'b0}};
            wr_en <= 1'b0; wr_sel <= 2'd0; wr_addr <= {AW{1'b0}}; wr_data <= 40'd0;
            rd_en <= 1'b0; rd_sel <= 2'd0; rd_addr <= {AW{1'b0}};
            b_valid <= 1'b0; b_sof <= 1'b0; b_eol <= 1'b0;
            b_topclamp <= 1'b0; b_botclamp <= 1'b0; b_hsum <= 40'd0;
            flush_c <= {CGW{1'b0}}; flush_row <= {HBITS{1'b0}};
            o_drop <= 16'd0;
        end else begin
            wr_en      <= 1'b0;
            rd_en      <= 1'b0;
            b_valid    <= 1'b0;
            b_sof      <= 1'b0;
            b_eol      <= 1'b0;
            b_topclamp <= 1'b0;
            b_botclamp <= 1'b0;

            if (i_valid && !accept) o_drop <= o_drop + 16'd1;

            case (st)
            // --------------------------------------------------------------- streaming
            S_RUN: if (i_valid) begin
                if (p_ok) begin
                    wr_en   <= 1'b1;
                    wr_sel  <= p_row % 2'd3;
                    wr_addr <= p_cg;
                    wr_data <= hsum_pack;
                    rd_en   <= 1'b1;
                    rd_sel  <= p_row % 2'd3;
                    rd_addr <= p_cg;

                    b_valid    <= (p_row != {HBITS{1'b0}});          // output row = p_row-1
                    b_hsum     <= hsum_pack;
                    b_sof      <= (p_cg == {CGW{1'b0}}) && (p_row == {{(HBITS-1){1'b0}}, 1'b1});
                    b_eol      <= (p_cg == NG_LAST[CGW-1:0]);
                    b_topclamp <= (p_row == {{(HBITS-1){1'b0}}, 1'b1});
                    b_botclamp <= 1'b0;
                end

                p0    <= i_y[7:0];
                p1    <= i_y[15:8];
                p2    <= i_y[23:16];
                p3    <= i_y[31:24];
                p_pl  <= p_last ? i_y[7:0] : p3;     // left edge clamp at a new line
                p_cg  <= cg;
                p_row <= row;
                p_last<= i_eol;
                p_ok  <= 1'b1;

                if (i_sof) begin                        // defensive resync
                    cg  <= {{(CGW-1){1'b0}}, 1'b1};
                    row <= {HBITS{1'b0}};
                end else if (i_eol) begin
                    cg  <= {CGW{1'b0}};
                    row <= row + {{(HBITS-1){1'b0}}, 1'b1};
                end else begin
                    cg  <= cg + 1'b1;
                end

                if (i_eol && i_eof) begin
                    flush_row <= p_row;                 // the last group still needs its hsum
                    st        <= S_LASTH;
                end
            end

            // ---------------------------------------- last group of the frame: own hsum
            S_LASTH: begin
                wr_en   <= 1'b1;
                wr_sel  <= p_row % 2'd3;
                wr_addr <= p_cg;
                wr_data <= {h3_rc, h2, h1, h0};
                rd_en   <= 1'b1;
                rd_sel  <= p_row % 2'd3;
                rd_addr <= p_cg;

                b_valid    <= (p_row != {HBITS{1'b0}});
                b_hsum     <= {h3_rc, h2, h1, h0};
                b_sof      <= (p_cg == {CGW{1'b0}}) && (p_row == {{(HBITS-1){1'b0}}, 1'b1});
                b_eol      <= (p_cg == NG_LAST[CGW-1:0]);
                b_topclamp <= (p_row == {{(HBITS-1){1'b0}}, 1'b1});
                b_botclamp <= 1'b0;

                flush_c   <= {CGW{1'b0}};
                flush_row <= flush_row + {{(HBITS-1){1'b0}}, 1'b1};
                st        <= S_FLUSH;
            end

            // ------------------------------------------------ last row, bottom clamped
            S_FLUSH: begin
                rd_en   <= 1'b1;
                rd_sel  <= flush_row % 2'd3;
                rd_addr <= flush_c;

                b_valid    <= 1'b1;
                b_hsum     <= 40'd0;                       // unused: bot = mid
                b_sof      <= 1'b0;
                b_eol      <= (flush_c == NG_LAST[CGW-1:0]);
                b_topclamp <= (flush_row == {{(HBITS-1){1'b0}}, 1'b1});
                b_botclamp <= 1'b1;

                if (flush_c == NG_LAST[CGW-1:0]) begin
                    st    <= S_RUN;                        // ready for the next frame
                    p_ok  <= 1'b0;
                    cg    <= {CGW{1'b0}};
                    row   <= {HBITS{1'b0}};
                end else begin
                    flush_c <= flush_c + 1'b1;
                end
            end

            default: st <= S_RUN;
            endcase
        end
    end

    // ---- vertical combine: (top + 2*mid + bot + 8) >> 4, stage C
    wire [11:0] t0 = c_topclamp ? {2'd0, rd_mid[ 9: 0]} : {2'd0, rd_top[ 9: 0]};
    wire [11:0] m0 = {2'd0, rd_mid[ 9: 0]};
    wire [11:0] u0 = c_botclamp ? {2'd0, rd_mid[ 9: 0]} : {2'd0, c_hsum[ 9: 0]};
    wire [11:0] t1 = c_topclamp ? {2'd0, rd_mid[19:10]} : {2'd0, rd_top[19:10]};
    wire [11:0] m1 = {2'd0, rd_mid[19:10]};
    wire [11:0] u1 = c_botclamp ? {2'd0, rd_mid[19:10]} : {2'd0, c_hsum[19:10]};
    wire [11:0] t2 = c_topclamp ? {2'd0, rd_mid[29:20]} : {2'd0, rd_top[29:20]};
    wire [11:0] m2 = {2'd0, rd_mid[29:20]};
    wire [11:0] u2 = c_botclamp ? {2'd0, rd_mid[29:20]} : {2'd0, c_hsum[29:20]};
    wire [11:0] t3 = c_topclamp ? {2'd0, rd_mid[39:30]} : {2'd0, rd_top[39:30]};
    wire [11:0] m3 = {2'd0, rd_mid[39:30]};
    wire [11:0] u3 = c_botclamp ? {2'd0, rd_mid[39:30]} : {2'd0, c_hsum[39:30]};

    wire [11:0] s0 = t0 + {m0[10:0], 1'b0} + u0 + 12'd8;
    wire [11:0] s1 = t1 + {m1[10:0], 1'b0} + u1 + 12'd8;
    wire [11:0] s2 = t2 + {m2[10:0], 1'b0} + u2 + 12'd8;
    wire [11:0] s3 = t3 + {m3[10:0], 1'b0} + u3 + 12'd8;

    always @(posedge clk) begin
        if (rst) begin
            o_valid <= 1'b0; o_sof <= 1'b0; o_eol <= 1'b0; o_y <= 32'd0;
        end else begin
            o_valid <= c_valid;
            o_sof   <= c_sof;
            o_eol   <= c_eol;
            if (c_valid) begin
                o_y[ 7: 0] <= s0[11:4];
                o_y[15: 8] <= s1[11:4];
                o_y[23:16] <= s2[11:4];
                o_y[31:24] <= s3[11:4];
            end
        end
    end

endmodule

`default_nettype wire