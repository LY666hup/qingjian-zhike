`timescale 1ns/1ps
// Actual preview DDR service directly feeding a grayscale JPEG consumer.
// Exclusive output mode: it does not fork or sustain simultaneous raw preview.
// Logical integration boundary only; no CSI/PCIe compressed transport is claimed.
module qj_five_view_color_jpeg_service #(
 parameter integer ADDR_WIDTH=30, ID_WIDTH=4,
 parameter integer TILE_W=640, TILE_H=360,
 parameter integer FRAME_WIDTH=1920, FRAME_HEIGHT=1080, JPEG_HEIGHT=3*TILE_H, SOURCE_EMPTY_CYCLES=0, KNOWN_BUFFERED_BURSTS=0
)(
 input wire clk, input wire rst,
 input wire [1023:0] s_data, input wire [7:0] s_valid,
 output wire [7:0] s_ready,
 input wire [7:0] s_sof,s_eof,s_bad,
 input wire [255:0] s_frame,
 input wire [39:0] s_buffered,
 input wire jpeg_enable,
 output wire [7:0] jpeg_byte, output wire jpeg_valid, input wire jpeg_ready,
 output wire jpeg_sof,jpeg_eof,
 output wire descriptor_valid, input wire descriptor_ready,
 output wire [31:0] jpeg_length,jpeg_crc32,record_sequence,
 output wire [255:0] source_frames, output wire [7:0] source_ready,
 output wire jpeg_locked, output wire [31:0] jpeg_error,frames_started,frames_skipped,
 output wire [255:0] o_display_frame, output wire [7:0] o_view_ready,
 output wire [31:0] o_fault, output wire [31:0] o_published,o_dropped,
 output wire [ID_WIDTH-1:0] m_axi_awid,
 output wire [ADDR_WIDTH-1:0] m_axi_awaddr,
 output wire [7:0] m_axi_awlen, output wire [2:0] m_axi_awsize,
 output wire [1:0] m_axi_awburst,
 output wire m_axi_awvalid, input wire m_axi_awready,
 output wire [127:0] m_axi_wdata, output wire [15:0] m_axi_wstrb,
 output wire m_axi_wlast, output wire m_axi_wvalid, input wire m_axi_wready,
 input wire [ID_WIDTH-1:0] m_axi_bid, input wire [1:0] m_axi_bresp,
 input wire m_axi_bvalid, output wire m_axi_bready,
 output wire [ID_WIDTH-1:0] m_axi_arid,
 output wire [ADDR_WIDTH-1:0] m_axi_araddr,
 output wire [7:0] m_axi_arlen, output wire [2:0] m_axi_arsize,
 output wire [1:0] m_axi_arburst,
 output wire m_axi_arvalid, input wire m_axi_arready,
 input wire [ID_WIDTH-1:0] m_axi_rid, input wire [127:0] m_axi_rdata,
 input wire [1:0] m_axi_rresp, input wire m_axi_rlast,m_axi_rvalid,
 output wire m_axi_rready
);
wire [127:0] pv_data;wire pv_valid,pv_ready,pv_sof,pv_eol,pv_eof;
qj_five_view_ddr #(.ADDR_WIDTH(ADDR_WIDTH),.ID_WIDTH(ID_WIDTH),.TILE_W(TILE_W),.TILE_H(TILE_H),.FRAME_WIDTH(FRAME_WIDTH),.FRAME_HEIGHT(FRAME_HEIGHT),.SOURCE_EMPTY_CYCLES(SOURCE_EMPTY_CYCLES),.KNOWN_BUFFERED_BURSTS(KNOWN_BUFFERED_BURSTS)) memory_service(
 .clk(clk),
 .rst(rst),
 .s_data(s_data),
 .s_valid(s_valid),
 .s_ready(s_ready),
 .s_sof(s_sof),
 .s_eof(s_eof),
 .s_bad(s_bad),
 .s_frame(s_frame),
 .s_buffered(s_buffered),
 .m_data(pv_data),
 .m_valid(pv_valid),
 .m_ready(pv_ready),
 .m_sof(pv_sof),
 .m_eol(pv_eol),
 .m_eof(pv_eof),
 .o_display_frame(o_display_frame),
 .o_view_ready(o_view_ready),
 .o_fault(o_fault),
 .o_published(o_published),
 .o_dropped(o_dropped),
 .m_axi_awid(m_axi_awid),
 .m_axi_awaddr(m_axi_awaddr),
 .m_axi_awlen(m_axi_awlen),
 .m_axi_awsize(m_axi_awsize),
 .m_axi_awburst(m_axi_awburst),
 .m_axi_awvalid(m_axi_awvalid),
 .m_axi_awready(m_axi_awready),
 .m_axi_wdata(m_axi_wdata),
 .m_axi_wstrb(m_axi_wstrb),
 .m_axi_wlast(m_axi_wlast),
 .m_axi_wvalid(m_axi_wvalid),
 .m_axi_wready(m_axi_wready),
 .m_axi_bid(m_axi_bid),
 .m_axi_bresp(m_axi_bresp),
 .m_axi_bvalid(m_axi_bvalid),
 .m_axi_bready(m_axi_bready),
 .m_axi_arid(m_axi_arid),
 .m_axi_araddr(m_axi_araddr),
 .m_axi_arlen(m_axi_arlen),
 .m_axi_arsize(m_axi_arsize),
 .m_axi_arburst(m_axi_arburst),
 .m_axi_arvalid(m_axi_arvalid),
 .m_axi_arready(m_axi_arready),
 .m_axi_rid(m_axi_rid),
 .m_axi_rdata(m_axi_rdata),
 .m_axi_rresp(m_axi_rresp),
 .m_axi_rlast(m_axi_rlast),
 .m_axi_rvalid(m_axi_rvalid),
 .m_axi_rready(m_axi_rready));
// Two registered entries;ready depends only on registered occupancy.
// Metadata travels with the actual accepted word,including SOF snapshots.
wire[395:0]jpeg_ingress_data;
wire jpeg_ingress_valid,jpeg_ingress_ready;
qj_jpeg_ingress_queue ingress(
 .clk(clk),.rst(rst),
 .s_data({jpeg_enable,o_view_ready,o_display_frame,pv_eof,pv_eol,pv_sof,pv_data}),
 .s_valid(pv_valid),.s_ready(pv_ready),
 .m_data(jpeg_ingress_data),.m_valid(jpeg_ingress_valid),.m_ready(jpeg_ingress_ready));
wire[127:0]jpeg_input_pixels=jpeg_ingress_data[127:0];
wire jpeg_input_sof=jpeg_ingress_data[128];
wire jpeg_input_eol=jpeg_ingress_data[129];
wire jpeg_input_eof=jpeg_ingress_data[130];
wire[255:0]jpeg_input_frames=jpeg_ingress_data[131+:256];
wire[7:0]jpeg_input_ready=jpeg_ingress_data[387+:8];
wire jpeg_input_enable=jpeg_ingress_data[395];
qj_color_mosaic_jpeg_stream #(.WIDTH(FRAME_WIDTH),.HEIGHT(FRAME_HEIGHT),.JPEG_HEIGHT(JPEG_HEIGHT)) compressor(
 .clk(clk),.rst(rst),.enable(jpeg_enable && jpeg_input_enable),
 .s_data(jpeg_input_pixels),.s_valid(jpeg_ingress_valid),.s_ready(jpeg_ingress_ready),.s_sof(jpeg_input_sof),.s_eol(jpeg_input_eol),.s_eof(jpeg_input_eof),
 .s_view_frames(jpeg_input_frames),.s_view_ready(jpeg_input_ready),.s_faults(o_fault),
 .m_byte(jpeg_byte),.m_valid(jpeg_valid),.m_ready(jpeg_ready),.m_sof(jpeg_sof),.m_eof(jpeg_eof),
 .descriptor_valid(descriptor_valid),.descriptor_ready(descriptor_ready),
 .jpeg_length(jpeg_length),.jpeg_crc32(jpeg_crc32),.record_sequence(record_sequence),
 .source_frames(source_frames),.source_ready(source_ready),.o_locked(jpeg_locked),.o_error(jpeg_error),
 .frames_started(frames_started),.frames_skipped(frames_skipped));
endmodule


module qj_jpeg_ingress_queue(
 input wire clk,rst,input wire[395:0]s_data,input wire s_valid,output wire s_ready,
 output wire[395:0]m_data,output wire m_valid,input wire m_ready
);
reg[395:0]front_word,back_word;
reg[1:0]count;
assign s_ready=!rst && count!=2;
assign m_valid=!rst && count!=0;
assign m_data=front_word;
wire push=s_valid && s_ready;
wire pop=m_valid && m_ready;
always@(posedge clk)begin
 if(rst)count<=0;
 else case({push,pop})
  2'b10:count<=count+1'b1;
  2'b01:count<=count-1'b1;
  default:count<=count;
 endcase
 // Payload validity is solely count. No reset-driven data mux is required.
 if(push && count==0)front_word<=s_data;
 else if(pop)begin
  if(count==2)front_word<=back_word;
  else if(push)front_word<=s_data;
 end
 if(push && count==1 && !pop)back_word<=s_data;
end
`ifndef SYNTHESIS
always@(posedge clk)if(!rst && count>2)$fatal(1,"JPEG ingress occupancy invalid");
`endif
endmodule
