`timescale 1ns/1ps
// Backpressured native YUYV raster -> baseline 4:2:2 MCU order Y0,Y1,Cb,Cr.
// Two 8-row banks. A bank is released only after its final word is prefetched.
// MATRIX_709=1 matches current rgb_to_yuv422_quad coefficient family.
module raster_yuyv_to_mcu422 #(
 parameter integer WIDTH=1920,HEIGHT=720,MATRIX_709=1
)(
 input wire clk,rst_n,
 input wire [127:0] i_yuyv,input wire i_valid,i_sof,i_eol,output wire i_ready,
 output reg [31:0] o_pixels,output reg o_valid,input wire o_ready,
 output reg o_protocol_error
);
 `include "jpeg_range_tables.vh"
 localparam integer BPL=WIDTH/8,WORDS=BPL*8,MCUS=WIDTH/16,STRIPES=HEIGHT/8;
 localparam integer AW=$clog2(WORDS),CW=(BPL>1)?$clog2(BPL):1;
 localparam integer MW=(MCUS>1)?$clog2(MCUS):1,SW=(STRIPES>1)?$clog2(STRIPES):1;
 (* ram_style="bram" *) reg [127:0] memory0[0:WORDS-1];
 (* ram_style="bram" *) reg [127:0] memory1[0:WORDS-1];
 reg [1:0] full;
 reg wr_bank,rd_bank;
 reg [AW-1:0] wr_address;
 reg [CW-1:0] wr_col;
 reg [SW-1:0] wr_stripe;
 reg [MW-1:0] rd_mcu;
 reg [1:0] rd_component;
 reg [3:0] rd_beat;
 reg [127:0] q0,q1;
 reg pending,p_bank,p_half;
 reg [1:0] p_component;
 reg lookup_valid,sum_valid,lookup_sof,lookup_eol,sum_sof,sum_eol;
 reg[447:0]lookup_words;
 reg[255:0]sum_words;
 wire[447:0]lookup_next;
 wire[255:0]sum_next;
 wire[127:0]normalized;
 wire store_ready=!full[wr_bank];
 wire sum_advance=!sum_valid || store_ready;
 wire lookup_advance=!lookup_valid || sum_advance;
 wire advance=!o_valid || o_ready;
 wire req_ready=!pending || advance;
 wire request=req_ready && full[rd_bank];
 wire [AW-1:0] read_address=rd_beat[3:1]*BPL+rd_mcu*2+
                             (rd_component[1]?rd_beat[0]:rd_component[0]);
 wire last_request=rd_mcu==MCUS-1 && rd_component==3 && rd_beat==15;
 assign i_ready=rst_n && lookup_advance;
 wire write_request=rst_n && sum_valid && store_ready;
 function [7:0] clip(input signed [15:0] v);
  begin clip=v<0 ? 8'd0 : v>255 ? 8'd255 : v[7:0];end
 endfunction

 // Stage1:exact frozen lookup outputs. Stage2:exact signed16 arithmetic.
 // Clip/write RAM is a separate stage. No rounding/coefficient changes.
 for(genvar lane=0;lane<4;lane=lane+1)begin:range_pipeline
  wire[31:0]pair_word=i_yuyv[lane*32+:32];
  assign lookup_next[lane*112+:8]=expand_y(pair_word[7:0]);
  assign lookup_next[lane*112+8+:8]=expand_y(pair_word[23:16]);
  if(MATRIX_709)begin:bt709
   assign lookup_next[lane*112+16+:16]=y_from_cb(pair_word[15:8]);
   assign lookup_next[lane*112+32+:16]=y_from_cr(pair_word[31:24]);
   assign lookup_next[lane*112+48+:16]=cb_from_cb(pair_word[15:8]);
   assign lookup_next[lane*112+64+:16]=cb_from_cr(pair_word[31:24]);
   assign lookup_next[lane*112+80+:16]=cr_from_cb(pair_word[15:8]);
   assign lookup_next[lane*112+96+:16]=cr_from_cr(pair_word[31:24]);
  end else begin:bt601_range
   assign lookup_next[lane*112+16+:16]=0;
   assign lookup_next[lane*112+32+:16]=0;
   assign lookup_next[lane*112+48+:16]={8'b0,expand_c(pair_word[15:8])};
   assign lookup_next[lane*112+64+:16]=0;
   assign lookup_next[lane*112+80+:16]={8'b0,expand_c(pair_word[31:24])};
   assign lookup_next[lane*112+96+:16]=0;
  end
  wire signed[15:0]y_delta=$signed(lookup_words[lane*112+16+:16])+$signed(lookup_words[lane*112+32+:16]);
  assign sum_next[lane*64+:16]=$signed({8'b0,lookup_words[lane*112+:8]})+y_delta;
  assign sum_next[lane*64+32+:16]=$signed({8'b0,lookup_words[lane*112+8+:8]})+y_delta;
  assign sum_next[lane*64+16+:16]=(MATRIX_709?16'sd128:16'sd0)+$signed(lookup_words[lane*112+48+:16])+$signed(lookup_words[lane*112+64+:16]);
  assign sum_next[lane*64+48+:16]=(MATRIX_709?16'sd128:16'sd0)+$signed(lookup_words[lane*112+80+:16])+$signed(lookup_words[lane*112+96+:16]);
  assign normalized[lane*32+:32]={clip(sum_words[lane*64+48+:16]),clip(sum_words[lane*64+32+:16]),clip(sum_words[lane*64+16+:16]),clip(sum_words[lane*64+:16])};
 end
 // Payload registers have no reset mux;valid bits and metadata govern use.
 always@(posedge clk)begin
  if(rst_n && lookup_advance && i_valid)lookup_words<=lookup_next;
  if(rst_n && sum_advance && lookup_valid)sum_words<=sum_next;
 end
 always@(posedge clk or negedge rst_n)begin
  if(!rst_n)begin
   lookup_valid<=0;sum_valid<=0;lookup_sof<=0;lookup_eol<=0;sum_sof<=0;sum_eol<=0;
  end else begin
   if(lookup_advance)begin
    lookup_valid<=i_valid;
    if(i_valid)begin lookup_sof<=i_sof;lookup_eol<=i_eol;end
   end
   if(sum_advance)begin
    sum_valid<=lookup_valid;
    if(lookup_valid)begin sum_sof<=lookup_sof;sum_eol<=lookup_eol;end
   end
  end
 end
 function [31:0] samples(input [127:0] word_data,input [1:0] component,input high_half);
  begin
   case(component)
    0,1:samples=high_half ? {word_data[112+:8],word_data[96+:8],word_data[80+:8],word_data[64+:8]}:
                           {word_data[48+:8],word_data[32+:8],word_data[16+:8],word_data[0+:8]};
    2:samples={word_data[104+:8],word_data[72+:8],word_data[40+:8],word_data[8+:8]};
    default:samples={word_data[120+:8],word_data[88+:8],word_data[56+:8],word_data[24+:8]};
   endcase
  end
 endfunction
 always @(posedge clk)begin
  if(write_request && !wr_bank)memory0[wr_address]<=normalized;
  if(write_request && wr_bank)memory1[wr_address]<=normalized;
  if(request && !rd_bank)q0<=memory0[read_address];
  if(request && rd_bank)q1<=memory1[read_address];
 end
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin
   full<=0;wr_bank<=0;rd_bank<=0;wr_address<=0;wr_col<=0;wr_stripe<=0;
   rd_mcu<=0;rd_component<=0;rd_beat<=0;pending<=0;p_bank<=0;p_half<=0;p_component<=0;
   o_pixels<=0;o_valid<=0;o_protocol_error<=0;
  end else begin
   if(write_request)begin
    if(sum_sof!=(wr_stripe==0 && wr_address==0) || sum_eol!=(wr_col==BPL-1))o_protocol_error<=1;
    if(wr_col==BPL-1)wr_col<=0;else wr_col<=wr_col+1'b1;
    if(wr_address==WORDS-1)begin
     full[wr_bank]<=1;wr_address<=0;wr_bank<=!wr_bank;
     if(wr_stripe==STRIPES-1)wr_stripe<=0;else wr_stripe<=wr_stripe+1'b1;
    end else wr_address<=wr_address+1'b1;
   end
   if(req_ready)begin
    pending<=request;
    if(request)begin
     p_bank<=rd_bank;p_half<=rd_beat[0];p_component<=rd_component;
     if(last_request)begin
      full[rd_bank]<=0;rd_bank<=!rd_bank;rd_mcu<=0;rd_component<=0;rd_beat<=0;
     end else if(rd_beat==15)begin
      rd_beat<=0;
      if(rd_component==3)begin rd_component<=0;rd_mcu<=rd_mcu+1'b1;end
      else rd_component<=rd_component+1'b1;
     end else rd_beat<=rd_beat+1'b1;
    end
   end
   if(advance)begin
    o_valid<=pending;
    if(pending)o_pixels<=samples(p_bank?q1:q0,p_component,p_half);
   end
  end
 end
 initial if(WIDTH<16 || WIDTH%16 || HEIGHT<8 || HEIGHT%8)$error("Unsupported color MCU geometry");
endmodule
