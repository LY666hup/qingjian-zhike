`timescale 1ns/1ps
// Common cold reset only. wrst/rrst must be asserted together; the caller
// synchronizes release in each clock domain. Single-sided reset is unsupported.
// Registered read prefetch adds one holding entry beyond the RAM ring capacity.
module qj_async_fifo #(
 parameter integer WIDTH=163,
 parameter integer ADDR_BITS=9
)(
 input wire wclk, input wire wrst,
 input wire s_valid, output wire s_ready, input wire [WIDTH-1:0] s_data,
 input wire rclk, input wire rrst,
 output reg m_valid, input wire m_ready, output reg [WIDTH-1:0] m_data
);
 localparam integer P=ADDR_BITS+1;
 reg [WIDTH-1:0] mem[0:(1<<ADDR_BITS)-1];
 reg [P-1:0] wbin,wgray,rbin,rgray;
 (* async_reg="true" *) reg [P-1:0] rq_wgray1,rq_wgray2,wq_rgray1,wq_rgray2;
 reg full;
 wire write_take=s_valid && s_ready;
 wire [P-1:0] next_wbin=wbin+write_take;
 wire [P-1:0] next_wgray=(next_wbin>>1)^next_wbin;
 wire [P-1:0] full_compare={~wq_rgray2[P-1:P-2],wq_rgray2[P-3:0]};
 wire ram_empty=(rgray==rq_wgray2);
 wire read_take=(!m_valid || m_ready) && !ram_empty;
 wire [P-1:0] next_rbin=rbin+read_take;
 assign s_ready=!full && !wrst;
 always @(posedge wclk) begin
  if(wrst) begin wbin<=0;wgray<=0;full<=0;wq_rgray1<=0;wq_rgray2<=0;end
  else begin
   wq_rgray1<=rgray;wq_rgray2<=wq_rgray1;
   wbin<=next_wbin;wgray<=next_wgray;
   full<=(next_wgray==full_compare);
   if(write_take) mem[wbin[ADDR_BITS-1:0]]<=s_data;
  end
 end
 always @(posedge rclk) begin
  if(rrst) begin rbin<=0;rgray<=0;rq_wgray1<=0;rq_wgray2<=0;m_valid<=0;m_data<=0;end
  else begin
   rq_wgray1<=wgray;rq_wgray2<=rq_wgray1;
   if(!m_valid || m_ready) begin
    m_valid<=!ram_empty;
    if(!ram_empty) begin
     m_data<=mem[rbin[ADDR_BITS-1:0]];
     rbin<=next_rbin;rgray<=(next_rbin>>1)^next_rbin;
    end
   end
  end
 end
`ifndef SYNTHESIS
 initial if(ADDR_BITS<2) $fatal(1,"qj_async_fifo requires ADDR_BITS >= 2");
`endif
endmodule
