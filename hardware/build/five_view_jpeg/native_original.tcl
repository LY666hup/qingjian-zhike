set src [file normalize [file join [file dirname [info script]] ../../projects/five_view_jpeg]]
set out [file normalize [file join [file dirname [info script]] build]]
file mkdir $out
file copy -force [file join [file dirname [info script]] stage_budget.sdc] $out/stage_budget.sdc
cd $src
if {[catch {

import_device ph1_90.db -package PH1A90SEG324 -speed 2
open_project -single_run $src/five_view_color_jpeg.al
elaborate -top system_top
read_adc $src/uisrc/04_pin/fpga_pin.adc
read_adc -ip ui_ddr $src/uisrc/03_ip/al_ip/ui_ddr/ui_ddr.adc
read_sdc -ip ui_ddr $src/uisrc/03_ip/al_ip/ui_ddr/ui_ddr.sdc
read_sdc $src/uisrc/04_pin/timing.sdc

set guard_regs [get_regs -hier {*byte_release*}]
set guard_count 0
foreach rr $guard_regs {if {[regexp {(^|/)byte_release\[[0-2]\]$} $rr]} {incr guard_count}}
puts "NATIVE_RX_GUARD_REG_COUNT=$guard_count"
if {$guard_count!=24} {error "Expected4 lanes x3 release stages x2 cameras"}
if {[llength [get_regs -hier {*r_reset_lat*}]]!=0} {error "Old RX pad-reset register still present"}
if {[llength [get_regs -hier {*S_clk_hs_data_oddr*}]]!=0} {error "Interrupt-mode TX clock-data register still present"}
puts NATIVE_PHY_SELECTION_GUARD_PASS
# A and B use the same stream-preserving raw and AWB latency paths.
set delay_count 0
foreach instance {
 X_ui_isp_top/X_ui_awb/X_ui_signal_delay
 u_B_isp/X_ui_awb/X_ui_signal_delay
 X_ui_isp_top/X_ui_top_demosaic/X_ui_raw_matrix_3x3_buffer/X_ui_zhenghe/stream_delay
 u_B_isp/X_ui_top_demosaic/X_ui_raw_matrix_3x3_buffer/X_ui_zhenghe/stream_delay
} {
 set exact_count 0
 foreach rr [get_regs -hier [format {%s/*valid_pipe*} $instance]] {
  if {[regexp {(^|[/$])valid_pipe\[[0-9]+\]$} $rr]} {incr exact_count}
 }
 puts "NATIVE_STREAM_DELAY=$instance,BITS=$exact_count"
 if {$exact_count!=50} {error "Stream-preserving latency absent or wrong size"}
 incr delay_count $exact_count
}
puts "NATIVE_STREAM_DELAY_TOTAL_BITS=$delay_count"
set ac_presence_count 0
set lowspan_total 0
set tail_count 0
set held_count 0
foreach rr [get_regs -hier {u_five_preview_ddr/service/compressor/encoder/pool/*}] {
 if {[regexp {(^|[/\$])block_has_ac$} $rr]} {incr ac_presence_count}
 if {[regexp {(^|[/\$])span_(low|high|sum)\[[0-9]+\]$} $rr]} {incr lowspan_total}
 if {[regexp {(^|[/\$])last_ac_index\[[0-9]+\]$} $rr]} {incr tail_count}
 if {[string match {*pixel_held*} $rr]} {incr held_count}
}
if {$ac_presence_count!=6 || $lowspan_total!=192 || $tail_count!=36 || $held_count!=192} {
 error "Parallel codec or explicit sample pipeline absent/wrong shape:ac=$ac_presence_count,span=$lowspan_total,tail=$tail_count,held=$held_count"
}
puts "NATIVE_DC_ONLY_FAST_PATH_REGS=$ac_presence_count"
puts "NATIVE_LOWSPAN_TRACKER_BITS=$lowspan_total"
puts "NATIVE_AC_TAIL_INDEX_BITS=$tail_count"
puts "NATIVE_PARALLEL_SAMPLE_HELD_BITS=$held_count"
set gapregs [get_regs -hier {u_five_preview_ddr/service/memory_service/empty_wait*}]
set gap_count 0
foreach rr $gapregs {if {[regexp {(^|/)empty_wait\[[0-9]+\]$} $rr]} {incr gap_count}}
if {$gap_count!=24} {error "Expected24 enabled source-gap counter bits in JPEG memory service"}
puts "NATIVE_SOURCE_GAP_BITS=$gap_count"

set bpre [get_regs -hier {u_B_views/*}]
if {[llength $bpre]!=0} {error "Unexpected B processed branches in five-source profile"}
puts "NATIVE_B_PREPROC_REGS=[llength $bpre]"
puts NATIVE_ACTIVE_SOURCE_MASK=31

# Reject absent or unexpectedly sized CDC bindings before long optimization.
foreach signal {wr_ptr_gray wr_ptr_gray_rdsync0 rd_ptr_gray rd_ptr_gray_wrsync0} {
 set names [list]
 for {set bit 0} {$bit<12} {incr bit} {lappend names [format {u_csi2_pack/u_async_fifo/%s[%d]} $signal $bit]}
 set regs [get_regs $names]
 puts "EXACT_CDC_BINDING_$signal=$regs"
 if {[llength $regs]!=12} {error "Expected 12 exact CDC registers for $signal"}
}
puts EXACT_CDC_BINDING_DONE


# Exact-instance lookups avoid TD's module-local leaf names at elaboration.
set credit_count 0
foreach capture {u_capture_A_original u_capture_A_gray u_capture_A_gauss u_capture_A_hist u_capture_B_original} {
 for {set b 0} {$b<5} {incr b} {
  set n [format {%s/queue/m_buffered[%d]} $capture $b]
  if {[llength [get_regs $n]]!=1} {error "Registered source-credit bit absent:$n"}
  incr credit_count
 }
}
if {$credit_count!=25} {error "Expected5x5 registered source credits"}
puts "NATIVE_FIFO_REGISTERED_CREDIT_BITS=$credit_count"
set count_bits 0
foreach rr [get_regs -hier {u_five_preview_ddr/*burst_words_done*}] {
 if {[regexp {(^|/)burst_words_done\[[0-9]+\]$} $rr]} {incr count_bits}
}
if {$count_bits!=15} {error "Expected15 bounded burst-word counter bits:$count_bits"}
puts "NATIVE_BOUNDED_BURST_COUNT_BITS=$count_bits"


set ingress_count_bits 0
for {set b 0} {$b<2} {incr b} {
 set n [format {u_five_preview_ddr/service/ingress/count[%d]} $b]
 if {[llength [get_regs $n]]!=1} {error "JPEG ingress occupancy absent:$n"}
 incr ingress_count_bits
}
puts "NATIVE_REGISTERED_JPEG_INGRESS_COUNT_BITS=$ingress_count_bits"


foreach leaf {lookup_valid sum_valid} {
 set n [format {u_five_preview_ddr/service/compressor/encoder/raster/%s} $leaf]
 if {[llength [get_regs $n]]!=1} {error "JPEG range pipeline register missing:$n"}
}
puts NATIVE_JPEG_RANGE_PIPELINE_VALID_BITS=2

insert_debugger
optimize_rtl
optimize_gate
legalize_phy_inst
set sync_names {u_capture_A_original/queue/rq_wgray1[0] u_capture_A_original/queue/rq_wgray2[0] u_capture_A_original/queue/rq_wgray1[1] u_capture_A_original/queue/rq_wgray2[1] u_capture_A_original/queue/rq_wgray1[2] u_capture_A_original/queue/rq_wgray2[2] u_capture_A_original/queue/rq_wgray1[3] u_capture_A_original/queue/rq_wgray2[3] u_capture_A_original/queue/rq_wgray1[4] u_capture_A_original/queue/rq_wgray2[4] u_capture_A_original/queue/rq_wgray1[5] u_capture_A_original/queue/rq_wgray2[5] u_capture_A_original/queue/rq_wgray1[6] u_capture_A_original/queue/rq_wgray2[6] u_capture_A_original/queue/rq_wgray1[7] u_capture_A_original/queue/rq_wgray2[7] u_capture_A_original/queue/rq_wgray1[8] u_capture_A_original/queue/rq_wgray2[8] u_capture_A_original/queue/rq_wgray1[9] u_capture_A_original/queue/rq_wgray2[9] u_capture_A_original/queue/wq_rgray1[0] u_capture_A_original/queue/wq_rgray2[0] u_capture_A_original/queue/wq_rgray1[1] u_capture_A_original/queue/wq_rgray2[1] u_capture_A_original/queue/wq_rgray1[2] u_capture_A_original/queue/wq_rgray2[2] u_capture_A_original/queue/wq_rgray1[3] u_capture_A_original/queue/wq_rgray2[3] u_capture_A_original/queue/wq_rgray1[4] u_capture_A_original/queue/wq_rgray2[4] u_capture_A_original/queue/wq_rgray1[5] u_capture_A_original/queue/wq_rgray2[5] u_capture_A_original/queue/wq_rgray1[6] u_capture_A_original/queue/wq_rgray2[6] u_capture_A_original/queue/wq_rgray1[7] u_capture_A_original/queue/wq_rgray2[7] u_capture_A_original/queue/wq_rgray1[8] u_capture_A_original/queue/wq_rgray2[8] u_capture_A_original/queue/wq_rgray1[9] u_capture_A_original/queue/wq_rgray2[9] u_capture_A_gray/queue/rq_wgray1[0] u_capture_A_gray/queue/rq_wgray2[0] u_capture_A_gray/queue/rq_wgray1[1] u_capture_A_gray/queue/rq_wgray2[1] u_capture_A_gray/queue/rq_wgray1[2] u_capture_A_gray/queue/rq_wgray2[2] u_capture_A_gray/queue/rq_wgray1[3] u_capture_A_gray/queue/rq_wgray2[3] u_capture_A_gray/queue/rq_wgray1[4] u_capture_A_gray/queue/rq_wgray2[4] u_capture_A_gray/queue/rq_wgray1[5] u_capture_A_gray/queue/rq_wgray2[5] u_capture_A_gray/queue/rq_wgray1[6] u_capture_A_gray/queue/rq_wgray2[6] u_capture_A_gray/queue/rq_wgray1[7] u_capture_A_gray/queue/rq_wgray2[7] u_capture_A_gray/queue/rq_wgray1[8] u_capture_A_gray/queue/rq_wgray2[8] u_capture_A_gray/queue/rq_wgray1[9] u_capture_A_gray/queue/rq_wgray2[9] u_capture_A_gray/queue/wq_rgray1[0] u_capture_A_gray/queue/wq_rgray2[0] u_capture_A_gray/queue/wq_rgray1[1] u_capture_A_gray/queue/wq_rgray2[1] u_capture_A_gray/queue/wq_rgray1[2] u_capture_A_gray/queue/wq_rgray2[2] u_capture_A_gray/queue/wq_rgray1[3] u_capture_A_gray/queue/wq_rgray2[3] u_capture_A_gray/queue/wq_rgray1[4] u_capture_A_gray/queue/wq_rgray2[4] u_capture_A_gray/queue/wq_rgray1[5] u_capture_A_gray/queue/wq_rgray2[5] u_capture_A_gray/queue/wq_rgray1[6] u_capture_A_gray/queue/wq_rgray2[6] u_capture_A_gray/queue/wq_rgray1[7] u_capture_A_gray/queue/wq_rgray2[7] u_capture_A_gray/queue/wq_rgray1[8] u_capture_A_gray/queue/wq_rgray2[8] u_capture_A_gray/queue/wq_rgray1[9] u_capture_A_gray/queue/wq_rgray2[9] u_capture_A_gauss/queue/rq_wgray1[0] u_capture_A_gauss/queue/rq_wgray2[0] u_capture_A_gauss/queue/rq_wgray1[1] u_capture_A_gauss/queue/rq_wgray2[1] u_capture_A_gauss/queue/rq_wgray1[2] u_capture_A_gauss/queue/rq_wgray2[2] u_capture_A_gauss/queue/rq_wgray1[3] u_capture_A_gauss/queue/rq_wgray2[3] u_capture_A_gauss/queue/rq_wgray1[4] u_capture_A_gauss/queue/rq_wgray2[4] u_capture_A_gauss/queue/rq_wgray1[5] u_capture_A_gauss/queue/rq_wgray2[5] u_capture_A_gauss/queue/rq_wgray1[6] u_capture_A_gauss/queue/rq_wgray2[6] u_capture_A_gauss/queue/rq_wgray1[7] u_capture_A_gauss/queue/rq_wgray2[7] u_capture_A_gauss/queue/rq_wgray1[8] u_capture_A_gauss/queue/rq_wgray2[8] u_capture_A_gauss/queue/rq_wgray1[9] u_capture_A_gauss/queue/rq_wgray2[9] u_capture_A_gauss/queue/wq_rgray1[0] u_capture_A_gauss/queue/wq_rgray2[0] u_capture_A_gauss/queue/wq_rgray1[1] u_capture_A_gauss/queue/wq_rgray2[1] u_capture_A_gauss/queue/wq_rgray1[2] u_capture_A_gauss/queue/wq_rgray2[2] u_capture_A_gauss/queue/wq_rgray1[3] u_capture_A_gauss/queue/wq_rgray2[3] u_capture_A_gauss/queue/wq_rgray1[4] u_capture_A_gauss/queue/wq_rgray2[4] u_capture_A_gauss/queue/wq_rgray1[5] u_capture_A_gauss/queue/wq_rgray2[5] u_capture_A_gauss/queue/wq_rgray1[6] u_capture_A_gauss/queue/wq_rgray2[6] u_capture_A_gauss/queue/wq_rgray1[7] u_capture_A_gauss/queue/wq_rgray2[7] u_capture_A_gauss/queue/wq_rgray1[8] u_capture_A_gauss/queue/wq_rgray2[8] u_capture_A_gauss/queue/wq_rgray1[9] u_capture_A_gauss/queue/wq_rgray2[9] u_capture_A_hist/queue/rq_wgray1[0] u_capture_A_hist/queue/rq_wgray2[0] u_capture_A_hist/queue/rq_wgray1[1] u_capture_A_hist/queue/rq_wgray2[1] u_capture_A_hist/queue/rq_wgray1[2] u_capture_A_hist/queue/rq_wgray2[2] u_capture_A_hist/queue/rq_wgray1[3] u_capture_A_hist/queue/rq_wgray2[3] u_capture_A_hist/queue/rq_wgray1[4] u_capture_A_hist/queue/rq_wgray2[4] u_capture_A_hist/queue/rq_wgray1[5] u_capture_A_hist/queue/rq_wgray2[5] u_capture_A_hist/queue/rq_wgray1[6] u_capture_A_hist/queue/rq_wgray2[6] u_capture_A_hist/queue/rq_wgray1[7] u_capture_A_hist/queue/rq_wgray2[7] u_capture_A_hist/queue/rq_wgray1[8] u_capture_A_hist/queue/rq_wgray2[8] u_capture_A_hist/queue/rq_wgray1[9] u_capture_A_hist/queue/rq_wgray2[9] u_capture_A_hist/queue/wq_rgray1[0] u_capture_A_hist/queue/wq_rgray2[0] u_capture_A_hist/queue/wq_rgray1[1] u_capture_A_hist/queue/wq_rgray2[1] u_capture_A_hist/queue/wq_rgray1[2] u_capture_A_hist/queue/wq_rgray2[2] u_capture_A_hist/queue/wq_rgray1[3] u_capture_A_hist/queue/wq_rgray2[3] u_capture_A_hist/queue/wq_rgray1[4] u_capture_A_hist/queue/wq_rgray2[4] u_capture_A_hist/queue/wq_rgray1[5] u_capture_A_hist/queue/wq_rgray2[5] u_capture_A_hist/queue/wq_rgray1[6] u_capture_A_hist/queue/wq_rgray2[6] u_capture_A_hist/queue/wq_rgray1[7] u_capture_A_hist/queue/wq_rgray2[7] u_capture_A_hist/queue/wq_rgray1[8] u_capture_A_hist/queue/wq_rgray2[8] u_capture_A_hist/queue/wq_rgray1[9] u_capture_A_hist/queue/wq_rgray2[9] u_capture_B_original/queue/rq_wgray1[0] u_capture_B_original/queue/rq_wgray2[0] u_capture_B_original/queue/rq_wgray1[1] u_capture_B_original/queue/rq_wgray2[1] u_capture_B_original/queue/rq_wgray1[2] u_capture_B_original/queue/rq_wgray2[2] u_capture_B_original/queue/rq_wgray1[3] u_capture_B_original/queue/rq_wgray2[3] u_capture_B_original/queue/rq_wgray1[4] u_capture_B_original/queue/rq_wgray2[4] u_capture_B_original/queue/rq_wgray1[5] u_capture_B_original/queue/rq_wgray2[5] u_capture_B_original/queue/rq_wgray1[6] u_capture_B_original/queue/rq_wgray2[6] u_capture_B_original/queue/rq_wgray1[7] u_capture_B_original/queue/rq_wgray2[7] u_capture_B_original/queue/rq_wgray1[8] u_capture_B_original/queue/rq_wgray2[8] u_capture_B_original/queue/rq_wgray1[9] u_capture_B_original/queue/rq_wgray2[9] u_capture_B_original/queue/wq_rgray1[0] u_capture_B_original/queue/wq_rgray2[0] u_capture_B_original/queue/wq_rgray1[1] u_capture_B_original/queue/wq_rgray2[1] u_capture_B_original/queue/wq_rgray1[2] u_capture_B_original/queue/wq_rgray2[2] u_capture_B_original/queue/wq_rgray1[3] u_capture_B_original/queue/wq_rgray2[3] u_capture_B_original/queue/wq_rgray1[4] u_capture_B_original/queue/wq_rgray2[4] u_capture_B_original/queue/wq_rgray1[5] u_capture_B_original/queue/wq_rgray2[5] u_capture_B_original/queue/wq_rgray1[6] u_capture_B_original/queue/wq_rgray2[6] u_capture_B_original/queue/wq_rgray1[7] u_capture_B_original/queue/wq_rgray2[7] u_capture_B_original/queue/wq_rgray1[8] u_capture_B_original/queue/wq_rgray2[8] u_capture_B_original/queue/wq_rgray1[9] u_capture_B_original/queue/wq_rgray2[9] u_preview_output/fifo/rq_wgray1[0] u_preview_output/fifo/rq_wgray2[0] u_preview_output/fifo/rq_wgray1[1] u_preview_output/fifo/rq_wgray2[1] u_preview_output/fifo/rq_wgray1[2] u_preview_output/fifo/rq_wgray2[2] u_preview_output/fifo/rq_wgray1[3] u_preview_output/fifo/rq_wgray2[3] u_preview_output/fifo/rq_wgray1[4] u_preview_output/fifo/rq_wgray2[4] u_preview_output/fifo/rq_wgray1[5] u_preview_output/fifo/rq_wgray2[5] u_preview_output/fifo/rq_wgray1[6] u_preview_output/fifo/rq_wgray2[6] u_preview_output/fifo/rq_wgray1[7] u_preview_output/fifo/rq_wgray2[7] u_preview_output/fifo/rq_wgray1[8] u_preview_output/fifo/rq_wgray2[8] u_preview_output/fifo/rq_wgray1[9] u_preview_output/fifo/rq_wgray2[9] u_preview_output/fifo/wq_rgray1[0] u_preview_output/fifo/wq_rgray2[0] u_preview_output/fifo/wq_rgray1[1] u_preview_output/fifo/wq_rgray2[1] u_preview_output/fifo/wq_rgray1[2] u_preview_output/fifo/wq_rgray2[2] u_preview_output/fifo/wq_rgray1[3] u_preview_output/fifo/wq_rgray2[3] u_preview_output/fifo/wq_rgray1[4] u_preview_output/fifo/wq_rgray2[4] u_preview_output/fifo/wq_rgray1[5] u_preview_output/fifo/wq_rgray2[5] u_preview_output/fifo/wq_rgray1[6] u_preview_output/fifo/wq_rgray2[6] u_preview_output/fifo/wq_rgray1[7] u_preview_output/fifo/wq_rgray2[7] u_preview_output/fifo/wq_rgray1[8] u_preview_output/fifo/wq_rgray2[8] u_preview_output/fifo/wq_rgray1[9] u_preview_output/fifo/wq_rgray2[9] u_csi2_pack/u_async_fifo/wr_ptr_gray_rdsync0[0] u_csi2_pack/u_async_fifo/wr_ptr_gray_rdsync[0] u_csi2_pack/u_async_fifo/wr_ptr_gray_rdsync0[1] u_csi2_pack/u_async_fifo/wr_ptr_gray_rdsync[1] u_csi2_pack/u_async_fifo/wr_ptr_gray_rdsync0[2] u_csi2_pack/u_async_fifo/wr_ptr_gray_rdsync[2] u_csi2_pack/u_async_fifo/wr_ptr_gray_rdsync0[3] u_csi2_pack/u_async_fifo/wr_ptr_gray_rdsync[3] u_csi2_pack/u_async_fifo/wr_ptr_gray_rdsync0[4] u_csi2_pack/u_async_fifo/wr_ptr_gray_rdsync[4] u_csi2_pack/u_async_fifo/wr_ptr_gray_rdsync0[5] u_csi2_pack/u_async_fifo/wr_ptr_gray_rdsync[5] u_csi2_pack/u_async_fifo/wr_ptr_gray_rdsync0[6] u_csi2_pack/u_async_fifo/wr_ptr_gray_rdsync[6] u_csi2_pack/u_async_fifo/wr_ptr_gray_rdsync0[7] u_csi2_pack/u_async_fifo/wr_ptr_gray_rdsync[7] u_csi2_pack/u_async_fifo/wr_ptr_gray_rdsync0[8] u_csi2_pack/u_async_fifo/wr_ptr_gray_rdsync[8] u_csi2_pack/u_async_fifo/wr_ptr_gray_rdsync0[9] u_csi2_pack/u_async_fifo/wr_ptr_gray_rdsync[9] u_csi2_pack/u_async_fifo/wr_ptr_gray_rdsync0[10] u_csi2_pack/u_async_fifo/wr_ptr_gray_rdsync[10] u_csi2_pack/u_async_fifo/wr_ptr_gray_rdsync0[11] u_csi2_pack/u_async_fifo/wr_ptr_gray_rdsync[11] u_csi2_pack/u_async_fifo/rd_ptr_gray_wrsync0[0] u_csi2_pack/u_async_fifo/rd_ptr_gray_wrsync[0] u_csi2_pack/u_async_fifo/rd_ptr_gray_wrsync0[1] u_csi2_pack/u_async_fifo/rd_ptr_gray_wrsync[1] u_csi2_pack/u_async_fifo/rd_ptr_gray_wrsync0[2] u_csi2_pack/u_async_fifo/rd_ptr_gray_wrsync[2] u_csi2_pack/u_async_fifo/rd_ptr_gray_wrsync0[3] u_csi2_pack/u_async_fifo/rd_ptr_gray_wrsync[3] u_csi2_pack/u_async_fifo/rd_ptr_gray_wrsync0[4] u_csi2_pack/u_async_fifo/rd_ptr_gray_wrsync[4] u_csi2_pack/u_async_fifo/rd_ptr_gray_wrsync0[5] u_csi2_pack/u_async_fifo/rd_ptr_gray_wrsync[5] u_csi2_pack/u_async_fifo/rd_ptr_gray_wrsync0[6] u_csi2_pack/u_async_fifo/rd_ptr_gray_wrsync[6] u_csi2_pack/u_async_fifo/rd_ptr_gray_wrsync0[7] u_csi2_pack/u_async_fifo/rd_ptr_gray_wrsync[7] u_csi2_pack/u_async_fifo/rd_ptr_gray_wrsync0[8] u_csi2_pack/u_async_fifo/rd_ptr_gray_wrsync[8] u_csi2_pack/u_async_fifo/rd_ptr_gray_wrsync0[9] u_csi2_pack/u_async_fifo/rd_ptr_gray_wrsync[9] u_csi2_pack/u_async_fifo/rd_ptr_gray_wrsync0[10] u_csi2_pack/u_async_fifo/rd_ptr_gray_wrsync[10] u_csi2_pack/u_async_fifo/rd_ptr_gray_wrsync0[11] u_csi2_pack/u_async_fifo/rd_ptr_gray_wrsync[11] u_csi2_pack/u_out_fifo/wr_ptr_gray_rdsync0[0] u_csi2_pack/u_out_fifo/wr_ptr_gray_rdsync[0] u_csi2_pack/u_out_fifo/wr_ptr_gray_rdsync0[1] u_csi2_pack/u_out_fifo/wr_ptr_gray_rdsync[1] u_csi2_pack/u_out_fifo/wr_ptr_gray_rdsync0[2] u_csi2_pack/u_out_fifo/wr_ptr_gray_rdsync[2] u_csi2_pack/u_out_fifo/wr_ptr_gray_rdsync0[3] u_csi2_pack/u_out_fifo/wr_ptr_gray_rdsync[3] u_csi2_pack/u_out_fifo/wr_ptr_gray_rdsync0[4] u_csi2_pack/u_out_fifo/wr_ptr_gray_rdsync[4] u_csi2_pack/u_out_fifo/wr_ptr_gray_rdsync0[5] u_csi2_pack/u_out_fifo/wr_ptr_gray_rdsync[5] u_csi2_pack/u_out_fifo/wr_ptr_gray_rdsync0[6] u_csi2_pack/u_out_fifo/wr_ptr_gray_rdsync[6] u_csi2_pack/u_out_fifo/wr_ptr_gray_rdsync0[7] u_csi2_pack/u_out_fifo/wr_ptr_gray_rdsync[7] u_csi2_pack/u_out_fifo/wr_ptr_gray_rdsync0[8] u_csi2_pack/u_out_fifo/wr_ptr_gray_rdsync[8] u_csi2_pack/u_out_fifo/wr_ptr_gray_rdsync0[9] u_csi2_pack/u_out_fifo/wr_ptr_gray_rdsync[9] u_csi2_pack/u_out_fifo/wr_ptr_gray_rdsync0[10] u_csi2_pack/u_out_fifo/wr_ptr_gray_rdsync[10] u_csi2_pack/u_out_fifo/wr_ptr_gray_rdsync0[11] u_csi2_pack/u_out_fifo/wr_ptr_gray_rdsync[11] u_csi2_pack/u_out_fifo/rd_ptr_gray_wrsync0[0] u_csi2_pack/u_out_fifo/rd_ptr_gray_wrsync[0] u_csi2_pack/u_out_fifo/rd_ptr_gray_wrsync0[1] u_csi2_pack/u_out_fifo/rd_ptr_gray_wrsync[1] u_csi2_pack/u_out_fifo/rd_ptr_gray_wrsync0[2] u_csi2_pack/u_out_fifo/rd_ptr_gray_wrsync[2] u_csi2_pack/u_out_fifo/rd_ptr_gray_wrsync0[3] u_csi2_pack/u_out_fifo/rd_ptr_gray_wrsync[3] u_csi2_pack/u_out_fifo/rd_ptr_gray_wrsync0[4] u_csi2_pack/u_out_fifo/rd_ptr_gray_wrsync[4] u_csi2_pack/u_out_fifo/rd_ptr_gray_wrsync0[5] u_csi2_pack/u_out_fifo/rd_ptr_gray_wrsync[5] u_csi2_pack/u_out_fifo/rd_ptr_gray_wrsync0[6] u_csi2_pack/u_out_fifo/rd_ptr_gray_wrsync[6] u_csi2_pack/u_out_fifo/rd_ptr_gray_wrsync0[7] u_csi2_pack/u_out_fifo/rd_ptr_gray_wrsync[7] u_csi2_pack/u_out_fifo/rd_ptr_gray_wrsync0[8] u_csi2_pack/u_out_fifo/rd_ptr_gray_wrsync[8] u_csi2_pack/u_out_fifo/rd_ptr_gray_wrsync0[9] u_csi2_pack/u_out_fifo/rd_ptr_gray_wrsync[9] u_csi2_pack/u_out_fifo/rd_ptr_gray_wrsync0[10] u_csi2_pack/u_out_fifo/rd_ptr_gray_wrsync[10] u_csi2_pack/u_out_fifo/rd_ptr_gray_wrsync0[11] u_csi2_pack/u_out_fifo/rd_ptr_gray_wrsync[11]}
set sync_names [concat $sync_names {                                                                                                                       }]
foreach name $sync_names {
 set reg [get_regs $name]
 if {[llength $reg]!=1} {error "Missing/aliased FIFO synchronizer net $name"}
 set_property dont_touch true $reg
 if {[get_property -quiet dont_touch $reg]!=1} {error "Preserve property absent on $name"}
}
puts "FIFO_PRESERVED_NETS=[llength $sync_names]"

read_sdc $out/stage_budget.sdc
export_db $out/gate.db
report_area -file $out/area_synth.rpt
update_timing -mode basic
report_timing_summary -file $out/timing_synth.rpt -ep_num 20
check_timing -verbose -file $out/health_synth.rpt
puts "AUTOBUILD_SYNTH_DONE"
insert_debugger
place
export_db $out/placed.db
report_area -io_info -file $out/area_placed.rpt
puts "AUTOBUILD_PLACE_DONE"
route
export_db $out/route.db
update_timing -mode final
report_timing_status -file $out/status_route.rpt
report_timing_summary -file $out/timing_route.rpt -ep_num 30
report_area -io_info -file $out/area_route.rpt
check_timing -verbose -file $out/health_route.rpt
puts "AUTOBUILD_ROUTE_DONE"

} err]} {puts "AUTOBUILD_FAILED: $err"; exit 2}
close_project
exit
