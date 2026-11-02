# Only run after independent warning review. Never programs a device.
set root [file normalize [file join [file dirname [info script]] ../..]]
set work [file join $root build pynq_z2]
file mkdir [file join $work reports]
set fh [open [file join $work reports check_timing.rpt] r]
set check [read $fh]
close $fh
foreach rule {no_clock unconstrained_internal_endpoints loops latch_loops} {
    if {![regexp [format {checking %s \(([0-9]+)\)} $rule] $check -> count] || $count != 0} {error "CHECK_TIMING_FAILED $rule"}
}
open_project [file join $work vivado pynq_original4.xpr]
config_ip_cache -disable_cache
open_run impl_1
report_drc -file [file join $work reports drc_release.rpt]
if {[llength [get_drc_violations -quiet -filter {SEVERITY == Error || SEVERITY == {Critical Warning}}]]} {error "DRC_RELEASE_FAILED"}
set fh [open [file join $work reports timing_summary.rpt] r]
set timing [read $fh]
close $fh
if {[string first "All user specified timing constraints are met." $timing] < 0} {error "TIMING_SUMMARY_FAILED"}
close_design
launch_runs impl_1 -to_step write_bitstream -jobs 2
wait_on_run impl_1
if {[get_property STATUS [get_runs impl_1]] ne "write_bitstream Complete!"} {error "BITSTREAM_FAILED"}
set delivery [file join $work deliverables]
file mkdir $delivery
set stem pynq_z2_original4_v14_raw_100mhz
set bit [file join $work vivado pynq_original4.runs impl_1 design_1_wrapper.bit]
set hwh [file join $work vivado pynq_original4.gen sources_1 bd design_1 hw_handoff design_1.hwh]
if {![file exists $bit] || ![file exists $hwh]} {error "Matched outputs missing"}
file copy $bit [file join $delivery $stem.bit]
file copy $hwh [file join $delivery $stem.hwh]
puts "PYNQ_ORIGINAL4_DEVELOPMENT_PAIR_READY $delivery; board test still required"
close_project
exit
