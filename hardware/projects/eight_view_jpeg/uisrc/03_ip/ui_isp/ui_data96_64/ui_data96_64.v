/*****************************************************************
Company : Nanjing Weiku Robot Technology Co., Ltd.
Brand   : VLKUS
Technical forum:www.uisrc.com
@Author      :   XiaoQingquan 
@Time        :   2024/09/18 
@Description :   
*****************************************************************/

module ui_data96_64 (

    input wire [95:0] I_tdata,

    output wire [63:0] O_tdata

);

  assign O_tdata = {
    I_tdata[95:91], I_tdata[87:82], I_tdata[79:75],  // 像素0
	I_tdata[71:67], I_tdata[63:58], I_tdata[55:51],  // 像素1  
	I_tdata[47:43], I_tdata[39:34], I_tdata[31:27],  // 像素2
	I_tdata[23:19], I_tdata[15:10], I_tdata[7:3]     // 像素3
  };
endmodule
