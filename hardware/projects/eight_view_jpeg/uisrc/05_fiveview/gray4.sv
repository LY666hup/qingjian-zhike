// gray4.sv -- R46/P1.  Four-pixel-per-clock luminance, bit-exact with rgb2gray.sv.
//
// Why: the vendor VID008 video path only ever presents pixels in parallel groups
// (128-bit RGBX x4 at the ISP output, 32-bit YUYV x2 after DDR).  There is no
// 1 px/clk stage, so the R44 core cannot be dropped in.  This module is the first
// stage of the 4 px/clk variant and is required to be *bit-identical* per pixel to
// the frozen R44 reference (rgb2gray.sv), not merely "close enough".
//
//   Y = (77*R + 150*G + 29*B + 128) >> 8      (full range, coefficients sum to 256)
//
// Lane layout follows the vendor convention (`rgb_to_yuv422_quad.v` L26):
//   i_data[lane*32 + 23 : lane*32 + 16] = R
//   i_data[lane*32 + 15 : lane*32 +  8] = G
//   i_data[lane*32 +  7 : lane*32 +  0] = B      (bits 31:24 = 0xFF padding, ignored)
//   lane 0 is the first pixel in time within the group.
// Output o_y[lane] = gray of lane, same group order.
//
// Latency: two clocks, identical to rgb2gray (stage 1 weighted sum, stage 2 shift).
// No back-pressure: the source of this stream (ISP output) has no ready signal.
`default_nettype none

module gray4 #(
    parameter integer COEF_R = 77,
    parameter integer COEF_G = 150,
    parameter integer COEF_B = 29,
    parameter integer SHIFT  = 8
) (
    input  wire                clk,
    input  wire                rst,      // synchronous, active high
    input  wire                i_valid,
    input  wire                i_sof,    // first group of a frame
    input  wire                i_last,   // last group of a line
    input  wire [4*32-1:0]     i_data,   // {pix3, pix2, pix1, pix0}
    output reg                 o_valid,
    output reg                 o_sof,
    output reg                 o_last,
    output reg  [4*8-1:0]      o_y       // {y3, y2, y1, y0}
);

    genvar lane;

    // stage 1: four independent weighted sums (worst case 256*255+128 = 65408 -> 17 bits)
    reg [17:0] acc  [0:3];
    reg        v1, sof1, last1;

    always @(posedge clk) begin : stage1
        integer k;
        if (rst) begin
            v1 <= 1'b0; sof1 <= 1'b0; last1 <= 1'b0;
            for (k = 0; k < 4; k = k + 1) acc[k] <= 18'd0;
        end else begin
            v1    <= i_valid;
            sof1  <= i_valid & i_sof;
            last1 <= i_valid & i_last;
            if (i_valid) begin
                for (k = 0; k < 4; k = k + 1)
                    acc[k] <= COEF_R * i_data[k*32 + 23 -: 8] +
                              COEF_G * i_data[k*32 + 15 -: 8] +
                              COEF_B * i_data[k*32 +  7 -: 8] +
                              (1 << (SHIFT - 1));
            end
        end
    end

    // stage 2: shift + saturation, per lane, same rule as rgb2gray (acc[17:SHIFT] > 255)
    always @(posedge clk) begin : stage2
        integer k;
        if (rst) begin
            o_valid <= 1'b0; o_sof <= 1'b0; o_last <= 1'b0; o_y <= 32'd0;
        end else begin
            o_valid <= v1;
            o_sof   <= sof1;
            o_last  <= last1;
            if (v1) begin
                for (k = 0; k < 4; k = k + 1) begin
                    if (acc[k][17:SHIFT] > 18'd255) o_y[k*8 +: 8] <= 8'd255;
                    else                            o_y[k*8 +: 8] <= acc[k][SHIFT +: 8];
                end
            end
        end
    end

endmodule

`default_nettype wire