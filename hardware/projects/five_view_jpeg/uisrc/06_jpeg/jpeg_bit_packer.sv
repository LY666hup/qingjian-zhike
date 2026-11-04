`timescale 1ns/1ps
// Independent lower-logic candidate, same jpeg_bit_packer module ABI. Select
// exactly one source. Valid bits are MSB-aligned, unused bits are always zero.
// Byte output and pop are fixed wiring; only the incoming32bit token aligns.
module jpeg_bit_packer(
 input wire clk,rst_n,i_clear,input wire i_valid,input wire[31:0]i_bits,
 input wire[5:0]i_length,output wire i_ready,input wire i_flush,
 output wire[7:0]o_byte,output wire o_valid,input wire o_ready,output reg o_flushed
);
 reg[63:0]reservoir;reg[6:0]count;reg stuffing,flushing;
 wire[7:0]current_byte=reservoir[63:56];
 assign o_valid=stuffing||count>=8;assign o_byte=stuffing?8'd0:current_byte;
 assign i_ready=count<=32&&!flushing&&!i_clear;
 wire remove_byte=o_valid&&o_ready;
 wire remove_entropy=remove_byte&&!stuffing;
 wire append_bits=i_valid&&i_ready&&!i_flush;
 wire[6:0]left_count=count-(remove_entropy?7'd8:7'd0);
 wire[63:0]left_bits=remove_entropy?(reservoir<<8):reservoir;
 wire[31:0]masked=i_bits&(32'hffffffff>>(32-i_length));
 wire[63:0]aligned={32'd0,masked}<<(64-left_count-i_length);
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin reservoir<=0;count<=0;stuffing<=0;flushing<=0;o_flushed<=0;end
  else if(i_clear)begin reservoir<=0;count<=0;stuffing<=0;flushing<=0;o_flushed<=0;end
  else begin
   o_flushed<=0;count<=left_count+(append_bits?i_length:7'd0);
   reservoir<=left_bits|(append_bits?aligned:64'd0);
   if(remove_byte)begin if(stuffing)stuffing<=0;else if(current_byte==255)stuffing<=1;end
   if(i_ready&&i_flush)flushing<=1;
   if(flushing)begin
    if(count>0&&count<8&&!stuffing)begin reservoir<=reservoir|{(8'hff>>count),56'd0};count<=8;end
    if(count==0&&!stuffing)begin flushing<=0;o_flushed<=1;end
   end
  end
 end
endmodule
