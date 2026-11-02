"""INT16 软件算子参考 — 对齐 export_int16_weights.py 的 FP32 fake-quant 路径。

与 int16_spec §3 硬件整数累加在舍入边界上可差 ≤1 LSB；本参考以对齐
golden/int16_ptq 层 golden 为准，供硬件/运行时对照。
"""

from __future__ import annotations

import numpy as np

QMAX = 32767.0


def to_grid(x_fp: np.ndarray, scale: float) -> np.ndarray:
    return np.clip(np.rint(np.asarray(x_fp, dtype=np.float32) / scale), -QMAX, QMAX)


def dequant(q: np.ndarray, scale: float) -> np.ndarray:
    return np.asarray(q, dtype=np.float32) * np.float32(scale)


def _conv3x3_fp32(x: np.ndarray, w: np.ndarray, b: np.ndarray) -> np.ndarray:
    """x:[Cin,H,W], w:[Cout,Cin,3,3], b:[Cout] — padding=1, stride=1, float32。"""
    cin, h, w_in = x.shape
    cout = w.shape[0]
    xp = np.pad(x, ((0, 0), (1, 1), (1, 1)))
    y = np.zeros((cout, h, w_in), dtype=np.float32)
    for o in range(cout):
        acc = np.zeros((h, w_in), dtype=np.float32)
        for ic in range(cin):
            for kh in range(3):
                for kw in range(3):
                    acc += xp[ic, kh : kh + h, kw : kw + w_in] * w[o, ic, kh, kw]
        y[o] = acc + b[o]
    return y


def conv_block_int16(
    x_fp: np.ndarray,
    w_int: np.ndarray,
    b: np.ndarray,
    s_w: np.ndarray,
    s_out: float,
    pool: tuple[int, int] | int | None,
):
    """输入为已反量化、网格对齐的激活；返回 conv 的 int16 网格，及 pool 后的 int16 网格。"""
    w_fp = w_int.astype(np.float32) * s_w.astype(np.float32)[:, None, None, None]
    y = _conv3x3_fp32(x_fp, w_fp, b.astype(np.float32))
    y = np.maximum(y, 0.0)
    y_q = to_grid(y, s_out)
    pool_q = None
    if pool is not None:
        kh, kw = (pool, pool) if isinstance(pool, int) else pool
        c, h, w = y_q.shape
        # 与 export 一致：在反量化值上 maxpool，再落回同一 grid
        y_fp = dequant(y_q, s_out)
        z = y_fp.reshape(c, h // kh, kh, w // kw, kw).max(axis=(2, 4))
        pool_q = to_grid(z, s_out)
    return y_q, pool_q


def gap_int16(x_fp: np.ndarray, s_head: float) -> np.ndarray:
    """x_fp 为 c4out 网格反量化后的值；均值后重量化到 head scale。"""
    g = x_fp.astype(np.float32).mean(axis=(1, 2))
    return to_grid(g, s_head)


def head_fp32(
    gap_q: np.ndarray,
    s_head: float,
    fc1_w: np.ndarray,
    fc1_b: np.ndarray,
    fc2_w: np.ndarray,
    fc2_b: np.ndarray,
) -> float:
    """返回未反标准化的模型输出 z（PhiPS2 = z*std+mean）。"""
    x = dequant(gap_q, s_head)
    h = np.maximum(fc1_w.astype(np.float32) @ x + fc1_b.astype(np.float32), 0.0)
    w2 = np.asarray(fc2_w, dtype=np.float32).reshape(-1)
    return float(w2 @ h + float(np.asarray(fc2_b, dtype=np.float32).reshape(-1)[0]))
