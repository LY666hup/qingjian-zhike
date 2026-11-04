`timescale 1ns/1ps
// Delay complete stream words instead of reading a nominal line ahead of its
// producer. Legal input bubbles remain bubbles; SOF/EOL travel with their data.
module ui_zhenghe #(
 parameter [12:0] IMG_WIDTH=640,IMG_HEIGHT=480,
 parameter [8:0] DATA_WIDTH=96,data_complete_delay=50
)(input wire I_clk,I_rst_n,I_tlast,I_tuser,
 input wire [DATA_WIDTH-1:0]I_tdata,input wire I_tvalid,
 output wire O_tlast,O_tuser,output wire[DATA_WIDTH-1:0]O_tdata,output wire O_tvalid);
 ui_signal_delay #(.IMG_WIDTH(IMG_WIDTH),.IMG_HEIGHT(IMG_HEIGHT),
  .DATA_WIDTH(DATA_WIDTH),.DELAY_CYCLE(data_complete_delay)) stream_delay(
  .I_clk(I_clk),.I_rst_n(I_rst_n),.I_img_width(IMG_WIDTH),.I_img_height(IMG_HEIGHT),
  .I_tuser(I_tuser),.I_tlast(I_tlast),.I_valid(I_tvalid),.I_data(I_tdata),
  .O_valid(O_tvalid),.O_data(O_tdata),.O_tuser(O_tuser),.O_tlast(O_tlast));
endmodule
