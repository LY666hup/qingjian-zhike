`timescale 1ns / 1ps
// =============================================================================
// Module : system_top_tx_tpg   (AFC03 / PH1A90SEG324)
// Brief  : 独立 TX 隔离测试顶层。内部 YUV422 TPG -> csi2_frame_tx -> Anlogic
//          D-PHY TX wrapper -> RK3576 MIPI RX (/dev/video22)。
//
//          本顶层故意 *不含* IMX415 RX / ISP / DDR / csi2_pack 路径, 只保留
//          AFC03 量产工程中完全相同的 Layer-1 (csi2_frame_tx) + Layer-0
//          (mipi_dphy_tx_wrapper) + pll_dsi。用已知确定的 TPG 图案替代 sensor
//          +ISP, 以判定 "首列竖线 / 每行 word0 损坏" 究竟来自 TX 侧 还是 RX 侧:
//            - 若 RK3576 抓帧仍见首列异常 -> 问题在 csi2_frame_tx / D-PHY / RX
//            - 若 RK3576 抓帧首列干净   -> 问题在 ISP / csi2_pack 上游路径
//
//          TPG 默认 pattern=1 (灰阶水平 ramp): 每行 col0 像素值=0,1, col1=2,3...
//          因此首列应为纯黑; 任何 word0 损坏都会立即在首列暴露。
//
//          时钟 (pll_dsi, 25MHz in):
//            clk0=25M(fb) clk1=125M(byte) clk2=250M clk3=500M(hs x4)
//            clk4=500M@90 clk5=10M(LP)
// =============================================================================

module system_top_tx_tpg #(
    parameter integer H_ACTIVE    = 1920,
    parameter integer V_ACTIVE    = 1080,
    parameter [1:0]   TPG_PATTERN = 2'd1   // 0=color bars 1=gray ramp 2=checker 3=coord stamp
)(
    input  wire        I_sysclk_25m,
    input  wire        I_dsi_en,

    output wire        O_mipi_dsi_hs_clk_p,
    output wire [3:0]  O_mipi_dsi_hs_data_p,
    output wire        O_mipi_dsi_lp_clk_p,
    output wire        O_mipi_dsi_lp_clk_n,
    output wire [3:0]  O_mipi_dsi_lp_data_p,
    output wire [3:0]  O_mipi_dsi_lp_data_n
);

    // ---- DSI 专用 PLL (25MHz -> 125/250/500/500@90/10MHz) ----
    wire S_dsi_pll_lock;
    wire S_dsi_25m_fb;
    wire S_dsi_byte_clk;   // 125MHz
    wire S_dsi_hs_x2;      // 250MHz
    wire S_dsi_hs_x4;      // 500MHz
    wire S_dsi_hs_x4_90d;  // 500MHz @90
    wire S_dsi_lp_clk;     // 10MHz

    pll_dsi u_ui_pll_dsi (
        .refclk   (I_sysclk_25m   ),
        .reset    (1'b0           ),
        .lock     (S_dsi_pll_lock ),
        .clk0_out (S_dsi_25m_fb   ),
        .clk1_out (S_dsi_byte_clk ),
        .clk2_out (S_dsi_hs_x2    ),
        .clk3_out (S_dsi_hs_x4    ),
        .clk4_out (S_dsi_hs_x4_90d),
        .clk5_out (S_dsi_lp_clk   )
    );

    // ---- I_dsi_en 同步到 byte_clk 域 ----
    reg [2:0] r_en_sync = 3'b000;
    always @(posedge S_dsi_byte_clk or negedge S_dsi_pll_lock) begin
        if (!S_dsi_pll_lock)
            r_en_sync <= 3'b000;
        else
            r_en_sync <= {r_en_sync[1:0], I_dsi_en};
    end
    wire S_dsi_en_int = r_en_sync[2];

    // ---- 复位: PLL 锁定 & enable 后, 经移位寄存器干净释放 ----
    reg [7:0] r_rst_sync = 8'd0;
    always @(posedge S_dsi_byte_clk or negedge S_dsi_pll_lock) begin
        if (!S_dsi_pll_lock)
            r_rst_sync <= 8'd0;
        else
            r_rst_sync <= {r_rst_sync[6:0], S_dsi_en_int};
    end
    wire S_dphy_rst_n = r_rst_sync[7];

    // ---- Layer-2 替代: 内部 YUV422 TPG (直接喂 csi2_frame_tx) ----
    wire        S_tpg_tvalid;
    wire        S_tpg_tready;
    wire [31:0] S_tpg_tdata;
    wire        S_tpg_tsof;
    wire        S_tpg_tlast;

    yuv422_tpg_axis #(
        .H_ACTIVE     (H_ACTIVE),
        .V_ACTIVE     (V_ACTIVE),
        .CHECKER_SIZE (64)
    ) X_tpg (
        .clk         (S_dsi_byte_clk),
        .rst_n       (S_dphy_rst_n  ),
        .enable      (S_dsi_en_int  ),
        .pattern     (TPG_PATTERN   ),
        .m_tvalid    (S_tpg_tvalid  ),
        .m_tready    (S_tpg_tready  ),
        .m_tdata     (S_tpg_tdata   ),
        .m_tuser_sof (S_tpg_tsof    ),
        .m_tlast     (S_tpg_tlast   ),
        .frame_count (),
        .dbg_word_x  (),
        .dbg_line_y  ()
    );

    // ---- Layer-1: CSI-2 帧 FSM (与量产工程逐字节相同) ----
    wire        S_hs_en;
    wire [31:0] S_hs_data;
    wire        S_hs_last;

    csi2_frame_tx #(
        .DT        (6'h1E),   // YUV422-8bit
        .VC        (2'd0 ),
        .H_ACTIVE  (H_ACTIVE),
        .V_ACTIVE  (V_ACTIVE),
        .BPP_NUM   (2    ),
        .BPP_DEN   (1    ),
        .GAP_LINE  (32'd200 ),
        .GAP_FRAME (32'd4000)
    ) u_csi2_frame_tx (
        .clk         (S_dsi_byte_clk),
        .rst_n       (S_dphy_rst_n  ),
        .enable      (S_dsi_en_int  ),
        .s_tvalid    (S_tpg_tvalid  ),
        .s_tready    (S_tpg_tready  ),
        .s_tdata     (S_tpg_tdata   ),
        .s_tuser_sof (S_tpg_tsof    ),
        .s_tlast     (S_tpg_tlast   ),
        .hs_en       (S_hs_en       ),
        .hs_data     (S_hs_data     ),
        .hs_last     (S_hs_last     ),
        .frame_cnt   (),
        .packet_cnt  ()
    );

    // ---- Layer-0: Anlogic D-PHY TX wrapper (与量产工程参数相同) ----
    mipi_dphy_tx_wrapper #(
        .LANE_NUM               (4      ),
        .HS_TX_FIFO_TYPE        ("DRAM" ),
        .LP_TX_FIFO_TYPE        ("DRAM" ),
        .LP_TX_MAX_DATA_LENGTH  (8      ),
        .CLK_LANE_MODE          ("INTERRUPT"),
        .T_DATA_LPX             (10),
        .T_DATA_HS_PREPARE      (7 ),
        .T_DATA_HS_ZERO         (30),
        .T_DATA_HS_TRAIL        (20),
        .T_CLK_LPX              (10),
        .T_CLK_PREPARE          (10),
        .T_CLK_ZERO             (10),
        .T_CLK_PRE              (10),
        .T_CLK_POST             (10),
        .T_CLK_TRAIL            (10)
    ) u_dphy_tx (
        .I_hs_clk        (S_dsi_byte_clk ),
        .I_hs_clk_x2     (S_dsi_hs_x2    ),
        .I_hs_clk_x4     (S_dsi_hs_x4    ),
        .I_hs_clk_x4_90d (S_dsi_hs_x4_90d),
        .I_lpdt_clk      (S_dsi_lp_clk   ),
        .I_rst           (~S_dphy_rst_n  ),

        .I_hs_tx_valid   (S_hs_en        ),
        .I_hs_tx_data    (S_hs_data      ),
        .I_hs_tx_last    (S_hs_last      ),
        .O_hs_tx_ready   (               ),

        .I_lp_tx_valid   (1'b0           ),
        .I_lp_tx_data    (8'h00          ),
        .I_lp_tx_last    (1'b0           ),
        .O_lp_tx_ready   (               ),

        .O_clk_hs_p      (O_mipi_dsi_hs_clk_p ),
        .O_data_hs_p     (O_mipi_dsi_hs_data_p),
        .O_clk_lp_p      (O_mipi_dsi_lp_clk_p ),
        .O_clk_lp_n      (O_mipi_dsi_lp_clk_n ),
        .O_data_lp_p     (O_mipi_dsi_lp_data_p),
        .O_data_lp_n     (O_mipi_dsi_lp_data_n)
    );

endmodule
