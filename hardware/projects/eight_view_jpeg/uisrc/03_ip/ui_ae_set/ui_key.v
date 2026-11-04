
/*******************************MILIANKE*******************************
*Company : MiLianKe Electronic Technology Co., Ltd.
*WebSite:https://www.milianke.com
*TechWeb:https://www.uisrc.com
*tmall-shop:https://milianke.tmall.com
*jd-shop:https://milianke.jd.com
*taobao-shop1: https://milianke.taobao.com
*Create Date: 2023/08/07
*Module Name:key
*File Name:key.v
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
*4) S_ system internal signal
*5) _n activ low
*6) _dg debug signal 
*7) _r delay or register
*8) _s state mechine
*********************************************************************/
/*********ui_key 按键消抖 ***********
--版本号1.0
*********************************************************************/

module ui_key #
(
parameter REF_CLK = 64'd50_000_000        //设置时钟为参数，方便上层调用修改
)
(
input  I_sysclk,
input  I_rstn,
input  I_key,
output O_key_down,
output O_key_up
);

localparam  T10MS = (REF_CLK/100 - 1'b1);                      //设置10MS的时钟分频计数
localparam  KEY_S0 = 2'd0;                                      //设置按键状态机的状态
localparam  KEY_S1 = 2'd1;
localparam  KEY_S2 = 2'd2;
localparam  KEY_S3 = 2'd3;

reg [32:0] t10ms_cnt = 25'd0;
reg [3:0] key_r = 4'd0;
reg [1:0] key_s = 2'b0;
reg [1:0] key_s_r = 2'b0;
wire t10ms_done ;
 
assign t10ms_done = (t10ms_cnt == T10MS);
assign O_key_down   = (key_s == KEY_S2)&&( key_s_r == KEY_S1);    //设置判断按键按下时的条件
assign O_key_up     = (key_s == KEY_S0)&&( key_s_r == KEY_S3);     //设置判断按键松开时的条件

//10ms timer counter
always @(posedge I_sysclk or negedge I_rstn)begin                 //系统时钟的上升沿以及复位的下降沿触发
    if(I_rstn == 1'b0)begin
        t10ms_cnt <= 25'd0;                                          //系统复位
    end
    else if(t10ms_cnt < T10MS)                                        //10ms计数，目标值是 T10MS
        t10ms_cnt <= t10ms_cnt + 1'b1;                                //未达到目标t10ms_cnt+1 
    else 
        t10ms_cnt <= 25'd0;                                             //达到目标值复位
end
always @(posedge I_sysclk)begin                                     //将key_s的状态缓存一拍
    key_s_r <= key_s;
end
always @(posedge I_sysclk)begin                                     //将I_key的状态缓存一拍
    key_r <= {key_r[2:0],I_key};
end
always @(posedge I_sysclk or negedge I_rstn)begin                //设置状态机，设定按键的4种状态
    if(I_rstn == 1'b0)begin
        key_s <= KEY_S0;
    end
    else if(t10ms_done)begin                                           //触发条件为t10ms_done，说明下列所有的状态转移都是每10ms
        case(key_s)                                                     //触发一次
            KEY_S0:begin
            if(!key_r[3])                                                //收到第一个按键的低电平信号，不能判断是否为毛刺
                key_s <= KEY_S1;                                        //转到状态S1
            end  
            KEY_S1:begin//recheck key done                               //第二次判断按键是否按下
            if(!key_r[3])
                key_s <= KEY_S2;                                        //按下转入S2状态
                else 
                key_s <= KEY_S0;                                        //没按下，判断为毛刺，转入S0状态，等待触发
            end 
            KEY_S2:begin//wait key up                                     //确定按键按下后
            if(key_r[3])                                                //等待按键松开，接收到按键的高电平信号，
                key_s <= KEY_S3;                                        //不能确定是否为毛刺
            end                                                              //转入状态S3
            KEY_S3:begin//recheck key up                                   
            if(key_r[3])                                                 //第二次判断案件是否松开
                key_s <= KEY_S0;                                           //依然检测到按键是松开的状态，转入S0状态，等待触发
            end
        endcase                  
    end
end

endmodule   
