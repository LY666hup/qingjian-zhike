`timescale 1ns/1ps
// Independent repair candidate for the plaintext board-example interface.
// Selected in A/B protected receivers by explicit late compile order; native
// elaboration verifies both heartbeat instances. Physical/board acceptance pending.
// I_ref_clock must remain running. With nominal RX/ref clocks the heartbeat
// period is much shorter than the 250-reference-cycle timeout. Arbitrarily
// slow external clocks and arbitrary clock-ratio aliasing are not supported.
module csi_rx_clock_det (
 input wire I_ref_clock,
 input wire I_ext_clock,
 input wire I_enable,
 input wire I_reset_in,
 output wire O_reset_out
);
 wire request_n = I_reset_in & I_enable;
 (* async_reg="true" *) reg [1:0] ref_release,ext_release;
 always @(posedge I_ref_clock or negedge request_n)
  if(!request_n) ref_release<=0;
  else ref_release<={ref_release[0],1'b1};
 always @(posedge I_ext_clock or negedge request_n)
  if(!request_n) ext_release<=0;
  else ext_release<={ext_release[0],1'b1};

 // A single-bit divided heartbeat crosses domains; no multi-bit count crosses.
 // Toggle once every eight external edges, avoiding direct clock-level sampling.
 reg [2:0] beat_count;
 reg heartbeat;
 always @(posedge I_ext_clock or negedge request_n) begin
  if(!request_n) begin beat_count<=0;heartbeat<=0;end
  else if(!ext_release[1]) begin beat_count<=0;heartbeat<=0;end
  else begin
   beat_count<=beat_count+1'b1;
   if(beat_count==7) heartbeat<=~heartbeat;
  end
 end
 (* async_reg="true" *) reg [1:0] beat_sync;
 reg last_beat;
 reg [7:0] idle_count;
 reg [1:0] good_beats;
 reg alive;
 always @(posedge I_ref_clock or negedge request_n) begin
  if(!request_n) begin
   beat_sync<=0;last_beat<=0;idle_count<=0;good_beats<=0;alive<=0;
  end else if(!ref_release[1]) begin
   beat_sync<=0;last_beat<=0;idle_count<=0;good_beats<=0;alive<=0;
  end else begin
   beat_sync<={beat_sync[0],heartbeat};
   last_beat<=beat_sync[1];
   if(beat_sync[1]!=last_beat) begin
    idle_count<=0;
    if(good_beats<2)good_beats<=good_beats+1'b1;
    if(good_beats>=1)alive<=1;
   end else if(idle_count==249) begin
    idle_count<=249;good_beats<=0;alive<=0;
   end else idle_count<=idle_count+1'b1;
  end
 end

 // Loss/disable asserts reset even when the RX clock is stopped. Release is
 // synchronized to RX, whose downstream logic consumes O_reset_out.
 wire rx_request_n=request_n & alive;
 (* async_reg="true" *) reg [1:0] rx_release;
 always @(posedge I_ext_clock or negedge rx_request_n)
  if(!rx_request_n)rx_release<=0;
  else rx_release<={rx_release[0],1'b1};
 assign O_reset_out=rx_release[1];
endmodule
