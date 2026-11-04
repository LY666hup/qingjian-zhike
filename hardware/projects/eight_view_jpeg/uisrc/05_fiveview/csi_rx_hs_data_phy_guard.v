`timescale 1ps/1ps
// PH1 sampling-data path without per-packet/control reset on IO data flops.
// Receiver control remains reset by the original clock detector/packet parser.
// Three local byte-clock edges fill the capture path before exposing data.
// This is a candidate, not board acceptance. Only PH1 projects select it.
module csi_rx_hs_data_phy(
 input wire I_rxclk,I_rxclk_div_x2,I_rxclk_div_x4,I_reset_n,I_hs_data_p,
 input wire [5:0] I_delay_value,input wire I_delay_vaild,
 output wire [7:0] O_rx_data,output wire O_delay_data_val);
 wire delayed;
 wire [3:0] nibble;
 reg [3:0] previous;
 reg [7:0] byte_data;
 (* dont_touch="true" *) reg [2:0] byte_release;
 PH1_LOGIC_DYNAMIC_IDELAY #(.WIDTH(5)) u_delay(
  .i(I_hs_data_p),.indly_en(I_delay_vaild),.in_dly(I_delay_value[4:0]),.o(delayed));
 PH1_LOGIC_IDDRx2 #(.ASYNCRST("DISABLE")) u_sample(
  .q3(nibble[3]),.q2(nibble[2]),.q1(nibble[1]),.q0(nibble[0]),
  .pclk(I_rxclk_div_x2),.sclk(I_rxclk_div_x4),.d(delayed),.rst(1'b0));
 always @(posedge I_rxclk_div_x2)previous<=nibble;
 always @(posedge I_rxclk)byte_data<={nibble,previous};
 always @(posedge I_rxclk or negedge I_reset_n)
  if(!I_reset_n)byte_release<=3'b000;
  else byte_release<={byte_release[1:0],1'b1};
 assign O_rx_data=byte_release[2]?byte_data:8'b0;
 assign O_delay_data_val=1'b1;
endmodule
