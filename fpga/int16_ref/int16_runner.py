"""加载 int16_ptq 开发包，对 00001 做整链前向并与逐层 golden 对拍。

用法:
  python fpga/int16_ref/int16_runner.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

from int16_ops import conv_block_int16, dequant, gap_int16, head_fp32

ROOT = Path(__file__).resolve().parents[2]
PACK = ROOT / "golden" / "int16_ptq"
LAYER_DIR = PACK / "00001"
NORM = ROOT / "model" / "original_2dcnnmask" / "run_metadata" / "normalization.json"

CONV_SHAPES = {
    1: (32, 205, 3, 3),
    2: (64, 32, 3, 3),
    3: (128, 64, 3, 3),
    4: (160, 128, 3, 3),
}

CONV_IN_KEY = {1: "in", 2: "c2", 3: "c3", 4: "c4"}
CONV_OUT_KEY = {1: "c2", 2: "c3", 3: "c4", 4: "c4out"}
POOL = {1: (2, 4), 2: 2, 3: 2, 4: None}


def load_conv(i: int):
    shape = CONV_SHAPES[i]
    w = np.fromfile(PACK / "weights" / f"conv{i}_w.int16.bin", dtype="<i2").reshape(shape)
    s_w = np.fromfile(PACK / "weights" / f"conv{i}_w_scale.f32.bin", dtype="<f4")
    b = np.fromfile(PACK / "weights" / f"conv{i}_b.f32.bin", dtype="<f4")
    return w, s_w, b


def load_head():
    fc1_w = np.fromfile(PACK / "weights" / "head_fc1_w.f32.bin", dtype="<f4").reshape(96, 160)
    fc1_b = np.fromfile(PACK / "weights" / "head_fc1_b.f32.bin", dtype="<f4")
    fc2_w = np.fromfile(PACK / "weights" / "head_fc2_w.f32.bin", dtype="<f4")
    fc2_b = np.fromfile(PACK / "weights" / "head_fc2_b.f32.bin", dtype="<f4")
    return fc1_w, fc1_b, fc2_w, fc2_b


def compare(name: str, got: np.ndarray, ref: np.ndarray) -> tuple[bool, int]:
    got = got.astype(np.int16)
    ref = ref.astype(np.int16)
    if got.shape != ref.shape:
        print(f"FAIL {name}: shape {got.shape} != golden {ref.shape}")
        return False, 10**9
    delta = got.astype(np.int64) - ref.astype(np.int64)
    max_lsb = int(np.abs(delta).max()) if delta.size else 0
    if max_lsb == 0:
        print(f"PASS {name}: {got.shape} exact match")
        return True, 0
    n = int(np.count_nonzero(delta))
    if max_lsb <= 1:
        print(f"PASS {name}: {got.shape} within 1 LSB (mismatch={n}/{delta.size})")
        return True, max_lsb
    print(f"FAIL {name}: {got.shape} max|Δ|={max_lsb} LSB, mismatch={n}/{delta.size}")
    return False, max_lsb


def main() -> int:
    scales = json.loads((PACK / "scales.json").read_text())
    q_in = np.load(LAYER_DIR / "input.int16.npy")
    x_fp = dequant(q_in, scales["in"])
    ok = True

    for i in range(1, 5):
        w, s_w, b = load_conv(i)
        s_out = scales[CONV_OUT_KEY[i]]
        y_q, pool_q = conv_block_int16(x_fp, w, b, s_w, s_out, POOL[i])
        layer = f"c{i}"
        ok &= compare(layer, y_q, np.load(LAYER_DIR / f"{layer}.int16.npy"))[0]
        if pool_q is not None:
            pname = f"pool_after_{i}"
            ok &= compare(pname, pool_q, np.load(LAYER_DIR / f"{pname}.int16.npy"))[0]
            x_fp = dequant(pool_q, s_out)
        else:
            x_fp = dequant(y_q, s_out)

    gap_q = gap_int16(x_fp, scales["head"])
    ok &= compare("gap", gap_q, np.load(LAYER_DIR / "gap.int16.npy"))[0]

    fc1_w, fc1_b, fc2_w, fc2_b = load_head()
    z = head_fp32(gap_q, scales["head"], fc1_w, fc1_b, fc2_w, fc2_b)
    norm = json.loads(NORM.read_text(encoding="utf-8"))
    phips2 = z * float(norm["target_std"]) + float(norm["target_mean"])
    print(f"INFO head z={z:.8f}  PhiPS2(00001)={phips2:.8f}  (golden INT16≈0.69900901)")
    print("PASS all layers" if ok else "FAIL layer compare")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
