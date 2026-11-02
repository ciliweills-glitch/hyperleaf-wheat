"""Operator-approved PL download; no PS initialization or boot-media writes."""
import argparse
from pynq import Overlay

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--bit', required=True)
args = parser.parse_args()
overlay = Overlay(args.bit)
print('COMBINED_OVERLAY_LOADED', overlay.bitfile_name, flush=True)
print('CNN_VERSION', hex(overlay.cnn_axis_accel_0.read(0x28)), flush=True)
print('CNN_STATUS', hex(overlay.cnn_axis_accel_0.read(0x24)), flush=True)
