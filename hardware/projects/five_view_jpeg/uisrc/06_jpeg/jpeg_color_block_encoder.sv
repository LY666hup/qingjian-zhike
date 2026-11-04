`timescale 1ns/1ps
// Baseline color JPEG 4:2:2. Input blocks MUST be MCU Y0,Y1,Cb,Cr order.
// Four gray samples per input beat. This sequential prototype does not meet
// 1080p60 and must not be placed directly on a non-backpressured camera bus.
module jpeg_color_block_encoder #(
    parameter integer IMAGE_WIDTH=32,IMAGE_HEIGHT=24,SEGMENT_ONLY=0
) (
    input wire clk,rst_n,i_start,
    input wire [31:0] i_pixels,input wire i_valid,output wire i_ready,
    output wire [7:0] o_byte,output wire o_valid,o_sof,o_eof,input wire o_ready,
    output reg o_done,output reg [31:0] o_bytes,output reg o_error
);
    `include "jpeg_color_tables.vh"
    localparam IDLE=0,HEADER=1,LOAD=2,ROW_DCT=3,COL_DCT=4,DC=5,AC=6,NEXT_BLOCK=7,FLUSH=8,EOI=9,AC_READ=10,DC_READ=11,COL_SAVE=12,DC_PREP=13,AC_PREP=14,FAST_DC_SAVE=15;
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
    // Byte-identical fast path for blocks whose quantized ACs are all zero.
    // rounded_quant(x)==0 iff -8388608 < x < 8388608. Track this while
    // coefficients are saved; avoid a 64-way coefficient scan/priority mux.
    function automatic [5:0] zigzag_rank(input [5:0] location);
    begin case(location)
      6'd0:zigzag_rank=6'd0;
      6'd1:zigzag_rank=6'd1;
      6'd2:zigzag_rank=6'd5;
      6'd3:zigzag_rank=6'd6;
      6'd4:zigzag_rank=6'd14;
      6'd5:zigzag_rank=6'd15;
      6'd6:zigzag_rank=6'd27;
      6'd7:zigzag_rank=6'd28;
      6'd8:zigzag_rank=6'd2;
      6'd9:zigzag_rank=6'd4;
      6'd10:zigzag_rank=6'd7;
      6'd11:zigzag_rank=6'd13;
      6'd12:zigzag_rank=6'd16;
      6'd13:zigzag_rank=6'd26;
      6'd14:zigzag_rank=6'd29;
      6'd15:zigzag_rank=6'd42;
      6'd16:zigzag_rank=6'd3;
      6'd17:zigzag_rank=6'd8;
      6'd18:zigzag_rank=6'd12;
      6'd19:zigzag_rank=6'd17;
      6'd20:zigzag_rank=6'd25;
      6'd21:zigzag_rank=6'd30;
      6'd22:zigzag_rank=6'd41;
      6'd23:zigzag_rank=6'd43;
      6'd24:zigzag_rank=6'd9;
      6'd25:zigzag_rank=6'd11;
      6'd26:zigzag_rank=6'd18;
      6'd27:zigzag_rank=6'd24;
      6'd28:zigzag_rank=6'd31;
      6'd29:zigzag_rank=6'd40;
      6'd30:zigzag_rank=6'd44;
      6'd31:zigzag_rank=6'd53;
      6'd32:zigzag_rank=6'd10;
      6'd33:zigzag_rank=6'd19;
      6'd34:zigzag_rank=6'd23;
      6'd35:zigzag_rank=6'd32;
      6'd36:zigzag_rank=6'd39;
      6'd37:zigzag_rank=6'd45;
      6'd38:zigzag_rank=6'd52;
      6'd39:zigzag_rank=6'd54;
      6'd40:zigzag_rank=6'd20;
      6'd41:zigzag_rank=6'd22;
      6'd42:zigzag_rank=6'd33;
      6'd43:zigzag_rank=6'd38;
      6'd44:zigzag_rank=6'd46;
      6'd45:zigzag_rank=6'd51;
      6'd46:zigzag_rank=6'd55;
      6'd47:zigzag_rank=6'd60;
      6'd48:zigzag_rank=6'd21;
      6'd49:zigzag_rank=6'd34;
      6'd50:zigzag_rank=6'd37;
      6'd51:zigzag_rank=6'd47;
      6'd52:zigzag_rank=6'd50;
      6'd53:zigzag_rank=6'd56;
      6'd54:zigzag_rank=6'd59;
      6'd55:zigzag_rank=6'd61;
      6'd56:zigzag_rank=6'd35;
      6'd57:zigzag_rank=6'd36;
      6'd58:zigzag_rank=6'd48;
      6'd59:zigzag_rank=6'd49;
      6'd60:zigzag_rank=6'd57;
      6'd61:zigzag_rank=6'd58;
      6'd62:zigzag_rank=6'd62;
      6'd63:zigzag_rank=6'd63;
      default:zigzag_rank=0;
    endcase end
    endfunction
    wire [7:0] column_has_ac;
    reg [5:0] last_ac_index;
    reg [5:0] column_last_ac;
    integer column_k;
    // Same exact nonzero predicate used by the saved quantized coefficients.
    // Track the final nonzero position in zigzag order while saving columns.
    // This changes only how trailing zeros are traversed, never quantization.
    always @* begin
      column_last_ac=0;
      for(column_k=0;column_k<8;column_k=column_k+1)
        if(column_has_ac[column_k] && zigzag_rank({column_k[2:0],n[5:3]})>column_last_ac)
          column_last_ac=zigzag_rank({column_k[2:0],n[5:3]});
    end
    reg block_has_ac;

    for(genvar ac_lane=0;ac_lane<8;ac_lane=ac_lane+1)begin:ac_presence
        assign column_has_ac[ac_lane]=(ac_lane!=0 || n[5:3]!=0) &&
            ($signed(accum[ac_lane])>=32'sd8388608 || $signed(accum[ac_lane])<=-32'sd8388608);
    end

    // Exact arithmetic bypass for accepted blocks spanning at most2 levels.
    // The pinned basis bound8386816 is strictly below half-quantum8388608.
    // Register the sum before the constant multiply;no new timing exception.
    reg [7:0] span_low,span_high;
    reg signed [15:0] span_sum;
    wire [7:0] pair_lo0=i_pixels[7:0]<i_pixels[15:8]?i_pixels[7:0]:i_pixels[15:8];
    wire [7:0] pair_lo1=i_pixels[23:16]<i_pixels[31:24]?i_pixels[23:16]:i_pixels[31:24];
    wire [7:0] pair_hi0=i_pixels[7:0]>i_pixels[15:8]?i_pixels[7:0]:i_pixels[15:8];
    wire [7:0] pair_hi1=i_pixels[23:16]>i_pixels[31:24]?i_pixels[23:16]:i_pixels[31:24];
    wire [7:0] beat_low=pair_lo0<pair_lo1?pair_lo0:pair_lo1;
    wire [7:0] beat_high=pair_hi0>pair_hi1?pair_hi0:pair_hi1;
    wire [7:0] accepted_low=(sample_index==0 || beat_low<span_low)?beat_low:span_low;
    wire [7:0] accepted_high=(sample_index==0 || beat_high>span_high)?beat_high:span_high;
    wire [8:0] completed_span={1'b0,span_high}-{1'b0,span_low};
    wire [9:0] beat_sum={2'b0,i_pixels[7:0]}+{2'b0,i_pixels[15:8]}+{2'b0,i_pixels[23:16]}+{2'b0,i_pixels[31:24]};
    wire signed [15:0] beat_centered=$signed({1'b0,beat_sum})-16'sd512;
    wire signed [15:0] accepted_sum=(sample_index==0?16'sd0:span_sum)+beat_centered;
    wire signed [31:0] fast_dc_total=span_sum*32'sd131044;
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
                LOAD:begin dct_enable<=0;dct_row<=1;end
                FAST_DC_SAVE:begin dct_enable<=completed_span>2;dct_row<=1;end
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
            block_has_ac<=0;last_ac_index<=0;span_low<=255;span_high<=0;span_sum<=0;
        end else begin
            o_done<=0;
            if(o_valid && o_ready) o_bytes<=o_bytes+1'b1;
            case(state)
                IDLE:if(i_start) begin
                    state<=SEGMENT_ONLY?LOAD:HEADER;header_index<=0;sample_index<=0;o_bytes<=0;o_error<=0;
                    block_number<=0;previous_dc[0]<=0;previous_dc[1]<=0;previous_dc[2]<=0;flush_sent<=0;
                end
                HEADER:if(o_ready) begin
                    if(header_index==HEADER_SIZE-1) begin state<=LOAD;sample_index<=0;end
                    else header_index<=header_index+1'b1;
                end
                LOAD:if(i_valid) begin
                    span_low<=accepted_low;span_high<=accepted_high;span_sum<=accepted_sum;
                    if(sample_index==0)begin block_has_ac<=0;last_ac_index<=0;end
                    for(k=0;k<4;k=k+1) samples[{load_word_phase,2'b00}+k]<=$signed({1'b0,i_pixels[k*8 +: 8]})-9'sd128;
                    if(sample_index==60) begin n<=0;state<=FAST_DC_SAVE;end
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

                    block_has_ac<=block_has_ac || (|column_has_ac);
                    if(column_last_ac>last_ac_index)last_ac_index<=column_last_ac;                    for(k=0;k<8;k=k+1) quantized[k*8+dct_group_phase]<=rounded_quant(accum[k]);
                    if(n==63) state<=DC_READ;else begin n<=n+1'b1;state<=COL_DCT;end
                end
                FAST_DC_SAVE:begin
                    // Classify only after the final min/max/sum have been registered.
                    // The normal path pays one decision clock; fast blocks
                    // retain their original latency and exact quantized DC.
                    if(completed_span<=2)begin
                     quantized[0]<=rounded_quant(fast_dc_total);block_has_ac<=0;state<=DC_READ;
                    end else begin n<=0;state<=ROW_DCT;end
                end
                DC_READ:begin dc_value<=$signed(quantized[0])-$signed(current_dc);state<=DC_PREP;end
                DC_PREP:state<=DC;
                AC_PREP:state<=AC;
                DC:if(queue_ready) begin
                    if(size>11) o_error<=1;
                    previous_dc[component_id]<=quantized[0];ac_prefetch_address<=zigzag(1);
                    if(block_has_ac)begin ac_index<=1;zero_run<=0;state<=AC_READ;end
                    else begin ac_index<=64;zero_run<=63;state<=AC;end
                end
                AC_READ:begin ac_value<=quantized[ac_prefetch_address];ac_prefetch_address<=zigzag(2);state<=AC_PREP;end
                AC:begin
                    if(ac_index==64) begin
                        if(zero_run==0 || queue_ready) begin zero_run<=0;state<=NEXT_BLOCK;end
                    end else if(value==0) begin
                        zero_run<=zero_run+1'b1;ac_index<=ac_index+1'b1;
                        if(ac_index<63)begin
                            ac_value<=quantized[ac_prefetch_address];state <= (quantized[ac_prefetch_address] == 16'sd0) ? AC : AC_PREP;
                            ac_prefetch_address<=zigzag(ac_index+2);
                        end
                    end
                    else if(queue_ready) begin
                        if(size>10) o_error<=1;
                        if(zero_run>=16) zero_run<=zero_run-16;
                        else begin
                            if(ac_index==last_ac_index)begin
                                // The baseline would walk all remaining zeros
                                // and then emit exactly this same EOB token.
                                ac_index<=64;zero_run<=7'd63-{1'b0,last_ac_index};state<=AC;
                            end else begin
                                zero_run<=0;ac_index<=ac_index+1'b1;
                                if(ac_index<63)begin
                                    ac_value<=quantized[ac_prefetch_address];state <= (quantized[ac_prefetch_address] == 16'sd0) ? AC : AC_PREP;
                                    ac_prefetch_address<=zigzag(ac_index+2);
                                end
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
                    if(flushed)begin if(SEGMENT_ONLY)begin o_done<=1;state<=IDLE;end else begin state<=EOI;eoi_second<=0;end end
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
