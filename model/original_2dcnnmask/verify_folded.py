"""Verify Masked2DCNNFolded against the original Masked2DCNN on the fixed
10 formal-test samples (the same IDs as golden/fp32_repro_check.csv).

Pass criterion: max |PhiPS2 difference after inverse target normalization|
< 1e-5 (FP32 CPU, both directions compared against the frozen golden too,
reported for information only).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from masked_2d_cnn import Masked2DCNN, Masked2DCNNFolded
from preprocess import load_image_tensor, normalize_image_id

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]

FIXED_TEST_IDS = ["00001", "00002", "00014", "00022", "00034",
                  "00047", "00048", "00059", "00064", "00070"]


def load_model(cls, checkpoint: Path):
    model = cls()
    ckpt = torch.load(checkpoint, map_location="cpu", weights_only=False)
    model.load_state_dict(ckpt["state_dict"])
    model.eval()
    return model


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, default=HERE / "checkpoint" / "best.pt")
    parser.add_argument("--folded-checkpoint", type=Path, default=HERE / "checkpoint" / "best_folded.pt")
    parser.add_argument("--image-dir", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--labels-test", type=Path, required=True)
    parser.add_argument("--tol", type=float, default=1e-5)
    args = parser.parse_args()

    norm = json.loads((HERE / "run_metadata" / "normalization.json").read_text(encoding="utf-8"))
    band_mean = np.asarray(norm["band_mean"], dtype=np.float32)
    band_std = np.asarray(norm["band_std"], dtype=np.float32)
    mean, std = float(norm["target_mean"]), float(norm["target_std"])

    labels = pd.concat(
        [
            pd.read_csv(args.labels, usecols=["ImageId", "PhiPS2"]),
            pd.read_csv(args.labels_test, usecols=["ImageId", "PhiPS2"]),
        ],
        ignore_index=True,
    )
    labels["ImageId"] = labels["ImageId"].map(normalize_image_id)
    labels = labels.drop_duplicates("ImageId", keep="first")
    gt = dict(zip(labels["ImageId"], labels["PhiPS2"]))

    golden_path = PROJECT / "golden" / "fp32_repro_check.csv"
    golden = {}
    if golden_path.exists():
        g = pd.read_csv(golden_path)
        golden = dict(zip(g["ImageId"].astype(str).str.zfill(5), g["Prediction"]))

    orig = load_model(Masked2DCNN, args.checkpoint)
    folded = load_model(Masked2DCNNFolded, args.folded_checkpoint)

    rows = []
    with torch.no_grad():
        for image_id in FIXED_TEST_IDS:
            tensor = load_image_tensor(args.image_dir / f"{image_id}.tiff", band_mean, band_std)
            x = torch.from_numpy(tensor[None]).float()
            p_orig = float(orig(x)) * std + mean
            p_fold = float(folded(x)) * std + mean
            rows.append(
                {
                    "ImageId": image_id,
                    "GroundTruth": gt[image_id],
                    "Orig": p_orig,
                    "Folded": p_fold,
                    "abs_diff": abs(p_orig - p_fold),
                    "vs_golden": abs(p_fold - golden[image_id]) if image_id in golden else float("nan"),
                }
            )

    df = pd.DataFrame(rows)
    print(df.to_string(index=False))
    max_d = df["abs_diff"].max()
    print(f"max |fold diff| = {max_d:.3e}  (tol {args.tol:g})")
    if max_d >= args.tol:
        raise SystemExit(f"fold verification FAILED: {max_d}")
    print("fold verification PASSED")


if __name__ == "__main__":
    main()
