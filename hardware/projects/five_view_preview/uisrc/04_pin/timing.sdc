create_clock -name {sys_clk} -period 40.000 -waveform {0.000 20.000} [get_ports {I_sysclk_25m}]
create_clock -name {ddr_clk} -period 10.000 -waveform {0.000 5.000} [get_ports {I_ddr_clk}]

# IMX415 SYS_MODE=5 is 891 Mbps/lane. The forwarded D-PHY clock is half the
# lane bit rate: 445.5 MHz, period 2.24467 ns.
create_clock -name {mipi_hs_clk} -period 2.24467 -waveform {0.00000 1.12234} [get_ports {I_clk_hs_p}]

# Independent B forwarded clock; same sensor configuration assumption as A.
create_clock -name {mipi_hs_clk_B} -period 2.24467 -waveform {0.00000 1.12234} [get_ports {I_clk_hs_p_B}]

derive_clocks

# Main video PLL: only CLKC0 and CLKC1 survive synthesis in this design.
rename_clock -name {S_clk_24m} [get_clocks {X_ui_pll/pll_inst.clkc[0]}]
rename_clock -name {S_pclkx1}  [get_clocks {X_ui_pll/pll_inst.clkc[1]}]

# RK-side CSI-2 transmitter PLL.
rename_clock -name {S_dsi_25m}     [get_clocks {u_ui_pll_dsi/pll_inst.clkc[0]}]
rename_clock -name {S_dsi_byte}    [get_clocks {u_ui_pll_dsi/pll_inst.clkc[1]}]
rename_clock -name {S_dsi_hs_x2}   [get_clocks {u_ui_pll_dsi/pll_inst.clkc[2]}]
rename_clock -name {S_dsi_hs_x4}   [get_clocks {u_ui_pll_dsi/pll_inst.clkc[3]}]
rename_clock -name {S_dsi_hs_x4_q} [get_clocks {u_ui_pll_dsi/pll_inst.clkc[4]}]

# TD accepts one ratio operator per generated clock. 25 MHz / 2.5 = 10 MHz.
# No master_clock is needed: this source has one root clock.
create_generated_clock -name {S_dsi_lp} \
    -source [get_ports {I_sysclk_25m}] -divide_by 2.5 \
    [get_pins {u_ui_pll_dsi/pll_inst.clkc[5]}]

