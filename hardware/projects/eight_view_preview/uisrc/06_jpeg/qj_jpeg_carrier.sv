`timescale 1ns/1ps
// Variable JPEG -> fixed small YUYV byte carriers. Bytes are opaque, NOT pixels.
// Only a final descriptor-validated chunk commits a JPEG at the receiver.
// Carrier mode requires an RK endpoint mode matching CARRIER_W/H; it is not
// compatible with the current fixed 1920x1080 preview configuration unchanged.
module qj_jpeg_carrier #(
 parameter integer CARRIER_W=512,CARRIER_H=64,
 parameter integer NATIVE_W=1920,NATIVE_H=1080,JPEG_W=1920,JPEG_H=1080
)(
 input wire clk,rst,abort_input,
 input wire [7:0] s_byte,input wire s_valid,output wire s_ready,input wire s_sof,s_eof,
 input wire [31:0] s_record_sequence,input wire [255:0] s_source_frames,input wire [7:0] s_source_ready,
 input wire descriptor_valid,output wire descriptor_ready,
 input wire [31:0] descriptor_length,descriptor_crc32,
 output wire [127:0] m_data,output wire m_valid,input wire m_ready,
 output wire m_sof,m_eol,m_eof,
 output reg o_locked,output reg [31:0] o_error,carriers_sent
);
 localparam integer BYTES=CARRIER_W*CARRIER_H*2,WORDS=BYTES/16,CAPACITY=BYTES-128;
 localparam integer AW=$clog2(WORDS),BPL=CARRIER_W/8;
 localparam [2:0] COLLECT=0,WAIT_DESCRIPTOR=1,BUILD_HEADER=2,SEND=3,LOCKED=4;
 reg [2:0] state;
 reg [31:0] count,payload_length,offset,chunk_index,record_id,total_count;
 reg [31:0] payload_crc,total_crc,payload_crc_final,final_length,final_crc;
 reg [255:0] source_frames;
 reg [7:0] source_ready;
 reg frame_open,last_chunk;
 reg [7:0] build_index;
 reg [31:0] header_crc;
 reg [127:0] header_words[0:7];
 reg [AW-1:0] request_word;
 reg requests_done,pending,p_is_header,p_is_data,p_sof,p_eol,p_eof;
 reg [127:0] p_header,out_data;
 reg [4:0] p_valid_bytes;
 reg out_valid,out_sof,out_eol,out_eof;
 wire advance=!out_valid || m_ready;
 wire request_ready=!pending || advance;
 wire request=state==SEND && !requests_done && request_ready;
 wire data_request=request && request_word>=8 && (request_word-8)*16<payload_length;
 wire [AW-1:0] read_address=request_word-8;
 wire [127:0] bank_data;
 assign m_data=out_data;assign m_valid=out_valid && !rst && !o_locked;
 assign m_sof=out_sof && m_valid;assign m_eol=out_eol && m_valid;assign m_eof=out_eof && m_valid;
 assign s_ready=state==COLLECT && !rst && !o_locked;
 wire byte_take=s_valid && s_ready;
 wire descriptor_ok=descriptor_length==total_count && descriptor_crc32==~total_crc;
 assign descriptor_ready=state==WAIT_DESCRIPTOR && descriptor_valid && descriptor_ok && !o_locked;

 function automatic [31:0] crc_byte(input [31:0] old,input [7:0] data);
  reg [31:0] x;integer i;begin x=old^{24'b0,data};
   for(i=0;i<8;i=i+1)x=x[0]?(x>>1)^32'hedb88320:x>>1;
   crc_byte=x;
  end
 endfunction
 function [7:0] header_byte(input integer index);
  reg [31:0] value;
  begin
   header_byte=0;value=0;
   case(index)
    0:header_byte=8'h51;1:header_byte=8'h4a;2:header_byte=8'h4a;3:header_byte=8'h43;
    4:header_byte=2;
    5:header_byte={5'b0,last_chunk,last_chunk,(chunk_index==0)};
    6:header_byte=128;7:header_byte=0;
    36:header_byte=NATIVE_W%256;37:header_byte=NATIVE_W/256;
    38:header_byte=NATIVE_H%256;39:header_byte=NATIVE_H/256;
    40:header_byte=JPEG_W%256;41:header_byte=JPEG_W/256;
    42:header_byte=JPEG_H%256;43:header_byte=JPEG_H/256;
    44:header_byte=source_ready;45:header_byte=1;46:header_byte=2;47:header_byte=8;
    default:begin
     if(index>=8 && index<36)begin
      case(index/4)
       2:value=record_id;3:value=chunk_index;4:value=offset;5:value=payload_length;
       6:value=payload_crc_final;7:value=last_chunk?final_length:0;8:value=last_chunk?final_crc:0;
       default:value=0;
      endcase
      header_byte=value[(index%4)*8+:8];
     end else if(index>=48 && index<80)header_byte=source_frames[(index-48)*8+:8];
    end
   endcase
  end
 endfunction
 wire [7:0] build_byte=header_byte(build_index);
 wire [31:0] next_header_crc=crc_byte(header_crc,build_byte);
 // Sixteen banks provide one byte write and one 128bit synchronous read.
 for(genvar bank=0;bank<16;bank=bank+1)begin:storage
  (* ram_style="bram" *) reg [7:0] memory[0:WORDS-1];
  reg [7:0] q;
  always @(posedge clk)begin
   if(byte_take && count[3:0]==bank)memory[count[AW+3:4]]<=s_byte;
   if(data_request)q<=memory[read_address];
  end
  assign bank_data[bank*8+:8]=q;
 end
 reg [127:0] masked_bank_data;
 integer lane;
 always @* begin
  masked_bank_data=0;
  for(lane=0;lane<16;lane=lane+1)
   if(lane<p_valid_bytes)masked_bank_data[lane*8+:8]=bank_data[lane*8+:8];
 end
 always @(posedge clk)begin
  if(rst)begin
   state<=COLLECT;count<=0;payload_length<=0;offset<=0;chunk_index<=0;record_id<=0;total_count<=0;
   payload_crc<=32'hffffffff;total_crc<=32'hffffffff;payload_crc_final<=0;final_length<=0;final_crc<=0;
   source_frames<=0;source_ready<=0;frame_open<=0;last_chunk<=0;build_index<=0;header_crc<=32'hffffffff;
   request_word<=0;requests_done<=0;pending<=0;p_is_header<=0;p_is_data<=0;p_sof<=0;p_eol<=0;p_eof<=0;p_header<=0;p_valid_bytes<=0;
   out_data<=0;out_valid<=0;out_sof<=0;out_eol<=0;out_eof<=0;o_locked<=0;o_error<=0;carriers_sent<=0;
  end else begin
   if(byte_take)begin
    if(s_sof!=!frame_open || total_count==32'hffffffff)begin state<=LOCKED;o_locked<=1;o_error<=1;end
    else begin
     if(!frame_open)begin frame_open<=1;record_id<=s_record_sequence;source_frames<=s_source_frames;source_ready<=s_source_ready;end
     count<=count+1;total_count<=total_count+1;
     payload_crc<=crc_byte(payload_crc,s_byte);total_crc<=crc_byte(total_crc,s_byte);
     if(s_eof || count==CAPACITY-1)begin
      payload_length<=count+1;payload_crc_final<=~crc_byte(payload_crc,s_byte);last_chunk<=s_eof;
      build_index<=0;header_crc<=32'hffffffff;
      state<=s_eof?WAIT_DESCRIPTOR:BUILD_HEADER;
     end
    end
   end
   if(state==WAIT_DESCRIPTOR && descriptor_valid)begin
    if(!descriptor_ok)begin state<=LOCKED;o_locked<=1;o_error<=2;end
    else begin final_length<=descriptor_length;final_crc<=descriptor_crc32;state<=BUILD_HEADER;end
   end
   if(state==BUILD_HEADER)begin
    header_words[build_index[6:4]][build_index[3:0]*8+:8]<=build_byte;
    header_crc<=next_header_crc;
    if(build_index==123)begin
     header_words[7][127:96]<=~next_header_crc;
     state<=SEND;request_word<=0;requests_done<=0;
    end else build_index<=build_index+1'b1;
   end
   if(request_ready)begin
    pending<=request;
    if(request)begin
     p_is_header<=request_word<8;p_is_data<=data_request;
     if(request_word<8)p_header<=header_words[request_word];
     p_valid_bytes<=(payload_length-(request_word-8)*16>=16)?16:payload_length-(request_word-8)*16;
     p_sof<=request_word==0;p_eol<=request_word%BPL==BPL-1;p_eof<=request_word==WORDS-1;
     if(request_word==WORDS-1)requests_done<=1;else request_word<=request_word+1'b1;
    end
   end
   if(advance)begin
    out_valid<=pending;
    if(pending)begin out_data<=p_is_header?p_header:p_is_data?masked_bank_data:128'b0;out_sof<=p_sof;out_eol<=p_eol;out_eof<=p_eof;end
   end
   if(m_valid && m_ready && m_eof)begin
    carriers_sent<=carriers_sent+1;count<=0;payload_crc<=32'hffffffff;state<=COLLECT;
    if(last_chunk)begin frame_open<=0;offset<=0;chunk_index<=0;total_count<=0;total_crc<=32'hffffffff;end
    else begin offset<=offset+payload_length;chunk_index<=chunk_index+1;end
   end
   if(abort_input)begin state<=LOCKED;o_locked<=1;o_error<=4;end
  end
 end
 initial if(CARRIER_W%8 || BYTES<256 || BYTES%16 || WORDS!=(1<<AW) || CAPACITY>65535)
  $error("Unsupported JPEG carrier geometry");
endmodule
