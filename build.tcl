# Vivado 2024.1. Copy the expanded project to an English path before building.
# vivado -mode batch -source build.tcl -tclargs bd
# vivado -mode batch -source build.tcl
# Optional second argument: directory for the generated Vivado project.
set root [file dirname [file normalize [info script]]]
set mode [lindex $argv 0]
set workspace [file join $root build ax7020]
if {[llength $argv] > 1} {set workspace [file normalize [lindex $argv 1]]}
set reports [file join $workspace reports]
file mkdir $reports
set_param general.maxThreads 4
create_project original4 $workspace -part xc7z020clg400-2 -force
set_property ip_repo_paths [file join $root ip] [current_project]
update_ip_catalog
source [file join $root board ax7020 ax7020_cnn_lcd_bd.tcl]
add_files -fileset constrs_1 [list [file join $root board ax7020 system.xdc] [file join $root board ax7020 lcd7.xdc]]
validate_bd_design
save_bd_design
add_files -fileset utils_1 [file join $root board ax7020 repair_netlist.tcl]
set_property STEPS.OPT_DESIGN.TCL.PRE [file join $root board ax7020 repair_netlist.tcl] [get_runs impl_1]
set f [open [file join $reports board_configuration.txt] w]
puts $f "part=[get_property PART [current_project]]"
foreach item {processing_system7_0 axi_dma_0 axi_interconnect_0} {
    puts $f "cell=$item"
    report_property [get_bd_cells $item] -file [file join $reports ${item}.txt]
}
puts $f "bd=[get_files cnn.bd]"
close $f
puts ORIGINAL4_LCD_BD_RECREATED
if {$mode eq "bd"} {close_project; return}
generate_target all [get_files cnn.bd]
set wrapper [make_wrapper -files [get_files cnn.bd] -top]
add_files -norecurse $wrapper
set_property top cnn_wrapper [current_fileset]
update_compile_order -fileset sources_1
launch_runs synth_1 -jobs 4
wait_on_run synth_1
open_run synth_1
report_utilization -file [file join $reports utilization_synth.rpt]
close_design
launch_runs impl_1 -to_step write_bitstream -jobs 4
wait_on_run impl_1
open_run impl_1
report_timing_summary -delay_type min_max -report_unconstrained -file [file join $reports timing_summary.rpt]
report_utilization -file [file join $reports utilization.rpt]
report_drc -file [file join $reports drc.rpt]
report_methodology -file [file join $reports methodology.rpt]
report_cdc -details -file [file join $reports cdc.rpt]
report_route_status -file [file join $reports route_status.rpt]
set release [file join $workspace release]
file mkdir $release
write_hw_platform -fixed -include_bit -force -file [file join $release ax7020_cnn_lcd.xsa]
file copy -force [file join $workspace original4.runs impl_1 cnn_wrapper.bit] [file join $release ax7020_cnn.bit]
file copy -force [file join $workspace original4.gen sources_1 bd cnn hw_handoff cnn.hwh] [file join $release ax7020_cnn.hwh]
puts "ORIGINAL4_LCD_BIT_HWH_BUILT $release"
close_project
