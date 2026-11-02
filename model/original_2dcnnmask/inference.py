"""FP32 inference for Original 2DCNNMask / Masked2DCNN on formal HyperLeaf TIFFs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from masked_2d_cnn import Masked2DCNN
from preprocess import load_image_tensor, normalize_image_id

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]


def load_normalization(path: Path | None = None) -> dict:
    path = path or (HERE / "run_metadata" / "normalization.json")
    return json.loads(path.read_text(encoding="utf-8"))


def load_model(checkpoint: Path | None = None) -> Masked2DCNN:
    checkpoint = checkpoint or (HERE / "checkpoint" / "best.pt")
    model = Masked2DCNN()
    ckpt = torch.load(checkpoint, map_location="cpu", weights_only=False)
    if "state_dict" not in ckpt:
        raise ValueError(f"Unexpected checkpoint keys: {list(ckpt)}")
    model.load_state_dict(ckpt["state_dict"])
    model.eval()
    return model


def inverse_target(z: float, mean: float, std: float) -> float:
    return float(z * std + mean)


def predict_ids(
    image_ids: list[str],
    image_dir: Path,
    model: Masked2DCNN | None = None,
    norm: dict | None = None,
    device: str = "cpu",
) -> pd.DataFrame:
    model = model or load_model()
    norm = norm or load_normalization()
    band_mean = np.asarray(norm["band_mean"], dtype=np.float32)
    band_std = np.asarray(norm["band_std"], dtype=np.float32)
    mean, std = float(norm["target_mean"]), float(norm["target_std"])
    model = model.to(device)
    rows = []
    with torch.no_grad():
        for image_id in image_ids:
            image_id = normalize_image_id(image_id)
            tensor = load_image_tensor(image_dir / f"{image_id}.tiff", band_mean, band_std)
            x = torch.from_numpy(tensor[None]).to(device)
            # FP32 path: cast model and input to float32 explicitly
            z = model(x.float()).float().cpu().numpy().reshape(-1)[0]
            pred = inverse_target(float(z), mean, std)
            rows.append({"ImageId": image_id, "Prediction": pred})
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", type=Path, default=PROJECT / "data_contract/formal_split_seed42.csv")
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--labels-test", type=Path, required=True)
    parser.add_argument("--image-dir", type=Path, required=True)
    parser.add_argument("--role", default="test", choices=["train", "val", "test"])
    parser.add_argument("--n", type=int, default=10, help="First N image IDs from the role (sorted).")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    split = pd.read_csv(args.split)
    ids = sorted(split.loc[split["split"].astype(str).eq(args.role), "norm_id"].astype(int).astype(str).str.zfill(5))
    if args.n > 0:
        ids = ids[: args.n]

    labels = pd.concat(
        [
            pd.read_csv(args.labels, usecols=["ImageId", "PhiPS2"]),
            pd.read_csv(args.labels_test, usecols=["ImageId", "PhiPS2"]),
        ],
        ignore_index=True,
    )
    labels["ImageId"] = labels["ImageId"].map(normalize_image_id)
    labels = labels.drop_duplicates("ImageId", keep="first")

    preds = predict_ids(ids, args.image_dir, device=args.device)
    df = preds.merge(labels, on="ImageId", how="left").rename(columns={"PhiPS2": "GroundTruth"})
    df["AbsoluteError"] = (df["Prediction"] - df["GroundTruth"]).abs()
    out = args.out or (PROJECT / "golden" / f"fp32_predictions_{len(df)}samples_{args.role}.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(df.to_string(index=False))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
