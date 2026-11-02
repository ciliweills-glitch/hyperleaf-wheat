# PYNQ-Z2 entrypoint, Vivado 2024.1. Creates files; never programs a board.
set root [file dirname [file normalize [info script]]]
set phase [lindex $argv 0]
if {$phase eq "" || $phase eq "bd"} {set phase create}
if {$phase ni {create build cleanup release}} {error "Use create, build, cleanup or release"}
source [file join $root board pynq-z2 ${phase}_pynq.tcl]
