`timescale 1ns/1ps
module qj_reset_sync(input wire clk,input wire arst,output wire rst);
 (* async_reg="true" *) reg [1:0] pipe;
 always @(posedge clk or posedge arst)
  if(arst) pipe<=2'b11; else pipe<={pipe[0],1'b0};
 assign rst=pipe[1];
endmodule

// Private pixel FIFO. Stop drains the DDR producer but discards output;
// restart waits for an actual new mosaic SOF, rather than replaying old prefetch.
// Common cold reset for the whole FIFO; no single-sided reset claim.
module qj_preview_output(
 input wire ddr_clk,input wire ddr_rst,input wire session_reset,
 input wire [127:0] s_data,input wire s_valid,output wire s_ready,
 input wire s_sof,input wire s_eol,input wire s_eof,
 input wire pix_clk,input wire pix_rst,
 output wire [31:0] m_data,output wire m_valid,input wire m_ready,
 output wire m_sof,output wire m_eol
);
 wire wrst,rrst;
 // Either side's reset invalidates the entire FIFO, including prefetch.
 // This is FIFO coherence only, not recovery of outstanding DDR transactions.
 wire common_reset=ddr_rst | pix_rst | session_reset;
 qj_reset_sync wr(ddr_clk,common_reset,wrst);
 qj_reset_sync rd(pix_clk,common_reset,rrst);
 reg armed;
 wire fq_ready,fq_valid;
 wire [130:0] fq_data;
 wire allow_frame=armed|s_sof;
 assign s_ready=wrst?1'b1:(allow_frame?fq_ready:1'b1);
 always @(posedge ddr_clk) begin
  if(wrst) armed<=0;
  else if(s_valid && s_ready && s_sof) armed<=1;
 end
 wire [130:0] din={s_eof,s_eol,s_sof,s_data};
 reg held_valid;
 reg [130:0] held;
 reg [1:0] quarter;
 wire finish_word=held_valid && m_ready && quarter==3;
 wire pop_ready=!held_valid || finish_word;
 qj_async_fifo #(.WIDTH(131),.ADDR_BITS(9)) fifo(
  .wclk(ddr_clk),.wrst(wrst),.s_valid(s_valid && !wrst && allow_frame),
  .s_ready(fq_ready),.s_data(din),.rclk(pix_clk),.rrst(rrst),
  .m_valid(fq_valid),.m_ready(pop_ready),.m_data(fq_data));
 always @(posedge pix_clk) begin
  if(rrst) begin held_valid<=0;held<=0;quarter<=0;end
  else if(pop_ready) begin
   held_valid<=fq_valid;
   if(fq_valid) held<=fq_data;
   quarter<=0;
  end else if(held_valid && m_ready) quarter<=quarter+1'b1;
 end
 assign m_valid=held_valid && !rrst;
 assign m_data=held[quarter*32+:32];
 assign m_sof=held[128] && quarter==0;
 assign m_eol=held[129] && quarter==3;
endmodule

// Diagnostic identity lives only in the unused mosaic region (x1280,y360),
// never in the five image tiles. Raw identity bytes go into Y; U/V remain128.
// 40 bytes: QJ5V, version1, ready mask, reserved2, composite32LE,
// five observed source-frame32LE, DDR-fault32LE, XOR8(first36), reserved3.
// These are local observation IDs, NOT exposure counters or a session namespace.
module qj_preview_metadata(
 input wire clk,input wire rst,
 input wire [127:0] s_data,input wire s_valid,output wire s_ready,
 input wire s_sof,input wire s_eol,input wire s_eof,
 input wire [159:0] view_frames,input wire [4:0] view_ready,input wire [31:0] faults,
 output reg [127:0] m_data,output wire m_valid,input wire m_ready,
 output wire m_sof,output wire m_eol,output wire m_eof
);
 reg [8:0] block_x;
 reg [10:0] row_y;
 reg [31:0] composite;
 reg [287:0] payload;
 reg [7:0] checksum;
 wire [319:0] message={24'b0,checksum,payload};
 integer j,k;
 always @* begin
  checksum=0;
  for(j=0;j<36;j=j+1)checksum=checksum^payload[j*8+:8];
  m_data=s_data;
  if(row_y==360 && block_x>=160 && block_x<165)
   for(k=0;k<8;k=k+1) begin
    m_data[k*16+:8]=message[((block_x-160)*8+k)*8+:8];
    m_data[k*16+8+:8]=8'd128;
   end
 end
 assign s_ready=m_ready;
 assign m_valid=s_valid && !rst;
 assign m_sof=s_sof;assign m_eol=s_eol;assign m_eof=s_eof;
 always @(posedge clk) begin
  if(rst) begin block_x<=0;row_y<=0;composite<=0;payload<=0;end
  else if(s_valid && s_ready) begin
   if(s_sof) begin
    payload<={faults,view_frames,composite+32'd1,16'b0,3'b0,view_ready,8'd1,32'h56354a51};
    composite<=composite+1'b1;
    block_x<=1;row_y<=0;
   end else if(s_eof) begin block_x<=0;row_y<=0;end
   else if(s_eol) begin block_x<=0;row_y<=row_y+1'b1;end
   else block_x<=block_x+1'b1;
  end
 end
endmodule
