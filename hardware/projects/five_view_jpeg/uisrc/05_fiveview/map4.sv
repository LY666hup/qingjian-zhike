// map4.sv -- R46/P2.  Four-pixel-per-clock equalisation mapper, bit-exact with the mapping stage
// of the frozen preproc_pipe.sv.
//
// Frozen mapping contract (preproc_pipe.sv, stage after lut_build):
//   do_switch  = i_sof && sw_pend && (pub_bank != building_bank || !busy)
//   map_q      = do_switch ? lut_new[g] : (lut_rdy ? lut_q[g] : g)
//   pix_gen    = do_real_switch ? lut_gen + 1 : lut_gen
// i.e. the LUT switches ON the first pixel of a frame, the pixel that triggers the switch is
// already mapped with the NEW table, and with no valid table yet the pixel passes through.
//
// Quad variant: a 256-entry table has ONE read port, so four pixels per clock need four copies
// per bank (2 banks x 4 copies x 256 x 8 bit = 4 kbit of ROM in total, i.e. one BRAM18 or a few
// LUTRAMs).  All four copies of a bank are written with the same data on the same clock, so they
// can never disagree.
//
// `o_gen` is the LUT generation that the pixels leaving this clock were mapped with: 0 = no table
// yet (pass-through), N = built from the N-th completed histogram.  It travels with the pixel
// because the consumer must know which table produced a pixel (R36 finding).
`default_nettype none

module map4 #(
    parameter integer NC = 4
) (
    input  wire          clk,
    input  wire          rst,
    input  wire          i_valid,
    input  wire          i_sof,
    input  wire          i_eof,
    input  wire [31:0]   i_y,
    // control from the build/ownership block
    input  wire          i_sw_pend,       // a finished table wants to take effect at the next sof
    input  wire          i_pub_bank,      // bank that table lives in
    input  wire          i_lut_rdy,       // at least one table has been published
    // table write port (one entry per clock, from lut_build)
    input  wire          we,
    input  wire          wbank,
    input  wire [7:0]    waddr,
    input  wire [7:0]    wdata,
    output reg           o_valid,
    output reg           o_sof,
    output reg           o_eol,
    output reg  [31:0]   o_y,
    output reg  [15:0]   o_gen,
    output reg           o_switch         // this group started a new LUT generation
);

    reg [7:0] rom [0:1][0:NC-1][0:255];

    // ---- table write: all copies of the selected bank, same data, same clock
    genvar c;
    generate
        for (c = 0; c < NC; c = c + 1) begin : copy
            always @(posedge clk) begin
                if (we) rom[wbank][c][waddr] <= wdata;
            end
        end
    endgenerate

    // ---- active table state
    reg        act;
    reg        rdy;
    reg [15:0] gen;

    // The switch must apply to the FIRST group of the frame, i.e. the very group that carries
    // i_sof.  A registered `act` would still hold the old bank during that clock, so the lookup
    // uses the *effective* (next) state and the registers store the same values at the edge.
    wire sw = i_valid && i_sof && i_sw_pend;
    wire        act_eff = sw ? i_pub_bank : act;
    wire        rdy_eff = sw ? 1'b1       : (rdy | i_lut_rdy);
    wire [15:0] gen_eff = sw ? (gen + 16'd1) : gen;

    always @(posedge clk) begin
        if (rst) begin
            act <= 1'b0; rdy <= 1'b0; gen <= 16'd0;
        end else begin
            act <= act_eff;
            rdy <= rdy_eff;
            gen <= gen_eff;
        end
    end

    // ---- per-pixel lookup, four independent copies of the active bank
    wire [7:0] y0 = i_y[ 7: 0];
    wire [7:0] y1 = i_y[15: 8];
    wire [7:0] y2 = i_y[23:16];
    wire [7:0] y3 = i_y[31:24];

    wire [7:0] m0 = rdy_eff ? rom[act_eff][0][y0] : y0;
    wire [7:0] m1 = rdy_eff ? rom[act_eff][1][y1] : y1;
    wire [7:0] m2 = rdy_eff ? rom[act_eff][2][y2] : y2;
    wire [7:0] m3 = rdy_eff ? rom[act_eff][3][y3] : y3;

    always @(posedge clk) begin
        if (rst) begin
            o_valid <= 1'b0; o_sof <= 1'b0; o_eol <= 1'b0; o_y <= 32'd0;
            o_gen <= 16'd0; o_switch <= 1'b0;
        end else begin
            o_valid  <= i_valid;
            o_sof    <= i_sof && i_valid;
            o_eol    <= i_eof && i_valid;
            o_switch <= sw && i_valid;
            o_gen    <= gen_eff;
            if (i_valid) begin
                o_y[ 7: 0] <= m0;
                o_y[15: 8] <= m1;
                o_y[23:16] <= m2;
                o_y[31:24] <= m3;
            end
        end
    end

endmodule

`default_nettype wire