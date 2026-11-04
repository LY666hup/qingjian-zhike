`timescale 1ns/1ps
// Candidate: encode only first JPEG_HEIGHT rows, drain and validate whole native
// frame before descriptor commit. Removes the unused bottom region, no scaling.
// Consumer of the real, backpressured DDR mosaic (BEFORE QJ5V pixel overlay).
// All logic in DDR clock domain. Color 4:2:2 baseline JPEG, one shared DCT core.
// This is NOT a direct camera ingress and NOT a CSI transmitter.
// Receiver must retain speculative bytes privately until descriptor acceptance.
// Any o_locked/abort invalidates the entire uncommitted byte prefix; coordinated
// reset of producer + codec + receiver is required, never a local live reset.
module qj_color_mosaic_jpeg_stream #(
 parameter integer WIDTH=1920, HEIGHT=1080, JPEG_HEIGHT=1080, MATRIX_709=1,
 parameter [7:0] REQUIRED_READY=8'h00
)(
 input wire clk, input wire rst, input wire enable,
 input wire [127:0] s_data, input wire s_valid, output wire s_ready,
 input wire s_sof,s_eol,s_eof,
 input wire [255:0] s_view_frames, input wire [7:0] s_view_ready,
 input wire [31:0] s_faults,
 output wire [7:0] m_byte, output wire m_valid, input wire m_ready,
 output wire m_sof,m_eof,
 output reg descriptor_valid, input wire descriptor_ready,
 output reg [31:0] jpeg_length,jpeg_crc32,record_sequence,
 output reg [255:0] source_frames, output reg [7:0] source_ready,
 output reg o_locked, output reg [31:0] o_error,
 output reg [31:0] frames_started,frames_skipped
);
 localparam integer BPL=WIDTH/8;
 localparam [2:0] IDLE=0,START=1,RUN=2,WAIT_DONE=3,REPORT=4,LOCKED=5;
 reg [2:0] state;
 reg [127:0] held;
 reg held_valid,held_sof,held_eol;
 reg [31:0] blocks_seen,sequence_counter,byte_count,crc;
 reg input_complete,byte_eof_seen,codec_finished;
 reg [15:0] col,row;
 wire codec_ready,codec_valid,codec_sof,codec_eof,codec_done,codec_error;
 wire [7:0] codec_byte;
 wire [31:0] codec_length;
 wire codec_take=held_valid && codec_ready && state==RUN && !o_locked;
 wire pop_word=codec_take;
 // One valid real source can run; absent sources remain black and not-ready.
 wire eligible=enable && (|s_view_ready) && (s_view_ready&REQUIRED_READY)==REQUIRED_READY;
 wire image_region=(row<JPEG_HEIGHT);
 assign s_ready=!rst && !o_locked &&
     (state==IDLE || (state==RUN && !input_complete && (!image_region || !held_valid || pop_word)));
 wire take=s_valid && s_ready;
 wire bad_geometry=(s_sof!=(blocks_seen==0)) ||
                   (s_eol!=(col==BPL-1)) ||
                   (s_eof!=(col==BPL-1 && row==HEIGHT-1));
 assign m_byte=codec_byte;
 assign m_valid=codec_valid && !rst && !o_locked;
 assign m_sof=codec_sof && m_valid;
 assign m_eof=codec_eof && m_valid;
 wire byte_take=m_valid && m_ready;

 function automatic [31:0] crc_byte(input [31:0] old,input [7:0] data);
  reg [31:0] x; integer i;
  begin x=old^{24'b0,data};
   for(i=0;i<8;i=i+1) x=x[0] ? (x>>1)^32'hedb88320 : x>>1;
   crc_byte=x;
  end
 endfunction

 jpeg_color_raster_encoder #(.WIDTH(WIDTH),.HEIGHT(JPEG_HEIGHT),.MATRIX_709(MATRIX_709)) encoder(
  .clk(clk),.rst_n(!rst),.i_start(state==START),
  .i_yuyv(held),.i_valid(held_valid && state==RUN && !o_locked),
  .i_sof(held_sof),.i_eol(held_eol),.i_ready(codec_ready),
  .o_byte(codec_byte),.o_valid(codec_valid),.o_sof(codec_sof),.o_eof(codec_eof),
  .o_ready(m_ready && !o_locked),.o_done(codec_done),.o_bytes(codec_length),.o_error(codec_error));

 always @(posedge clk) begin
  if(rst) begin
   state<=IDLE;held<=0;held_valid<=0;held_sof<=0;held_eol<=0;
   blocks_seen<=0;sequence_counter<=0;byte_count<=0;crc<=32'hffffffff;
   input_complete<=0;byte_eof_seen<=0;codec_finished<=0;col<=0;row<=0;
   descriptor_valid<=0;jpeg_length<=0;jpeg_crc32<=0;record_sequence<=0;
   source_frames<=0;source_ready<=0;o_locked<=0;o_error<=0;
   frames_started<=0;frames_skipped<=0;
  end else begin
   if(state==IDLE && take && s_sof) begin
    if(eligible) begin
     held<=s_data;held_valid<=1;held_sof<=1;held_eol<=s_eol;
     blocks_seen<=1;col<=1;row<=0;input_complete<=0;byte_eof_seen<=0;codec_finished<=0;
     sequence_counter<=sequence_counter+1;record_sequence<=sequence_counter+1;
     source_frames<=s_view_frames;source_ready<=s_view_ready;
     byte_count<=0;crc<=32'hffffffff;state<=START;frames_started<=frames_started+1;
     if(s_eol || s_eof)begin o_locked<=1;o_error<=1;state<=LOCKED;end
    end else frames_skipped<=frames_skipped+1;
   end
   if(state==START)state<=RUN;
   if(state==RUN) begin
    if(codec_take)held_valid<=0;
    if(take) begin
     if(image_region)begin held<=s_data;held_valid<=1;held_sof<=s_sof;held_eol<=s_eol;end
     blocks_seen<=blocks_seen+1;
     if(col==BPL-1)begin col<=0;row<=row+1;end else col<=col+1;
     if(s_eof)input_complete<=1;
     if(bad_geometry)begin o_locked<=1;o_error<=1;state<=LOCKED;end
    end
    if(input_complete && !held_valid)state<=WAIT_DONE;
   end
   if(byte_take) begin
    byte_count<=byte_count+1;crc<=crc_byte(crc,m_byte);
    if(m_eof)byte_eof_seen<=1;
   end
   if(codec_done && !o_locked) begin
    if(!byte_eof_seen || codec_length!=byte_count) begin
     o_locked<=1;o_error<=8;state<=LOCKED;
    end else begin
     codec_finished<=1;
     jpeg_length<=byte_count;jpeg_crc32<=~crc;
    end
   end
   if(codec_finished && input_complete && !descriptor_valid && (state==RUN || state==WAIT_DONE) && !o_locked)begin
    descriptor_valid<=1;state<=REPORT;
   end
   if(descriptor_valid && descriptor_ready)begin descriptor_valid<=0;state<=IDLE;end
   // DDR faults are sticky in the producer. No descriptor may commit after one.
   if(s_faults!=0 || codec_error) begin
    descriptor_valid<=0;o_locked<=1;
    o_error<=codec_error?32'h4:32'h2;state<=LOCKED;
   end
  end
 end
 initial if(WIDTH<16 || WIDTH%16 || HEIGHT<8 || HEIGHT%8 || JPEG_HEIGHT<8 || JPEG_HEIGHT%8 || JPEG_HEIGHT>HEIGHT || WIDTH>65535 || HEIGHT>65535)
  $error("Unsupported mosaic JPEG geometry");
endmodule
