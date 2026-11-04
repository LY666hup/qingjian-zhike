// Independent RAM-inference restructuring plus peer F58 fix.
// hist4.sv -- R48.  Four-pixel-per-clock luminance histogram, ERAM-oriented, bit-exact with
// hist_accum_pp.sv.  External port list is IDENTICAL to R47 except that rd_data has two clocks of
// latency instead of one.  The consumer must match that: lut_build takes READ_LAT=2 on this chain
// (READ_LAT=1 is the frozen one-pixel chain).  Getting it wrong does not break the image -- it makes
// every mapped pixel one level too dark, because bin a is then evaluated with hist[a-1] (F35).
//
// WHY THIS REWRITE (R47 measurements, see R48_HIST4.md):
//   TD reported `SYN-5114: Implement the large ram "u_hist/ramread0_syn_13" may requires 24576
//   registers` -- the R47 histogram landed entirely in flip-flops, and its 256-way bin decode was the
//   critical path (10.203 ns, 64% route delay, one net with fanout 1029).  Root cause: two
//   asynchronous reads of a 256-entry array.  An asynchronous read cannot become ERAM; it becomes a
//   256:1 LUT mux, times four lanes.
//
// STRUCTURE
//   Four lanes, each with its own pair of bank memories (8 blocks).  A lane needs one read-modify-write
//   per clock, which occupies a two-port block completely, so the readback cannot share those ports;
//   instead the frame is ping-ponged: frame N accumulates in one bank, and at eof the roles swap, so
//   the consumer reads the finished bank while the next frame accumulates in the other one.  Each
//   array therefore has exactly ONE read statement and ONE write statement (the ERAM template), and
//   the read address is a combinational mux in front of the block:
//       finished bank -> external rd_addr (the consumer walking the 256 bins)
//       accumulating  -> registered pixel bin (the RMW)
//
// CONTRACT PROPERTIES
//   * four lanes on the same bin in one clock: separate per-lane memories, no collision; the readback
//     is the exact sum of the four lane counts for that bin.
//   * RAW hazard inside a lane: the write of one group lands in the same clock edge at which the next
//     group's read is captured, and a block RAM read returns the pre-write value, so two consecutive
//     groups on the same bin must forward.  `la/lw/lv` carry the last written address and value and
//     are compared against the address of the write in progress, which is exactly the colliding pair.
//   * frame boundary: cur_bank flips ON the eof group, while that group's read and write are still in
//     flight.  The read-address mux therefore selects with acc_is_A_rd (one clock late), otherwise the
//     last group of every frame reads the other bank, sees base = 0 and OVERWRITES its four bins with
//     1: four bins wrong per frame, and a constant frame reads back as all zeros (F36).
//   * bank generation: every word holds {epoch, count} with a 12-bit epoch, so a bin not written this
//     frame reads as 0 without any 256-entry clear pass, and 4096 frames must pass before an epoch can
//     alias.  Unwritten words are initialised to zero, so they read 0 in simulation and in hardware.
//   * cross-frame LUT ownership: the finished bank is frozen for a whole frame, so the consumer's
//     readback can never race the accumulating frame.  R47 relied on an inter-frame gap for this.
//
// No back-pressure: every group presented is consumed.  W % 4 == 0.
`default_nettype none

module hist4 #(
    parameter integer CW = 24
) (
    input  wire          clk,
    input  wire          rst,
    input  wire          i_flush,        // abandon the frame in progress, keep the bank phase
    input  wire          i_valid,
    input  wire          i_sof,          // first group of a frame
    input  wire          i_eof,          // last group of a frame
    input  wire [31:0]   i_d,            // four gray pixels, lane 0 = first in time
    output reg           frame_done,     // one-clock pulse, after the last write has landed
    output reg  [7:0]    done_epoch,
    output reg  [31:0]   done_frame_id,
    output reg  [CW-1:0] total_pixels,
    output reg  [7:0]    min_value,
    output reg  [CW-1:0] min_pixels,
    output reg           const_frame,
    input  wire          rd_en,
    input  wire [7:0]    rd_addr,
    output reg  [CW-1:0] rd_data,        // frame histogram, two-clock read latency
    input  wire [7:0]    rd_exp_epoch,
    output wire          rd_stale,
    output wire [7:0]    cur_epoch,
    output reg  [31:0]   frame_cnt
);

    localparam integer EW = 12;           // generation width: 4096 frames before any wrap
    localparam integer DW = CW + EW;      // {epoch, count}

    reg [7:0]    bin_r  [0:3];            // registered pixel bin (RMW read address)
    wire [DW-1:0] rdqA   [0:3], rdqB [0:3];
    reg [7:0]    wr_ptr  [0:3], wr_ptr2 [0:3];
    reg          wr_v    [0:3], wr_v2 [0:3], wr_bank [0:3], wr_bank2 [0:3];
    reg [EW-1:0] wr_ep   [0:3], wr_ep2 [0:3];
    reg [8:0]    la      [0:3];           // {bank, bin} of the previous write
    reg [CW-1:0] lw      [0:3];
    reg          lv      [0:3];

    reg [EW-1:0] epoch_cnt, done_epoch_f;
    reg          cur_bank;                // bank the current frame accumulates into
    assign cur_epoch = epoch_cnt[7:0];

    reg [3:0]    pend;                    // cycles to let the last writes land after eof

    wire acc_is_A = ~cur_bank;            // the finished bank is ~cur_bank

    // R48-2 FIX (found by the offline bench, one frame boundary per frame): cur_bank flips ON the eof
    // group, but that group's read-modify-write is still in flight -- its read happens one clock later
    // and its write two clocks later.  Selecting the read address with the NEW acc_is_A made that one
    // group read the wrong bank, so its four pixels saw base = 0 and OVERWROTE their bins with 1:
    // exactly four histogram bins wrong per frame, each off by minus one, with one bin up by one (the
    // lane that happened to forward).  The read stage therefore uses the bank value of the cycle the
    // group was presented in.  The write stage already uses wr_bank2, and the readback is unaffected
    // because it only starts after frame_done (pend = 4 clocks later).
    reg acc_is_A_rd;                      // acc_is_A delayed one clock: the RMW read stage
    always @(posedge clk) begin
        if (rst) acc_is_A_rd <= 1'b1; else acc_is_A_rd <= acc_is_A;
    end

    wire [7:0] v0  = i_d[ 7: 0];
    wire [7:0] v1d = i_d[15: 8];
    wire [7:0] v2d = i_d[23:16];
    wire [7:0] v3d = i_d[31:24];

    wire [7:0] gm01 = (v0 < v1d) ? v0 : v1d;
    wire [7:0] gm23 = (v2d < v3d) ? v2d : v3d;
    wire [7:0] gmin = (gm01 < gm23) ? gm01 : gm23;
    wire [2:0] gcnt = ((v0  == gmin) ? 3'd1 : 3'd0) + ((v1d == gmin) ? 3'd1 : 3'd0) +
                      ((v2d == gmin) ? 3'd1 : 3'd0) + ((v3d == gmin) ? 3'd1 : 3'd0);

    // ---------------------------------------------------------------- memory invalidation (F41)
    // The counters are stamped with the frame epoch and a word is trusted only when its stamp matches
    // done_epoch_f.  That is enough WHILE the arrays only ever contain words of this power-up, but a
    // synchronous reset used to clear epoch_cnt and not the arrays: the first frames after a reset then
    // reused epoch values that the pre-reset words still carried, those words were adopted as the new
    // frame's own counts (the build summed 30720 pixels for a 15360-pixel frame) and the LUT built from
    // that histogram was all-zero or saturated while o_lut_rdy was legitimately high.
    // The arrays are therefore zeroed across 256 clocks after every reset, and again when the epoch
    // counter is about to wrap (so a wrapped epoch cannot meet a word of the previous generation).
    // While the sweep runs no pixel is accumulated and no frame completes: the ownership block simply
    // never sees a table for the discarded frame, and the mapper keeps using the table it already has.
    reg        clr_busy;
    reg [7:0]  clr_addr;
    // A bench that predates this port leaves it unconnected (Z); `rst | Z` would be X and would stop
    // the accumulation block entirely, so the flush is tested with case equality: 0 for Z, and exactly
    // i_flush for a driven input (which is what the top always is).
    wire       fl       = (i_flush === 1'b1);
    // A frame whose sof fell inside the sweep loses its head, so it must be dropped whole: the mark is
    // set at that sof and cleared only by the next one, which keeps a half-accumulated frame from
    // completing with a truncated histogram (that produced a published all-zero table).
    reg        frame_dying;
    // The first group of a frame arrives on the same clock as its sof, when frame_dying still holds the
    // PREVIOUS value, so the sof clock must be judged by clr_busy alone -- otherwise every frame would
    // lose its first group of pixels (4 counts, enough to shift cdf_min and move a few hundred outputs
    // by one level).
    wire       acc_en   = i_valid && !clr_busy && (!frame_dying || i_sof);
    wire       wrap_now = i_valid && i_sof && !clr_busy && (epoch_cnt == {EW{1'b1}});

    always @(posedge clk) begin
        if (rst)               frame_dying <= 1'b1;
        else if (i_valid && i_sof) frame_dying <= clr_busy | wrap_now;
    end

    always @(posedge clk) begin
        if (rst) begin
            clr_busy <= 1'b1; clr_addr <= 8'd0;
        end else if (clr_busy) begin
            clr_addr <= clr_addr + 8'd1;
            if (clr_addr == 8'hFF) clr_busy <= 1'b0;
        end else if (wrap_now) begin
            clr_busy <= 1'b1; clr_addr <= 8'd0;
        end
    end

    // ---------------------------------------------------------------- lanes
    genvar L;
    generate
        for (L = 0; L < 4; L = L + 1) begin : lane
            (* ram_style = "bram" *) reg [DW-1:0] memA [0:255];
            (* ram_style = "bram" *) reg [DW-1:0] memB [0:255];
            integer init_bin;
            initial begin
                for (init_bin=0;init_bin<256;init_bin=init_bin+1) begin
                    memA[init_bin]={DW{1'b0}};
                    memB[init_bin]={DW{1'b0}};
                end
            end


            wire [7:0] bin = i_d[L*8 +: 8];

            // combinational address mux in front of each block: one read statement per array
            // accumulating bank -> the RMW bin; finished bank -> the consumer's rd_addr
            // NOTE: the accumulating/finished roles here come from acc_is_A_rd, not acc_is_A, so that
            // the last group of a frame still reads the bank it is writing into (see the fix above).
            wire [7:0] aA = acc_is_A_rd ? bin_r[L] : rd_addr;
            wire [7:0] aB = acc_is_A_rd ? rd_addr   : bin_r[L];

            reg [DW-1:0] ram_qA, ram_qB;
            reg read_reset;
            always @(posedge clk) begin
                ram_qA <= memA[aA];
                ram_qB <= memB[aB];
                read_reset <= rst;
            end
            assign rdqA[L] = read_reset ? {DW{1'b0}} : ram_qA;
            assign rdqB[L] = read_reset ? {DW{1'b0}} : ram_qB;

            // ---- RMW: stage 1 registers the pixel bin, stage 2 lines the write up with the read data
            wire [DW-1:0] g    = wr_bank2[L] ? rdqB[L] : rdqA[L];
            wire [CW-1:0] base = (g[DW-1:CW] == wr_ep2[L]) ? g[CW-1:0] : {CW{1'b0}};
            wire [CW-1:0] fwd  = (lv[L] && (la[L] == {wr_bank2[L], wr_ptr2[L]})) ? lw[L] : base;
            wire [CW-1:0] nv   = fwd + 1'b1;
            // Dedicated RAM write process; same enable as original sequential branch.
            always @(posedge clk) begin
                if (!(rst | fl)) begin
                    if (clr_busy) begin
                        // invalidation sweep: both banks of this lane, one address per clock.  It has
                        // priority over the RMW write, and any write it displaces is zeroed by a later
                        // sweep step because the sweep starts at 0 and only moves forward.
                        memA[clr_addr] <= {DW{1'b0}};
                        memB[clr_addr] <= {DW{1'b0}};
                    end else if (wr_v2[L]) begin
                        if (wr_bank2[L]) memB[wr_ptr2[L]] <= {wr_ep2[L], nv};
                        else             memA[wr_ptr2[L]] <= {wr_ep2[L], nv};
                    end

                end
            end



            always @(posedge clk) begin
                if (rst | fl) begin
                    bin_r[L] <= 8'd0;
                    wr_ptr[L] <= 8'd0; wr_v[L] <= 1'b0; wr_bank[L] <= 1'b0; wr_ep[L] <= {EW{1'b0}};
                    wr_ptr2[L] <= 8'd0; wr_v2[L] <= 1'b0; wr_bank2[L] <= 1'b0; wr_ep2[L] <= {EW{1'b0}};
                    la[L] <= 9'd0; lw[L] <= {CW{1'b0}}; lv[L] <= 1'b0;
                end else begin
                    if (acc_en) begin
                        bin_r[L]   <= bin;
                        wr_ptr[L]  <= bin;
                        wr_bank[L] <= ~acc_is_A;
                        wr_ep[L]   <= i_sof ? (epoch_cnt + 1'b1) : epoch_cnt;
                    end
                    wr_v[L] <= acc_en;

                    wr_ptr2[L] <= wr_ptr[L]; wr_bank2[L] <= wr_bank[L];
                    wr_ep2[L]  <= wr_ep[L];  wr_v2[L]    <= wr_v[L];

                    la[L] <= {wr_bank2[L], wr_ptr2[L]};
                    lw[L] <= nv;
                    lv[L] <= wr_v2[L];
                end
            end
        end
    endgenerate

    // ---------------------------------------------------------------- readback (two clocks)
    // Read the FINISHED bank only, so the accumulating frame cannot disturb the snapshot.  This is what
    // makes cross-frame LUT ownership safe without relying on an inter-frame gap.
    wire [DW-1:0] p0 = acc_is_A ? rdqB[0] : rdqA[0];
    wire [DW-1:0] p1 = acc_is_A ? rdqB[1] : rdqA[1];
    wire [DW-1:0] p2 = acc_is_A ? rdqB[2] : rdqA[2];
    wire [DW-1:0] p3 = acc_is_A ? rdqB[3] : rdqA[3];

    wire [CW-1:0] s0 = (p0[DW-1:CW] == done_epoch_f) ? p0[CW-1:0] : {CW{1'b0}};
    wire [CW-1:0] s1 = (p1[DW-1:CW] == done_epoch_f) ? p1[CW-1:0] : {CW{1'b0}};
    wire [CW-1:0] s2 = (p2[DW-1:CW] == done_epoch_f) ? p2[CW-1:0] : {CW{1'b0}};
    wire [CW-1:0] s3 = (p3[DW-1:CW] == done_epoch_f) ? p3[CW-1:0] : {CW{1'b0}};
    wire [CW+1:0] rsum = {2'b00, s0} + {2'b00, s1} + {2'b00, s2} + {2'b00, s3};

    // Registered output.  It cannot be combinational: lut_build prefetches, so in the cycle it consumes
    // rd_data its rd_addr already points at the NEXT bin, and a combinational sum would return that
    // bin's count.  Two clocks of latency (block read + this register); lut_build carries the matching
    // extra stage.
    reg rd_en_d;
    always @(posedge clk) begin
        if (rst) begin
            rd_data <= {CW{1'b0}};
            rd_en_d <= 1'b0;
        end else begin
            rd_en_d <= rd_en;
            if (rd_en_d) rd_data <= rsum[CW-1:0];
        end
    end

    assign rd_stale = (rd_exp_epoch != cur_epoch);

    // ---------------------------------------------------------------- statistics and identity
    always @(posedge clk) begin
        if (rst) begin
            frame_done <= 1'b0; done_epoch <= 8'd0; done_frame_id <= 32'd0;
            total_pixels <= {CW{1'b0}}; min_value <= 8'hFF; min_pixels <= {CW{1'b0}};
            const_frame <= 1'b0; epoch_cnt <= {EW{1'b0}}; frame_cnt <= 32'd0;
            done_epoch_f <= {EW{1'b0}}; cur_bank <= 1'b0; pend <= 4'd0;
        end else if (fl) begin
            // abandon the frame; the bank phase (cur_bank / epoch_cnt / done_epoch_f) survives, because
            // the mapper and the ownership block are not reset by a flush and the published table must
            // name the bank this block actually wrote.
            frame_done <= 1'b0; pend <= 4'd0;
            total_pixels <= {CW{1'b0}}; min_value <= 8'hFF; min_pixels <= {CW{1'b0}};
            const_frame <= 1'b0;
        end else begin
            frame_done <= 1'b0;

            if (acc_en) begin
                if (i_sof) begin
                    total_pixels <= {{(CW-3){1'b0}}, 3'd4};
                    min_value    <= gmin;
                    min_pixels   <= gcnt;
                    epoch_cnt    <= epoch_cnt + 1'b1;
                end else begin
                    total_pixels <= total_pixels + {{(CW-3){1'b0}}, 3'd4};
                    if (gmin < min_value) begin
                        min_value  <= gmin;
                        min_pixels <= gcnt;
                    end else if (gmin == min_value) begin
                        min_pixels <= min_pixels + gcnt;
                    end
                end

                if (i_eof) begin
                    done_frame_id <= frame_cnt + 32'd1;
                    frame_cnt     <= frame_cnt + 32'd1;
                    done_epoch_f  <= i_sof ? (epoch_cnt + 1'b1) : epoch_cnt;
                    done_epoch    <= i_sof ? (epoch_cnt[7:0] + 8'd1) : epoch_cnt[7:0];
                    cur_bank      <= ~cur_bank;      // freeze this frame's bank for the readback
                    pend          <= 4'd4;           // let the last RMW writes land
                end
            end

            if (pend != 4'd0) begin
                pend <= pend - 4'd1;
                if (pend == 4'd1) begin
                    frame_done  <= 1'b1;
                    const_frame <= (min_pixels == total_pixels);
                end
            end
        end
    end

endmodule

`default_nettype wire