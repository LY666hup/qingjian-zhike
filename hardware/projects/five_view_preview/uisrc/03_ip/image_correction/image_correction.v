/*****************************************************************
Company : MiLianKe Electronic Technology Co., Ltd.
WebSite:https://www.milianke.com
TechWeb:https://www.uisrc.com
tmall-shop:https://milianke.tmall.com
jd-shop:https://milianke.jd.com
taobao-shop: https://milianke.taobao.com
Description: 
The reference demo provided by Milianke is only used for learning. 
We cannot ensure that the demo itself is free of bugs, so users 
should be responsible for the technical problems and consequences
caused by the use of their own products.
@Author      :   XiaoQingquan 
@Time        :   2024/12 
version:     :   1.0
@Description :   消减图像的多余的�??
*****************************************************************/

module image_correction #(
    parameter DATA_WIDTH   = 40,
    parameter TDEST_WIDTH  = 10,
    parameter FRAME_SELECT = "RAW10_1920x1080"
)(
    input                          I_clk   ,
    input                          I_rst_n ,

    input   [DATA_WIDTH - 1:0]     I_raw_tdata ,
    input   [TDEST_WIDTH - 1:0]    I_raw_tdest ,
    input                          I_raw_tlast ,
    input                          I_raw_tvalid,
    input                          I_raw_tuser ,
    output                         I_raw_tready,

    // 输出RAW10数据
    output     [DATA_WIDTH - 1:0]     O_raw_tdata ,
    output                            O_raw_tlast ,
    output reg [TDEST_WIDTH - 1:0]    O_raw_tdest ,
    output                            O_raw_tvalid,
    output                            O_raw_tuser ,
    input                             O_raw_tready
);

    reg                        I_raw_tvalid_d; 
    reg                        I_raw_tlast_d ; 
    reg    [DATA_WIDTH - 1:0]  I_raw_tdata_r;
    reg    [14:0]              h_cnt,v_cnt;
    wire                       V_valid,H_valid;
    wire                       tuser;

    localparam RAW10_1920x1080 = "RAW10_1920x1080";
    localparam RAW10_3840x2160 = "RAW10_3840x2160";
    localparam RAW10_1280x720  = "RAW10_1280x720" ;

    always @(posedge I_clk or negedge I_rst_n) begin
        if(!I_rst_n )begin
            I_raw_tvalid_d <= 0;
            I_raw_tlast_d  <= 0;
            I_raw_tdata_r  <= 0;
            O_raw_tdest    <= 0;
        end
        else begin
            I_raw_tvalid_d <= I_raw_tvalid;
            I_raw_tlast_d  <= I_raw_tlast; 
            I_raw_tdata_r  <= I_raw_tdata;
            O_raw_tdest    <= I_raw_tdest;
        end
    end

generate 
    if(FRAME_SELECT == RAW10_1920x1080) begin : RAW10_1920x1080_60fps
        // 计算行数
        always @(posedge I_clk or negedge I_rst_n) begin
            if(!I_rst_n || I_raw_tuser)
                v_cnt <= 0;
            else if(I_raw_tlast_d)
                v_cnt  <=  v_cnt + 1;
        end
    
        assign V_valid = (v_cnt > 19) && (v_cnt < 1100);
    
        // 计算每行像素个数
        always @(posedge I_clk or negedge I_rst_n) begin
            if(!I_rst_n || I_raw_tuser) 
                h_cnt <= 0;
            else
                h_cnt <= I_raw_tvalid_d ? (I_raw_tlast_d ? 0 : h_cnt + 1) : h_cnt;
        end
        assign H_valid = (h_cnt < 481) && (h_cnt > 0);
        
        assign tuser = (h_cnt == 1) && (v_cnt == 20) && I_raw_tvalid_d;
        
        assign O_raw_tdata  = V_valid && H_valid? I_raw_tdata_r:0;
        assign I_raw_tready = O_raw_tready;
        assign O_raw_tvalid = H_valid && V_valid && I_raw_tvalid_d;
        assign O_raw_tlast  = V_valid && (h_cnt == 480) && I_raw_tvalid_d;
        assign O_raw_tuser  = tuser;
    end

    else if(FRAME_SELECT == RAW10_3840x2160)begin:RAW10_3840x2160_60fps
        // 计算行数
        always @(posedge I_clk or negedge I_rst_n) begin
            if(!I_rst_n || I_raw_tuser)
                v_cnt <= 0;
            else if(I_raw_tlast_d)
                v_cnt  <=  v_cnt + 1;
        end
        assign V_valid = (v_cnt > 36) && (v_cnt < 2197);
        // 计算每行像素个数
        always @(posedge I_clk or negedge I_rst_n) begin
            if(!I_rst_n || I_raw_tuser) 
                h_cnt <= 0;
            else
                h_cnt <= I_raw_tvalid_d ? (I_raw_tlast_d ? 0 : h_cnt + 1) : h_cnt;
        end
        assign H_valid = (h_cnt < 961) && (h_cnt > 0);
        
        assign tuser = (h_cnt == 1) && (v_cnt == 37) && I_raw_tvalid_d;
        
        
        assign O_raw_tdata  = V_valid && H_valid? I_raw_tdata_r:0;
        assign I_raw_tready = O_raw_tready;
        assign O_raw_tvalid = H_valid && V_valid && I_raw_tvalid_d;
        assign O_raw_tlast  = V_valid && (h_cnt == 960) && I_raw_tvalid_d;
        assign O_raw_tuser  = tuser;
    end

    else if(FRAME_SELECT == RAW10_1280x720)begin:RAW10_1280_720_60fps
        // 计算行数
        always @(posedge I_clk or negedge I_rst_n) begin
            if(!I_rst_n || I_raw_tuser)
                v_cnt <= 0;
            else if(I_raw_tlast_d)
                v_cnt  <=  v_cnt + 1;
        end
        assign V_valid = (v_cnt > 35) && (v_cnt < 756);
        // 计算每行像素个数
        always @(posedge I_clk or negedge I_rst_n) begin
            if(!I_rst_n || I_raw_tuser) 
                h_cnt <= 0;
            else
                h_cnt <= I_raw_tvalid_d ? (I_raw_tlast_d ? 0 : h_cnt + 1) : h_cnt;
        end
        assign H_valid = (h_cnt < 321) && (h_cnt > 0);
        
        assign tuser = (h_cnt == 1) && (v_cnt == 36) && I_raw_tvalid_d;
        
// assign O_raw_tdata  = V_valid && H_valid? {I_raw_tdata_r[9:0],I_raw_tdata_r[19:10],I_raw_tdata_r[29:20],I_raw_tdata_r[39:30]}:0;
        assign O_raw_tdata  = V_valid && H_valid? I_raw_tdata_r:0;
        assign I_raw_tready = O_raw_tready;
        assign O_raw_tvalid = H_valid && V_valid && I_raw_tvalid_d;
        assign O_raw_tlast  = V_valid && (h_cnt == 320) && I_raw_tvalid_d;
        assign O_raw_tuser  = tuser;
    end
endgenerate

endmodule