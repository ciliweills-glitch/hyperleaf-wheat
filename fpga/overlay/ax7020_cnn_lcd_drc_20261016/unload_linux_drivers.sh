#!/bin/sh
# Send TERM to kms_touch_demo or kms_screen before calling this script.
set -e
# Wait for the KMS file and framebuffer cleanup to finish before detaching DRM.
while pgrep -x 'kms_touch_demo|kms_screen' > /dev/null; do sleep 0.1; done
rmdir /sys/kernel/config/device-tree/overlays/ax7020_lcd
rmmod ax_touchscreen ax_pl_display ax_lcd_encoder ax_vtc ax_dynclk ax_bridge
echo 40000000.fabric > /sys/bus/platform/drivers/uio_pdrv_genirq/bind
