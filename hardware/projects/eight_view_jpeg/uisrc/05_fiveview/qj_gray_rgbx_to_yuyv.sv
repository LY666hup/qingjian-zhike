`timescale 1ns/1ps
// Exact for R=G=B, selected only for the six real grayscale branch outputs.
// Preserves the original converter's four-cycle data/valid/SOF/EOL latency.
module qj_gray_rgbx_to_yuyv(
 input wire clk,rst_n,input wire [127:0] i_data,
 input wire i_de,i_user_sof,i_last,
 output wire [63:0] o_data,output wire o_de,o_user_sof,o_last);
 wire [31:0] gray={i_data[103:96],i_data[71:64],i_data[39:32],i_data[7:0]};
 wire i_valid=i_de,i_sof=i_user_sof,i_eol=i_last;
 wire [63:0] data;wire valid,sof,eol;
 assign o_data=data;assign o_de=valid;assign o_user_sof=sof;assign o_last=eol;
 reg [63:0] p0,p1,p2,p3;
 reg [3:0] vp,sp,ep;
 function [7:0] limited_gray(input [7:0] g);
  reg [17:0] x,total;
  begin x={10'b0,g};total=(x<<8)-(x<<5)-(x<<2)+18'd4096;limited_gray=total[15:8];end
 endfunction
 always @(posedge clk or negedge rst_n)begin
  if(!rst_n)begin p0<=0;p1<=0;p2<=0;p3<=0;vp<=0;sp<=0;ep<=0;end
  else begin
   p0<={8'd128,limited_gray(gray[31:24]),8'd128,limited_gray(gray[23:16]),
         8'd128,limited_gray(gray[15:8]),8'd128,limited_gray(gray[7:0])};
   p1<=p0;p2<=p1;p3<=p2;
   vp<={vp[2:0],i_valid};sp<={sp[2:0],i_sof};ep<={ep[2:0],i_eol};
  end
 end
 assign data=p3;assign valid=vp[3];assign sof=sp[3];assign eol=ep[3];
endmodule
