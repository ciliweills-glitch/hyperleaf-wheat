set root [file normalize [file join [file dirname [info script]] ../..]]
set work [file join $root build pynq_z2]
file mkdir [file join $work reports]
set source $root
set project_dir [file join $work vivado]
if {[file exists $project_dir]} {error "Refusing overwrite"}
if {[version -short] ne "2024.1"} {error "Vivado 2024.1 required"}
set_param general.maxThreads 4
set_param board.repoPaths [list [file join $root board pynq-z2 board_files]]
create_project pynq_original4 $project_dir -part xc7z020clg400-1
set_property board_part tul.com.tw:pynq-z2:part0:1.0 [current_project]
set_property target_language Verilog [current_project]
set_property ip_repo_paths [list [file join $source ip cnn_axis_accel]] [current_project]
update_ip_catalog
create_bd_design design_1
set ps [create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7:5.5 processing_system7_0]
apply_bd_automation -rule xilinx.com:bd_rule:processing_system7 -config {make_external "FIXED_IO, DDR" apply_board_preset "1" Master "Disable" Slave "Disable"} $ps
set_property -dict [list CONFIG.PCW_USE_M_AXI_GP0 1 CONFIG.PCW_USE_S_AXI_HP0 1 CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ 100 CONFIG.PCW_EN_CLK0_PORT 1 CONFIG.PCW_EN_RST0_PORT 1] $ps
report_property $ps -file [file join $work reports ps_configuration.rpt]
foreach {key expected} {PCW_CRYSTAL_PERIPHERAL_FREQMHZ 50 PCW_DDR_RAM_HIGHADDR 0x1FFFFFFF PCW_UIPARAM_DDR_BUS_WIDTH {16 Bit}} {
    set actual [get_property CONFIG.$key $ps]
    if {[string is double -strict $expected] && [string is double -strict $actual]} {
        if {abs(double($actual)-double($expected)) > 0.0001} {error "PS mismatch $key=$actual expected $expected"}
    } elseif {$actual ne $expected} {error "PS mismatch $key=$actual expected $expected"}
}
set dma [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_dma:7.1 axi_dma_0]
set_property -dict [list CONFIG.c_include_sg 0 CONFIG.c_sg_length_width 23 CONFIG.c_m_axi_mm2s_data_width 32 CONFIG.c_m_axi_s2mm_data_width 32 CONFIG.c_m_axis_mm2s_tdata_width 32 CONFIG.c_s_axis_s2mm_tdata_width 32] $dma
create_bd_cell -type ip -vlnv hyperleaf.org:user:cnn_axis_accel:1.0 cnn_axis_accel_0
set reset [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 proc_sys_reset_0]
# C_EXT_RESET_HIGH is read-only; infer active-low polarity from FCLK_RESET0_N.
set one [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 reset_clock_locked]
set_property CONFIG.CONST_VAL 1 $one
connect_bd_net [get_bd_pins reset_clock_locked/dout] [get_bd_pins proc_sys_reset_0/dcm_locked]
set ctrl [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 ps7_0_axi_periph]
set_property CONFIG.NUM_MI 2 $ctrl
set mem [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 axi_mem_intercon]
set_property -dict [list CONFIG.NUM_SI 2 CONFIG.NUM_MI 1] $mem
foreach {a b} {
 processing_system7_0/M_AXI_GP0 ps7_0_axi_periph/S00_AXI
 ps7_0_axi_periph/M00_AXI axi_dma_0/S_AXI_LITE
 ps7_0_axi_periph/M01_AXI cnn_axis_accel_0/s_axi
 axi_dma_0/M_AXI_MM2S axi_mem_intercon/S00_AXI
 axi_dma_0/M_AXI_S2MM axi_mem_intercon/S01_AXI
 axi_mem_intercon/M00_AXI processing_system7_0/S_AXI_HP0
 axi_dma_0/M_AXIS_MM2S cnn_axis_accel_0/s_axis
 cnn_axis_accel_0/m_axis axi_dma_0/S_AXIS_S2MM
} {connect_bd_intf_net [get_bd_intf_pins $a] [get_bd_intf_pins $b]}
set clock_pins {processing_system7_0/FCLK_CLK0 processing_system7_0/M_AXI_GP0_ACLK processing_system7_0/S_AXI_HP0_ACLK proc_sys_reset_0/slowest_sync_clk axi_dma_0/s_axi_lite_aclk axi_dma_0/m_axi_mm2s_aclk axi_dma_0/m_axi_s2mm_aclk cnn_axis_accel_0/aclk ps7_0_axi_periph/ACLK ps7_0_axi_periph/S00_ACLK ps7_0_axi_periph/M00_ACLK ps7_0_axi_periph/M01_ACLK axi_mem_intercon/ACLK axi_mem_intercon/S00_ACLK axi_mem_intercon/S01_ACLK axi_mem_intercon/M00_ACLK}
connect_bd_net {*}[lmap pin $clock_pins {get_bd_pins $pin}]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_RESET0_N] [get_bd_pins proc_sys_reset_0/ext_reset_in]
set reset_pins {proc_sys_reset_0/peripheral_aresetn cnn_axis_accel_0/aresetn axi_dma_0/axi_resetn ps7_0_axi_periph/ARESETN ps7_0_axi_periph/S00_ARESETN ps7_0_axi_periph/M00_ARESETN ps7_0_axi_periph/M01_ARESETN axi_mem_intercon/ARESETN axi_mem_intercon/S00_ARESETN axi_mem_intercon/S01_ARESETN axi_mem_intercon/M00_ARESETN}
connect_bd_net {*}[lmap pin $reset_pins {get_bd_pins $pin}]
assign_bd_address -offset 0x40000000 -range 0x10000 -target_address_space [get_bd_addr_spaces processing_system7_0/Data] [get_bd_addr_segs cnn_axis_accel_0/s_axi/regs]
assign_bd_address -offset 0x41E00000 -range 0x10000 -target_address_space [get_bd_addr_spaces processing_system7_0/Data] [get_bd_addr_segs axi_dma_0/S_AXI_LITE/Reg]
assign_bd_address -offset 0 -range 0x20000000 -target_address_space [get_bd_addr_spaces axi_dma_0/Data_MM2S] [get_bd_addr_segs processing_system7_0/S_AXI_HP0/HP0_DDR_LOWOCM]
assign_bd_address -offset 0 -range 0x20000000 -target_address_space [get_bd_addr_spaces axi_dma_0/Data_S2MM] [get_bd_addr_segs processing_system7_0/S_AXI_HP0/HP0_DDR_LOWOCM]
validate_bd_design
save_bd_design
write_bd_tcl [file join $work recreate_pynq_bd.tcl]
generate_target all [get_files */design_1.bd]
set wrapper [make_wrapper -files [get_files */design_1.bd] -top]
add_files -norecurse $wrapper
set_property top design_1_wrapper [current_fileset]
update_compile_order -fileset sources_1
report_ip_status -file [file join $work reports ip_status.rpt]
puts PYNQ_ORIGINAL4_BD_READY
close_project
exit