# Do not blanket-cut the new async FIFOs with clock groups: that would hide
# Gray-pointer delay constraints. Unreviewed cross-domain paths remain visible.
# A 4 ns bound is below both the 7.5 ns DDR and 8.98 ns RX clock periods.
# The actual routed bound, synchronizer placement, MTBF and IP CDC still need review.
set_max_delay -datapath_only 4.0 -from [get_regs -regexp {^u_capture_A_original/queue/(wgray.[0-9]+.|wbin.9.)$}] -to [get_regs -regexp {^u_capture_A_original/queue/rq_wgray1.[0-9]+.$}]
set_false_path -hold -from [get_regs -regexp {^u_capture_A_original/queue/(wgray.[0-9]+.|wbin.9.)$}] -to [get_regs -regexp {^u_capture_A_original/queue/rq_wgray1.[0-9]+.$}]
set_max_delay -datapath_only 4.0 -from [get_regs -regexp {^u_capture_A_original/queue/(rgray.[0-9]+.|rbin.9.)$}] -to [get_regs -regexp {^u_capture_A_original/queue/wq_rgray1.[0-9]+.$}]
set_false_path -hold -from [get_regs -regexp {^u_capture_A_original/queue/(rgray.[0-9]+.|rbin.9.)$}] -to [get_regs -regexp {^u_capture_A_original/queue/wq_rgray1.[0-9]+.$}]
set_max_delay -datapath_only 4.0 -from [get_regs -regexp {^u_capture_A_gray/queue/(wgray.[0-9]+.|wbin.9.)$}] -to [get_regs -regexp {^u_capture_A_gray/queue/rq_wgray1.[0-9]+.$}]
set_false_path -hold -from [get_regs -regexp {^u_capture_A_gray/queue/(wgray.[0-9]+.|wbin.9.)$}] -to [get_regs -regexp {^u_capture_A_gray/queue/rq_wgray1.[0-9]+.$}]
set_max_delay -datapath_only 4.0 -from [get_regs -regexp {^u_capture_A_gray/queue/(rgray.[0-9]+.|rbin.9.)$}] -to [get_regs -regexp {^u_capture_A_gray/queue/wq_rgray1.[0-9]+.$}]
set_false_path -hold -from [get_regs -regexp {^u_capture_A_gray/queue/(rgray.[0-9]+.|rbin.9.)$}] -to [get_regs -regexp {^u_capture_A_gray/queue/wq_rgray1.[0-9]+.$}]
set_max_delay -datapath_only 4.0 -from [get_regs -regexp {^u_capture_A_gauss/queue/(wgray.[0-9]+.|wbin.9.)$}] -to [get_regs -regexp {^u_capture_A_gauss/queue/rq_wgray1.[0-9]+.$}]
set_false_path -hold -from [get_regs -regexp {^u_capture_A_gauss/queue/(wgray.[0-9]+.|wbin.9.)$}] -to [get_regs -regexp {^u_capture_A_gauss/queue/rq_wgray1.[0-9]+.$}]
set_max_delay -datapath_only 4.0 -from [get_regs -regexp {^u_capture_A_gauss/queue/(rgray.[0-9]+.|rbin.9.)$}] -to [get_regs -regexp {^u_capture_A_gauss/queue/wq_rgray1.[0-9]+.$}]
set_false_path -hold -from [get_regs -regexp {^u_capture_A_gauss/queue/(rgray.[0-9]+.|rbin.9.)$}] -to [get_regs -regexp {^u_capture_A_gauss/queue/wq_rgray1.[0-9]+.$}]
set_max_delay -datapath_only 4.0 -from [get_regs -regexp {^u_capture_A_hist/queue/(wgray.[0-9]+.|wbin.9.)$}] -to [get_regs -regexp {^u_capture_A_hist/queue/rq_wgray1.[0-9]+.$}]
set_false_path -hold -from [get_regs -regexp {^u_capture_A_hist/queue/(wgray.[0-9]+.|wbin.9.)$}] -to [get_regs -regexp {^u_capture_A_hist/queue/rq_wgray1.[0-9]+.$}]
set_max_delay -datapath_only 4.0 -from [get_regs -regexp {^u_capture_A_hist/queue/(rgray.[0-9]+.|rbin.9.)$}] -to [get_regs -regexp {^u_capture_A_hist/queue/wq_rgray1.[0-9]+.$}]
set_false_path -hold -from [get_regs -regexp {^u_capture_A_hist/queue/(rgray.[0-9]+.|rbin.9.)$}] -to [get_regs -regexp {^u_capture_A_hist/queue/wq_rgray1.[0-9]+.$}]
set_max_delay -datapath_only 4.0 -from [get_regs -regexp {^u_capture_B_original/queue/(wgray.[0-9]+.|wbin.9.)$}] -to [get_regs -regexp {^u_capture_B_original/queue/rq_wgray1.[0-9]+.$}]
set_false_path -hold -from [get_regs -regexp {^u_capture_B_original/queue/(wgray.[0-9]+.|wbin.9.)$}] -to [get_regs -regexp {^u_capture_B_original/queue/rq_wgray1.[0-9]+.$}]
set_max_delay -datapath_only 4.0 -from [get_regs -regexp {^u_capture_B_original/queue/(rgray.[0-9]+.|rbin.9.)$}] -to [get_regs -regexp {^u_capture_B_original/queue/wq_rgray1.[0-9]+.$}]
set_false_path -hold -from [get_regs -regexp {^u_capture_B_original/queue/(rgray.[0-9]+.|rbin.9.)$}] -to [get_regs -regexp {^u_capture_B_original/queue/wq_rgray1.[0-9]+.$}]
set_max_delay -datapath_only 4.0 -from [get_regs -regexp {^u_preview_output/fifo/(wgray.[0-9]+.|wbin.9.)$}] -to [get_regs -regexp {^u_preview_output/fifo/rq_wgray1.[0-9]+.$}]
set_false_path -hold -from [get_regs -regexp {^u_preview_output/fifo/(wgray.[0-9]+.|wbin.9.)$}] -to [get_regs -regexp {^u_preview_output/fifo/rq_wgray1.[0-9]+.$}]
set_max_delay -datapath_only 4.0 -from [get_regs -regexp {^u_preview_output/fifo/(rgray.[0-9]+.|rbin.9.)$}] -to [get_regs -regexp {^u_preview_output/fifo/wq_rgray1.[0-9]+.$}]
set_false_path -hold -from [get_regs -regexp {^u_preview_output/fifo/(rgray.[0-9]+.|rbin.9.)$}] -to [get_regs -regexp {^u_preview_output/fifo/wq_rgray1.[0-9]+.$}]

# Explicit CDC budget only for the new single-bit RX heartbeats. Do not cut
# the second synchronizer stage or broad RX/system clock groups.
set_max_delay -datapath_only 4.0 -from [get_regs {u_mipi_csi_rx/u_csi_rx_phy/u_csi_rx_clock_det/heartbeat}] -to [get_regs {u_mipi_csi_rx/u_csi_rx_phy/u_csi_rx_clock_det/beat_sync[0]}]
set_false_path -hold -from [get_regs {u_mipi_csi_rx/u_csi_rx_phy/u_csi_rx_clock_det/heartbeat}] -to [get_regs {u_mipi_csi_rx/u_csi_rx_phy/u_csi_rx_clock_det/beat_sync[0]}]
set_max_delay -datapath_only 4.0 -from [get_regs {u_B_rx/u_csi_rx_phy/u_csi_rx_clock_det/heartbeat}] -to [get_regs {u_B_rx/u_csi_rx_phy/u_csi_rx_clock_det/beat_sync[0]}]
set_false_path -hold -from [get_regs {u_B_rx/u_csi_rx_phy/u_csi_rx_clock_det/heartbeat}] -to [get_regs {u_B_rx/u_csi_rx_phy/u_csi_rx_clock_det/beat_sync[0]}]
