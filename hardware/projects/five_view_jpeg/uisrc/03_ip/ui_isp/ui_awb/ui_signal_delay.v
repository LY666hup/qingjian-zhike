`timescale 1ns/1ps
// Stream-preserving latency for AWB. Never invent pixels, SOFs or line ends.
// Input controls must refer to the same input word. No frame/line regeneration.
module ui_signal_delay #(
 parameter IMG_HEIGHT=1080,IMG_WIDTH=1920,DATA_WIDTH=96,DELAY_CYCLE=50
)(
 input wire I_clk,I_rst_n,
 input wire [12:0] I_img_width,I_img_height,
 input wire I_tuser,I_tlast,I_valid,
 input wire [DATA_WIDTH-1:0] I_data,
 output wire O_valid,output wire [DATA_WIDTH-1:0] O_data,
 output wire O_tuser,O_tlast
);
 generate if(DELAY_CYCLE==1)begin:g_one
  reg valid_q,sof_q,last_q;reg[DATA_WIDTH-1:0]data_q;
  always @(posedge I_clk or negedge I_rst_n)
   if(!I_rst_n)begin valid_q<=0;sof_q<=0;last_q<=0;data_q<=0;end
   else begin valid_q<=I_valid;sof_q<=I_tuser;last_q<=I_tlast;data_q<=I_data;end
  assign O_valid=valid_q,O_tuser=sof_q,O_tlast=last_q;
  assign O_data=valid_q?data_q:{DATA_WIDTH{1'b0}};
 end else begin:g_delay
  localparam integer AW=$clog2(DELAY_CYCLE),DEPTH=1<<AW;
  reg[AW-1:0]write_pointer;
  wire[AW-1:0]read_pointer=write_pointer-(DELAY_CYCLE-1);
  (* ram_style="bram" *)reg[DATA_WIDTH-1:0]memory[0:DEPTH-1];
  reg[DATA_WIDTH-1:0]read_data;
  reg[DELAY_CYCLE-1:0]valid_pipe,sof_pipe,last_pipe;
  // RAM is intentionally not reset. Valid/control pipelines suppress stale
  // memory after reset;read and write addresses differ for all DELAY_CYCLE>=2.
  always @(posedge I_clk)begin
   memory[write_pointer]<=I_data;
   read_data<=memory[read_pointer];
  end
  always @(posedge I_clk or negedge I_rst_n)
   if(!I_rst_n)begin write_pointer<=0;valid_pipe<=0;sof_pipe<=0;last_pipe<=0;end
   else begin
    write_pointer<=write_pointer+1'b1;
    valid_pipe<={valid_pipe[DELAY_CYCLE-2:0],I_valid};
    sof_pipe<={sof_pipe[DELAY_CYCLE-2:0],I_tuser};
    last_pipe<={last_pipe[DELAY_CYCLE-2:0],I_tlast};
   end
  assign O_valid=valid_pipe[DELAY_CYCLE-1];
  assign O_tuser=sof_pipe[DELAY_CYCLE-1],O_tlast=last_pipe[DELAY_CYCLE-1];
  assign O_data=O_valid?read_data:{DATA_WIDTH{1'b0}};
 end endgenerate
`ifndef SYNTHESIS
 initial if(DELAY_CYCLE<1)$fatal(1,"AWB stream latency must be positive");
`endif
endmodule
