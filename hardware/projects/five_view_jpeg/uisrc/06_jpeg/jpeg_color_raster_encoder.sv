`timescale 1ns/1ps
// Exclusive alternate wrapper;never compile with the original same-name file.
module jpeg_color_raster_encoder #(
 parameter integer WIDTH=1920,HEIGHT=1080,MATRIX_709=1,WORKERS=6
)(
 input wire clk,rst_n,i_start,input wire[127:0]i_yuyv,input wire i_valid,i_sof,i_eol,
 output wire i_ready,output wire[7:0]o_byte,output wire o_valid,o_sof,o_eof,input wire o_ready,
 output wire o_done,output wire[31:0]o_bytes,output wire o_error
);
 reg busy;
 wire raster_ready;
 always@(posedge clk or negedge rst_n)
  if(!rst_n)busy<=0;else if(o_done)busy<=0;else if(i_start)busy<=1;
 assign i_ready=busy&&raster_ready;
 wire[31:0]pixels;wire pv,pr,pe,ce;
 raster_yuyv_to_mcu422 #(.WIDTH(WIDTH),.HEIGHT(HEIGHT),.MATRIX_709(MATRIX_709)) raster(
  .clk(clk),.rst_n(rst_n),.i_yuyv(i_yuyv),.i_valid(i_valid&&busy),.i_sof(i_sof),.i_eol(i_eol),.i_ready(raster_ready),
  .o_pixels(pixels),.o_valid(pv),.o_ready(pr),.o_protocol_error(pe));
 jpeg_color_mcu_pool #(.IMAGE_WIDTH(WIDTH),.IMAGE_HEIGHT(HEIGHT),.WORKERS(WORKERS)) pool(
  .clk(clk),.rst_n(rst_n),.i_start(i_start&&!busy),.i_pixels(pixels),.i_valid(pv),.i_ready(pr),
  .o_byte(o_byte),.o_valid(o_valid),.o_sof(o_sof),.o_eof(o_eof),.o_ready(o_ready),
  .o_done(o_done),.o_bytes(o_bytes),.o_error(ce));
 assign o_error=pe|ce;
endmodule
