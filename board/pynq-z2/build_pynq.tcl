set root [file normalize [file join [file dirname [info script]] ../..]]
set work [file join $root build pynq_z2]
file mkdir [file join $work reports]
set fh [open [file join $root build simulation xsim.stdout.log] r]
set sim [read $fh]
close $fh
if {[string first "TEST_PASS overlay" $sim] < 0} {error "RTL simulation not passed"}
open_project [file join $work vivado pynq_original4.xpr]
set_param general.maxThreads 4
config_ip_cache -disable_cache
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {
    launch_runs synth_1 -jobs 4
    wait_on_run synth_1
}
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {error "Synthesis failed"}
launch_runs impl_1 -to_step route_design -jobs 4
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {error "Implementation failed"}
open_run impl_1
set reports [file join $work reports]
report_utilization -file [file join $reports utilization.rpt]
report_timing_summary -delay_type min_max -report_unconstrained -file [file join $reports timing_summary.rpt]
report_drc -file [file join $reports drc.rpt]
report_methodology -file [file join $reports methodology.rpt]
report_cdc -details -file [file join $reports cdc.rpt]
report_route_status -file [file join $reports route_status.rpt]
report_clocks -file [file join $reports clocks.rpt]
check_timing -verbose -file [file join $reports check_timing.rpt]
set setup [get_timing_paths -delay_type max -max_paths 1]
set hold [get_timing_paths -delay_type min -max_paths 1]
if {![llength $setup] || ![llength $hold]} {error "No constrained timing paths"}
set wns [get_property SLACK $setup]
set whs [get_property SLACK $hold]
if {$wns < 0 || $whs < 0} {error "TIMING_FAIL WNS=$wns WHS=$whs"}
if {[llength [get_drc_violations -quiet -filter {SEVERITY == Error || SEVERITY == {Critical Warning}}]]} {error "DRC error or critical warning"}
set fh [open [file join $reports gate.txt] w]
puts $fh "WNS=$wns\nWHS=$whs\npart=xc7z020clg400-1\nfrequency_mhz=100\nprogrammed_device=false"
close $fh
if {[file exists [file join $work routed.dcp]] && ![file exists [file join $work routed_before_cleanup.dcp]]} {
    file copy [file join $work routed.dcp] [file join $work routed_before_cleanup.dcp]
}
write_checkpoint -force [file join $work routed.dcp]
close_design
puts PYNQ_ORIGINAL4_ROUTE_READY_FOR_WARNING_REVIEW
close_project
exit
