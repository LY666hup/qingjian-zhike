`timescale 1ns/1ps
module jpeg_color_raster_encoder #(
 parameter integer WIDTH=1920,HEIGHT=720,MATRIX_709=1
)(
 input wire clk,rst_n,i_start,
 input wire [127:0] i_yuyv,input wire i_valid,i_sof,i_eol,output wire i_ready,
 output wire [7:0] o_byte,output wire o_valid,o_sof,o_eof,input wire o_ready,
 output wire o_done,output wire [31:0] o_bytes,output wire o_error
);
 reg busy;
 wire [31:0] pixels;
 wire valid,ready,raster_ready,raster_error,codec_error;
 always @(posedge clk or negedge rst_n)
  if(!rst_n)busy<=0;else if(o_done)busy<=0;else if(i_start)busy<=1;
 assign i_ready=busy && raster_ready;
 assign o_error=raster_error || codec_error;
 raster_yuyv_to_mcu422 #(.WIDTH(WIDTH),.HEIGHT(HEIGHT),.MATRIX_709(MATRIX_709)) raster(
  .clk(clk),.rst_n(rst_n),.i_yuyv(i_yuyv),.i_valid(i_valid && busy),.i_sof(i_sof),.i_eol(i_eol),.i_ready(raster_ready),
  .o_pixels(pixels),.o_valid(valid),.o_ready(ready),.o_protocol_error(raster_error));
 jpeg_color_block_encoder #(.IMAGE_WIDTH(WIDTH),.IMAGE_HEIGHT(HEIGHT)) codec(
  .clk(clk),.rst_n(rst_n),.i_start(i_start && !busy),.i_pixels(pixels),.i_valid(valid),.i_ready(ready),
  .o_byte(o_byte),.o_valid(o_valid),.o_sof(o_sof),.o_eof(o_eof),.o_ready(o_ready),.o_done(o_done),.o_bytes(o_bytes),.o_error(codec_error));
endmodule
