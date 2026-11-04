set src [file normalize [file join [file dirname [info script]] ../../projects/five_view_preview]]
set out [file normalize [file join [file dirname [info script]] build]]
file mkdir $out
cd $src
if {[catch {

import_device ph1_90.db -package PH1A90SEG324 -speed 2
open_project -single_run $src/five_view_rx_cdc.al
elaborate -top system_top
read_adc $src/uisrc/04_pin/fpga_pin.adc
read_adc -ip ui_ddr $src/uisrc/03_ip/al_ip/ui_ddr/ui_ddr.adc
read_sdc -ip ui_ddr $src/uisrc/03_ip/al_ip/ui_ddr/ui_ddr.sdc
read_sdc $src/uisrc/04_pin/timing.sdc
insert_debugger
optimize_rtl
optimize_gate
legalize_phy_inst
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
