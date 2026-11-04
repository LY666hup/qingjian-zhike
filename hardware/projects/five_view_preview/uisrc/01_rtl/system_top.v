`timescale 1ns/1ns
/*******************************MILIANKE*******************************
*Company : MiLianKe Electronic Technology Co., Ltd.
*WebSite:https://www.milianke.com
*TechWeb:https://www.uisrc.com
*tmall-shop:https://milianke.tmall.com
*jd-shop:https://milianke.jd.com
*taobao-shop1: https://milianke.taobao.com
*Create Date: 2025/12/10
*Module Name:
*File Name:
*Description: 
*The reference demo provided by Milianke is only used for learning. 
*We cannot ensure that the demo itself is free of bugs, so users 
*should be responsible for the technical problems and consequences
*caused by the use of their own products.
*Copyright: Copyright (c) MiLianKe
*All rights reserved.
*Revision: 1.0
*Signal description
*1) I_ input
*2) O_ output
*3) IO_ input output
*4) S_system internal signal
*5) n_ activ low
*6) dg_ debug signal
*7) r_ delay or register
*8) s_ state mechine
*********************************************************************/
/*********system_top***********
--本方案采用IMX415摄像头,将图像数据经米联客ISP图像算法处理后,
缓存至DDR中,最终通过mipi输出1080P_60HZ的图像
*********************************************************************/

`define DRAM_BYTE_NUM 2   	   // 1 = x8, 2 = x16,4 = x32,8=x64
`define DRAM_SIZE    "2G"     // AP106,AP102=2G ,AP104=4G    1G , 2G , 4G = 256x16bit, 8G    ,ONE DDR particle capacity

//`define XLNX_NATIVE
//`define ALC_NATIVE  
`define MC_AXI  
 
//`define AXI_ATG  
//`define ATG   
//`define DFI
  
