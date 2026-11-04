/************************************************************\
**	Copyright (c) 2012-2025 Anlogic Inc.
**	All Right Reserved.
\************************************************************/
/************************************************************\
**	Build time: Oct 19 2026 16:51:49
**	TD version	:	6.2.178840
************************************************************/
module cwc1
(
  input   [0:0]                 probe0,
  input   [0:0]                 probe1,
  input   [0:0]                 probe2,
  input   [0:0]                 probe3,
  input   [0:0]                 probe4,
  input   [0:0]                 probe5,
  input                         clk
);

  ChipWatcher_1cf9d8696f20  ChipWatcher_1cf9d8696f20_Inst
  (
      .probe0(probe0),
      .probe1(probe1),
      .probe2(probe2),
      .probe3(probe3),
      .probe4(probe4),
      .probe5(probe5),
      .clk(clk)
  );
endmodule
