`timescale 1ns/1ps
// Independent prototype. One MCU is Y0,Y1,Cb,Cr,64 accepted32bit words.
// DRI=1 makes each MCU independently encoded with DC predictors reset.
// Private scan caches let workers finish while earlier MCUs are transmitted.
module jpeg_color_mcu_pool #(
 parameter integer IMAGE_WIDTH=1920,IMAGE_HEIGHT=1080,WORKERS=6,
 parameter integer SCAN_CAPACITY=2048
)(
 input wire clk,rst_n,i_start,input wire[31:0]i_pixels,input wire i_valid,
 output wire i_ready,output reg[7:0]o_byte,output reg o_valid,
 output wire o_sof,o_eof,input wire o_ready,
 output reg o_done,output reg[31:0]o_bytes,output reg o_error
);
 `include "jpeg_color_tables.vh"
 localparam integer MCUS=(IMAGE_WIDTH/16)*(IMAGE_HEIGHT/8);
 localparam integer WW=(WORKERS>1)?$clog2(WORKERS):1;
 localparam integer OW=$clog2(SCAN_CAPACITY);
 localparam [2:0]IDLE=0,HEAD=1,WAIT_JOB=2,DRAIN=3,RST_FF=4,RST_ID=5,EOI_FF=6,EOI_ID=7;
 localparam [2:0]FREE=0,FILL=1,START_JOB=2,ENCODE=3,COMPLETE=4;
 // The pinned baseline header's SOS marker starts at byte324.
 localparam integer SOS_POSITION=324;
 reg[2:0]state;
 reg[9:0]header_index;
 reg[2:0]restart_id;
 reg[WW-1:0]fill_worker,read_worker;
 reg[5:0]fill_beat;
 reg[31:0]input_job,output_job;
 reg[2:0]job_state[0:WORKERS-1];
 reg[31:0]job_sequence[0:WORKERS-1];
 reg[OW:0]scan_length[0:WORKERS-1];
 wire[WORKERS-1:0]core_done,core_error,core_valid,core_ready;
 wire[WORKERS*8-1:0]core_byte;
 reg[6:0]input_issued[0:WORKERS-1];
 reg[WORKERS-1:0]pixel_valid;
 reg[WORKERS-1:0]sample_valid;
 reg[31:0]sample_q[0:WORKERS-1];
 (* dont_touch="true" *)reg[31:0]pixel_held[0:WORKERS-1];
 reg[7:0]cache_q[0:WORKERS-1];
 reg[OW:0]read_issued,read_sent,read_length;
 reg q_valid,held_valid;
 reg[7:0]held_byte;
 wire put=i_valid&&i_ready;
 wire byte_take=o_valid&&o_ready;
 wire drain_take=(state==DRAIN)&&held_valid&&o_ready;
 wire held_accept=!held_valid||o_ready;
 wire pop_q=held_accept&&q_valid;
 wire issue_read=state==DRAIN&&(!q_valid||pop_q)&&read_issued<read_length;
 wire release_job=drain_take&&read_sent==read_length-1'b1;
 assign i_ready=rst_n&&!o_error&&state!=IDLE&&input_job<MCUS&&
                (job_state[fill_worker]==FREE||job_state[fill_worker]==FILL);
 assign o_sof=state==HEAD&&header_index==0&&o_valid;
 assign o_eof=state==EOI_ID&&o_valid;
 function automatic[7:0]pool_header(input integer ix);
  begin
   if(ix<SOS_POSITION)pool_header=header_byte(ix);
   else if(ix<SOS_POSITION+6)begin
    case(ix-SOS_POSITION)
     0:pool_header=8'hff;1:pool_header=8'hdd;2:pool_header=0;
     3:pool_header=4;4:pool_header=0;default:pool_header=1;
    endcase
   end else pool_header=header_byte(ix-6);
  end
 endfunction
 always@*begin
  o_byte=0;o_valid=0;
  if(rst_n&&!o_error)case(state)
   HEAD:begin o_byte=pool_header(header_index);o_valid=1;end
   DRAIN:begin o_byte=held_byte;o_valid=held_valid;end
   RST_FF,EOI_FF:begin o_byte=8'hff;o_valid=1;end
   RST_ID:begin o_byte=8'hd0|{5'b0,restart_id};o_valid=1;end
   EOI_ID:begin o_byte=8'hd9;o_valid=1;end
   default:begin end
  endcase
 end
 genvar g;
 generate for(g=0;g<WORKERS;g=g+1)begin:worker
  (* ram_style="bram" *)reg[31:0]samples[0:63];
  (* ram_style="bram" *)reg[7:0]scan[0:SCAN_CAPACITY-1];
  wire enc_ready;
  wire[31:0]enc_bytes;
  wire pixel_advance=!pixel_valid[g]||core_ready[g];
  wire sample_accept=!sample_valid[g]||pixel_advance;
  wire fetch_pixel=job_state[g]==ENCODE&&sample_accept&&input_issued[g]<64;
  assign enc_ready=job_state[g]==ENCODE&&scan_length[g]<SCAN_CAPACITY;
  jpeg_color_block_encoder #(.IMAGE_WIDTH(16),.IMAGE_HEIGHT(8),.SEGMENT_ONLY(1)) codec(
   .clk(clk),.rst_n(rst_n),.i_start(job_state[g]==START_JOB),
   .i_pixels(pixel_held[g]),.i_valid(pixel_valid[g]&&job_state[g]==ENCODE),.i_ready(core_ready[g]),
   .o_byte(core_byte[g*8+:8]),.o_valid(core_valid[g]),.o_sof(),.o_eof(),.o_ready(enc_ready),
   .o_done(core_done[g]),.o_bytes(enc_bytes),.o_error(core_error[g]));
  always@(posedge clk)begin
   if(put&&fill_worker==g)samples[fill_beat]<=i_pixels;
   if(fetch_pixel)sample_q[g]<=samples[input_issued[g][5:0]];
   if(pixel_advance&&sample_valid[g])pixel_held[g]<=sample_q[g];
   if(core_valid[g]&&enc_ready)scan[scan_length[g][OW-1:0]]<=core_byte[g*8+:8];
   if(issue_read&&read_worker==g)cache_q[g]<=scan[read_issued[OW-1:0]];
  end
  always@(posedge clk or negedge rst_n)begin
   if(!rst_n)begin
    job_state[g]<=FREE;job_sequence[g]<=0;scan_length[g]<=0;
    input_issued[g]<=0;pixel_valid[g]<=0;sample_valid[g]<=0;
   end else if(i_start&&state==IDLE)begin
    job_state[g]<=FREE;job_sequence[g]<=0;scan_length[g]<=0;
    input_issued[g]<=0;pixel_valid[g]<=0;sample_valid[g]<=0;
   end else begin
    if(put&&fill_worker==g)begin
     if(fill_beat==0)begin job_state[g]<=FILL;job_sequence[g]<=input_job;end
     if(fill_beat==63)job_state[g]<=START_JOB;
    end
    if(job_state[g]==START_JOB)begin
     job_state[g]<=ENCODE;scan_length[g]<=0;input_issued[g]<=0;pixel_valid[g]<=0;sample_valid[g]<=0;
    end else if(job_state[g]==ENCODE)begin
     if(pixel_advance)pixel_valid[g]<=sample_valid[g];
     if(sample_accept)sample_valid[g]<=fetch_pixel;
     if(fetch_pixel)input_issued[g]<=input_issued[g]+1'b1;
     if(core_valid[g]&&enc_ready)scan_length[g]<=scan_length[g]+1'b1;
     if(core_done[g])begin job_state[g]<=COMPLETE;pixel_valid[g]<=0;sample_valid[g]<=0;end
    end
    if(release_job&&read_worker==g)job_state[g]<=FREE;
   end
  end
 end endgenerate
 integer k;
 always@(posedge clk or negedge rst_n)begin
  if(!rst_n)begin
   state<=IDLE;header_index<=0;restart_id<=0;fill_worker<=0;read_worker<=0;
   fill_beat<=0;input_job<=0;output_job<=0;read_issued<=0;read_sent<=0;read_length<=0;
   q_valid<=0;held_valid<=0;held_byte<=0;o_done<=0;o_bytes<=0;o_error<=0;
  end else begin
   o_done<=0;
   if(byte_take)o_bytes<=o_bytes+1'b1;
   for(k=0;k<WORKERS;k=k+1)begin
    if(core_error[k]||(job_state[k]==ENCODE&&core_valid[k]&&scan_length[k]==SCAN_CAPACITY))o_error<=1;
   end
   if(put)begin
    if(fill_beat==63)begin
     fill_beat<=0;input_job<=input_job+1;
     fill_worker<=(fill_worker==WORKERS-1)?0:fill_worker+1'b1;
    end else fill_beat<=fill_beat+1'b1;
   end
   case(state)
    IDLE:if(i_start)begin
     state<=HEAD;header_index<=0;fill_worker<=0;read_worker<=0;fill_beat<=0;
     input_job<=0;output_job<=0;q_valid<=0;held_valid<=0;o_bytes<=0;o_error<=0;
    end
    HEAD:if(byte_take)begin
     if(header_index==HEADER_SIZE+5)state<=WAIT_JOB;else header_index<=header_index+1'b1;
    end
    WAIT_JOB:if(job_state[read_worker]==COMPLETE)begin
     if(job_sequence[read_worker]!=output_job||scan_length[read_worker]==0)o_error<=1;
     else begin
      read_length<=scan_length[read_worker];read_issued<=0;read_sent<=0;
      q_valid<=0;held_valid<=0;state<=DRAIN;
     end
    end
    DRAIN:begin
     if(held_accept)begin held_valid<=q_valid;if(q_valid)held_byte<=cache_q[read_worker];end
     if(!q_valid||pop_q)q_valid<=issue_read;
     if(issue_read)read_issued<=read_issued+1'b1;
     if(drain_take)read_sent<=read_sent+1'b1;
     if(release_job)begin
      q_valid<=0;held_valid<=0;restart_id<=output_job[2:0];
      if(output_job==MCUS-1)state<=EOI_FF;
      else begin
       output_job<=output_job+1;read_worker<=(read_worker==WORKERS-1)?0:read_worker+1'b1;state<=RST_FF;
      end
     end
    end
    RST_FF:if(byte_take)state<=RST_ID;
    RST_ID:if(byte_take)state<=WAIT_JOB;
    EOI_FF:if(byte_take)state<=EOI_ID;
    EOI_ID:if(byte_take)begin state<=IDLE;o_done<=1;end
    default:state<=IDLE;
   endcase
  end
 end
`ifndef SYNTHESIS
 initial if(WORKERS<1||WORKERS>16||IMAGE_WIDTH%16||IMAGE_HEIGHT%8||SCAN_CAPACITY<2048)
  $fatal(1,"Parallel JPEG geometry/capacity unsupported");
`endif
endmodule
