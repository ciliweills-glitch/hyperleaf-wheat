"""Fold BatchNorm into Conv for Original 2DCNNMask (FP32, CPU).

Converts checkpoint/best.pt (Conv bias=False + BatchNorm) into
checkpoint/best_folded.pt (Conv bias=True, no BatchNorm). The folded
checkpoint is the single source for verify_folded.py, export_fpga_fp32.py
and later quantization.

Fold math (per output channel c):
  scale_c = gamma_c / sqrt(running_var_c + eps)
  W'[c]   = W[c] * scale_c
  b'[c]   = beta_c - running_mean_c * scale_c
"""

from __future__ import annotations

import argparse
from pathlib import Path

import torch

from masked_2d_cnn import Masked2DCNN, Masked2DCNNFolded

HERE = Path(__file__).resolve().parent

CONV_IDX = [0, 4, 8, 12]
BN_IDX = [1, 5, 9, 13]
FOLDED_CONV_IDX = [0, 3, 6, 9]


def fold_state_dict(model: Masked2DCNN) -> dict:
    sd = model.state_dict()
    folded = {}
    for conv_i, bn_i, fconv_i in zip(CONV_IDX, BN_IDX, FOLDED_CONV_IDX):
        w = sd[f"features.{conv_i}.weight"].float()
        bn = model.features[bn_i]
        gamma = sd[f"features.{bn_i}.weight"].float()
        beta = sd[f"features.{bn_i}.bias"].float()
        mean = sd[f"features.{bn_i}.running_mean"].float()
        var = sd[f"features.{bn_i}.running_var"].float()
        scale = gamma / torch.sqrt(var + bn.eps)
        folded[f"features.{fconv_i}.weight"] = w * scale[:, None, None, None]
        folded[f"features.{fconv_i}.bias"] = beta - mean * scale
    for k, v in sd.items():
        if k.startswith("head."):
            folded[k] = v
    return folded


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, default=HERE / "checkpoint" / "best.pt")
    parser.add_argument("--out", type=Path, default=HERE / "checkpoint" / "best_folded.pt")
    args = parser.parse_args()

    model = Masked2DCNN()
    ckpt = torch.load(args.checkpoint, map_location="cpu", weights_only=False)
    model.load_state_dict(ckpt["state_dict"])
    model.eval()

    folded_model = Masked2DCNNFolded()
    folded_model.load_state_dict(fold_state_dict(model))
    folded_model.eval()

    torch.manual_seed(0)
    x = torch.randn(2, 205, 48, 352)
    with torch.no_grad():
        d = (model(x) - folded_model(x)).abs().max().item()
    print(f"random-input end-to-end max |diff| = {d:.3e}")
    if d > 1e-5:
        raise SystemExit(f"fold check failed: {d}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "state_dict": folded_model.state_dict(),
            "folded_from": args.checkpoint.name,
            "arch": "Masked2DCNNFolded",
        },
        args.out,
    )
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
