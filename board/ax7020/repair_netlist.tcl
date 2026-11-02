# Keep DMA FIFO architecture; use READ_FIRST on the four SDP FIFO primitives.
set fifo_rams [get_cells -hier -filter {(REF_NAME == RAMB18E1 || REF_NAME == RAMB36E1) && NAME =~ *I_DATA_FIFO*}]
foreach ram $fifo_rams {
    puts "FIFO_READ_FIRST $ram"
    set_property WRITE_MODE_A READ_FIRST $ram
    set_property WRITE_MODE_B READ_FIRST $ram
}
# FIFO Generator leaves one logically unused LUT input connected. Reduce LUT4 to LUT3 only
# when the INIT truth table proves I1 is independent for every other input value.
foreach cell [get_cells -hier -filter {NAME =~ *m00_couplers/auto_pc/*gpregsm1.user_valid_i_1 && REF_NAME =~ LUT*}] {
    set init [get_property INIT $cell]
    regexp {h([0-9A-Fa-f]+)} $init -> hex
    scan $hex %x bits
    set n [string range [get_property REF_NAME $cell] 3 end]
    for {set i 0} {$i < (1 << $n)} {incr i} {
        if {(($bits >> $i) & 1) != (($bits >> ($i ^ 2)) & 1)} {error "I1 is used: $cell"}
    }
    set wires [list]
    foreach pin {I0 I2 I3 O} {lappend wires [get_nets -of_objects [get_pins $cell/$pin]]}
    set compact 0
    for {set i 0} {$i < 8} {incr i} {
        set original [expr {($i & 1) | (($i & 6) << 1)}]
        set compact [expr {$compact | ((($bits >> $original) & 1) << $i)}]
    }
    set preserved [list]
    foreach net [get_nets -of_objects [get_pins -of_objects $cell]] {
        if {[get_property DONT_TOUCH $net] in {1 true TRUE}} {lappend preserved $net; set_property DONT_TOUCH false $net}
    }
    set_property DONT_TOUCH false $cell
    set name "[get_property NAME $cell]_compact"
    remove_cell $cell
    create_cell -reference LUT3 $name
    if {[get_property REF_NAME [get_cells $name]] ne "LUT3"} {error "LUT replacement failed: $name ref=[get_property REF_NAME [get_cells $name]]"}
    set_property INIT [format "8'h%02X" $compact] [get_cells $name]
    foreach pin {I0 I1 I2 O} wire $wires {connect_net -net $wire -objects [get_pins $name/$pin]}
    foreach net $preserved {set_property DONT_TOUCH true $net}
    puts "COMPACTED_FIFO_LUT $name OLD_INIT=$init NEW_INIT=[get_property INIT [get_cells $name]]"
}
# Remove dead FIFO logic before placement, including chains left by KEEP attrs.
set total_removed 0
set removed 1
while {$removed} {
    set removed 0
    foreach cell [get_cells -hier -filter {(REF_NAME =~ FD* || REF_NAME =~ LUT*) && NAME =~ *fifo_gen*}] {
        set outputs [get_nets -segments -of_objects [get_pins -of_objects $cell -filter {DIRECTION == OUT}]]
        set sinks [get_pins -leaf -of_objects $outputs -filter {DIRECTION == IN}]
        if {[llength $outputs] && ![llength $sinks]} {
            set preserved [list]
            foreach net [get_nets -of_objects [get_pins -of_objects $cell]] {
                if {[get_property DONT_TOUCH $net] in {1 true TRUE}} {
                    set_property DONT_TOUCH false $net
                    if {$net ni $outputs} {lappend preserved $net}
                }
            }
            foreach net $outputs {set_property DONT_TOUCH false $net; set_property KEEP false $net}
            set_property DONT_TOUCH false $cell
            set_property KEEP false $cell
            remove_cell $cell
            foreach net $preserved {set_property DONT_TOUCH true $net}
            incr removed
            incr total_removed
        }
    }
}
puts "REMOVED_DEAD_FIFO_CELLS $total_removed"
