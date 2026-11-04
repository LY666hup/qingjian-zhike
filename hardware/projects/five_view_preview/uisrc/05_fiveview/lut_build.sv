// lut_build.sv -- R29-P2.  Builds the 256-entry equalisation LUT from a finished histogram.
//
//   * one histogram bin per clock (read port, 1 clock of latency);
//   * the mapping division goes through div_pipe (33 stages, one result per clock), so the
//     block is fully pipelined: build time = 256 + 33 clocks, no variable-latency divider;
//   * every bin passes through the divider (den = 1 for the degenerate cases) so results come
//     back in bin order and can be written with a simple counter;
//   * the LUT is written into the bank selected by `bank`; the caller switches banks only after
//     `done`, so the LUT being used for mapping is never written while in use;
//   * clamping to 255 and the rounding (`+ den/2`) match the R25 golden rule exactly.
// READ_LAT -- how many clocks the histogram's rd_data takes to arrive.
//   The table builder walks the 256 bins and consumes one bin per clock, so the address it presents at
//   bin a must be answered with hist[a].  Two different consumers exist:
//       preproc_pipe / preproc_pipe_r33 : hist_accum_pp, one-clock read  -> READ_LAT = 1 (default)
//       preproc4_top                    : hist4_eram,   two-clock read   -> READ_LAT = 2
//   A mismatch is silent: with READ_LAT=1 against a two-clock histogram, bin a is evaluated with
//   hist[a-1], the inclusive CDF loses its own bin and cdf_min is taken one bin early, so every entry
//   comes out one level too dark and the image still looks plausible.  That was F35; the end-to-end
//   golden bench caught it only because it compares pixels, not histograms.
//
`default_nettype none

module lut_build #(
    parameter integer CW       = 24,
    // Clocks from presenting rd_addr to rd_data being valid.  This is a property of the HISTOGRAM
    // module, not of this one, so it must be declared:
    //   hist_accum_pp (frozen 1 px/clk chain): asynchronous array read + register = 1
    //   hist4_eram    (4 px/clk chain, ERAM) : registered block read + register   = 2
    // R48 defect (F35): hist4_eram was changed from 1 to 2 clocks but this module kept consuming
    // after one, so bin a was evaluated with hist[a-1]: the CDF excluded its own bin and cdf_min
    // was taken at bin min_value-1 (count 0), which made every LUT entry one step too dark.  The
    // end-to-end golden bench caught it as "mapped = golden - 1" in 3907 pixels.
    parameter integer READ_LAT = 1
) (
    input  wire          clk,
    input  wire          rst,
    input  wire          start,          // pulse: begin a build
    input  wire          bank,           // 0/1: LUT bank to fill
    input  wire [CW-1:0] total,
    input  wire [7:0]    min_value,
    input  wire          const_frame,
    // histogram read port
    output reg           rd_en,
    output reg  [7:0]    rd_addr,
    input  wire [CW-1:0] rd_data,
    // LUT write port
    output reg           we,
    output reg           wbank,
    output reg  [7:0]    waddr,
    output reg  [7:0]    wdata,
    output reg           done
);

    localparam integer DIVLAT = 33;      // div_pipe latency for W=32

    reg        busy;
    reg [8:0]  i;                        // bin being read
    reg [CW-1:0] cdf;
    reg [CW-1:0] cdf_min;
    reg [CW-1:0] den_r;
    reg        const_r;
    reg [7:0]  min_r;
    reg        res_pending;
    reg [8:0]  res_idx;

    reg        dv_valid;
    reg [31:0] dv_num, dv_den;
    wire       dv_out_valid;
    wire [31:0] dv_quo;

    div_pipe #(.W(32)) u_div (
        .clk(clk), .rst(rst), .in_valid(dv_valid), .in_num(dv_num), .in_den(dv_den),
        .out_valid(dv_out_valid), .out_quo(dv_quo), .out_rem()
    );

    // Read pipeline: READ_LAT stages so that the bin index and the data returned for it meet.
    // READ_LAT == 1 reproduces the original single stage exactly (bit-identical for preproc_pipe).
    localparam integer RL = (READ_LAT < 1) ? 1 : READ_LAT;

    reg        rv [0:RL-1];
    reg [8:0]  ri [0:RL-1];
    integer    rs;

    always @(posedge clk) begin
        if (rst) begin
            for (rs = 0; rs < RL; rs = rs + 1) begin
                rv[rs] <= 1'b0;
                ri[rs] <= 9'd0;
            end
        end else begin
            rv[0] <= rd_en && busy;
            ri[0] <= i;
            for (rs = 1; rs < RL; rs = rs + 1) begin
                rv[rs] <= rv[rs-1];
                ri[rs] <= ri[rs-1];
            end
        end
    end

    wire        rd_vld = rv[RL-1];
    wire [8:0]  rd_i   = ri[RL-1];

    always @(posedge clk) begin
        if (rst) begin
            busy <= 1'b0; i <= 9'd0; cdf <= {CW{1'b0}}; cdf_min <= {CW{1'b0}};
            den_r <= {CW{1'b0}}; const_r <= 1'b0; min_r <= 8'd0;
            rd_en <= 1'b0; rd_addr <= 8'd0;
            dv_valid <= 1'b0; dv_num <= 32'd0; dv_den <= 32'd1;
            res_pending <= 1'b0; res_idx <= 9'd0;
            we <= 1'b0; wbank <= 1'b0; waddr <= 8'd0; wdata <= 8'd0; done <= 1'b0;
        end else begin
            done <= 1'b0;
            we   <= 1'b0;
            dv_valid <= 1'b0;

            if (start && !busy) begin
                busy <= 1'b1;
                i <= 9'd0;
                cdf <= {CW{1'b0}};
                den_r <= {CW{1'b0}};
                const_r <= const_frame;
                min_r <= min_value;
                rd_en <= 1'b1;
                rd_addr <= 8'd0;
                wbank <= bank;
                res_pending <= 1'b0;
                res_idx <= 9'd0;
            end else if (busy) begin
                // issue the next bin read
                if (i < 9'd255) begin
                    i <= i + 9'd1;
                    rd_en <= 1'b1;
                    rd_addr <= i[7:0] + 8'd1;
                end else begin
                    rd_en <= 1'b0;
                end

                // consume the bin that arrived this cycle
                if (rd_vld) begin
                    cdf <= cdf + rd_data;
                    if (rd_i == {1'd0, min_r}) cdf_min <= cdf + rd_data;
                    // the denominator is constant once cdf_min is known
                    if (const_r) begin
                        dv_num   <= {24'd0, rd_i[7:0]};
                        dv_den   <= 32'd1;
                        dv_valid <= 1'b1;
                    end else if (rd_i <= {1'd0, min_r}) begin
                        dv_num   <= 32'd0;
                        dv_den   <= 32'd1;
                        dv_valid <= 1'b1;
                    end else begin
                        dv_num   <= ((cdf + rd_data) - cdf_min) * 32'd255 +
                                    ((total - cdf_min) >> 1);
                        dv_den   <= total - cdf_min;
                        dv_valid <= 1'b1;
                    end
                    if (!res_pending) begin res_pending <= 1'b1; res_idx <= 9'd0; end
                    if (rd_i == 9'd255) busy <= 1'b0;
                end
            end

            // write back results in bin order
            if (dv_out_valid) begin
                we    <= 1'b1;
                waddr <= res_idx[7:0];
                wdata <= (dv_quo > 32'd255) ? 8'd255 : dv_quo[7:0];
                res_idx <= res_idx + 9'd1;
                if (res_idx == 9'd255) done <= 1'b1;
            end
        end
    end

endmodule

`default_nettype wire
