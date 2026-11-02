obj-m += ax_touchscreen.o ax_lcd_encoder.o ax_dynclk.o ax_bridge.o ax_vtc.o ax_pl_display.o
ax_touchscreen-y := ax-touchscreen-drv.o
ax_lcd_encoder-y := ax-lcd-drv.o
ax_dynclk-y := clk-dglnt-dynclk.o
ax_bridge-y := xlnx_bridge.o
ax_vtc-y := xlnx_vtc.o
ax_pl_display-y := xlnx_pl_disp.o
ccflags-y += -DCONFIG_DRM_XLNX_BRIDGE=1
