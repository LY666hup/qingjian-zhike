`timescale 1ns / 1ps
/*******************************MILIANKE*******************************
*Company : MiLianKe Electronic Technology Co., Ltd.
*WebSite:https://www.milianke.com
*TechWeb:https://www.uisrc.com
*tmall-shop:https://milianke.tmall.com
*jd-shop:https://milianke.jd.com
*taobao-shop1: https://milianke.taobao.com
*Create Date: 2019/12/17
*Module Name:HDMI_IN_Test
*File Name:HDMI_IN_Test.v
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

/*******************************ui_imx415reg***************************
--1.IMX415寄存器信息
*********************************************************************/

module ui_imx415reg (
    input      [8 : 0] REG_INDEX,
    output reg [31: 0] REG_DATA,
    output     [7 : 0] REG_SIZE
);

  assign REG_SIZE = 201;
  //-----------------------------------------------------------------
  /////////////////////   Config Data LUT   //////////////////////////    
  always @(*) begin
    case (REG_INDEX)
      0:   REG_DATA = {16'h3000, 8'h01};// STANDBY
      1:   REG_DATA = {16'h3001, 8'h00};// REGHOLD
      2:   REG_DATA = {16'h3002, 8'h01};// XMSTA
      3:   REG_DATA = {16'h3003, 8'h00};// XMASTER
      4:   REG_DATA = {16'h3008, 8'h7F};// BCWAIT_TIME[9:0], INCK=37.125MHz
      5:   REG_DATA = {16'h3009, 8'h00};// BCWAIT_TIME[9:0]
      6:   REG_DATA = {16'h300A, 8'h5B};// CPWAIT_TIME[9:0], INCK=37.125MHz
      7:   REG_DATA = {16'h300B, 8'hA0};// CPWAIT_TIME[9:0]
      8:   REG_DATA = {16'h301C, 8'h00};// WINMODE[3:0]
      9:   REG_DATA = {16'h301D, 8'h08};// -
      10:  REG_DATA = {16'h3020, 8'h01};// HADD
      11:  REG_DATA = {16'h3021, 8'h01};// VADD
      12:  REG_DATA = {16'h3022, 8'h01};// ADDMODE[1:0]
      13:  REG_DATA = {16'h3023, 8'h01};// VCMODE
      14:  REG_DATA = {16'h3024, 8'hEB};// VMAX[19:0]
      15:  REG_DATA = {16'h3025, 8'h08};// VMAX[19:0]
      16:  REG_DATA = {16'h3026, 8'h00};// VMAX[19:0]
      17:  REG_DATA = {16'h3028, 8'h1E};// HMAX[15:0]
      18:  REG_DATA = {16'h3029, 8'h02};// HMAX[15:0]
      19:  REG_DATA = {16'h302C, 8'h00};// WDMODE[1:0]
      20:  REG_DATA = {16'h302D, 8'h00};// WDSEL[1:0]
      21:  REG_DATA = {16'h3030, 8'h00};// HREVERSE
      22:  REG_DATA = {16'h3031, 8'h00};// ADBIT[1:0]
      23:  REG_DATA = {16'h3032, 8'h00};// MDBIT
      24:  REG_DATA = {16'h3033, 8'h05};// SYS_MODE[3:0]
      25:  REG_DATA = {16'h3040, 8'h00};// PIX_HST[12:0]
      26:  REG_DATA = {16'h3041, 8'h00};// PIX_HST[12:0]
      27:  REG_DATA = {16'h3042, 8'h18};// PIX_HWIDTH[12:0]
      28:  REG_DATA = {16'h3043, 8'h0F};// PIX_HWIDTH[12:0]
      29:  REG_DATA = {16'h3044, 8'h00};// PIX_VST[12:0]
      30:  REG_DATA = {16'h3045, 8'h00};// PIX_VST[12:0]
      31:  REG_DATA = {16'h3046, 8'h20};// PIX_VWIDTH[12:0]
      32:  REG_DATA = {16'h3047, 8'h11};// PIX_VWIDTH[12:0]
      33:  REG_DATA = {16'h3050, 8'h08};// SHR0[19:0]
      34:  REG_DATA = {16'h3051, 8'h00};// SHR0[19:0]
      35:  REG_DATA = {16'h3052, 8'h00};// SHR0[19:0]
      36:  REG_DATA = {16'h3054, 8'h19};// SHR1[19:0]
      37:  REG_DATA = {16'h3055, 8'h00};// SHR1[19:0]
      38:  REG_DATA = {16'h3056, 8'h00};// SHR1[19:0]
      39:  REG_DATA = {16'h3058, 8'h3E};// SHR2[19:0]
      40:  REG_DATA = {16'h3059, 8'h00};// SHR2[19:0]
      41:  REG_DATA = {16'h305A, 8'h00};// SHR2[19:0]
      42:  REG_DATA = {16'h305C, 8'h66};// SHR3[19:0]
      43:  REG_DATA = {16'h305D, 8'h00};// SHR3[19:0]
      44:  REG_DATA = {16'h305E, 8'h00};// SHR3[19:0]
      45:  REG_DATA = {16'h3060, 8'h25};// RHS1[19:0]
      46:  REG_DATA = {16'h3061, 8'h00};// RHS1[19:0]
      47:  REG_DATA = {16'h3062, 8'h00};// RHS1[19:0]
      48:  REG_DATA = {16'h3064, 8'h4A};// RHS2[19:0]
      49:  REG_DATA = {16'h3065, 8'h00};// RHS2[19:0]
      50:  REG_DATA = {16'h3066, 8'h00};// RHS2[19:0]
      51:  REG_DATA = {16'h3090, 8'h30};// GAIN_PGC_0[8:0], 0x64=30dB, match PCIe setup AE_GAIN=100
      52:  REG_DATA = {16'h3091, 8'h00};// GAIN_PGC_0[8:0]
      53:  REG_DATA = {16'h3092, 8'h30};// GAIN_PGC_1[8:0]
      54:  REG_DATA = {16'h3093, 8'h00};// GAIN_PGC_1[8:0]
      55:  REG_DATA = {16'h3094, 8'h30};// GAIN_PGC_2[8:0]
      56:  REG_DATA = {16'h3095, 8'h00};// GAIN_PGC_2[8:0]
      57:  REG_DATA = {16'h3096, 8'h30};// GAIN_PGC_3[8:0]
      58:  REG_DATA = {16'h3097, 8'h00};// GAIN_PGC_3[8:0]
      59:  REG_DATA = {16'h30C0, 8'h2A};// XVSOUTSEL[1:0]
      60:  REG_DATA = {16'h30C1, 8'h00};// XVS_DRV[1:0]
      61:  REG_DATA = {16'h30CC, 8'h00};// -
      62:  REG_DATA = {16'h30CD, 8'h00};// -
      63:  REG_DATA = {16'h30CF, 8'h00};// XVSMSKCNT_INT[1:0]
      64:  REG_DATA = {16'h30D9, 8'h02};// DIG_CLP_VSTART[4:0]
      65:  REG_DATA = {16'h30DA, 8'h01};// DIG_CLP_VNUM[1:0]
      66:  REG_DATA = {16'h30E2, 8'h32};// BLKELVEL[9:0]
      67:  REG_DATA = {16'h30E3, 8'h00};// BLKELVEL[9:0]
      68:  REG_DATA = {16'h3115, 8'h00};// INCKSEL1[7:0]
      69:  REG_DATA = {16'h3116, 8'h24};// INCKSEL2[7:0], INCK=37.125MHz
      70:  REG_DATA = {16'h3118, 8'hC0};// INCKSEL3[10:0], INCK=37.125MHz
      71:  REG_DATA = {16'h3119, 8'h00};// INCKSEL3[10:0]
      72:  REG_DATA = {16'h311A, 8'hE0};// INCKSEL4[10:0], INCK=37.125MHz
      73:  REG_DATA = {16'h311B, 8'h00};// INCKSEL4[10:0]
      74:  REG_DATA = {16'h311E, 8'h24};// INCKSEL5[7:0], INCK=37.125MHz
      75:  REG_DATA = {16'h3260, 8'h01};// GAIN_PGC_FIDMD
      76:  REG_DATA = {16'h32C8, 8'h01};
      77:  REG_DATA = {16'h32D4, 8'h21};
      78:  REG_DATA = {16'h32EC, 8'hA1};
      79:  REG_DATA = {16'h344C, 8'h2B};
      80:  REG_DATA = {16'h344D, 8'h01};
      81:  REG_DATA = {16'h344E, 8'hED};
      82:  REG_DATA = {16'h344F, 8'h01};
      83:  REG_DATA = {16'h3450, 8'hF6};
      84:  REG_DATA = {16'h3451, 8'h02};
      85:  REG_DATA = {16'h3452, 8'h7F};
      86:  REG_DATA = {16'h3453, 8'h03};
      87:  REG_DATA = {16'h358A, 8'h04};
      88:  REG_DATA = {16'h35A1, 8'h02};
      89:  REG_DATA = {16'h35EC, 8'h27};
      90:  REG_DATA = {16'h35EE, 8'h8D};
      91:  REG_DATA = {16'h35F0, 8'h8D};
      92:  REG_DATA = {16'h35F2, 8'h29};
      93:  REG_DATA = {16'h36BC, 8'h0C};
      94:  REG_DATA = {16'h36CC, 8'h53};
      95:  REG_DATA = {16'h36CD, 8'h00};
      96:  REG_DATA = {16'h36CE, 8'h3C};
      97:  REG_DATA = {16'h36D0, 8'h8C};
      98:  REG_DATA = {16'h36D1, 8'h00};
      99:  REG_DATA = {16'h36D2, 8'h71};
      100: REG_DATA = {16'h36D4, 8'h3C};
      101: REG_DATA = {16'h36D6, 8'h53};
      102: REG_DATA = {16'h36D7, 8'h00};
      103: REG_DATA = {16'h36D8, 8'h71};
      104: REG_DATA = {16'h36DA, 8'h8C};
      105: REG_DATA = {16'h36DB, 8'h00};
      106: REG_DATA = {16'h3701, 8'h00};
      107: REG_DATA = {16'h3720, 8'h00};
      108: REG_DATA = {16'h3724, 8'h02};
      109: REG_DATA = {16'h3726, 8'h02};
      110: REG_DATA = {16'h3732, 8'h02};
      111: REG_DATA = {16'h3734, 8'h03};
      112: REG_DATA = {16'h3736, 8'h03};
      113: REG_DATA = {16'h3742, 8'h03};
      114: REG_DATA = {16'h3862, 8'hE0};
      115: REG_DATA = {16'h38CC, 8'h30};
      116: REG_DATA = {16'h38CD, 8'h2F};
      117: REG_DATA = {16'h395C, 8'h0C};
      118: REG_DATA = {16'h39A4, 8'h07};
      119: REG_DATA = {16'h39A8, 8'h32};
      120: REG_DATA = {16'h39AA, 8'h32};
      121: REG_DATA = {16'h39AC, 8'h32};
      122: REG_DATA = {16'h39AE, 8'h32};
      123: REG_DATA = {16'h39B0, 8'h32};
      124: REG_DATA = {16'h39B2, 8'h2F};
      125: REG_DATA = {16'h39B4, 8'h2D};
      126: REG_DATA = {16'h39B6, 8'h28};
      127: REG_DATA = {16'h39B8, 8'h30};
      128: REG_DATA = {16'h39BA, 8'h30};
      129: REG_DATA = {16'h39BC, 8'h30};
      130: REG_DATA = {16'h39BE, 8'h30};
      131: REG_DATA = {16'h39C0, 8'h30};
      132: REG_DATA = {16'h39C2, 8'h2E};
      133: REG_DATA = {16'h39C4, 8'h2B};
      134: REG_DATA = {16'h39C6, 8'h25};
      135: REG_DATA = {16'h3A42, 8'hD1};
      136: REG_DATA = {16'h3A4C, 8'h77};
      137: REG_DATA = {16'h3AE0, 8'h02};
      138: REG_DATA = {16'h3AEC, 8'h0C};
      139: REG_DATA = {16'h3B00, 8'h2E};
      140: REG_DATA = {16'h3B06, 8'h29};
      141: REG_DATA = {16'h3B98, 8'h25};
      142: REG_DATA = {16'h3B99, 8'h21};
      143: REG_DATA = {16'h3B9B, 8'h13};
      144: REG_DATA = {16'h3B9C, 8'h13};
      145: REG_DATA = {16'h3B9D, 8'h13};
      146: REG_DATA = {16'h3B9E, 8'h13};
      147: REG_DATA = {16'h3BA1, 8'h00};
      148: REG_DATA = {16'h3BA2, 8'h06};
      149: REG_DATA = {16'h3BA3, 8'h0B};
      150: REG_DATA = {16'h3BA4, 8'h10};
      151: REG_DATA = {16'h3BA5, 8'h14};
      152: REG_DATA = {16'h3BA6, 8'h18};
      153: REG_DATA = {16'h3BA7, 8'h1A};
      154: REG_DATA = {16'h3BA8, 8'h1A};
      155: REG_DATA = {16'h3BA9, 8'h1A};
      156: REG_DATA = {16'h3BAC, 8'hED};
      157: REG_DATA = {16'h3BAD, 8'h01};
      158: REG_DATA = {16'h3BAE, 8'hF6};
      159: REG_DATA = {16'h3BAF, 8'h02};
      160: REG_DATA = {16'h3BB0, 8'hA2};
      161: REG_DATA = {16'h3BB1, 8'h03};
      162: REG_DATA = {16'h3BB2, 8'hE0};
      163: REG_DATA = {16'h3BB3, 8'h03};
      164: REG_DATA = {16'h3BB4, 8'hE0};
      165: REG_DATA = {16'h3BB5, 8'h03};
      166: REG_DATA = {16'h3BB6, 8'hE0};
      167: REG_DATA = {16'h3BB7, 8'h03};
      168: REG_DATA = {16'h3BB8, 8'hE0};
      169: REG_DATA = {16'h3BBA, 8'hE0};
      170: REG_DATA = {16'h3BBC, 8'hDA};
      171: REG_DATA = {16'h3BBE, 8'h88};
      172: REG_DATA = {16'h3BC0, 8'h44};
      173: REG_DATA = {16'h3BC2, 8'h7B};
      174: REG_DATA = {16'h3BC4, 8'hA2};
      175: REG_DATA = {16'h3BC8, 8'hBD};
      176: REG_DATA = {16'h3BCA, 8'hBD};
      177: REG_DATA = {16'h4000, 8'h10};
      178: REG_DATA = {16'h4001, 8'h03};//LANEMODE[2:0]
      179: REG_DATA = {16'h4004, 8'h48};//TXCLKESC_FREQ[15:0], INCK=37.125MHz
      180: REG_DATA = {16'h4005, 8'h09};//TXCLKESC_FREQ[15:0], INCK=37.125MHz
      181: REG_DATA = {16'h400C, 8'h00};//INCKSEL6
      182: REG_DATA = {16'h4018, 8'h7F};//TCLKPOST[15:0]
      183: REG_DATA = {16'h4019, 8'h00};//TCLKPOST[15:0]
      184: REG_DATA = {16'h401A, 8'h37};//TCLKPREPARE[15:0]
      185: REG_DATA = {16'h401B, 8'h00};//TCLKPREPARE[15:0]
      186: REG_DATA = {16'h401C, 8'h37};//TCLKTRAIL[15:0]
      187: REG_DATA = {16'h401D, 8'h00};//TCLKTRAIL[15:0]
      188: REG_DATA = {16'h401E, 8'hF7};//TCLKZERO[15:0]
      189: REG_DATA = {16'h401F, 8'h00};//TCLKZERO[15:0]
      190: REG_DATA = {16'h4020, 8'h3F};//THSPREPARE[15:0]
      191: REG_DATA = {16'h4021, 8'h00};//THSPREPARE[15:0]
      192: REG_DATA = {16'h4022, 8'h6F};//THSZERO[15:0]
      193: REG_DATA = {16'h4023, 8'h00};//THSZERO[15:0]
      194: REG_DATA = {16'h4024, 8'h3F};//THSTRAIL[15:0]
      195: REG_DATA = {16'h4025, 8'h00};//THSTRAIL[15:0]
      196: REG_DATA = {16'h4026, 8'h5F};//THSEXIT[15:0]
      197: REG_DATA = {16'h4027, 8'h00};//THSEXIT[15:0]
      198: REG_DATA = {16'h4028, 8'h2F};//TLPX[15:0]
      199: REG_DATA = {16'h4029, 8'h00};//TLPX[15:0]
      200: REG_DATA = {16'h4074, 8'h01};//INCKSEL7[2:0]
      default: REG_DATA = {16'h0000, 8'h00};
    endcase
  end

endmodule

