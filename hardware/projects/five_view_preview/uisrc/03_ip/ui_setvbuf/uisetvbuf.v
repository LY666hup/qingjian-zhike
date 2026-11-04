
/*******************************MILIANKE*******************************
*Company : MiLianKe Electronic Technology Co., Ltd.
*WebSite:https://www.milianke.com
*TechWeb:https://www.uisrc.com
*tmall-shop:https://milianke.tmall.com
*jd-shop:https://milianke.jd.com
*taobao-shop1: https://milianke.taobao.com
*Create Date: 2023/03/23
*Module Name:
*File Name:
*Description: 
*The reference demo provided by Milianke is only used for learning. 
*We cannot ensure that the demo itself is free of bugs, so users 
*should be responsible for the technical problems and consequences
*caused by the use of their own products.
*Copyright: Copyright (c) MiLianKe
*All rights reserved.
*Revision: 1.1
*Signal description
*1) I_ input
*2) O_ output
*3) IO_ input output
*3) S_ system internal signal
*3) _n activ low
*4) _dg debug signal 
*5) _r delay or register
*6) _s state mechine
*********************************************************************/
`timescale 1ns / 1ps

module ui_setvbuf#(
parameter  integer                  BUF_DELAY     = 1,
parameter  integer                  BUF_LENTH     = 3
)
(

input      [7   :0]                 I_bufn,
output     [7   :0]                 O_bufn
);   


// 3帧缓存 延迟1帧推导 BUF_DELAY     = 1 ,BUF_LENTH     = 3
//0<BUF_DELAY(1)     BUF_LENTH(3)   - BUF_DELAY(1) + I_bufn(0)  = 2
//1=BUF_DELAY(1)     I_bufn(1)      - BUF_DELAY(1)   = 0
//2>BUF_DELAY(1)     I_bufn(2)      - BUF_DELAY(1)   = 1



// 3帧缓存 延迟2帧推导 BUF_DELAY     = 2 ,BUF_LENTH     = 3
//0<BUF_DELAY(2)     BUF_LENTH(3) - BUF_DELAY(2) + I_bufn(0)  = 1
//1<BUF_DELAY(2)     BUF_LENTH(3) - BUF_DELAY(2) + I_bufn(1)  = 2
//2=BUF_DELAY(2)     I_bufn(2)    - BUF_DELAY(2)   = 0


// 4帧缓存 延迟1帧推导 BUF_DELAY     = 1 ,BUF_LENTH     = 4
//0<BUF_DELAY(1)     BUF_LENTH(4) - BUF_DELAY(1) + I_bufn(0)   = 3

//1=BUF_DELAY(1)     I_bufn(1)    - BUF_DELAY(1)               = 0

//2>BUF_DELAY(1)     I_bufn(2)    - BUF_DELAY(1)               = 1

//3>BUF_DELAY(1)     I_bufn(3)    - BUF_DELAY(1)               = 2


// 4帧缓存 延迟2帧推导 BUF_DELAY     = 2 ,BUF_LENTH     = 4
//0<BUF_DELAY(2)     BUF_LENTH(4) - BUF_DELAY(2) + I_bufn(0)   = 2

//1<BUF_DELAY(2)     BUF_LENTH(4) - BUF_DELAY(2) + I_bufn(1)   = 3

//2=BUF_DELAY(2)     I_bufn(2)    - BUF_DELAY(2)               = 0

//3>BUF_DELAY(2)     I_bufn(3)    - BUF_DELAY(2)               = 1

//后面不再推导

assign O_bufn = I_bufn < BUF_DELAY?  (BUF_LENTH - BUF_DELAY + I_bufn) : (I_bufn - BUF_DELAY) ;


endmodule

