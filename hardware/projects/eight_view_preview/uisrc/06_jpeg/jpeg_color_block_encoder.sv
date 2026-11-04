`timescale 1ns/1ps
// Baseline color JPEG 4:2:2. Input blocks MUST be MCU Y0,Y1,Cb,Cr order.
// Four gray samples per input beat. This sequential prototype does not meet
// 1080p60 and must not be placed directly on a non-backpressured camera bus.
module jpeg_color_block_encoder #(
    parameter integer IMAGE_WIDTH=32,IMAGE_HEIGHT=24
) (
    input wire clk,rst_n,i_start,
    input wire [31:0] i_pixels,input wire i_valid,output wire i_ready,
    output wire [7:0] o_byte,output wire o_valid,o_sof,o_eof,input wire o_ready,
    output reg o_done,output reg [31:0] o_bytes,output reg o_error
);
    `include "jpeg_color_tables.vh"
    localparam IDLE=0,HEADER=1,LOAD=2,ROW_DCT=3,COL_DCT=4,DC=5,AC=6,NEXT_BLOCK=7,FLUSH=8,EOI=9,AC_READ=10,DC_READ=11,COL_SAVE=12,DC_PREP=13,AC_PREP=14;
    localparam integer BLOCKS=(IMAGE_WIDTH/16)*(IMAGE_HEIGHT/8)*4;
    reg [3:0] state;
    reg [9:0] header_index;
    reg [5:0] sample_index,n;
    // Independent address-phase candidate. Only write addresses cross the
    // falling-edge register; interfaces, arithmetic and positive-edge RAM
    // writes retain their original cycles. The same clk supplies both edges.
    reg [2:0] dct_group_phase;
    reg [3:0] load_word_phase;
    always @(negedge clk or negedge rst_n)begin
      if(!rst_n)begin dct_group_phase<=0;load_word_phase<=0;end
      else begin dct_group_phase<=n[5:3];load_word_phase<=sample_index[5:2];end
    end

    // Separate performance candidate: one coefficient read is prefetched
    // while the current AC is accepted. Arithmetic/Huffman/bitstream unchanged.
    reg[5:0] ac_prefetch_address;
    reg [6:0] ac_index,zero_run;
    reg [31:0] block_number;
    reg eoi_second,flush_sent;
    reg signed [8:0] samples [0:63];
    reg signed [20:0] intermediate [0:63];
    reg signed [15:0] quantized [0:63];
    // max(abs(sum(C)))=2896. 128*2896*2896 < 2^30, so signed
    // 32-bit accumulation is sufficient, including the rounding offset.
    reg signed [31:0] accum [0:7];
    reg signed [15:0] previous_dc[0:2];
    // Each 4:2:2 MCU contains Y-left, Y-right, Cb, Cr in this order.
    wire [1:0] component_id=block_number[1] ? (block_number[0]?2:1):0;
    wire signed [15:0] current_dc=previous_dc[component_id];
    wire entropy_output=state!=IDLE && state!=HEADER && state!=EOI;
    wire [7:0] packed_byte;
    wire packed_valid,token_ready,flushed;
    reg token_valid;
    reg [31:0] token_bits;
    reg [5:0] token_length;
    reg queued_valid;
    reg [31:0] queued_bits;
    reg [5:0] queued_length;
    reg signed [15:0] ac_value;
    reg signed [16:0] dc_value;
    wire queue_ready=!queued_valid || token_ready;
    wire flush_request=state==FLUSH && !flush_sent && !queued_valid;
    jpeg_bit_packer packer(.clk(clk),.rst_n(rst_n),.i_clear(i_start && state==IDLE),
        .i_valid(queued_valid),.i_bits(queued_bits),.i_length(queued_length),.i_ready(token_ready),
        .i_flush(flush_request),.o_byte(packed_byte),.o_valid(packed_valid),
        .o_ready(entropy_output && o_ready),.o_flushed(flushed));
    assign i_ready=state==LOAD;
    assign o_valid=(state==HEADER || state==EOI) ? 1'b1 : entropy_output && packed_valid;
    assign o_byte=state==HEADER ? header_byte(header_index) : state==EOI ? (eoi_second ? 8'hd9 : 8'hff) : packed_byte;
    assign o_sof=state==HEADER && header_index==0;
    assign o_eof=state==EOI && eoi_second;

    function integer category(input integer value);
        integer a,j;begin a=value<0 ? -value:value;category=0;
            for(j=0;j<16;j=j+1) if(a>=(1<<j)) category=j+1;
        end
    endfunction
    function [31:0] amplitude(input integer value,input integer size);
        begin amplitude=value>=0 ? value : value+(1<<size)-1;end
    endfunction
    function signed [15:0] rounded_quant(input signed [31:0] total);
        reg [31:0] magnitude;begin
            magnitude=(total<0 ? -total:total)+32'd8388608;
            rounded_quant=total<0 ? -$signed(magnitude>>24) : $signed(magnitude>>24);
        end
    endfunction
    // Separate coefficient category/amplitude preparation from Huffman shift.
    // One extra cycle for each new AC coefficient; bytes are unchanged.
    reg [4:0] dc_category,ac_category;
    reg [16:0] dc_amplitude,ac_amplitude;
    wire [7:0] ac_huffman=8'd1+({4'b0,zero_run[3:0]}<<3)+({4'b0,zero_run[3:0]}<<1)+ac_category;
    function [4:0] narrow_category(input signed [16:0] v);
      reg [16:0] a;integer b;
      begin a=v[16] ? -v:v;narrow_category=0;
       for(b=0;b<16;b=b+1)if(a[b])narrow_category=b+1;
      end
    endfunction
    function [16:0] narrow_amplitude(input signed [16:0] v,input [4:0] count);
      reg [16:0] a,mask;
      begin a=v[16] ? -v:v;mask=(17'd1<<count)-1'b1;
       narrow_amplitude=v[16] ? (~a & mask):a;
      end
    endfunction
    always @(posedge clk or negedge rst_n)begin
      if(!rst_n)begin dc_category<=0;ac_category<=0;dc_amplitude<=0;ac_amplitude<=0;end
      else begin
       if(state==DC_PREP)begin dc_category<=narrow_category(dc_value);dc_amplitude<=narrow_amplitude(dc_value,narrow_category(dc_value));end
       if(state==AC_PREP)begin ac_category<=narrow_category($signed(ac_value));ac_amplitude<=narrow_amplitude($signed(ac_value),narrow_category($signed(ac_value)));end
      end
    end
    integer value,size;
    always @* begin
        token_valid=0;token_bits=0;token_length=0;value=0;size=0;
        if(state==DC) begin
            value=$signed(dc_value);size=dc_category;
            token_valid=1;token_length=4+size;
            token_bits=(size<<size)|dc_amplitude;
        end else if(state==AC) begin
            if(ac_index==64) begin
                if(zero_run!=0) begin token_valid=1;token_length=8;token_bits=0;end
            end else begin
                value=$signed(ac_value);size=ac_category;
                if(value!=0) begin
                    token_valid=1;
                    if(zero_run>=16) begin token_length=8;token_bits=1;end
                    else begin token_length=8+size;token_bits=({24'b0,ac_huffman}<<size)|ac_amplitude;end
                end
            end
        end
    end
    // Break coefficient read -> category/Huffman -> variable bit packing into
    // separately registered paths. This is an actual pipeline, no STA exception.
    always @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin queued_valid<=0;queued_bits<=0;queued_length<=0;end
        else if(i_start && state==IDLE) queued_valid<=0;
        else if(queue_ready) begin
            queued_valid<=token_valid;
            if(token_valid) begin queued_bits<=token_bits;queued_length<=token_length;end
        end
    end
    integer k;
    reg signed [31:0] total;
    reg dct_enable,dct_row;
    // Predict the following state's accumulator activity; no added DCT cycles.
    always @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin dct_enable<=0;dct_row<=0;end
        else begin
            case(state)
                LOAD:begin dct_enable<=i_valid && sample_index==60;dct_row<=1;end
                ROW_DCT:begin dct_enable<=1;if(n==63) dct_row<=0;end
                COL_DCT:begin dct_enable<=n[2:0]!=7;dct_row<=0;end
                COL_SAVE:begin dct_enable<=n!=63;dct_row<=0;end
                default:begin dct_enable<=0;dct_row<=0;end
            endcase
        end
    end
    // Same fixed-point operations, one explicit MAC expression per output.
    // Reuse row-last result for intermediate RAM instead of recomputing it in
    // a second control process. This is an opt-in resource experiment.
    wire signed [20:0] mac_sample=dct_row ? $signed(samples[n]):$signed(intermediate[{n[2:0],n[5:3]}]);
    wire signed [31:0] mac_next[0:7];
    for(genvar a=0;a<8;a=a+1)begin:shared_mac
        assign mac_next[a]=(n[2:0]==0 ? 32'sd0:accum[a])+mac_sample*$signed(cosine(a,n[2:0]));
        always @(posedge clk)if(dct_enable)accum[a]<=mac_next[a];
    end
    always @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin
            state<=IDLE;header_index<=0;sample_index<=0;n<=0;ac_index<=0;zero_run<=0;
            block_number<=0;eoi_second<=0;flush_sent<=0;previous_dc[0]<=0;previous_dc[1]<=0;previous_dc[2]<=0;
            o_done<=0;o_bytes<=0;o_error<=0;ac_value<=0;dc_value<=0;ac_prefetch_address<=0;
        end else begin
            o_done<=0;
            if(o_valid && o_ready) o_bytes<=o_bytes+1'b1;
            case(state)
                IDLE:if(i_start) begin
                    state<=HEADER;header_index<=0;o_bytes<=0;o_error<=0;
                    block_number<=0;previous_dc[0]<=0;previous_dc[1]<=0;previous_dc[2]<=0;flush_sent<=0;
                end
                HEADER:if(o_ready) begin
                    if(header_index==HEADER_SIZE-1) begin state<=LOAD;sample_index<=0;end
                    else header_index<=header_index+1'b1;
                end
                LOAD:if(i_valid) begin
                    for(k=0;k<4;k=k+1) samples[{load_word_phase,2'b00}+k]<=$signed({1'b0,i_pixels[k*8 +: 8]})-9'sd128;
                    if(sample_index==60) begin n<=0;state<=ROW_DCT;end
                    else sample_index<=sample_index+4;
                end
                ROW_DCT:begin
                    for(k=0;k<8;k=k+1) begin
                        if(n[2:0]==7) intermediate[{dct_group_phase,3'b0}+k]<=mac_next[k];
                    end
                    if(n==63) begin n<=0;state<=COL_DCT;end else n<=n+1'b1;
                end
                COL_DCT:begin
                    if(n[2:0]==7) state<=COL_SAVE;else n<=n+1'b1;
                end
                COL_SAVE:begin
                    for(k=0;k<8;k=k+1) quantized[k*8+dct_group_phase]<=rounded_quant(accum[k]);
                    if(n==63) state<=DC_READ;else begin n<=n+1'b1;state<=COL_DCT;end
                end
                DC_READ:begin dc_value<=$signed(quantized[0])-$signed(current_dc);state<=DC_PREP;end
                DC_PREP:state<=DC;
                AC_PREP:state<=AC;
                DC:if(queue_ready) begin
                    if(size>11) o_error<=1;
                    previous_dc[component_id]<=quantized[0];ac_index<=1;zero_run<=0;state<=AC_READ;ac_prefetch_address<=zigzag(1);
                end
                AC_READ:begin ac_value<=quantized[ac_prefetch_address];ac_prefetch_address<=zigzag(2);state<=AC_PREP;end
                AC:begin
                    if(ac_index==64) begin
                        if(zero_run==0 || queue_ready) begin zero_run<=0;state<=NEXT_BLOCK;end
                    end else if(value==0) begin
                        zero_run<=zero_run+1'b1;ac_index<=ac_index+1'b1;
                        if(ac_index<63)begin
                            ac_value<=quantized[ac_prefetch_address];state<=AC_PREP;
                            ac_prefetch_address<=zigzag(ac_index+2);
                        end
                    end
                    else if(queue_ready) begin
                        if(size>10) o_error<=1;
                        if(zero_run>=16) zero_run<=zero_run-16;
                        else begin
                            zero_run<=0;ac_index<=ac_index+1'b1;
                            if(ac_index<63)begin
                                ac_value<=quantized[ac_prefetch_address];state<=AC_PREP;
                                ac_prefetch_address<=zigzag(ac_index+2);
                            end
                        end
                    end
                end
                NEXT_BLOCK:begin
                    if(block_number==BLOCKS-1) state<=FLUSH;
                    else begin block_number<=block_number+1'b1;sample_index<=0;state<=LOAD;end
                end
                FLUSH:begin
                    if(flush_request && token_ready) flush_sent<=1;
                    if(flushed) begin state<=EOI;eoi_second<=0;end
                end
                EOI:if(o_ready) begin
                    if(eoi_second) begin o_done<=1;state<=IDLE;end
                    else eoi_second<=1;
                end
                default:state<=IDLE;
            endcase
        end
    end
endmodule
