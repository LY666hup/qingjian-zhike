/*===========================================================================
 * Filename     : csi2_frame_tx.v
 * Description  : Layer-1 CSI-2 frame transmitter (格式无关).
 *                从 AXI-Stream slave 接口吃 payload word, 不关心内部格式.
 *                通过 DT/BPP 参数支持 YUV422/RGB888/RAW10/... 等任意 CSI-2
 *                Data Type. Payload pack 由 Layer-2 完成.
 *
 *===========================================================================*/
`timescale 1ns / 1ps

module csi2_frame_tx #(
    parameter [5:0]   DT         = 6'h1E,        // Data Type (YUV422=0x1E,RGB888=0x24,RAW10=0x2B,...)
    parameter [1:0]   VC         = 2'd0,         // Virtual Channel
    parameter integer H_ACTIVE   = 1920,
    parameter integer V_ACTIVE   = 1080,
    parameter integer BPP_NUM    = 2,            // bytes-per-pixel numerator
    parameter integer BPP_DEN    = 1,            // bytes-per-pixel denominator
                                                 //   YUV422→2/1, RGB888→3/1, RAW10→5/4
    parameter [31:0]  GAP_LINE   = 32'd200,      // byte_clk gap between packets (inter-line)
    parameter [31:0]  GAP_FRAME  = 32'd4000      // byte_clk gap after FE
) (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        enable,

    // --- AXI-Stream slave (from Layer-2 packer) ---
    input  wire        s_tvalid,
    output reg         s_tready,
    input  wire [31:0] s_tdata,
    input  wire        s_tuser_sof,   // frame start flag
    input  wire        s_tlast,       // end-of-line flag

    // --- HS stream to DPHY wrapper ---
    output reg         hs_en,
    output reg  [31:0] hs_data,
    output reg         hs_last,

    // --- Statistics ---
    output reg  [31:0] frame_cnt,
    output reg  [31:0] packet_cnt
);

    // ---- Derived constants ------------------------------------------------
    localparam integer WC_INT         = (H_ACTIVE * BPP_NUM) / BPP_DEN;
    localparam [15:0]  WC             = WC_INT[15:0];
    localparam integer WORDS_PAYLOAD  = WC_INT / 4;
    localparam [5:0]   DT_FS          = 6'h00;
    localparam [5:0]   DT_FE          = 6'h01;

    // ---- ECC (Anlogic DSI reference compatible) --------------------------
    // Input  : d24 = { DI[7:0], data_L[7:0], data_H[7:0] }
    // DI     = {VC, DT}
    function [7:0] ecc8;
        input [23:0] d;
        reg   [7:0]  e;
        begin
            e[7] = 1'b0;
            e[6] = 1'b0;
            e[5] = d[10]^d[11]^d[12]^d[13]^d[14]^d[15]^d[0]^d[1]^d[2]^d[3]^d[5]^d[6]^d[7];
            e[4] = d[20]^d[21]^d[22]^d[23]^d[8]^d[9]^d[0]^d[1]^d[2]^d[3]^d[4]^d[6]^d[7];
            e[3] = d[17]^d[18]^d[19]^d[23]^d[8]^d[9]^d[13]^d[14]^d[15]^d[3]^d[4]^d[5]^d[7];
            e[2] = d[16]^d[18]^d[19]^d[21]^d[22]^d[9]^d[11]^d[12]^d[15]^d[2]^d[4]^d[5]^d[6];
            e[1] = d[16]^d[17]^d[19]^d[20]^d[22]^d[8]^d[10]^d[12]^d[14]^d[1]^d[4]^d[5]^d[6]^d[7];
            e[0] = d[16]^d[17]^d[18]^d[20]^d[21]^d[23]^d[10]^d[11]^d[13]^d[0]^d[4]^d[5]^d[6]^d[7];
            ecc8 = e;
        end
    endfunction

    // ---- CRC-16 CSI-2 ------------------------------------------------------
    function [15:0] crc16_byte;
        input [15:0] cin;
        input [7:0]  b;
        reg   [15:0] c;
        integer i;
        begin
            c = cin ^ {8'h00, b};
            for (i = 0; i < 8; i = i + 1) begin
                if (c[0]) c = (c >> 1) ^ 16'h8408;
                else      c = c >> 1;
            end
            crc16_byte = c;
        end
    endfunction

    function [15:0] crc16_word;
        input [15:0] cin;
        input [31:0] w;
        begin
            crc16_word = crc16_byte(
                           crc16_byte(
                             crc16_byte(
                               crc16_byte(cin, w[7:0]),
                               w[15:8]),
                             w[23:16]),
                           w[31:24]);
        end
    endfunction

    // ---- Short / Long packet header builders -----------------------------
    function [31:0] build_short_hdr;
        input [5:0]  dt;
        input [15:0] data16;
        reg   [7:0]  di;
        reg   [23:0] d24;
        begin
            di              = {VC, dt};
            d24             = {di, data16[7:0], data16[15:8]};
            build_short_hdr = {ecc8(d24), data16[15:8], data16[7:0], di};
        end
    endfunction

    function [31:0] build_long_hdr;
        input [5:0]  dt;
        input [15:0] wc16;
        reg   [7:0]  di;
        reg   [23:0] d24;
        begin
            di             = {VC, dt};
            d24            = {di, wc16[7:0], wc16[15:8]};
            build_long_hdr = {ecc8(d24), wc16[15:8], wc16[7:0], di};
        end
    endfunction


    localparam [3:0] S_IDLE     = 4'd0;
    localparam [3:0] S_WAIT_SOF = 4'd1;
    localparam [3:0] S_FS       = 4'd2;
    localparam [3:0] S_LP_HDR_F = 4'd3;
    localparam [3:0] S_LP_PL_F  = 4'd4;
    localparam [3:0] S_LP_PL    = 4'd5;
    localparam [3:0] S_LP_HDR_N = 4'd6;
    localparam [3:0] S_LP_PL_N  = 4'd7;
    localparam [3:0] S_LP_CRC   = 4'd8;
    localparam [3:0] S_FE       = 4'd9;
    localparam [3:0] S_GAP      = 4'd10;

    reg [3:0]  state;
    reg [3:0]  state_after_gap;
    reg [31:0] gap_cnt;
    reg [31:0] gap_target;
    reg [15:0] pl_word_cnt;
    reg [15:0] line_cnt;
    reg [15:0] frame_num;
    reg [15:0] crc;
    reg [31:0] shadow_word;

    wire [31:0] fs_word = build_short_hdr(DT_FS, frame_num);
    wire [31:0] fe_word = build_short_hdr(DT_FE, frame_num);
    wire [31:0] lp_hdr  = build_long_hdr (DT,    WC);

    // ---- tready combinatorial: 仅在等 sof / 吃 payload 时拉高 ----
    always @(*) begin
        case (state)
            S_WAIT_SOF: s_tready = 1'b1;
            S_LP_PL:    s_tready = 1'b1;
            S_LP_PL_N:  s_tready = 1'b1;
            default:    s_tready = 1'b0;
        endcase
    end

    // ---- FSM --------------------------------------------------------------
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state           <= S_IDLE;
            state_after_gap <= S_IDLE;
            gap_cnt         <= 32'd0;
            gap_target      <= GAP_LINE;
            pl_word_cnt     <= 16'd0;
            line_cnt        <= 16'd0;
            frame_num       <= 16'd0;
            crc             <= 16'hFFFF;
            shadow_word     <= 32'd0;
            hs_en           <= 1'b0;
            hs_data         <= 32'd0;
            hs_last         <= 1'b0;
            frame_cnt       <= 32'd0;
            packet_cnt      <= 32'd0;
        end else if (!enable) begin
            state           <= S_IDLE;
            hs_en           <= 1'b0;
            hs_data         <= 32'd0;
            hs_last         <= 1'b0;
            pl_word_cnt     <= 16'd0;
            line_cnt        <= 16'd0;
            gap_cnt         <= 32'd0;
        end else begin
            case (state)
                // --------------------------------------------------------
                S_IDLE: begin
                    hs_en    <= 1'b0;
                    hs_data  <= 32'd0;
                    hs_last  <= 1'b0;
                    line_cnt <= 16'd0;
                    state    <= S_WAIT_SOF;
                end

                // --------------------------------------------------------
                // Handshake 第一帧第一个 word -> shadow
                S_WAIT_SOF: begin
                    hs_en   <= 1'b0;
                    hs_data <= 32'd0;
                    hs_last <= 1'b0;
                    if (s_tvalid && s_tuser_sof) begin
                        shadow_word <= s_tdata;
                        state       <= S_FS;
                    end
                    // 未对齐的 valid 丢弃 (保持 tready=1 吸收)
                end

                // --------------------------------------------------------
                S_FS: begin
                    hs_en           <= 1'b1;
                    hs_data         <= fs_word;
                    hs_last         <= 1'b1;
                    packet_cnt      <= packet_cnt + 32'd1;
                    gap_cnt         <= 32'd0;
                    gap_target      <= GAP_LINE;
                    state_after_gap <= S_LP_HDR_F;
                    state           <= S_GAP;
                end

                // --------------------------------------------------------
                S_LP_HDR_F: begin
                    hs_en       <= 1'b1;
                    hs_data     <= lp_hdr;
                    hs_last     <= 1'b0;
                    crc         <= 16'hFFFF;
                    pl_word_cnt <= 16'd0;
                    state       <= S_LP_PL_F;
                end

                // Payload word 0 = shadow
                S_LP_PL_F: begin
                    hs_en       <= 1'b1;
                    hs_data     <= shadow_word;
                    hs_last     <= 1'b0;
                    crc         <= crc16_word(16'hFFFF, shadow_word);
                    pl_word_cnt <= 16'd1;
                    if (WORDS_PAYLOAD <= 1)
                        state <= S_LP_CRC;
                    else
                        state <= S_LP_PL;
                end

                // --------------------------------------------------------
                // 首行剩余 WORDS_PAYLOAD-1 个 word 从 AXIS 吃
                S_LP_PL: begin
                    if (s_tvalid) begin
                        hs_en       <= 1'b1;
                        hs_data     <= s_tdata;
                        hs_last     <= 1'b0;
                        crc         <= crc16_word(crc, s_tdata);
                        pl_word_cnt <= pl_word_cnt + 16'd1;
                        if (pl_word_cnt == (WORDS_PAYLOAD[15:0] - 16'd1))
                            state <= S_LP_CRC;
                    end else begin

                        hs_en <= 1'b0;
                    end
                end

                // --------------------------------------------------------
                // 后续行 header (AXIS 在此 stall, tready=0)
                S_LP_HDR_N: begin
                    hs_en       <= 1'b1;
                    hs_data     <= lp_hdr;
                    hs_last     <= 1'b0;
                    crc         <= 16'hFFFF;
                    pl_word_cnt <= 16'd0;
                    state       <= S_LP_PL_N;
                end

                // 后续行从 AXIS 从 word 0 开始吃
                S_LP_PL_N: begin
                    if (s_tvalid) begin
                        hs_en       <= 1'b1;
                        hs_data     <= s_tdata;
                        hs_last     <= 1'b0;
                        crc         <= crc16_word(crc, s_tdata);
                        pl_word_cnt <= pl_word_cnt + 16'd1;
                        if (pl_word_cnt == (WORDS_PAYLOAD[15:0] - 16'd1))
                            state <= S_LP_CRC;
                    end else begin
                        hs_en <= 1'b0;
                    end
                end

                // --------------------------------------------------------
                S_LP_CRC: begin
                    hs_en           <= 1'b1;
                    hs_data         <= {16'h0000, crc};
                    hs_last         <= 1'b1;
                    packet_cnt      <= packet_cnt + 32'd1;
                    line_cnt        <= line_cnt + 16'd1;
                    gap_cnt         <= 32'd0;
                    gap_target      <= GAP_LINE;
                    state           <= S_GAP;
                    if (line_cnt == (V_ACTIVE[15:0] - 16'd1))
                        state_after_gap <= S_FE;
                    else
                        state_after_gap <= S_LP_HDR_N;
                end

                // --------------------------------------------------------
                S_FE: begin
                    hs_en           <= 1'b1;
                    hs_data         <= fe_word;
                    hs_last         <= 1'b1;
                    packet_cnt      <= packet_cnt + 32'd1;
                    frame_num       <= frame_num + 16'd1;
                    frame_cnt       <= frame_cnt + 32'd1;
                    line_cnt        <= 16'd0;
                    gap_cnt         <= 32'd0;
                    gap_target      <= GAP_FRAME;
                    state_after_gap <= S_WAIT_SOF;  // 帧末回等下一帧 sof
                    state           <= S_GAP;
                end

                // --------------------------------------------------------
                S_GAP: begin
                    hs_en   <= 1'b0;
                    hs_data <= 32'd0;
                    hs_last <= 1'b0;
                    if (gap_cnt >= gap_target) begin

                        if (state_after_gap == S_LP_HDR_N ||
                            state_after_gap == S_LP_HDR_F) begin
                            if (s_tvalid)
                                state <= state_after_gap;
                        end else begin
                            state <= state_after_gap;
                        end
                    end else begin
                        gap_cnt <= gap_cnt + 32'd1;
                    end
                end

                default: state <= S_IDLE;
            endcase
        end
    end

endmodule
