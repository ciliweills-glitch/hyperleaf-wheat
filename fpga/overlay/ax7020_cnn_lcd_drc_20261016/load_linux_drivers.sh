#!/bin/sh
# Run only after the operator-approved combined bit/hwh has been loaded.
set -e
cd "$(dirname "$0")"
# The boot-image fabric UIO placeholder owns SPI29, now used by GT911.
echo 40000000.fabric > /sys/bus/platform/drivers/uio_pdrv_genirq/unbind
insmod ./ax_bridge.ko
insmod ./ax_dynclk.ko
insmod ./ax_vtc.ko
insmod ./ax_lcd_encoder.ko
insmod ./ax_pl_display.ko
insmod ./ax_touchscreen.ko
mkdir /sys/kernel/config/device-tree/overlays/ax7020_lcd
cat ./ax7020_lcd.dtbo > /sys/kernel/config/device-tree/overlays/ax7020_lcd/dtbo
cat /sys/kernel/config/device-tree/overlays/ax7020_lcd/status
ls /dev/dri /dev/input
