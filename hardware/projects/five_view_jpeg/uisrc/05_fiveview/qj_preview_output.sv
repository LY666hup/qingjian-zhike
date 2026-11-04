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
// Retains the original QJ5V/1 first-five header at META_X; appends QJ8V/1.
// Both describe one SOF snapshot. Observation IDs are NOT exposure numbers.
module qj_preview_metadata #(
 parameter integer META_ROW=360,META_X=1280,ENABLE_WRITER_DIAG=0,ENABLE_ERROR_DIAG=0
)(
 input wire clk,input wire rst,
 input wire [127:0] s_data,input wire s_valid,output wire s_ready,
 input wire s_sof,input wire s_eol,input wire s_eof,
 input wire [255:0] view_frames,input wire [7:0] view_ready,input wire [31:0] faults,
 input wire [495:0] writer_diag,
 input wire [767:0] source_errors,
 output reg [127:0] m_data,output wire m_valid,input wire m_ready,
 output wire m_sof,output wire m_eol,output wire m_eof
);
 localparam integer LEGACY_BLOCK=META_X/8,EXT_BLOCK=LEGACY_BLOCK+5,DIAG_BLOCK=EXT_BLOCK+8;
 reg [8:0] block_x;
 reg [10:0] row_y;
 reg [31:0] composite;
 reg [287:0] payload;
 reg [7:0] checksum;
 wire [319:0] message={24'b0,checksum,payload};
 // QJ8V: magic4, version1, ready8, view_count8, flags0, compositeLE32,
 // eight observed source numbers, faultLE32, reserved12, CRC32(first60).
 reg [479:0] ext_payload;
 reg [31:0] ext_crc;
 reg [5:0] crc_index;
 reg crc_busy;
 wire [511:0] extended_message={~ext_crc,ext_payload};
 // QJDX/1,80Y bytes after the104Y legacy/extended identity bytes.
 // Diagnostic snapshot belongs to the same accepted mosaic SOF. No new CDC.
 reg [607:0] diag_payload;
 reg [31:0] diag_crc;
 reg [6:0] diag_crc_index;
 reg diag_crc_busy;
 wire [639:0] diag_message={~diag_crc,diag_payload};
 // Optional QJER/1 follows the unchanged QJ5V/QJ8V/QJDX headers at x1464.
 localparam integer ERROR_BLOCK=DIAG_BLOCK+10;
 reg[991:0]error_payload;
 reg[31:0]error_crc;
 reg[6:0]error_crc_index;
 reg error_crc_busy;
 wire[1023:0]error_message={~error_crc,error_payload};
 integer j,k;
 function automatic [31:0] crc_byte(input [31:0] old,input [7:0] data);
  reg [31:0] value;integer b;
  begin value=old^{24'b0,data};
   for(b=0;b<8;b=b+1)value=value[0]?(value>>1)^32'hedb88320:value>>1;
   crc_byte=value;
  end
 endfunction
 always @* begin
  checksum=0;
  for(j=0;j<36;j=j+1)checksum=checksum^payload[j*8+:8];
  m_data=s_data;
  if(row_y==META_ROW && block_x>=LEGACY_BLOCK && block_x<LEGACY_BLOCK+5)
   for(k=0;k<8;k=k+1)begin
    m_data[k*16+:8]=message[((block_x-LEGACY_BLOCK)*8+k)*8+:8];
    m_data[k*16+8+:8]=8'd128;
   end
  else if(row_y==META_ROW && block_x>=EXT_BLOCK && block_x<EXT_BLOCK+8)
   for(k=0;k<8;k=k+1)begin
    m_data[k*16+:8]=extended_message[((block_x-EXT_BLOCK)*8+k)*8+:8];
    m_data[k*16+8+:8]=8'd128;
   end
  else if(ENABLE_WRITER_DIAG && row_y==META_ROW && block_x>=DIAG_BLOCK && block_x<DIAG_BLOCK+10)
   for(k=0;k<8;k=k+1)begin
    m_data[k*16+:8]=diag_message[((block_x-DIAG_BLOCK)*8+k)*8+:8];
    m_data[k*16+8+:8]=8'd128;
   end
  else if(ENABLE_ERROR_DIAG && row_y==META_ROW && block_x>=ERROR_BLOCK && block_x<ERROR_BLOCK+16)
   for(k=0;k<8;k=k+1)begin
    m_data[k*16+:8]=error_message[((block_x-ERROR_BLOCK)*8+k)*8+:8];
    m_data[k*16+8+:8]=8'd128;
   end
 end
 assign s_ready=m_ready;
 assign m_valid=s_valid && !rst;
 assign m_sof=s_sof;assign m_eol=s_eol;assign m_eof=s_eof;
 always @(posedge clk)begin
  if(rst)begin block_x<=0;row_y<=0;composite<=0;payload<=0;end
  else if(s_valid && s_ready)begin
   if(s_sof)begin
    payload<={faults,view_frames[159:0],composite+32'd1,16'b0,3'b0,view_ready[4:0],8'd1,32'h56354a51};
    composite<=composite+1'b1;block_x<=1;row_y<=0;
   end else if(s_eof)begin block_x<=0;row_y<=0;end
   else if(s_eol)begin block_x<=0;row_y<=row_y+1'b1;end
   else block_x<=block_x+1'b1;
  end
 end
 always @(posedge clk)begin
  if(rst)begin ext_payload<=0;ext_crc<=32'hffffffff;crc_index<=0;crc_busy<=0;end
  else if(s_valid && s_ready && s_sof)begin
   ext_payload<={96'b0,faults,view_frames,composite+32'd1,8'b0,8'd8,view_ready,8'd1,32'h56384a51};
   ext_crc<=32'hffffffff;crc_index<=0;crc_busy<=1;
  end else if(crc_busy)begin
   ext_crc<=crc_byte(ext_crc,ext_payload[crc_index*8+:8]);
   if(crc_index==59)crc_busy<=0;else crc_index<=crc_index+1'b1;
  end
 end
 always @(posedge clk)begin
  if(rst)begin diag_payload<=0;diag_crc<=32'hffffffff;diag_crc_index<=0;diag_crc_busy<=0;end
  else if(ENABLE_WRITER_DIAG && s_valid && s_ready && s_sof)begin
   diag_payload<={32'b0,writer_diag[495:16],composite+32'd1,writer_diag[15:0],8'd80,8'd1,32'h58444a51};
   diag_crc<=32'hffffffff;diag_crc_index<=0;diag_crc_busy<=1;
  end else if(ENABLE_WRITER_DIAG && diag_crc_busy)begin
   diag_crc<=crc_byte(diag_crc,diag_payload[diag_crc_index*8+:8]);
   if(diag_crc_index==75)diag_crc_busy<=0;else diag_crc_index<=diag_crc_index+1'b1;
  end
 end
 always@(posedge clk)begin
  if(rst)begin error_payload<=0;error_crc<=32'hffffffff;error_crc_index<=0;error_crc_busy<=0;end
  else if(ENABLE_ERROR_DIAG && s_valid && s_ready && s_sof)begin
   // 16byte header +8x12byte error records +12reserved bytes +4CRC.
   error_payload<={96'b0,source_errors,32'b0,composite+32'd1,8'b0,8'd8,8'd128,8'd1,32'h52454a51};
   error_crc<=32'hffffffff;error_crc_index<=0;error_crc_busy<=1;
  end else if(ENABLE_ERROR_DIAG && error_crc_busy)begin
   error_crc<=crc_byte(error_crc,error_payload[error_crc_index*8+:8]);
   if(error_crc_index==123)error_crc_busy<=0;else error_crc_index<=error_crc_index+1'b1;
  end
 end
 initial if(META_X%8 || META_ROW<2)$error("Invalid metadata location/CRC preparation interval");
endmodule
