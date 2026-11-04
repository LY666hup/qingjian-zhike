# =============================================================================
# timing_tx_tpg.sdc -- AFC03 standalone TPG -> MIPI TX 时序约束
# 只含 pll_dsi (实例名 u_ui_pll_dsi, 内部 PH1_PHY_PLL 实例名 pll_inst)。
#   clkc[0]=25M(fb) clkc[1]=125M(byte) clkc[2]=250M clkc[3]=500M(hs x4)
#   clkc[4]=500M@90 clkc[5]=10M(LP)
# =============================================================================

create_clock -name {sys_clk} -period 40.000 -waveform {0.000 20.000} [get_ports {I_sysclk_25m}]

derive_clocks

rename_clock -name {S_dsi_25m}     [get_clocks {u_ui_pll_dsi/pll_inst.clkc[0]}]
rename_clock -name {S_dsi_byte}    [get_clocks {u_ui_pll_dsi/pll_inst.clkc[1]}]
rename_clock -name {S_dsi_hs_x2}   [get_clocks {u_ui_pll_dsi/pll_inst.clkc[2]}]
rename_clock -name {S_dsi_hs_x4}   [get_clocks {u_ui_pll_dsi/pll_inst.clkc[3]}]
rename_clock -name {S_dsi_hs_x4_q} [get_clocks {u_ui_pll_dsi/pll_inst.clkc[4]}]
rename_clock -name {S_dsi_lp}      [get_clocks {u_ui_pll_dsi/pll_inst.clkc[5]}]

# I_dsi_en 经 3-FF 同步器进入 byte_clk 域, 作为异步输入处理
set_clock_groups -asynchronous \
    -group [get_clocks {sys_clk}] \
    -group [get_clocks {S_dsi_byte S_dsi_hs_x2 S_dsi_hs_x4 S_dsi_hs_x4_q S_dsi_lp}]
