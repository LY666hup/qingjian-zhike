`timescale 1ns/1ps
// Preview-only nearest sampling: x/y = 0,3,6,... . Native pixels are YUYV x4.
// Chroma policy: U from the left sampled pixel's native pair, V from the right
// sampled pixel's native pair. This is not an antialiasing or color-quality claim.
// Output record: {observed_frame32,bad130,eof129,sof128,YUYV_data128}.
// A bad/eof record is control-only;payload carries abort diagnostics,never pixels. The receiver MUST
// withhold publication until a good EOF and exact expected data count are seen.
// Width must be divisible by 24 and height by 3; resulting frame pixels / 8 exact.
// A missing frame tail is abandoned on the next SOF; no input-idle watchdog.
// FRAME_PHASE is the initial selection phase;0 preserves parent behavior.
// Frame observation IDs are NOT sensor exposure numbers. Common cold reset only.
module qj_preview_capture #(
 parameter integer WIDTH=1920, HEIGHT=1080, FRAME_DIV=4, FIFO_ADDR_BITS=9, FRAME_PHASE=0
)(
 input wire s_clk,input wire s_rst,input wire [63:0] s_data,
 input wire s_valid,input wire s_sof,input wire s_eol,
 input wire m_clk,input wire m_rst,
 output wire [127:0] m_data,output wire m_valid,input wire m_ready,
 output wire m_sof,output wire m_eof,output wire m_bad,
 output wire [31:0] m_frame,
 output reg [31:0] frames_seen,output reg [31:0] frames_dropped,
 output wire [4:0] m_buffered
);
 localparam integer NG=WIDTH/4, WORDS=(WIDTH/3)*(HEIGHT/3)/8;
 localparam integer CB=(NG>1)?$clog2(NG):1;
 localparam integer RB=(HEIGHT>1)?$clog2(HEIGHT):1;
 localparam integer WB=(WORDS>1)?$clog2(WORDS):1;
 localparam integer PB=(FRAME_DIV>1)?$clog2(FRAME_DIV):1;
 reg active,n_active,abort_pending,n_abort_pending;
 reg [31:0] frame_id,n_frame_id,abort_frame,n_abort_frame;
 reg [31:0] abort_detail,n_abort_detail,abort_words,n_abort_words;
 reg [7:0] error_reason;
 reg [9:0] error_col;
 reg [10:0] error_row;
 reg [CB-1:0] col,n_col;
 reg [RB-1:0] row,n_row;
 reg [PB-1:0] phase,n_phase;
 reg [WB-1:0] word_index,n_word_index;
 reg [1:0] pair_index,n_pair_index,xphase,n_xphase,yphase,n_yphase;
 reg half,n_half,final_valid,n_final_valid;
 reg [7:0] left_y,left_u,n_left_y,n_left_u;
 reg [127:0] pack,n_pack,final_word,n_final_word;
 reg [31:0] n_seen,n_dropped;
 reg push;reg [162:0] push_data;
 wire push_ready; wire [162:0] received;
 integer lane;
 reg process_input,bad_geometry;
 reg [31:0] pair_word;
 reg [127:0] completed_word;

 function [7:0] pixel_y(input [63:0] data,input integer index);
  begin case(index)
   0:pixel_y=data[7:0];1:pixel_y=data[23:16];
   2:pixel_y=data[39:32];default:pixel_y=data[55:48];endcase end
 endfunction
 function [7:0] pixel_u(input [63:0] data,input integer index);
  begin pixel_u=(index<2)?data[15:8]:data[47:40];end
 endfunction
 function [7:0] pixel_v(input [63:0] data,input integer index);
  begin pixel_v=(index<2)?data[31:24]:data[63:56];end
 endfunction

 always @* begin
  n_active=active;n_abort_pending=abort_pending;
  n_frame_id=frame_id;n_abort_frame=abort_frame;
  n_abort_detail=abort_detail;n_abort_words=abort_words;
  error_reason=0;error_col=col;error_row=row;
  n_col=col;n_row=row;n_phase=phase;n_word_index=word_index;n_pair_index=pair_index;
  n_xphase=xphase;n_yphase=yphase;
  n_half=half;n_final_valid=final_valid;n_left_y=left_y;n_left_u=left_u;
  n_pack=pack;n_final_word=final_word;
  n_seen=frames_seen;n_dropped=frames_dropped;
  push=0;push_data=0;process_input=0;bad_geometry=0;
  pair_word=0;completed_word=0;
  if(s_valid && s_sof) begin
   n_seen=frames_seen+1;
   if(phase==0)n_phase=FRAME_DIV-1;else n_phase=phase-1;
  end
  if(abort_pending) begin
   // This marker takes priority; a coinciding new SOF is intentionally skipped.
   if(push_ready) begin
    push=1;push_data={abort_frame,1'b1,1'b1,1'b0,32'h52454a51,abort_words,abort_detail,abort_frame};
    n_abort_pending=0;
   end
  end else if(s_valid && s_sof && active) begin
   n_active=0;n_abort_pending=1;n_abort_frame=frame_id;
   n_abort_detail={3'b0,error_row,error_col,8'h08};n_abort_words=word_index;
   n_dropped=frames_dropped+1;n_final_valid=0;
  end else begin
   if(s_valid && s_sof && !active && phase==0) begin
    n_active=1;n_frame_id=frames_seen+1;n_col=0;n_row=0;
    n_xphase=0;n_yphase=0;
    n_word_index=0;n_pair_index=0;n_half=0;n_pack=0;n_final_valid=0;
    process_input=1;
   end else if(s_valid && active) process_input=1;
   if(process_input) begin
    error_col=n_col;error_row=n_row;
    bad_geometry=(s_eol !== (n_col==NG-1));
    if(bad_geometry)error_reason[0]=1;
    if(!bad_geometry && n_yphase==0) begin
     // A group contains x=4*g..4*g+3. With x sampled every 3:
     // phase0 selects lanes0,3; phase1 saves lane2; phase2 completes
     // that pair with lane1. At phase0 half is always clear. There is
     // at most one completed YUYV pair per group, so no lane cascade.
     if(n_xphase==1) begin
      n_left_y=s_data[39:32];n_left_u=s_data[47:40];n_half=1;
     end else begin
      if(n_xphase==0)
       pair_word={s_data[63:56],s_data[55:48],s_data[15:8],s_data[7:0]};
      else
       pair_word={s_data[31:24],s_data[23:16],n_left_u,n_left_y};
      n_half=0;
      if(n_pair_index==3) begin
       completed_word={pair_word,n_pack[95:0]};n_pair_index=0;n_pack=0;
       if(n_word_index==WORDS-1) begin
        n_final_word=completed_word;n_final_valid=1;
       end else if(push_ready) begin
        push=1;push_data={n_frame_id,1'b0,1'b0,(n_word_index==0),completed_word};
        n_word_index=n_word_index+1;
       end else begin bad_geometry=1;error_reason[1]=1;end
      end else begin
       n_pack[n_pair_index*32+:32]=pair_word;n_pair_index=n_pair_index+1;
      end
     end
    end
    if(n_col==NG-1) begin
     if(n_row==HEIGHT-1) begin
      // Tail validation happens on the full native frame, not sampled row H-3.
      if(!n_final_valid || n_word_index!=WORDS-1 || n_pair_index!=0 || n_half || !push_ready)begin
       bad_geometry=1;error_reason[2]=1;
       if(!push_ready)error_reason[1]=1;
      end
      if(!bad_geometry) begin
       push=1;push_data={n_frame_id,1'b0,1'b1,(n_word_index==0),n_final_word};
       n_active=0;n_final_valid=0;
      end
     end else begin
      n_col=0;n_row=n_row+1;n_xphase=0;
      if(n_yphase==2)n_yphase=0;else n_yphase=n_yphase+1;
     end
    end else begin
     n_col=n_col+1;
     if(n_xphase==2)n_xphase=0;else n_xphase=n_xphase+1;
    end
    if(bad_geometry) begin
     push=0;n_active=0;n_abort_pending=1;n_abort_frame=n_frame_id;
     n_abort_detail={3'b0,error_row,error_col,error_reason};n_abort_words=n_word_index;
     n_final_valid=0;n_dropped=frames_dropped+1;
    end
   end
  end
 end
 always @(posedge s_clk) begin
  if(s_rst) begin
   active<=0;abort_pending<=0;frame_id<=0;abort_frame<=0;abort_detail<=0;abort_words<=0;
   col<=0;row<=0;phase<=FRAME_PHASE;word_index<=0;pair_index<=0;
   xphase<=0;yphase<=0;
   half<=0;final_valid<=0;left_y<=0;left_u<=0;pack<=0;final_word<=0;
   frames_seen<=0;frames_dropped<=0;
  end else begin
   active<=n_active;abort_pending<=n_abort_pending;frame_id<=n_frame_id;abort_frame<=n_abort_frame;
   abort_detail<=n_abort_detail;abort_words<=n_abort_words;
   col<=n_col;row<=n_row;phase<=n_phase;word_index<=n_word_index;pair_index<=n_pair_index;
   xphase<=n_xphase;yphase<=n_yphase;
   half<=n_half;final_valid<=n_final_valid;left_y<=n_left_y;left_u<=n_left_u;
   pack<=n_pack;final_word<=n_final_word;frames_seen<=n_seen;frames_dropped<=n_dropped;
  end
 end
 // One elastic record separates geometry/sampling decisions from the FIFO's
 // binary/Gray/full update. Backpressure still aborts the entire source frame;
 // this stage never drops or reorders a queued record.
 reg pipe_valid;
 reg [162:0] pipe_record;
 wire fifo_ready;
 assign push_ready=!pipe_valid || fifo_ready;
 // Payload is meaningful only when pipe_valid is set. Keep reset on the
 // validity bit, not the 163 payload bits. Writes during reset are harmless:
 // the FIFO is reset and pipe_valid stays clear. No reset/CDC exception added.
 always @(posedge s_clk) begin
  if(s_rst) pipe_valid<=0;
  else if(push_ready) pipe_valid<=push;
 end
 always @(posedge s_clk)
  if(push_ready && push) pipe_record<=push_data;
 qj_async_fifo #(.WIDTH(163),.ADDR_BITS(FIFO_ADDR_BITS)) queue(
  .wclk(s_clk),.wrst(s_rst),.s_valid(pipe_valid),.s_ready(fifo_ready),.s_data(pipe_record),
  .rclk(m_clk),.rrst(m_rst),.m_valid(m_valid),.m_ready(m_ready),.m_data(received),.m_buffered(m_buffered));
 assign m_data=received[127:0];assign m_sof=received[128];
 assign m_eof=received[129];assign m_bad=received[130];assign m_frame=received[162:131];
`ifndef SYNTHESIS
 initial if(WIDTH%24 || HEIGHT%3 || WIDTH<24 || HEIGHT<3 || FRAME_DIV<1 || FRAME_PHASE<0 || FRAME_PHASE>=FRAME_DIV)
  $fatal(1,"preview parameters require WIDTH %% 24 == 0, HEIGHT %% 3 == 0");
`endif
endmodule
