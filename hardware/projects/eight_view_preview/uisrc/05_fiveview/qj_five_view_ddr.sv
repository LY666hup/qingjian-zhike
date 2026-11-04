`timescale 1ns/1ps
// Independent eight-view preview memory service. Not a complete board top.
// Common cold reset only. AXI BYTE addresses; one write and one read in flight.
// Writes are AW-before-W and wait for B before publishing/reusing a buffer.
// Three 1MiB slots/view occupy [0,24MiB); ALL old video masters must be removed.
// A source bad token is control-only and is never written as a successful pixel.
// Reader faults lock closed and retain leases; external coordinated recovery is
// required. This module does not claim arbitrary AXI/reset fault recovery.
module qj_five_view_ddr #(
 parameter integer ADDR_WIDTH=30, ID_WIDTH=4,
 parameter integer TILE_W=640, TILE_H=360,
 parameter integer FRAME_WIDTH=1920, FRAME_HEIGHT=1080,
 // Opt-in source-gap bound in clk cycles. Zero preserves the parent policy.
 // Integration must choose a bound above the actual permitted interword gap.
 parameter integer SOURCE_EMPTY_CYCLES=0,
 // Set only with conservative source FIFO read-domain credits connected.
 // Never issue AW for more words than the selected FIFO already contains.
 parameter integer KNOWN_BUFFERED_BURSTS=0
)(
 input wire clk, input wire rst,
 input wire [1023:0] s_data, input wire [7:0] s_valid,
 output reg [7:0] s_ready,
 input wire [7:0] s_sof,s_eof,s_bad,
 input wire [255:0] s_frame,
 input wire [39:0] s_buffered,
 output wire [127:0] m_data, output wire m_valid, input wire m_ready,
 output wire m_sof,m_eol,m_eof,
 output wire [255:0] o_display_frame, output wire [7:0] o_view_ready,
 output wire [31:0] o_fault, output reg [31:0] o_published,o_dropped,
 output wire [255:0] o_source_timeouts,
 output wire [495:0] o_writer_diag,
 // Per-view last error: {sampled/work word count32,actual source observation32,detail32}.
 output wire [767:0] o_source_errors,
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
 localparam [2:0] W_IDLE=0,W_AW=1,W_DATA=2,W_B=3,W_LOCK=4,W_PREP=5;
 localparam [2:0] R_SNAP=0,R_CHECK=1,R_AR=2,R_DATA=3,R_DRAIN=4,R_LOCK=5;
 localparam [127:0] BLACK={4{32'h80108010}};
 reg [2:0] ws,rs;
 reg [7:0] active,dropping,latest_valid,leased;
 reg [1:0] work_slot[0:7],latest_slot[0:7],read_slot[0:7];
 reg [31:0] work_count[0:7],work_frame[0:7],latest_frame[0:7],read_frame[0:7];
 localparam integer EMPTY_WAIT_BITS=(SOURCE_EMPTY_CYCLES>1)?$clog2(SOURCE_EMPTY_CYCLES):1;
 reg [EMPTY_WAIT_BITS-1:0] empty_wait;
 reg [31:0] source_timeouts[0:7];
 reg [31:0] write_idle_age;
 reg [2:0] rr,wview;
 reg [4:0] wbeats,wbeat;
 reg [4:0] reserved_credit;
 // Legal service slots hold at most TILE_WORDS data words;even a failed
 // accepted burst can add at most15 padding words before its B/drain.
 localparam integer BURST_COUNT_BITS=($clog2(TILE_WORDS+17)>8)?$clog2(TILE_WORDS+17):8;
 reg [BURST_COUNT_BITS-1:0] burst_words_done;
 reg [31:0] burst_observed_frame;
 reg burst_first_word;
 reg w_abort,w_pad;
 reg [ADDR_WIDTH-1:0] waddr,raddr;
 reg [31:0] w_fault,r_fault;
 reg [15:0] rxblock,ry;
 reg [4:0] rbeats,rbeat;
 integer i,j,c,selected,free_slot;
 reg invalid_head,keep_head;
 reg [3:0] rview;
 reg [15:0] local_block,local_y;
 reg mapped_view;
 reg [4:0] read_length;
 reg [31:0] remaining_words;
 reg [4:0] admitted_beats;
 reg [8:0] write_4k_words,read_4k_words;
 reg [31:0] read_word_offset;

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
 generate for(gv=0;gv<8;gv=gv+1) begin:g_meta
  assign o_display_frame[gv*32+:32]=read_frame[gv];
  assign o_source_timeouts[gv*32+:32]=source_timeouts[gv];
 end endgenerate
 wire source_empty_timeout=(SOURCE_EMPTY_CYCLES>0) && ws==W_DATA &&
      !w_pad && !s_valid[wview] && empty_wait==SOURCE_EMPTY_CYCLES-1;
 // Error records travel with the existing FIFO abort token:no new CDC bus.
 reg[95:0]last_source_error[0:7];
 genvar ge;
 generate for(ge=0;ge<8;ge=ge+1)begin:g_error_record
  assign o_source_errors[ge*96+:96]=last_source_error[ge];
 end endgenerate
 reg[7:0]writer_error_reason;
 always@*begin
  writer_error_reason=0;
  if(s_frame[wview*32+:32]!=burst_observed_frame)writer_error_reason[4]=1;
  if(s_sof[wview]!=burst_first_word)writer_error_reason[5]=1;
  if(s_eof[wview]!=(burst_words_done==TILE_WORDS-1))writer_error_reason[6]=1;
  if(keep_head)writer_error_reason[7]=1;
 end
 always@(posedge clk)begin:error_telemetry
  integer ek;
  if(rst)for(ek=0;ek<8;ek=ek+1)last_source_error[ek]<=0;
  else begin
   if(source_empty_timeout)
    last_source_error[wview]<={work_count[wview],work_frame[wview],1'b0,1'b1,1'b1,11'h7ff,10'h3ff,8'b0};
   if(ws==W_DATA && m_axi_wvalid && m_axi_wready && invalid_head && !w_pad && !s_bad[wview])
    last_source_error[wview]<={work_count[wview],s_frame[wview*32+:32],1'b0,1'b0,1'b1,11'h7ff,10'h3ff,writer_error_reason};
   for(ek=0;ek<8;ek=ek+1)
    if(s_valid[ek] && s_ready[ek] && s_bad[ek] && s_data[ek*128+96+:32]==32'h52454a51)
     last_source_error[ek]<={s_data[ek*128+64+:32],s_frame[ek*32+:32],s_data[ek*128+32+:32]};
  end
 end
 // Never withdraw an asserted AXI WVALID. This condition requires WVALID=0:
 // the selected upstream FIFO is empty and padding has not started.
 // All fields are already in this DDR clock domain. Metadata snapshots them
 // at accepted mosaic SOF; this is not a sensor-clock or exposure snapshot.
 wire [31:0] empty_wait32=empty_wait;
 assign o_writer_diag={write_idle_age,empty_wait32,o_source_timeouts,
   o_dropped,o_published,work_frame[wview],work_count[wview],8'b0,
   2'b0,m_axi_bready,m_axi_bvalid,m_axi_wready,m_axi_wvalid,m_axi_awready,m_axi_awvalid,
   s_ready,s_valid,5'b0,wview,5'b0,ws};
 always @(posedge clk)begin
  if(rst)write_idle_age<=0;
  else if(ws==W_IDLE || (m_axi_awvalid&&m_axi_awready) ||
          (m_axi_wvalid&&m_axi_wready) || (m_axi_bvalid&&m_axi_bready))write_idle_age<=0;
  else if(write_idle_age!=32'hffffffff)write_idle_age<=write_idle_age+1'b1;
 end

 // Select a source only between transactions. A slot under construction remains
 // private even while the arbiter services a different view's burst.
 always @* begin
  // Constant-index priority chains replace a 32-bit dynamic-index loop.
  // Identical round-robin order; no pipeline or transaction changes.
  selected=-1;
  case(rr)
   3'd0: begin
    if(s_valid[0])selected=0;
    else if(s_valid[1])selected=1;
    else if(s_valid[2])selected=2;
    else if(s_valid[3])selected=3;
    else if(s_valid[4])selected=4;
    else if(s_valid[5])selected=5;
    else if(s_valid[6])selected=6;
    else if(s_valid[7])selected=7;
   end
   3'd1: begin
    if(s_valid[1])selected=1;
    else if(s_valid[2])selected=2;
    else if(s_valid[3])selected=3;
    else if(s_valid[4])selected=4;
    else if(s_valid[5])selected=5;
    else if(s_valid[6])selected=6;
    else if(s_valid[7])selected=7;
    else if(s_valid[0])selected=0;
   end
   3'd2: begin
    if(s_valid[2])selected=2;
    else if(s_valid[3])selected=3;
    else if(s_valid[4])selected=4;
    else if(s_valid[5])selected=5;
    else if(s_valid[6])selected=6;
    else if(s_valid[7])selected=7;
    else if(s_valid[0])selected=0;
    else if(s_valid[1])selected=1;
   end
   3'd3: begin
    if(s_valid[3])selected=3;
    else if(s_valid[4])selected=4;
    else if(s_valid[5])selected=5;
    else if(s_valid[6])selected=6;
    else if(s_valid[7])selected=7;
    else if(s_valid[0])selected=0;
    else if(s_valid[1])selected=1;
    else if(s_valid[2])selected=2;
   end
   3'd4: begin
    if(s_valid[4])selected=4;
    else if(s_valid[5])selected=5;
    else if(s_valid[6])selected=6;
    else if(s_valid[7])selected=7;
    else if(s_valid[0])selected=0;
    else if(s_valid[1])selected=1;
    else if(s_valid[2])selected=2;
    else if(s_valid[3])selected=3;
   end
   3'd5: begin
    if(s_valid[5])selected=5;
    else if(s_valid[6])selected=6;
    else if(s_valid[7])selected=7;
    else if(s_valid[0])selected=0;
    else if(s_valid[1])selected=1;
    else if(s_valid[2])selected=2;
    else if(s_valid[3])selected=3;
    else if(s_valid[4])selected=4;
   end
   3'd6: begin
    if(s_valid[6])selected=6;
    else if(s_valid[7])selected=7;
    else if(s_valid[0])selected=0;
    else if(s_valid[1])selected=1;
    else if(s_valid[2])selected=2;
    else if(s_valid[3])selected=3;
    else if(s_valid[4])selected=4;
    else if(s_valid[5])selected=5;
   end
   3'd7: begin
    if(s_valid[7])selected=7;
    else if(s_valid[0])selected=0;
    else if(s_valid[1])selected=1;
    else if(s_valid[2])selected=2;
    else if(s_valid[3])selected=3;
    else if(s_valid[4])selected=4;
    else if(s_valid[5])selected=5;
    else if(s_valid[6])selected=6;
   end
   default:selected=-1;
  endcase
  free_slot=-1;
  if(selected>=0) begin
   for(j=0;j<3;j=j+1)
    if(free_slot<0 && !(latest_valid[selected] && latest_slot[selected]==j) &&
       !(leased[selected] && read_slot[selected]==j)) free_slot=j;
  end
  // W_IDLE reserves a source without popping it. Registered scalar inputs
  // then drive the min/count/page calculation in W_PREP,breaking the original
  // FIFO-count -> source arbitration -> page cap -> AWLEN one-cycle path.
  remaining_words=TILE_WORDS-burst_words_done;
  admitted_beats=(remaining_words<16)?remaining_words:16;
  if(KNOWN_BUFFERED_BURSTS && reserved_credit<admitted_beats)
   admitted_beats=reserved_credit;
  // Every1MiB slot is4KiB-aligned. Count is in128bit(16byte) beats.
  // Short preceding bursts remove the old256byte address alignment;cap the
  // next AW at the remaining beats of its current4KiB page as well.
  write_4k_words=9'd256;
  write_4k_words=9'd256-{1'b0,burst_words_done[7:0]};
  if(write_4k_words<admitted_beats)admitted_beats=write_4k_words[4:0];
  invalid_head=0;keep_head=0;
  if(ws==W_DATA && !w_pad && s_valid[wview]) begin
   keep_head=s_sof[wview] && !burst_first_word;
   invalid_head=s_bad[wview] || keep_head ||
      (s_frame[wview*32+:32]!=burst_observed_frame) ||
      (s_sof[wview]!=burst_first_word) ||
      (s_eof[wview]!=(burst_words_done==TILE_WORDS-1));
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
   w_fault<=0;o_published<=0;o_dropped<=0;empty_wait<=0;
   reserved_credit<=0;burst_words_done<=0;burst_observed_frame<=0;burst_first_word<=1;
   for(k=0;k<8;k=k+1) begin
    work_slot[k]<=0;latest_slot[k]<=0;work_count[k]<=0;
    work_frame[k]<=0;latest_frame[k]<=0;source_timeouts[k]<=0;
   end
  end else begin
   if(ws!=W_DATA || w_pad || s_valid[wview])empty_wait<=0;
   else if(SOURCE_EMPTY_CYCLES>0)empty_wait<=empty_wait+1'b1;
   case(ws)
    W_IDLE: if(selected>=0) begin
     rr<=(selected==7)?0:selected+1;
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
       reserved_credit<=s_buffered[selected*5+:5];burst_words_done<=0;
       burst_observed_frame<=s_frame[selected*32+:32];burst_first_word<=1;
       wbeat<=0;w_abort<=0;w_pad<=0;ws<=W_PREP;
      end
     end else if(active[selected] && !dropping[selected]) begin
      wview<=selected;
      waddr<=slot_base(selected,work_slot[selected])+work_count[selected]*16;
      reserved_credit<=s_buffered[selected*5+:5];burst_words_done<=work_count[selected];
      burst_observed_frame<=work_frame[selected];burst_first_word<=(work_count[selected]==0);
      wbeat<=0;w_abort<=0;w_pad<=0;ws<=W_PREP;
     end else if(s_eof[selected]) dropping[selected]<=0;
    end
    W_PREP: begin
     // A source holding entry cannot change before W_DATA accepts it. The
     // reserved conservative credit remains valid while this state waits.
     if(!KNOWN_BUFFERED_BURSTS || reserved_credit!=0)begin wbeats<=admitted_beats;ws<=W_AW;end
     else ws<=W_IDLE; // Never emit AWLEN255 for a zero-credit caller.
    end
    W_AW: if(m_axi_awready)ws<=W_DATA;
    W_DATA: begin
     if(source_empty_timeout)begin
      // Finish the already accepted AW burst using WSTRB=0, then wait for B.
      // The partial frame remains private and can never become latest_frame.
      w_abort<=1;w_pad<=1;empty_wait<=0;
      source_timeouts[wview]<=source_timeouts[wview]+1'b1;
     end
     if(m_axi_wvalid && m_axi_wready) begin
     if(invalid_head && !w_pad) begin w_abort<=1;w_pad<=1;end
     work_count[wview]<=work_count[wview]+1;
     burst_words_done<=burst_words_done+1;burst_first_word<=0;
     if(wbeat==wbeats-1'b1) ws<=W_B;
     else wbeat<=wbeat+1'b1;
     end
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
  rview=15;local_block=0;local_y=0;
  if(ry<TILE_H) begin
   local_y=ry;
   if(rxblock<TILE_BLOCKS)begin rview=0;local_block=rxblock;end
   else if(rxblock<2*TILE_BLOCKS)begin rview=1;local_block=rxblock-TILE_BLOCKS;end
   else if(rxblock<3*TILE_BLOCKS)begin rview=2;local_block=rxblock-2*TILE_BLOCKS;end
  end else if(ry<2*TILE_H) begin
   local_y=ry-TILE_H;
   if(rxblock<TILE_BLOCKS)begin rview=3;local_block=rxblock;end
   else if(rxblock<2*TILE_BLOCKS)begin rview=4;local_block=rxblock-TILE_BLOCKS;end
  end else if(ry<3*TILE_H) begin
   local_y=ry-2*TILE_H;
   if(rxblock<TILE_BLOCKS)begin rview=5;local_block=rxblock;end
   else if(rxblock<2*TILE_BLOCKS)begin rview=6;local_block=rxblock-TILE_BLOCKS;end
   else if(rxblock<3*TILE_BLOCKS)begin rview=7;local_block=rxblock-2*TILE_BLOCKS;end
  end
  mapped_view=0;
  if(rview<8)mapped_view=leased[rview];
  read_length=(TILE_BLOCKS-local_block<16)?TILE_BLOCKS-local_block:16;
  // Keep the reader safe for legal tile widths that are not multiples of128.
  read_word_offset=local_y*TILE_BLOCKS+local_block;
  read_4k_words=9'd256-{1'b0,read_word_offset[7:0]};
  if(read_4k_words<read_length)read_length=read_4k_words[4:0];
 end

 always @(posedge clk) begin : reader
  integer k;
  if(rst) begin
   rs<=R_SNAP;leased<=0;rxblock<=0;ry<=0;raddr<=0;rbeats<=1;rbeat<=0;r_fault<=0;
   for(k=0;k<8;k=k+1)begin read_slot[k]<=0;read_frame[k]<=0;end
  end else begin
   case(rs)
    R_SNAP: begin
     leased<=latest_valid;rxblock<=0;ry<=0;
     for(k=0;k<8;k=k+1)begin read_slot[k]<=latest_slot[k];read_frame[k]<=latest_frame[k];end
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
   if(ADDR_WIDTH<25 || ADDR_WIDTH>32 || ID_WIDTH<1 || SOURCE_EMPTY_CYCLES<0 || TILE_W%8!=0 ||
     FRAME_WIDTH!=3*TILE_W || FRAME_HEIGHT<3*TILE_H || TILE_WORDS<1 ||
     TILE_WORDS*16>1048576) $error("Unsupported eight-view geometry/address configuration");
 end
`ifndef SYNTHESIS
 always @(posedge clk)if(!rst)begin
  if(m_axi_awvalid && m_axi_awready &&
    ({20'd0,m_axi_awaddr[11:0]}+(({24'd0,m_axi_awlen}+32'd1)<<4)>32'd4096))
   $fatal(1,"AXI AW crosses4KiB");
  if(m_axi_arvalid && m_axi_arready &&
    ({20'd0,m_axi_araddr[11:0]}+(({24'd0,m_axi_arlen}+32'd1)<<4)>32'd4096))
   $fatal(1,"AXI AR crosses4KiB");
 end
 always @(posedge clk)if(!rst && KNOWN_BUFFERED_BURSTS && ws==W_IDLE && selected>=0 && !s_bad[selected])
  if(s_buffered[selected*5+:5]===5'd0 || (^s_buffered[selected*5+:5]===1'bx))
   $fatal(1,"Buffered burst source credit missing");
`endif
endmodule
