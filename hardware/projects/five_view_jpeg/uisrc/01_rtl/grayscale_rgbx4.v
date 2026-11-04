`timescale 1ns / 1ps

// Four RGBX8888 pixels per clock. Pixel 0 occupies the low 32 bits.
// Data and frame/line markers are registered together for one clock.
module grayscale_rgbx4 (
    input  wire         I_clk,
    input  wire         I_rst_n,
    input  wire [127:0] I_tdata,
    input  wire         I_tvalid,
    input  wire         I_tuser,
    input  wire         I_tlast,
    output reg  [127:0] O_tdata,
    output reg          O_tvalid,
    output reg          O_tuser,
    output reg          O_tlast
);
    function [7:0] gray8;
        input [7:0] r;
        input [7:0] g;
        input [7:0] b;
        reg [15:0] weighted;
        begin
            weighted = (16'd77 * r) + (16'd150 * g) +
                       (16'd29 * b) + 16'd128;
            gray8 = weighted[15:8];
        end
    endfunction

    wire [127:0] gray_data;
    genvar pix;
    generate
        for (pix = 0; pix < 4; pix = pix + 1) begin : g_pixel
            wire [31:0] rgbx;
            wire [7:0] y;
            assign rgbx = I_tdata[pix*32 +: 32];
            assign y = gray8(rgbx[23:16], rgbx[15:8], rgbx[7:0]);
            assign gray_data[pix*32 +: 32] = {rgbx[31:24], y, y, y};
        end
    endgenerate

    always @(posedge I_clk or negedge I_rst_n) begin
        if (!I_rst_n) begin
            O_tdata  <= 128'd0;
            O_tvalid <= 1'b0;
            O_tuser  <= 1'b0;
            O_tlast  <= 1'b0;
        end else begin
            O_tdata  <= gray_data;
            O_tvalid <= I_tvalid;
            O_tuser  <= I_tuser;
            O_tlast  <= I_tlast;
        end
    end
endmodule
