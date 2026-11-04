`timescale 1ns/1ps
// Independent five-view preview memory service. Not a complete board top.
// Common cold reset only. AXI BYTE addresses; one write and one read in flight.
// Writes are AW-before-W and wait for B before publishing/reusing a buffer.
// Three 1MiB slots/view occupy [0,15MiB); ALL old video masters must be removed.
// A source bad token is control-only and is never written as a successful pixel.
// Reader faults lock closed and retain leases; external coordinated recovery is
// required. This module does not claim arbitrary AXI/reset fault recovery.
module qj_five_view_ddr #(
 parameter integer ADDR_WIDTH=30, ID_WIDTH=4,
 parameter integer TILE_W=640, TILE_H=360,
 parameter integer FRAME_WIDTH=1920, FRAME_HEIGHT=1080
)(
 input wire clk, input wire rst,
 input wire [639:0] s_data, input wire [4:0] s_valid,
 output reg [4:0] s_ready,
 input wire [4:0] s_sof,s_eof,s_bad,
 input wire [159:0] s_frame,
 output wire [127:0] m_data, output wire m_valid, input wire m_ready,
 output wire m_sof,m_eol,m_eof,
 output wire [159:0] o_display_frame, output wire [4:0] o_view_ready,
 output wire [31:0] o_fault, output reg [31:0] o_published,o_dropped,
 output wire [ID_WIDTH-1:0] m_axi_awid,
 output wire [ADDR_WIDTH-1:0] m_axi_awaddr,
 output wire [7:0] m_axi_awlen, output wire [2:0] m_axi_awsize,
 output wire [1:0] m_axi_awburst,
 output wire m_axi_awvalid, input wire m_axi_awready,
 output reg [127:0] m_axi_wdata, output reg [15:0] m_axi_wstrb,
 output wire m_axi_wlast, output reg m_axi_wvalid, input wire m_axi_wready,
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
 localparam integer TILE_BLOCKS=TILE_W/8;
 localparam integer FRAME_BLOCKS=FRAME_WIDTH/8;
 localparam integer TILE_WORDS=TILE_BLOCKS*TILE_H;
 localparam [2:0] W_IDLE=0,W_AW=1,W_DATA=2,W_B=3,W_LOCK=4;
 localparam [2:0] R_SNAP=0,R_CHECK=1,R_AR=2,R_DATA=3,R_DRAIN=4,R_LOCK=5;
 localparam [127:0] BLACK={4{32'h80108010}};
 reg [2:0] ws,rs;
 reg [4:0] active,dropping,latest_valid,leased;
 reg [1:0] work_slot[0:4],latest_slot[0:4],read_slot[0:4];
 reg [31:0] work_count[0:4],work_frame[0:4],latest_frame[0:4],read_frame[0:4];
 reg [2:0] rr,wview;
 reg [4:0] wbeats,wbeat;
 reg w_abort,w_pad;
 reg [ADDR_WIDTH-1:0] waddr,raddr;
 reg [31:0] w_fault,r_fault;
 reg [15:0] rxblock,ry;
 reg [4:0] rbeats,rbeat;
 integer i,j,c,selected,free_slot;
 reg invalid_head,keep_head;
 reg [2:0] rview;
 reg [15:0] local_block,local_y;
 reg mapped_view;
 reg [4:0] read_length;
 reg [31:0] remaining_words;

 function automatic [ADDR_WIDTH-1:0] slot_base(input integer view,input integer slot);
  reg [31:0] base;
  begin base=(view*3+slot)*32'h00100000;slot_base=base[ADDR_WIDTH-1:0]; end
 endfunction

 assign m_axi_awid={ID_WIDTH{1'b0}};
 assign m_axi_awaddr=waddr;
 assign m_axi_awlen=wbeats-1'b1;
 assign m_axi_awsize=3'd4;
 assign m_axi_awburst=2'b01;
 assign m_axi_awvalid=(ws==W_AW);
 assign m_axi_wlast=(wbeat==wbeats-1'b1);
 assign m_axi_bready=(ws==W_B);
 assign m_axi_arid={{(ID_WIDTH-1){1'b0}},1'b1};
 assign m_axi_araddr=raddr;
 assign m_axi_arlen=rbeats-1'b1;
 assign m_axi_arsize=3'd4;
 assign m_axi_arburst=2'b01;
 assign m_axi_arvalid=(rs==R_AR);
 wire r_id_ok=(m_axi_rid=={{(ID_WIDTH-1){1'b0}},1'b1});
 wire r_last_ok=(m_axi_rlast==(rbeat==rbeats-1'b1));
 wire r_good=r_id_ok && m_axi_rresp==0 && r_last_ok;
 wire black_output=(rs==R_CHECK && !mapped_view);
 assign m_valid=black_output || (rs==R_DATA && m_axi_rvalid && r_good);
 assign m_data=black_output ? BLACK : m_axi_rdata;
 assign m_sof=m_valid && rxblock==0 && ry==0;
 assign m_eol=m_valid && rxblock==FRAME_BLOCKS-1;
 assign m_eof=m_eol && ry==FRAME_HEIGHT-1;
 assign m_axi_rready=(rs==R_DRAIN) || (rs==R_DATA && (r_good ? m_ready : 1'b1));
 assign o_fault=w_fault|r_fault;
 assign o_view_ready=leased;
 genvar gv;
 generate for(gv=0;gv<5;gv=gv+1) begin:g_meta
  assign o_display_frame[gv*32+:32]=read_frame[gv];
 end endgenerate

 // Select a source only between transactions. A slot under construction remains
 // private even while the arbiter services a different view's burst.
 always @* begin
  selected=-1;
  for(i=0;i<5;i=i+1) begin
   c=rr+i;if(c>=5)c=c-5;
   if(selected<0 && s_valid[c])selected=c;
  end
  free_slot=-1;
  if(selected>=0) begin
   for(j=0;j<3;j=j+1)
    if(free_slot<0 && !(latest_valid[selected] && latest_slot[selected]==j) &&
       !(leased[selected] && read_slot[selected]==j)) free_slot=j;
  end
  remaining_words=0;
  if(selected>=0) remaining_words=TILE_WORDS-work_count[selected];
  invalid_head=0;keep_head=0;
  if(ws==W_DATA && !w_pad && s_valid[wview]) begin
   keep_head=s_sof[wview] && work_count[wview]!=0;
   invalid_head=s_bad[wview] || keep_head ||
      (s_frame[wview*32+:32]!=work_frame[wview]) ||
      (s_sof[wview]!=(work_count[wview]==0)) ||
      (s_eof[wview]!=(work_count[wview]==TILE_WORDS-1));
  end
  s_ready=0;m_axi_wvalid=0;m_axi_wdata=0;m_axi_wstrb=0;
  if(ws==W_IDLE && selected>=0) begin
   if(s_bad[selected] || (!s_sof[selected] && (!active[selected] || dropping[selected])) ||
      (s_sof[selected] && free_slot<0)) s_ready[selected]=1;
  end
  if(ws==W_DATA) begin
   if(w_pad) begin m_axi_wvalid=1; end
   else if(s_valid[wview]) begin
    m_axi_wvalid=1;
    if(!invalid_head) begin
     m_axi_wdata=s_data[wview*128+:128];m_axi_wstrb=16'hffff;
    end
    // Do not eat the SOF of a replacement frame while padding the old burst.
    s_ready[wview]=m_axi_wready && !keep_head;
   end
  end
 end

 always @(posedge clk) begin : writer
  integer k;
  if(rst) begin
   ws<=W_IDLE;active<=0;dropping<=0;latest_valid<=0;rr<=0;wview<=0;
   wbeats<=1;wbeat<=0;w_abort<=0;w_pad<=0;waddr<=0;
   w_fault<=0;o_published<=0;o_dropped<=0;
   for(k=0;k<5;k=k+1) begin
    work_slot[k]<=0;latest_slot[k]<=0;work_count[k]<=0;
    work_frame[k]<=0;latest_frame[k]<=0;
   end
  end else begin
   case(ws)
    W_IDLE: if(selected>=0) begin
     rr<=(selected==4)?0:selected+1;
     if(s_bad[selected]) begin
      if(active[selected] || s_sof[selected])o_dropped<=o_dropped+1;
      active[selected]<=0;dropping[selected]<=!s_eof[selected];work_count[selected]<=0;
     end else if(s_sof[selected]) begin
      if(active[selected])o_dropped<=o_dropped+1;
      if(free_slot<0) begin
       active[selected]<=0;dropping[selected]<=1;work_count[selected]<=0;
       if(!active[selected])o_dropped<=o_dropped+1;
      end else begin
       active[selected]<=1;dropping[selected]<=0;work_slot[selected]<=free_slot;
       work_count[selected]<=0;work_frame[selected]<=s_frame[selected*32+:32];
       wview<=selected;waddr<=slot_base(selected,free_slot);
       wbeats<=(TILE_WORDS<16)?TILE_WORDS:16;wbeat<=0;w_abort<=0;w_pad<=0;ws<=W_AW;
      end
     end else if(active[selected] && !dropping[selected]) begin
      wview<=selected;
      waddr<=slot_base(selected,work_slot[selected])+work_count[selected]*16;
      wbeats<=(remaining_words<16)?remaining_words:16;
      wbeat<=0;w_abort<=0;w_pad<=0;ws<=W_AW;
     end else if(s_eof[selected]) dropping[selected]<=0;
    end
    W_AW: if(m_axi_awready)ws<=W_DATA;
    W_DATA: if(m_axi_wvalid && m_axi_wready) begin
     if(invalid_head && !w_pad) begin w_abort<=1;w_pad<=1;end
     work_count[wview]<=work_count[wview]+1;
     if(wbeat==wbeats-1'b1) ws<=W_B;
     else wbeat<=wbeat+1'b1;
    end
    W_B: if(m_axi_bvalid) begin
     if(m_axi_bid!={ID_WIDTH{1'b0}} || m_axi_bresp!=0) begin
      w_fault<=32'h1;active[wview]<=0;o_dropped<=o_dropped+1;ws<=W_LOCK;
     end else begin
      ws<=W_IDLE;
      if(w_abort) begin
       active[wview]<=0;work_count[wview]<=0;o_dropped<=o_dropped+1;
      end else if(work_count[wview]==TILE_WORDS) begin
       latest_valid[wview]<=1;latest_slot[wview]<=work_slot[wview];
       latest_frame[wview]<=work_frame[wview];active[wview]<=0;
       work_count[wview]<=0;o_published<=o_published+1;
      end
     end
    end
    default: ws<=W_LOCK;
   endcase
  end
 end

 // Tile boundaries are capped in every AR. Padded output areas generate black
 // locally and do not create speculative reads of unallocated buffers.
 always @* begin
  rview=7;local_block=0;local_y=0;
  if(ry<TILE_H) begin
   local_y=ry;
   if(rxblock<TILE_BLOCKS)begin rview=0;local_block=rxblock;end
   else if(rxblock<2*TILE_BLOCKS)begin rview=1;local_block=rxblock-TILE_BLOCKS;end
   else if(rxblock<3*TILE_BLOCKS)begin rview=2;local_block=rxblock-2*TILE_BLOCKS;end
  end else if(ry<2*TILE_H) begin
   local_y=ry-TILE_H;
   if(rxblock<TILE_BLOCKS)begin rview=3;local_block=rxblock;end
   else if(rxblock<2*TILE_BLOCKS)begin rview=4;local_block=rxblock-TILE_BLOCKS;end
  end
  mapped_view=0;
  if(rview<5)mapped_view=leased[rview];
  read_length=(TILE_BLOCKS-local_block<16)?TILE_BLOCKS-local_block:16;
 end

 always @(posedge clk) begin : reader
  integer k;
  if(rst) begin
   rs<=R_SNAP;leased<=0;rxblock<=0;ry<=0;raddr<=0;rbeats<=1;rbeat<=0;r_fault<=0;
   for(k=0;k<5;k=k+1)begin read_slot[k]<=0;read_frame[k]<=0;end
  end else begin
   case(rs)
    R_SNAP: begin
     leased<=latest_valid;rxblock<=0;ry<=0;
     for(k=0;k<5;k=k+1)begin read_slot[k]<=latest_slot[k];read_frame[k]<=latest_frame[k];end
     rs<=R_CHECK;
    end
    R_CHECK: if(mapped_view) begin
     raddr<=slot_base(rview,read_slot[rview])+local_y*TILE_W*2+local_block*16;
     rbeats<=read_length;rbeat<=0;rs<=R_AR;
    end else if(m_ready) begin
     if(rxblock==FRAME_BLOCKS-1)begin
      rxblock<=0;
      if(ry==FRAME_HEIGHT-1)begin ry<=0;leased<=0;rs<=R_SNAP;end
      else ry<=ry+1'b1;
     end else rxblock<=rxblock+1'b1;
    end
    R_AR: if(m_axi_arready)rs<=R_DATA;
    R_DATA: if(m_axi_rvalid && m_axi_rready) begin
     if(!r_good) begin
      r_fault<=(r_id_ok?32'h0:32'h8) |
               (m_axi_rresp==0?32'h0:32'h2) | (r_last_ok?32'h0:32'h4);
      rs<=m_axi_rlast?R_LOCK:R_DRAIN;
     end else begin
      if(rxblock==FRAME_BLOCKS-1)begin
       rxblock<=0;
       if(ry==FRAME_HEIGHT-1)begin ry<=0;leased<=0;rs<=R_SNAP;end
       else ry<=ry+1'b1;
      end else rxblock<=rxblock+1'b1;
      if(rbeat==rbeats-1'b1) begin
       if(!(rxblock==FRAME_BLOCKS-1 && ry==FRAME_HEIGHT-1))rs<=R_CHECK;
      end else rbeat<=rbeat+1'b1;
     end
    end
    R_DRAIN: if(m_axi_rvalid && m_axi_rlast)rs<=R_LOCK;
    default: rs<=R_LOCK;
   endcase
  end
 end

 // Configuration contract; no full-system resource or STA claim.
 initial begin
  if(ADDR_WIDTH<24 || ADDR_WIDTH>32 || ID_WIDTH<1 || TILE_W%8!=0 ||
     FRAME_WIDTH!=3*TILE_W || FRAME_HEIGHT<2*TILE_H || TILE_WORDS<1 ||
     TILE_WORDS*16>1048576) $error("Unsupported five-view geometry/address configuration");
 end
endmodule
