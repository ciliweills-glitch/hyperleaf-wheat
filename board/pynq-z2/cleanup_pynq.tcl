# Reuse only the source package's checked logical FIFO cleanup, not its board settings.
set root [file normalize [file join [file dirname [info script]] ../..]]
set work [file join $root build pynq_z2]
file mkdir [file join $work reports]
if {[file exists [file join $work reports_before_cleanup]]} {error "Cleanup already attempted; refusing overwrite"}
file copy [file join $work reports] [file join $work reports_before_cleanup]
open_project [file join $work vivado pynq_original4.xpr]
config_ip_cache -disable_cache
set_property STEPS.OPT_DESIGN.TCL.PRE [file join $root board pynq-z2 repair_fifo_netlist.tcl] [get_runs impl_1]
# Only this task's isolated implementation run is reset; source and previous reports/checkpoint remain.
reset_runs impl_1
close_project
source [file join $root board pynq-z2 build_pynq.tcl]