module system_top#(
parameter DRAM_TYPE       = "DDR3"  ,            //"DDR4" "DDR3"
parameter ECC             = "OFF",
parameter APP_ADDR_WIDTH  = (`DRAM_SIZE=="1G")?26:(`DRAM_SIZE=="2G")?27:(`DRAM_SIZE == "4G")?28:(`DRAM_SIZE == "8G")?29:28, //512Mx16bit=29 ,256Mx16bit=28 ,128Mx16bit=27,64Mx16bit = 26,
parameter AXI_ID_WIDTH    =  4,                                                                                   
parameter APP_DATA_WIDTH  = (ECC == "ON")? (`DRAM_BYTE_NUM-1)*8*8 : `DRAM_BYTE_NUM*8*8,
parameter APP_MASK_WIDTH  = (ECC == "ON")? (`DRAM_BYTE_NUM-1)*8   : `DRAM_BYTE_NUM*8,
parameter AXI_ADDR_WIDTH  = APP_ADDR_WIDTH+3,
parameter AXI_DATA_WIDTH  = APP_DATA_WIDTH,
parameter DQ_WIDTH        = `DRAM_BYTE_NUM * 8,
parameter DQS_WIDTH       = `DRAM_BYTE_NUM,
parameter DM_WIDTH        = `DRAM_BYTE_NUM,
parameter ADDR_WIDTH      = (DRAM_TYPE=="DDR4")?17:(`DRAM_SIZE=="1G")?13:(`DRAM_SIZE=="2G")?14:(`DRAM_SIZE == "4G")?15:(`DRAM_SIZE == "8G")?16:15, // 512Mx16bit=16 ,256Mx16bit=15,128Mx16bit=14,64Mx16bit=13
parameter ROW_WIDTH       = (`DRAM_SIZE=="1G")?13:(`DRAM_SIZE=="2G")?14:(`DRAM_SIZE == "4G")?15:(`DRAM_SIZE == "8G")?16:15,                        // 512Mx16bit=16 ,256Mx16bit=15,128Mx16bit=14,64Mx16bit=13
parameter COL_WIDTH       = 10,                  
parameter BA_WIDTH        = 3,                   
parameter BG_WIDTH        = 1,                   
parameter ODT_WIDTH       = 1,                   
parameter CKE_WIDTH       = 1,                   
parameter CS_WIDTH        = 1                    
)
(
    input                           I_sysclk_25m,	//系统时钟
    input                           I_ddr_clk   ,	//DDR参考时钟
	
	//DDR
    output    [ADDR_WIDTH-1:0]      ddr_addr    , 
    output    [  BA_WIDTH-1:0]      ddr_ba      ,
    output    [ CKE_WIDTH-1:0]      ddr_cke     ,
    output    [ ODT_WIDTH-1:0]      ddr_odt     ,
    output    [  CS_WIDTH-1:0]      ddr_cs_n    ,
    output                          ddr_ras_n   ,
    output                          ddr_cas_n   ,
    output                          ddr_we_n    ,
    output                          ddr_ck_p    ,
    output                          ddr_ck_n    ,
    output                          ddr_reset_n ,
    inout     [  DM_WIDTH-1:0]      ddr_dm  	,
    inout     [  DQ_WIDTH-1:0]      ddr_dq      ,
    inout     [ DQS_WIDTH-1:0]      ddr_dqs_n   ,
    inout     [ DQS_WIDTH-1:0]      ddr_dqs_p   ,

	//摄像头配置信号
	// IMX415 INCK is supplied by the on-board 37.125 MHz oscillator.
	output wire 					O_clk_rst	,	//摄像头复位信号,默认上拉
    output wire 					O_cam_scl	,   //I2C总线，SCL时钟
    inout  wire 					IO_cam_sda	,   //I2C总线，SDA数据
	// I_button 口已删除: U17/V17 LVDS 对被 DSI HS data lane[1] 占用, 本工程不再外提按键
	
	//MIPI_RX
	input  wire		    			I_clk_hs_p  ,	
	input  wire	[3:0]				I_data_hs_p , 
    
	    // Camera B: independent control and MIPI input
    output wire       O_clk_rst_B,
    output wire       O_cam_scl_B,
    inout  wire       IO_cam_sda_B,
    input  wire       I_clk_hs_p_B,
    input  wire [3:0] I_data_hs_p_B,
	input  wire						I_dsi_en			,	//RK3576 高表示使能 DSI 发送
	output wire						O_mipi_dsi_hs_clk_p	,
	output wire	[3:0]				O_mipi_dsi_hs_data_p,
	output wire						O_mipi_dsi_lp_clk_p	,
	output wire						O_mipi_dsi_lp_clk_n	,
	output wire	[3:0]				O_mipi_dsi_lp_data_p,
	output wire	[3:0]				O_mipi_dsi_lp_data_n
);                                      

//-------- User Clock --------//
wire                       		S_dfi_clk             ;
wire                       		S_ddr_init_cal_done   ;

// AXI Write Addr
wire [AXI_ADDR_WIDTH-1:0]      	S_axi_awaddr  	;
wire                           	S_axi_awvalid 	;
wire                           	S_axi_awready 	;
wire   [AXI_ID_WIDTH-1:0]      	S_axi_awid    	;
wire                [7:0]      	S_axi_awlen   	; 
wire                [2:0]      	S_axi_awsize  	; 
wire                [1:0]      	S_axi_awburst 	; 
wire                [0:0]      	S_axi_awlock  	; 
wire                [3:0]      	S_axi_awcache 	; 
wire                [2:0]      	S_axi_awprot  	; 
wire                [3:0]      	S_axi_awqos   	;
// AXI Write Data              		
wire [APP_DATA_WIDTH-1:0]      	S_axi_wdata   	;
wire [APP_MASK_WIDTH-1:0]      	S_axi_wstrb   	;
wire                           	S_axi_wvalid  	;
wire                           	S_axi_wlast   	;
wire                           	S_axi_wready  	;
// Write Response Port         		
wire   [AXI_ID_WIDTH-1:0]      	S_axi_bid     	;    
wire                [1:0]      	S_axi_bresp   	;    
wire                           	S_axi_bvalid  	;    
wire                           	S_axi_bready  	;    
								
// AXI Read Addr               	
wire [AXI_ADDR_WIDTH-1:0]      	S_axi_araddr  	;
wire                           	S_axi_arvalid 	;
wire                           	S_axi_arready 	;
wire [  AXI_ID_WIDTH-1:0]      	S_axi_arid    	;
wire                [7:0]      	S_axi_arlen   	;
wire                [2:0]      	S_axi_arsize  	;
wire                [1:0]      	S_axi_arburst 	;
wire                [0:0]      	S_axi_arlock  	;
wire                [3:0]      	S_axi_arcache 	;
wire                [2:0]      	S_axi_arprot  	;
wire                [3:0]      	S_axi_arqos   	;
// AXI Read Data               		
wire [APP_DATA_WIDTH-1:0]      	S_axi_rdata   	;
wire                           	S_axi_rlast   	;
wire                           	S_axi_rvalid  	;
wire                           	S_axi_rready  	;
wire   [AXI_ID_WIDTH-1:0]      	S_axi_rid     	;
wire                [1:0]      	S_axi_rresp   	;

//MLK_FDMA
wire [AXI_ADDR_WIDTH-1:  0]    	S_fdma_waddr	;   //FDMA写通道地址
wire                           	S_fdma_wareq	;   //FDMA写通道请求
wire [15: 0]                   	S_fdma_wsize	;   //FDMA写通道一次FDMA的传输大小                                       
wire                           	S_fdma_wbusy	;   //FDMA处于BUSY状态，AXI总线正在写操作 		
wire [AXI_DATA_WIDTH-1 : 0]    	S_fdma_wdata	;   //FDMA写数据
wire                           	S_fdma_wvalid	;   //FDMA 写有效
wire                           	S_fdma_wready	;   //FDMA写准备好，用户可以写数据
								
wire [AXI_ADDR_WIDTH-1:  0]    	S_fdma_raddr	;   //FDMA读通道地址
wire                           	S_fdma_rareq	;   //FDMA读通道请求
wire [15: 0]                   	S_fdma_rsize	;   //FDMA读通道一次FDMA的传输大小                                       
wire                           	S_fdma_rbusy	;   //FDMA处于BUSY状态，AXI总线正在读操作 			
wire [AXI_DATA_WIDTH-1 : 0]    	S_fdma_rdata	;   //FDMA读数据
wire                           	S_fdma_rvalid	;   //FDMA 读有效
wire                           	S_fdma_rready	;   //FDMA读准备好，用户可以读数据	
wire                           	S_test_error	;   //测试是否有错误，1有错误，0没错误

wire   							S_clk_24m		;
wire        					S_pclkx1		;
wire        					S_pclkx5		;
wire        					S_clk_70m		;
wire        					S_pll_lock		;

	//---- DSI PLL outputs (sourced from I_sysclk_25m) ----
	wire							S_dsi_25m_unused;
	wire							S_dsi_byte_clk	;	// 75MHz  -> wrapper I_hs_clk
	wire							S_dsi_hs_x2		;	// 150MHz -> wrapper I_hs_clk_x2
	wire							S_dsi_hs_x4		;	// 300MHz -> wrapper I_hs_clk_x4
	wire							S_dsi_hs_x4_90d	;	// 300MHz @90 -> wrapper I_hs_clk_x4_90d
	wire							S_dsi_lp_clk	;	// 10MHz  -> wrapper I_lpdt_clk
	wire							S_dsi_pll_lock	;

	//---- ui_dbuf R-side AXIS hookups for DSI chain ----
	wire							S_dsi_R_tuser	;
	wire							S_dsi_R_tvalid	;
	wire							S_dsi_R_tlast	;
	wire	[31:0]					S_dsi_R_tdata32	;	// RGBX8888 (1 像素/cyc) 出 dbuf.R
	wire							S_dsi_pack_tready;

	//---- DSI 握手同步 + 复位 ----
	reg  [1:0]						S_dsi_en_sync	= 2'b00;
	wire							S_dsi_en_int	= S_dsi_en_sync[1];
	wire							S_dphy_rst_n	= S_pll_lock & S_dsi_pll_lock & I_dsi_en; // common raw session reset

wire [7:0]						S_ae			;
wire 							S_cam_cfg_done	;
wire 							S_ae_cfg_done	;
wire 							S_ae_req		;
wire                            S_cam_init_ready;
wire                            S_cam_iic_busy;
wire                            S_cam_iic_error;
wire [7:0]                      S_cam_reg_index;
wire [1:0]                      S_cam_cfg_state;

// ChipWatcher page selector. VPI_0 defaults to 8'h50, hence debug page 0.
wire [7:0]                      S_debug_ctrl;
reg  [5:0]                      S_debug_probe;

reg  [15:0]                     S_dbg_rx_clk_count;
reg                             S_dbg_raw_valid_seen;
reg                             S_dbg_raw_sof_toggle;
reg                             S_dbg_raw_eol_toggle;
reg                             S_dbg_axis_valid_seen;
reg                             S_dbg_axis_sof_toggle;
reg                             S_dbg_isp_valid_seen;
reg                             S_dbg_yuv_sof_toggle;

reg  [15:0]                     S_dbg_pclk_count;
reg                             S_dbg_rvalid_seen;
reg                             S_dbg_rsof_toggle;

reg  [15:0]                     S_dbg_dsi_clk_count;
reg                             S_dbg_pack_valid_seen;
reg                             S_dbg_pack_sof_toggle;
reg                             S_dbg_hs_seen;
reg                             S_dbg_hs_last;
reg                             S_dbg_hs_start_toggle;

reg  [7:0]                      S_dbg_rx_meta;
reg  [7:0]                      S_dbg_rx_sync;
reg  [3:0]                      S_dbg_pclk_meta;
reg  [3:0]                      S_dbg_pclk_sync;
reg  [5:0]                      S_dbg_dsi_meta;
reg  [5:0]                      S_dbg_dsi_sync;
reg  [4:0]                      S_dbg_ddr_meta;
reg  [4:0]                      S_dbg_ddr_sync;

wire          					S_hs_rx_clk		;  
wire          					S_raw10_tuser	;
wire          					S_raw10_tlast	;
wire          					S_raw10_tready	;
wire          					S_raw10_valid	;
wire [39 : 0] 					S_raw10_data	;

wire [39:0]						S_axis_tdata	;
wire 							S_axis_tvalid	;
wire 							S_axis_tlast	; 
wire 							S_axis_tuser	; 
wire 							S_axis_tready	;

wire							S_ISP_O_tready 	;
wire [127:0]					S_ISP_O_tdata  	;  // RGBX8888 ×4 真彩 (原 64b RGB565×4)
wire 							S_ISP_O_tlast  	;
wire 							S_ISP_O_tuser  	;
wire 							S_ISP_O_tvalid 	;

wire [7:0] 						S_wbuf_sync		;
wire [7:0] 						S_rbuf_sync		;
					
wire 							S_ud_wclk		;
wire 							S_ud_wtuser		; 
wire 							S_ud_wtvalid	; 
wire 							S_ud_wtlast		; 
wire 							S_ud_wtready	;  
wire [63:0] 					S_ud_wtdata		;  // YUV422 (YUYV-on-wire) ×4 入 dbuf 写侧 = 16bpp×4 = 64b


wire        S_yuv_de;
wire        S_yuv_sof;
wire        S_yuv_last;
wire [63:0] S_yuv_data;

wire 							S_vtc_vs		;
wire 							S_vtc_hs		;
wire 							S_vtc_de_valid	;
wire 							S_vtc_user		;
wire 							S_vtc_last      ;
					
wire [31:0] 					S_ud_rtdata		;  // RGBX8888 (1 像素/cyc) 出 dbuf 读侧
					
wire        					S_R_vrst		;
					
assign O_clk_rst				= 1'b1;
	
assign S_ud_wclk 				= S_hs_rx_clk	;
assign S_ud_wtuser 				= S_yuv_sof		;
assign S_ud_wtvalid 			= S_yuv_de		;
assign S_ud_wtlast 				= S_yuv_last	;
assign S_ud_wtdata 				= S_yuv_data	;


// LY hardware processing profile1: received gray core, 4 pixels/clock.
// No CPU effect, no test-pattern replacement. Source remains actual ISP RGBX.
wire [127:0] S_ly_gray_data;
wire S_ly_gray_valid, S_ly_gray_sof, S_ly_gray_last;
grayscale_rgbx4 u_ly_hw_gray (
 .I_clk(S_hs_rx_clk), .I_rst_n(S_pll_lock),
 .I_tdata(S_ISP_O_tdata), .I_tvalid(S_ISP_O_tvalid),
 .I_tuser(S_ISP_O_tuser), .I_tlast(S_ISP_O_tlast),
 .O_tdata(S_ly_gray_data), .O_tvalid(S_ly_gray_valid),
 .O_tuser(S_ly_gray_sof), .O_tlast(S_ly_gray_last)
);

rgb_to_yuv422_quad u_rgb2yuv_quad (
	.clk        (S_hs_rx_clk    ),
	.rst_n      (S_pll_lock     ),
	.i_de       (S_ly_gray_valid),
	.i_user_sof (S_ly_gray_sof),
	.i_last     (S_ly_gray_last),
	.i_data     (S_ly_gray_data),
	.o_de       (S_yuv_de       ),
	.o_user_sof (S_yuv_sof      ),
	.o_last     (S_yuv_last     ),
	.o_data     (S_yuv_data     )
);


//PLL时钟管理IP 
ui_pll X_ui_pll (
    .refclk  	(I_sysclk_25m	),
    .reset		(1'b0			),

    .lock		(S_pll_lock		),

    .clk0_out	(S_clk_24m		),
    .clk1_out	(S_pclkx1		),
    .clk2_out	(S_pclkx5		),
    .clk3_out	(S_clk_70m		)
  );

//手动配置相机AE
ui_ae_set X_ui_ae_set (
	.I_clk			(S_clk_24m		),
	.I_rst			(S_pll_lock		),
	.I_btn			(2'b11			),
	.I_cam_cfg_done	(S_cam_cfg_done	),
	.I_ae_cfg_done	(S_ae_cfg_done	),
	.O_ae_req		(S_ae_req		),
	.O_ae			(S_ae			)
  );
  
//IIC协议配置摄像头寄存器
ui_cfg_imx415#(
	.CLK_DIV(24_000_000/100_000-1)
	)
X_ui_cfg_imx415(
	.I_clk			(S_clk_24m		),  
	.I_rst_n		(S_pll_lock		),  
	.I_ae_req		(S_ae_req		),
	.I_ae_data		(S_ae			),
	.O_cam_scl		(O_cam_scl		), 
	.IO_cam_sda		(IO_cam_sda		), 
	.O_cfg_done		(S_cam_cfg_done	),
	.O_ae_cfg_done	(S_ae_cfg_done	),
	.O_dbg_init_ready(S_cam_init_ready),
	.O_dbg_iic_busy	(S_cam_iic_busy	),
	.O_dbg_iic_error	(S_cam_iic_error),
	.O_dbg_reg_index	(S_cam_reg_index),
	.O_dbg_cfg_state	(S_cam_cfg_state)
  );

// ===== A original and three FPGA processing branches =====
// Native 1920x1080 input. All branch controls follow their own pipeline.
// Each branch feeds a separate preview capture and a private DDR frame bank.
wire A_gray_valid, A_gray_sof, A_gray_eol;
wire [31:0] A_gray_y;
wire A_gauss_valid, A_gauss_sof, A_gauss_eol;
wire [31:0] A_gauss_y;
wire A_hist_valid, A_hist_sof, A_hist_eol;
wire [31:0] A_hist_y;
wire [15:0] A_hist_generation;
wire [31:0] A_pre_len_errors, A_pre_gauss_drops;
wire [31:0] A_pre_hist_drops, A_pre_stale;

qj_preproc4_views #(.W(1920), .H(1080), .MAXW(2048), .CW(24)) u_A_views (
    .clk(S_hs_rx_clk), .rst(!S_pll_lock), .i_flush(1'b0),
    .i_valid(S_ISP_O_tvalid), .i_sof(S_ISP_O_tuser),
    .i_eol(S_ISP_O_tlast), .i_pix(S_ISP_O_tdata),
    .o_gray_valid(A_gray_valid), .o_gray_sof(A_gray_sof),
    .o_gray_eol(A_gray_eol), .o_gray_y(A_gray_y),
    .o_gauss_valid(A_gauss_valid), .o_gauss_sof(A_gauss_sof),
    .o_gauss_eol(A_gauss_eol), .o_gauss_y(A_gauss_y),
    .o_valid(A_hist_valid), .o_sof(A_hist_sof), .o_eol(A_hist_eol),
    .o_y(A_hist_y), .o_gen(A_hist_generation),
    .c_len_err(A_pre_len_errors), .c_gauss_drop(A_pre_gauss_drops),
    .c_hist_drop(A_pre_hist_drops), .c_stale(A_pre_stale)
);

// Preserve original color: take ISP output BEFORE grayscale.
wire A_original_yuv_valid, A_original_yuv_sof, A_original_yuv_eol;
wire [63:0] A_original_yuv_data;
rgb_to_yuv422_quad u_A_original_yuv (
    .clk(S_hs_rx_clk), .rst_n(S_pll_lock),
    .i_de(S_ISP_O_tvalid), .i_user_sof(S_ISP_O_tuser),
    .i_last(S_ISP_O_tlast), .i_data(S_ISP_O_tdata),
    .o_de(A_original_yuv_valid), .o_user_sof(A_original_yuv_sof),
    .o_last(A_original_yuv_eol), .o_data(A_original_yuv_data)
);

wire [127:0] A_gauss_rgbx, A_hist_rgbx;
genvar view_lane;
generate for(view_lane=0;view_lane<4;view_lane=view_lane+1) begin: g_A_preview_gray
    assign A_gauss_rgbx[view_lane*32+:32] =
        {8'h00,A_gauss_y[view_lane*8+:8],A_gauss_y[view_lane*8+:8],A_gauss_y[view_lane*8+:8]};
    assign A_hist_rgbx[view_lane*32+:32] =
        {8'h00,A_hist_y[view_lane*8+:8],A_hist_y[view_lane*8+:8],A_hist_y[view_lane*8+:8]};
end endgenerate

wire A_gauss_yuv_valid, A_gauss_yuv_sof, A_gauss_yuv_eol;
wire [63:0] A_gauss_yuv_data;
wire A_hist_yuv_valid, A_hist_yuv_sof, A_hist_yuv_eol;
wire [63:0] A_hist_yuv_data;
rgb_to_yuv422_quad u_A_gauss_yuv (
    .clk(S_hs_rx_clk), .rst_n(S_pll_lock),
    .i_de(A_gauss_valid), .i_user_sof(A_gauss_sof),
    .i_last(A_gauss_eol), .i_data(A_gauss_rgbx),
    .o_de(A_gauss_yuv_valid), .o_user_sof(A_gauss_yuv_sof),
    .o_last(A_gauss_yuv_eol), .o_data(A_gauss_yuv_data)
);
rgb_to_yuv422_quad u_A_hist_yuv (
    .clk(S_hs_rx_clk), .rst_n(S_pll_lock),
    .i_de(A_hist_valid), .i_user_sof(A_hist_sof),
    .i_last(A_hist_eol), .i_data(A_hist_rgbx),
    .o_de(A_hist_yuv_valid), .o_user_sof(A_hist_yuv_sof),
    .o_last(A_hist_yuv_eol), .o_data(A_hist_yuv_data)
);
// Existing S_yuv_* is the retained A-gray baseline output.
// Camera streams cross into DDR through private asynchronous capture FIFOs.
// ===== End A branches =====

// ===== Camera B: configuration and RAW receiver =====
wire [7:0]  B_ae;
wire        B_ae_req;
wire        B_ae_done;
wire        B_cfg_done;
wire        B_rx_clk;
wire [39:0] B_raw_data;
wire        B_raw_valid;
wire        B_raw_sof;
wire        B_raw_eol;

assign O_clk_rst_B = 1'b1;

ui_ae_set u_B_ae (
    .I_clk          (S_clk_24m),
    .I_rst          (S_pll_lock),
    .I_btn          (2'b11),
    .I_cam_cfg_done (B_cfg_done),
    .I_ae_cfg_done  (B_ae_done),
    .O_ae_req       (B_ae_req),
    .O_ae           (B_ae)
);

ui_cfg_imx415 #(
    .CLK_DIV(24_000_000/100_000-1)
) u_B_config (
    .I_clk           (S_clk_24m),
    .I_rst_n         (S_pll_lock),
    .I_ae_req        (B_ae_req),
    .I_ae_data       (B_ae),
    .O_cam_scl       (O_cam_scl_B),
    .IO_cam_sda      (IO_cam_sda_B),
    .O_cfg_done      (B_cfg_done),
    .O_ae_cfg_done   (B_ae_done),
    .O_dbg_init_ready(),
    .O_dbg_iic_busy  (),
    .O_dbg_iic_error (),
    .O_dbg_reg_index (),
    .O_dbg_cfg_state ()
);

mipi_csi_rx #(
    .MIPI_LAN(4),
    .DEVICE("PH1")
) u_B_rx (
    .I_debug_button      (1'b1),
    .I_ref_clock         (S_pclkx1),
    .I_clk_hs_rx_p       (I_clk_hs_p_B),
    .I_reset_n           (S_pll_lock),
    .I_hs_data_p         (I_data_hs_p_B),
    .I_enable            (1'b1),
    .O_hs_div_clk        (B_rx_clk),
    .m_axis_video_aclk   (B_rx_clk),
    .m_axis_video_aresetn(S_pll_lock),
    .m_axis_video_tready (1'b1),
    .m_axis_video_tuser  (B_raw_sof),
    .m_axis_video_tlast  (B_raw_eol),
    .m_axis_video_tvalid (B_raw_valid),
    .m_axis_video_tdata  (B_raw_data)
);
// Camera B: crop -> ISP -> YUYV
wire [39:0]  B_crop_data;
wire         B_crop_valid;
wire         B_crop_sof;
wire         B_crop_eol;

wire [127:0] B_rgb_data;
wire         B_rgb_valid;
wire         B_rgb_sof;
wire         B_rgb_eol;

wire [63:0]  B_yuv_data;
wire         B_yuv_valid;
wire         B_yuv_sof;
wire         B_yuv_eol;

image_correction #(
    .DATA_WIDTH(40),
    .TDEST_WIDTH(10),
    .FRAME_SELECT("RAW10_1920x1080")
) u_B_crop (
    .I_clk        (B_rx_clk),
    .I_rst_n      (S_pll_lock),
    .I_raw_tdata  (B_raw_data),
    .I_raw_tdest  (10'b0),
    .I_raw_tlast  (B_raw_eol),
    .I_raw_tvalid (B_raw_valid),
    .I_raw_tuser  (B_raw_sof),
    .I_raw_tready (),
    .O_raw_tdata  (B_crop_data),
    .O_raw_tdest  (),
    .O_raw_tlast  (B_crop_eol),
    .O_raw_tvalid (B_crop_valid),
    .O_raw_tuser  (B_crop_sof),
    .O_raw_tready (1'b1)
);

ui_isp_top u_B_isp (
    .I_clk    (B_rx_clk),
    .I_rst_n  (S_pll_lock),
    .I_tlast  (B_crop_eol),
    .I_tuser  (B_crop_sof),
    .I_tdata  (B_crop_data),
    .I_tvalid (B_crop_valid),
    .I_tdest  (10'b0),
    .O_tready (1'b1),
    .O_tdata  (B_rgb_data),
    .O_tlast  (B_rgb_eol),
    .O_tuser  (B_rgb_sof),
    .O_tvalid (B_rgb_valid),
    .I_tready ()
);

rgb_to_yuv422_quad u_B_rgb2yuv (
    .clk        (B_rx_clk),
    .rst_n      (S_pll_lock),
    .i_de       (B_rgb_valid),
    .i_user_sof (B_rgb_sof),
    .i_last     (B_rgb_eol),
    .i_data     (B_rgb_data),
    .o_de       (B_yuv_valid),
    .o_user_sof (B_yuv_sof),
    .o_last     (B_yuv_eol),
    .o_data     (B_yuv_data)
);

// ===== End Camera B receiver =====

// MIPI CSI-2 RX: LVDS direct-connect PHY + CSI packet/RAW10 decoder from
// 09_ddr_vid005_imx415_hdmi. The PHY implementation is adapted to PH1.
mipi_csi_rx #(
	.MIPI_LAN	(4		),
	.DEVICE		("PH1"	)
) u_mipi_csi_rx (
	.I_debug_button		(1'b1			),
	.I_ref_clock			(S_pclkx1		),
	.I_clk_hs_rx_p		(I_clk_hs_p		),
	.I_reset_n			(S_pll_lock		),
	.I_hs_data_p		(I_data_hs_p	),
	.I_enable			(1'b1			),
	.O_hs_div_clk		(S_hs_rx_clk	),
	.m_axis_video_aclk	(S_hs_rx_clk	),
	.m_axis_video_aresetn(S_pll_lock		),
	.m_axis_video_tready(1'b1			),
	.m_axis_video_tuser	(S_raw10_tuser	),
	.m_axis_video_tlast	(S_raw10_tlast	),
	.m_axis_video_tvalid(S_raw10_valid	),
	.m_axis_video_tdata	(S_raw10_data	)
);

// Crop the IMX415 active RAW10 window exactly as in the reference design.
image_correction #(
	.DATA_WIDTH	(40					),
	.TDEST_WIDTH	(10					),
	.FRAME_SELECT	("RAW10_1920x1080"	)
) u_image_correction (
	.I_clk			(S_hs_rx_clk	),
	.I_rst_n		(S_pll_lock		),
	.I_raw_tdata	(S_raw10_data	),
	.I_raw_tdest	(10'b0			),
	.I_raw_tlast	(S_raw10_tlast	),
	.I_raw_tvalid	(S_raw10_valid	),
	.I_raw_tuser	(S_raw10_tuser	),
	.I_raw_tready	(S_raw10_tready	),
	.O_raw_tdata	(S_axis_tdata	),
	.O_raw_tdest	(				),
	.O_raw_tlast	(S_axis_tlast	),
	.O_raw_tvalid	(S_axis_tvalid	),
	.O_raw_tuser	(S_axis_tuser	),
	.O_raw_tready	(1'b1)
);



//米联客ISP图像处理模块
ui_isp_top X_ui_isp_top (
	.I_clk		     (S_hs_rx_clk		),
	.I_rst_n         (S_pll_lock		),
	.I_tlast         (S_axis_tlast 		),
	.I_tuser         (S_axis_tuser 		),
	.I_tdata         (S_axis_tdata 		),
	.I_tvalid        (S_axis_tvalid		),
	.I_tdest         (					),
	.O_tready        (1'b1),
	.O_tdata         (S_ISP_O_tdata 	),
	.O_tlast         (S_ISP_O_tlast 	),
	.O_tuser         (S_ISP_O_tuser 	),
	.O_tvalid        (S_ISP_O_tvalid	),
	.I_tready        ()
  );


// Five preview banks REPLACE the old ui_dbuf/ui_FDMA master completely.
// Each view has three private 1MiB slots in [0,15MiB), within the former
// reserved video address window. There is no concurrent old master.
wire preview_arst = !S_pll_lock || !S_ddr_init_cal_done;
wire pv_ddr_rst,pv_A_rst,pv_B_rst;
qj_reset_sync u_pv_ddr_reset(S_dfi_clk,preview_arst,pv_ddr_rst);
qj_reset_sync u_pv_A_reset(S_hs_rx_clk,preview_arst,pv_A_rst);
qj_reset_sync u_pv_B_reset(B_rx_clk,preview_arst,pv_B_rst);

// Hold FIFO read resets until its source clock has actually reset/released.
// This avoids prefetched unknown words while an absent sensor has no clock.
(* async_reg="true" *) reg [1:0] pv_A_alive,pv_B_alive;
always @(posedge S_dfi_clk) begin
    if(pv_ddr_rst) begin pv_A_alive<=0;pv_B_alive<=0;end
    else begin pv_A_alive<={pv_A_alive[0],!pv_A_rst};pv_B_alive<={pv_B_alive[0],!pv_B_rst};end
end
wire pv_A_read_rst=pv_ddr_rst || !pv_A_alive[1];
wire pv_B_read_rst=pv_ddr_rst || !pv_B_alive[1];
wire [639:0] pv_capture_data;
wire [4:0] pv_capture_valid,pv_capture_ready,pv_capture_sof,pv_capture_eof,pv_capture_bad;
wire [159:0] pv_capture_frame;
wire [31:0] pv_seen[0:4],pv_drop[0:4];
qj_preview_capture u_capture_A_original(
 .s_clk(S_hs_rx_clk),.s_rst(pv_A_rst),.s_data(A_original_yuv_data),
 .s_valid(A_original_yuv_valid),.s_sof(A_original_yuv_sof),.s_eol(A_original_yuv_eol),
 .m_clk(S_dfi_clk),.m_rst(pv_A_read_rst),.m_data(pv_capture_data[0+:128]),
 .m_valid(pv_capture_valid[0]),.m_ready(pv_capture_ready[0]),
 .m_sof(pv_capture_sof[0]),.m_eof(pv_capture_eof[0]),.m_bad(pv_capture_bad[0]),
 .m_frame(pv_capture_frame[0+:32]),.frames_seen(pv_seen[0]),.frames_dropped(pv_drop[0]));
qj_preview_capture u_capture_A_gray(
 .s_clk(S_hs_rx_clk),.s_rst(pv_A_rst),.s_data(S_yuv_data),
 .s_valid(S_yuv_de),.s_sof(S_yuv_sof),.s_eol(S_yuv_last),
 .m_clk(S_dfi_clk),.m_rst(pv_A_read_rst),.m_data(pv_capture_data[128+:128]),
 .m_valid(pv_capture_valid[1]),.m_ready(pv_capture_ready[1]),
 .m_sof(pv_capture_sof[1]),.m_eof(pv_capture_eof[1]),.m_bad(pv_capture_bad[1]),
 .m_frame(pv_capture_frame[32+:32]),.frames_seen(pv_seen[1]),.frames_dropped(pv_drop[1]));
qj_preview_capture u_capture_A_gauss(
 .s_clk(S_hs_rx_clk),.s_rst(pv_A_rst),.s_data(A_gauss_yuv_data),
 .s_valid(A_gauss_yuv_valid),.s_sof(A_gauss_yuv_sof),.s_eol(A_gauss_yuv_eol),
 .m_clk(S_dfi_clk),.m_rst(pv_A_read_rst),.m_data(pv_capture_data[256+:128]),
 .m_valid(pv_capture_valid[2]),.m_ready(pv_capture_ready[2]),
 .m_sof(pv_capture_sof[2]),.m_eof(pv_capture_eof[2]),.m_bad(pv_capture_bad[2]),
 .m_frame(pv_capture_frame[64+:32]),.frames_seen(pv_seen[2]),.frames_dropped(pv_drop[2]));
qj_preview_capture u_capture_A_hist(
 .s_clk(S_hs_rx_clk),.s_rst(pv_A_rst),.s_data(A_hist_yuv_data),
 .s_valid(A_hist_yuv_valid),.s_sof(A_hist_yuv_sof),.s_eol(A_hist_yuv_eol),
 .m_clk(S_dfi_clk),.m_rst(pv_A_read_rst),.m_data(pv_capture_data[384+:128]),
 .m_valid(pv_capture_valid[3]),.m_ready(pv_capture_ready[3]),
 .m_sof(pv_capture_sof[3]),.m_eof(pv_capture_eof[3]),.m_bad(pv_capture_bad[3]),
 .m_frame(pv_capture_frame[96+:32]),.frames_seen(pv_seen[3]),.frames_dropped(pv_drop[3]));
qj_preview_capture u_capture_B_original(
 .s_clk(B_rx_clk),.s_rst(pv_B_rst),.s_data(B_yuv_data),
 .s_valid(B_yuv_valid),.s_sof(B_yuv_sof),.s_eol(B_yuv_eol),
 .m_clk(S_dfi_clk),.m_rst(pv_B_read_rst),.m_data(pv_capture_data[512+:128]),
 .m_valid(pv_capture_valid[4]),.m_ready(pv_capture_ready[4]),
 .m_sof(pv_capture_sof[4]),.m_eof(pv_capture_eof[4]),.m_bad(pv_capture_bad[4]),
 .m_frame(pv_capture_frame[128+:32]),.frames_seen(pv_seen[4]),.frames_dropped(pv_drop[4]));

wire [127:0] pv_data,pv_tagged_data;
wire pv_valid,pv_ready,pv_sof,pv_eol,pv_eof;
wire pv_tagged_valid,pv_tagged_ready,pv_tagged_sof,pv_tagged_eol,pv_tagged_eof;
wire [159:0] pv_display_frames;
wire [4:0] pv_display_ready;
wire [31:0] pv_fault,pv_published,pv_dropped;
qj_five_view_ddr #(.ADDR_WIDTH(AXI_ADDR_WIDTH),.ID_WIDTH(AXI_ID_WIDTH)) u_five_preview_ddr(
 .clk(S_dfi_clk),.rst(pv_ddr_rst),
 .s_data(pv_capture_data),.s_valid(pv_capture_valid),.s_ready(pv_capture_ready),
 .s_sof(pv_capture_sof),.s_eof(pv_capture_eof),.s_bad(pv_capture_bad),.s_frame(pv_capture_frame),
 .m_data(pv_data),.m_valid(pv_valid),.m_ready(pv_ready),.m_sof(pv_sof),.m_eol(pv_eol),.m_eof(pv_eof),
 .o_display_frame(pv_display_frames),.o_view_ready(pv_display_ready),
 .o_fault(pv_fault),.o_published(pv_published),.o_dropped(pv_dropped),
 .m_axi_awid(S_axi_awid),.m_axi_awaddr(S_axi_awaddr),.m_axi_awlen(S_axi_awlen),
 .m_axi_awsize(S_axi_awsize),.m_axi_awburst(S_axi_awburst),
 .m_axi_awvalid(S_axi_awvalid),.m_axi_awready(S_axi_awready),
 .m_axi_wdata(S_axi_wdata),.m_axi_wstrb(S_axi_wstrb),.m_axi_wlast(S_axi_wlast),
 .m_axi_wvalid(S_axi_wvalid),.m_axi_wready(S_axi_wready),
 .m_axi_bid(S_axi_bid),.m_axi_bresp(S_axi_bresp),.m_axi_bvalid(S_axi_bvalid),.m_axi_bready(S_axi_bready),
 .m_axi_arid(S_axi_arid),.m_axi_araddr(S_axi_araddr),.m_axi_arlen(S_axi_arlen),
 .m_axi_arsize(S_axi_arsize),.m_axi_arburst(S_axi_arburst),
 .m_axi_arvalid(S_axi_arvalid),.m_axi_arready(S_axi_arready),
 .m_axi_rid(S_axi_rid),.m_axi_rdata(S_axi_rdata),.m_axi_rresp(S_axi_rresp),
 .m_axi_rlast(S_axi_rlast),.m_axi_rvalid(S_axi_rvalid),.m_axi_rready(S_axi_rready));

assign S_axi_awlock=0;
assign S_axi_awcache=4'b0011;
assign S_axi_awprot=0;
assign S_axi_awqos=0;
assign S_axi_arlock=0;
assign S_axi_arcache=4'b0011;
assign S_axi_arprot=0;
assign S_axi_arqos=0;

qj_preview_metadata u_preview_metadata(
 .clk(S_dfi_clk),.rst(pv_ddr_rst),
 .s_data(pv_data),.s_valid(pv_valid),.s_ready(pv_ready),.s_sof(pv_sof),.s_eol(pv_eol),.s_eof(pv_eof),
 .view_frames(pv_display_frames),.view_ready(pv_display_ready),.faults(pv_fault),
 .m_data(pv_tagged_data),.m_valid(pv_tagged_valid),.m_ready(pv_tagged_ready),
 .m_sof(pv_tagged_sof),.m_eol(pv_tagged_eol),.m_eof(pv_tagged_eof));
// Feed the common raw cold reset directly to the output's per-domain
// reset synchronizers. Do not re-cross a DDR-synchronized release into pixels.
qj_preview_output u_preview_output(
 .ddr_clk(S_dfi_clk),.ddr_rst(preview_arst),.session_reset(!I_dsi_en),
 .s_data(pv_tagged_data),.s_valid(pv_tagged_valid),.s_ready(pv_tagged_ready),
 .s_sof(pv_tagged_sof),.s_eol(pv_tagged_eol),.s_eof(pv_tagged_eof),
 .pix_clk(S_pclkx1),.pix_rst(preview_arst),
 .m_data(S_ud_rtdata),.m_valid(S_dsi_R_tvalid),.m_ready(S_dsi_pack_tready),
 .m_sof(S_dsi_R_tuser),.m_eol(S_dsi_R_tlast));

// Legacy debug ports are inactive after the single-path FDMA removal.
assign S_fdma_rbusy=0;assign S_fdma_rareq=0;
assign S_fdma_wbusy=0;assign S_fdma_wareq=0;
assign S_R_vrst=!S_pll_lock;
assign S_vtc_vs=0;assign S_vtc_hs=0;assign S_vtc_de_valid=0;
assign S_vtc_user=0;assign S_vtc_last=0;

//例化DDR IP
//===== DDR3 PHY INS =====//
ui_ddr X_ui_ddr (

	.sys_rst_n  		(S_pll_lock		),
    .sys_clk    		(I_ddr_clk		),

// DDRIO
`ifdef MC0_DRAM_DDR4
    .ddr_ck_t       	(				),
    .ddr_ck_c       	(				),
    .ddr_reset_n    	(				),
    .ddr_cke        	(				),
    .ddr_odt        	(				),
    .ddr_cs_n       	(				),
    .ddr_act_n      	(				),
    .ddr_bg         	(				),
    .ddr_ba         	(				),
    .ddr_addr       	(				),
    .ddr_dqs_t      	(				),
    .ddr_dqs_c      	(				),
    .ddr_dq         	(				),
    .ddr_dm_dbi     	(				),
`else
    .ddr_ck_p       	(ddr_ck_p		),    
    .ddr_ck_n       	(ddr_ck_n		),    
    .ddr_reset_n    	(ddr_reset_n	),   
    .ddr_cke        	(ddr_cke		),   
    .ddr_odt        	(ddr_odt		),    
    .ddr_cs_n       	(ddr_cs_n		),     
    .ddr_ras_n      	(ddr_ras_n		),    
    .ddr_cas_n      	(ddr_cas_n		), 		
    .ddr_we_n       	(ddr_we_n		),     
    .ddr_ba         	(ddr_ba			), 
    .ddr_addr       	(ddr_addr		),        
    .ddr_dqs_p      	(ddr_dqs_p		),   
    .ddr_dqs_n      	(ddr_dqs_n		),   
    .ddr_dq         	(ddr_dq			),
    .ddr_dm            	(ddr_dm			),
`endif

    .ctl_ram_rst_n     	(				),
    .ctl_ram_cke       	(				),

// user interface ports
    .dfi_clk           	(S_dfi_clk		),
    .pll_locked        	(				),
    .ddrphy_rst_n      	(				),
    .ddr_init_cal_done 	(S_ddr_init_cal_done),
    .ddr_init_cal_err  	(				),
    .ddr_init_cal_step 	(				),

// mc user interface
`ifdef MC0_MIS_AXI
// AXI Interface
    .axi_aclk          	(				),
    .axi_aresetn       	(				),
// AXI Write Address Channel
    .axi_awid          (S_axi_awid   	),
    .axi_awaddr        (S_axi_awaddr 	),
    .axi_awlen         (S_axi_awlen  	),
    .axi_awsize        (S_axi_awsize 	),
    .axi_awburst       (S_axi_awburst	),
    .axi_awlock        (S_axi_awlock 	),
    .axi_awcache       (S_axi_awcache	),
    .axi_awprot        (S_axi_awprot 	),
    .axi_awqos         (S_axi_awqos  	),
    .axi_awvalid       (S_axi_awvalid	),
    .axi_awready       (S_axi_awready	),
// AXI Write Data Channel
    .axi_wdata         (S_axi_wdata 	),
    .axi_wstrb         (S_axi_wstrb 	),
    .axi_wlast         (S_axi_wlast 	),
    .axi_wvalid        (S_axi_wvalid	),
    .axi_wready        (S_axi_wready	),
// AXI Write Response Channel
    .axi_bid           (S_axi_bid   	),
    .axi_bresp         (S_axi_bresp 	),
    .axi_bvalid        (S_axi_bvalid	),
    .axi_bready        (S_axi_bready	),
// AXI Read Address Channel
    .axi_arid          (S_axi_arid   	),
    .axi_araddr        (S_axi_araddr 	),
    .axi_arlen         (S_axi_arlen  	),
    .axi_arsize        (S_axi_arsize 	),
    .axi_arburst       (S_axi_arburst	),
    .axi_arlock        (S_axi_arlock 	),
    .axi_arcache       (S_axi_arcache	),
    .axi_arprot        (S_axi_arprot 	),
    .axi_arqos         (S_axi_arqos  	),
    .axi_arvalid       (S_axi_arvalid	),
    .axi_arready       (S_axi_arready	),
// AXI Read Data Channel
    .axi_rid           (S_axi_rid   	),
    .axi_rdata         (S_axi_rdata 	),
    .axi_rresp         (S_axi_rresp 	),
    .axi_rlast         (S_axi_rlast 	),
    .axi_rvalid        (S_axi_rvalid	),
    .axi_rready        (S_axi_rready	),
`else  // PAXI
// Write Address Ports
    .paxi_awaddr       (				), 
    .paxi_awvalid      (				), 
    .paxi_awready      (				), 
// Write Data Ports    				
    .paxi_wdata        (				), 
    .paxi_wstrb        (				), 
    .paxi_wvalid       (				), 
    .paxi_wlast        (				), 
    .paxi_wready       (				), 
// Write Response Port 				
    .paxi_bresp        (				), 
    .paxi_bvalid       (				), 
    .paxi_bready       (				), 
// Read Address Ports  				
    .paxi_araddr       (				), 
    .paxi_arvalid      (				), 
    .paxi_arready      (				), 			 
// Read Data Ports     				
    .paxi_rdata        (				), 
    .paxi_rlast        (				), 
    .paxi_rvalid       (				), 
    .paxi_rready       (				), 
`endif
// PZQ Share
    .pzq_cal_done_i    (				),
    .pzq_cal_done_o    (				),
// DFX				
    .user_clk          (				), 
    .apb_clk           (				), 
    .mcu_busy          (				), 
    .user_update       (				), 
    .user_ram_addr     (				), 
    .user_ram_wr_en    (				), 
    .user_ram_rd_en    (				), 
    .user_ram_wr_data  (				), 
    .user_ram_rd_data  (				), 
				
    .uart_rxd          (				),
    .uart_txd          (				)
);

//---- DSI 专用 PLL (25MHz 输入 -> 25/75/150/300/300@90/10MHz) ----
pll_dsi u_ui_pll_dsi (
	.refclk		(I_sysclk_25m		),
	.reset		(1'b0				),
	.lock		(S_dsi_pll_lock		),
	.clk0_out	(S_dsi_25m_unused	),	// 25MHz feedback (BUFG)
	.clk1_out	(S_dsi_byte_clk		),	// 75MHz
	.clk2_out	(S_dsi_hs_x2		),	// 150MHz
	.clk3_out	(S_dsi_hs_x4		),	// 300MHz
	.clk4_out	(S_dsi_hs_x4_90d	),	// 300MHz @90°
	.clk5_out	(S_dsi_lp_clk		)	// 10MHz LP init
);

// Common session reset asserts asynchronously, including when stopped.
// Release and the enable used by the packet FSM are local to byte_clk.
always @(posedge S_dsi_byte_clk or negedge S_dphy_rst_n) begin
 if(!S_dphy_rst_n) S_dsi_en_sync<=2'b00;
 else S_dsi_en_sync<={S_dsi_en_sync[0],1'b1};
end

assign S_dsi_R_tdata32 = S_ud_rtdata;

//---- Layer-2: RGB32 -> YUV422 跨时钟域打包 ----
wire        S_pack_tvalid;
wire        S_pack_tready;
wire [31:0] S_pack_tdata;
wire        S_pack_tsof;
wire        S_pack_tlast;

csi2_pack_yuv422_stream #(
	.H_ACTIVE	(1920),
	.V_ACTIVE	(1080)
) u_csi2_pack (
	// source: ui_dbuf.R (S_pclkx1 域)
	.src_clk		(S_pclkx1			),
	.src_rst_n		(S_dphy_rst_n		),
	.s_tvalid		(S_dsi_R_tvalid		),
	.s_tready		(S_dsi_pack_tready	),
	.s_tdata		(S_dsi_R_tdata32	),
	.s_tuser_sof	(S_dsi_R_tuser		),
	.s_tlast		(S_dsi_R_tlast		),
	// sink: byte_clk 域 YUV422 AXIS
	.dst_clk		(S_dsi_byte_clk		),
	.dst_rst_n		(S_dphy_rst_n		),
	.enable			(S_dsi_en_int		),
	.m_tvalid		(S_pack_tvalid		),
	.m_tready		(S_pack_tready		),
	.m_tdata		(S_pack_tdata		),
	.m_tuser_sof	(S_pack_tsof		),
	.m_tlast		(S_pack_tlast		)
);

//---- Layer-1: CSI-2 帧 FSM (DT=0x1E YUV422-8bit) ----
wire        S_hs_en;
wire [31:0] S_hs_data;
wire        S_hs_last;

csi2_frame_tx #(
	.DT			(6'h1E),	// YUV422-8bit
	.VC			(2'd0 ),
	.H_ACTIVE	(1920 ),
	.V_ACTIVE	(1080 ),
	.BPP_NUM	(2    ),
	.BPP_DEN	(1    ),
	.GAP_LINE	(32'd200 ),
	.GAP_FRAME	(32'd4000)
) u_csi2_frame_tx (
	.clk			(S_dsi_byte_clk	),
	.rst_n			(S_dsi_en_int	),
	.enable			(S_dsi_en_int	),
	.s_tvalid		(S_pack_tvalid	),
	.s_tready		(S_pack_tready	),
	.s_tdata		(S_pack_tdata	),
	.s_tuser_sof	(S_pack_tsof	),
	.s_tlast		(S_pack_tlast	),
	.hs_en			(S_hs_en		),
	.hs_data		(S_hs_data		),
	.hs_last		(S_hs_last		),
	.frame_cnt		(				),
	.packet_cnt		(				)
);

//---- Layer-0: Anlogic D-PHY TX wrapper (LP init + HS 串化) ----
mipi_dphy_tx_wrapper #(
	.LANE_NUM				(4      ),
	.HS_TX_FIFO_TYPE		("DRAM" ),
	.LP_TX_FIFO_TYPE		("DRAM" ),
	.LP_TX_MAX_DATA_LENGTH	(8      ),
	.CLK_LANE_MODE			("INTERRUPT"),
	.T_DATA_LPX				(10	),
	.T_DATA_HS_PREPARE		(7	),
	.T_DATA_HS_ZERO			(30	),
	.T_DATA_HS_TRAIL		(20	),
	.T_CLK_LPX				(10	),
	.T_CLK_PREPARE			(10	),
	.T_CLK_ZERO				(10	),
	.T_CLK_PRE				(10	),
	.T_CLK_POST				(10	),
	.T_CLK_TRAIL			(10	)
) u_dphy_tx (
	.I_hs_clk		(S_dsi_byte_clk			),
	.I_hs_clk_x2	(S_dsi_hs_x2			),
	.I_hs_clk_x4	(S_dsi_hs_x4			),
	.I_hs_clk_x4_90d(S_dsi_hs_x4_90d		),
	.I_lpdt_clk		(S_dsi_lp_clk			),
	.I_rst			(~S_dphy_rst_n			),

	// HS TX from Layer-1
	.I_hs_tx_valid	(S_hs_en				),
	.I_hs_tx_data	(S_hs_data				),
	.I_hs_tx_last	(S_hs_last				),
	.O_hs_tx_ready	(						),

	// LP TX: 不发 LP 配置 (CSI-2 host 不需)
	.I_lp_tx_valid	(1'b0					),
	.I_lp_tx_data	(8'h00					),
	.I_lp_tx_last	(1'b0					),
	.O_lp_tx_ready	(						),

	// pad
	.O_clk_hs_p		(O_mipi_dsi_hs_clk_p	),
	.O_data_hs_p	(O_mipi_dsi_hs_data_p	),
	.O_clk_lp_p		(O_mipi_dsi_lp_clk_p	),
	.O_clk_lp_n		(O_mipi_dsi_lp_clk_n	),
	.O_data_lp_p	(O_mipi_dsi_lp_data_p	),
	.O_data_lp_n	(O_mipi_dsi_lp_data_n	)
);

// -----------------------------------------------------------------------------
// On-board debug instrumentation
// -----------------------------------------------------------------------------
// MIPI RX-domain event history. Toggles preserve narrow events for a slower
// ChipWatcher sample clock.
always @(posedge S_hs_rx_clk or negedge S_pll_lock) begin
	if (!S_pll_lock) begin
		S_dbg_rx_clk_count    <= 16'd0;
		S_dbg_raw_valid_seen  <= 1'b0;
		S_dbg_raw_sof_toggle  <= 1'b0;
		S_dbg_raw_eol_toggle  <= 1'b0;
		S_dbg_axis_valid_seen <= 1'b0;
		S_dbg_axis_sof_toggle <= 1'b0;
		S_dbg_isp_valid_seen  <= 1'b0;
		S_dbg_yuv_sof_toggle  <= 1'b0;
	end
	else begin
		S_dbg_rx_clk_count <= S_dbg_rx_clk_count + 1'b1;
		if (S_raw10_valid)
			S_dbg_raw_valid_seen <= 1'b1;
		if (S_raw10_valid && S_raw10_tuser)
			S_dbg_raw_sof_toggle <= ~S_dbg_raw_sof_toggle;
		if (S_raw10_valid && S_raw10_tlast)
			S_dbg_raw_eol_toggle <= ~S_dbg_raw_eol_toggle;
		if (S_axis_tvalid)
			S_dbg_axis_valid_seen <= 1'b1;
		if (S_axis_tvalid && S_axis_tuser)
			S_dbg_axis_sof_toggle <= ~S_dbg_axis_sof_toggle;
		if (S_ISP_O_tvalid)
			S_dbg_isp_valid_seen <= 1'b1;
		if (S_yuv_sof)
			S_dbg_yuv_sof_toggle <= ~S_dbg_yuv_sof_toggle;
	end
end

// Frame-buffer read-side history (pixel clock domain).
always @(posedge S_pclkx1 or negedge S_pll_lock) begin
	if (!S_pll_lock) begin
		S_dbg_pclk_count  <= 16'd0;
		S_dbg_rvalid_seen <= 1'b0;
		S_dbg_rsof_toggle <= 1'b0;
	end
	else begin
		S_dbg_pclk_count <= S_dbg_pclk_count + 1'b1;
		if (S_dsi_R_tvalid)
			S_dbg_rvalid_seen <= 1'b1;
		if (S_dsi_R_tvalid && S_dsi_R_tuser)
			S_dbg_rsof_toggle <= ~S_dbg_rsof_toggle;
	end
end

// RK CSI-2 transmitter history (DSI byte-clock domain).
always @(posedge S_dsi_byte_clk or negedge S_dsi_en_int) begin
	if (!S_dsi_en_int) begin
		S_dbg_dsi_clk_count   <= 16'd0;
		S_dbg_pack_valid_seen <= 1'b0;
		S_dbg_pack_sof_toggle <= 1'b0;
		S_dbg_hs_seen         <= 1'b0;
		S_dbg_hs_last         <= 1'b0;
		S_dbg_hs_start_toggle <= 1'b0;
	end
	else begin
		S_dbg_dsi_clk_count <= S_dbg_dsi_clk_count + 1'b1;
		if (S_pack_tvalid)
			S_dbg_pack_valid_seen <= 1'b1;
		if (S_pack_tvalid && S_pack_tsof)
			S_dbg_pack_sof_toggle <= ~S_dbg_pack_sof_toggle;
		if (S_hs_en)
			S_dbg_hs_seen <= 1'b1;
		if (S_hs_en && !S_dbg_hs_last)
			S_dbg_hs_start_toggle <= ~S_dbg_hs_start_toggle;
		S_dbg_hs_last <= S_hs_en;
	end
end

// Synchronize non-camera status into the ChipWatcher clock domain.
always @(posedge S_clk_24m or negedge S_pll_lock) begin
	if (!S_pll_lock) begin
		S_dbg_rx_meta   <= 8'd0;
		S_dbg_rx_sync   <= 8'd0;
		S_dbg_pclk_meta <= 4'd0;
		S_dbg_pclk_sync <= 4'd0;
		S_dbg_dsi_meta  <= 6'd0;
		S_dbg_dsi_sync  <= 6'd0;
		S_dbg_ddr_meta  <= 5'd0;
		S_dbg_ddr_sync  <= 5'd0;
	end
	else begin
		S_dbg_rx_meta <= {
			S_dbg_yuv_sof_toggle, S_dbg_isp_valid_seen,
			S_dbg_axis_sof_toggle, S_dbg_axis_valid_seen,
			S_dbg_raw_eol_toggle, S_dbg_raw_sof_toggle,
			S_dbg_raw_valid_seen, S_dbg_rx_clk_count[12]
		};
		S_dbg_rx_sync <= S_dbg_rx_meta;

		S_dbg_pclk_meta <= {
			S_dbg_rsof_toggle, S_dbg_rvalid_seen,
			S_dbg_pclk_count[12], S_vtc_user
		};
		S_dbg_pclk_sync <= S_dbg_pclk_meta;

		S_dbg_dsi_meta <= {
			S_hs_en, S_dbg_hs_start_toggle, S_dbg_hs_seen,
			S_dbg_pack_sof_toggle, S_dbg_pack_valid_seen,
			S_dbg_dsi_clk_count[12]
		};
		S_dbg_dsi_sync <= S_dbg_dsi_meta;

		S_dbg_ddr_meta <= {
			S_fdma_rbusy, S_fdma_rareq, S_fdma_wbusy,
			S_fdma_wareq, S_ddr_init_cal_done
		};
		S_dbg_ddr_sync <= S_dbg_ddr_meta;
	end
end

endmodule

