// div_pipe.sv -- R29.  Fully pipelined restoring divider, one result per clock.
//
// Contract:
//   * in_valid/in_num/in_den accepted every clock (no back-pressure);
//   * out_valid/out_quo/out_rem appear exactly DEPTH clocks after the input clock;
//   * DEPTH = W (one bit per stage), latency = W cycles, throughput = 1/clk;
//   * division by zero produces all-ones quotient (defined, not X);
//   * rounding is NOT done here: the caller adds den/2 to the numerator beforehand, which keeps
//     this block exactly equal to floor((num + den/2)/den) (verified in R26 by software).
`default_nettype none

module div_pipe #(
    parameter integer W = 32
) (
    input  wire          clk,
    input  wire          rst,
    input  wire          in_valid,
    input  wire [W-1:0]  in_num,
    input  wire [W-1:0]  in_den,
    output wire          out_valid,
    output wire [W-1:0]  out_quo,
    output wire [W-1:0]  out_rem
);

    reg [W:0]   rem [0:W];      // remainder, one extra bit for the compare
    reg [W-1:0] quo [0:W];
    reg [W-1:0] den [0:W];
    reg [W-1:0] nsr [0:W];      // numerator shift register, MSB consumed first
    reg         vld [0:W];

    integer i;

    always @(posedge clk) begin
        if (rst) begin
            for (i = 0; i <= W; i = i + 1) begin
                rem[i] <= {(W+1){1'b0}}; quo[i] <= {W{1'b0}};
                den[i] <= {W{1'b0}}; nsr[i] <= {W{1'b0}}; vld[i] <= 1'b0;
            end
        end else begin
            // stage 0: sample the operands
            nsr[0] <= in_num;
            den[0] <= in_den;
            rem[0] <= {(W+1){1'b0}};
            quo[0] <= {W{1'b0}};
            vld[0] <= in_valid;
            for (i = 1; i <= W; i = i + 1) begin
                nsr[i] <= {nsr[i-1][W-2:0], 1'b0};
                den[i] <= den[i-1];
                vld[i] <= vld[i-1];
                if ({rem[i-1][W-1:0], nsr[i-1][W-1]} >= {1'b0, den[i-1]}) begin
                    rem[i] <= {rem[i-1][W-1:0], nsr[i-1][W-1]} - {1'b0, den[i-1]};
                    quo[i] <= {quo[i-1][W-2:0], 1'b1};
                end else begin
                    rem[i] <= {rem[i-1][W-1:0], nsr[i-1][W-1]};
                    quo[i] <= {quo[i-1][W-2:0], 1'b0};
                end
            end
        end
    end

    assign out_valid = vld[W];
    assign out_quo   = (den[W] == {W{1'b0}}) ? {W{1'b1}} : quo[W];
    assign out_rem   = rem[W][W-1:0];

endmodule

`default_nettype wire
