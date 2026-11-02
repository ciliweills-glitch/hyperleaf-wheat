#!/bin/sh
set -e
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
gcc -O2 -Wall -Wextra -pthread -I/usr/include/libdrm \
    "$root/fpga/linux/lcd_touch/kms_touch_demo.c" \
    -o "$root/ui/screen/kms_screen" -ldrm
