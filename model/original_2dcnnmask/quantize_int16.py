"""INT16 PTQ for Masked2DCNNFolded (FP32 accumulate fake-quant simulation).

Pipeline:
  1. calibrate activation ranges on the first --calib-n sorted train images
     (train only, per-tensor max-abs at each tensor boundary: model input,
     pool1 out / conv2 in, pool2 out, pool3 out, GAP out / head in)
  2. quantize conv weights per output channel (symmetric int16), keep
     bias + head in FP32
  3. fake-quant forward: activations clamped to the int16 grid, conv in
     FP32 with dequantized weights (models int16 x int16 -> int32+ acc)
  4. evaluate: fixed 10 test samples + full test split, FP32 vs INT16

Artifacts (default artifacts_int16/):
  quant_params.npz  int16 weights, per-channel weight scales, fp32 biases,
                    fp32 head weights
  calibration.json  per-point max-abs + scales
  metrics.json      val/test-free metrics on test split, FP32 vs INT16
  compare_10samples.csv
  test_predictions_int16_full.csv
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn.functional as F

from masked_2d_cnn import Masked2DCNNFolded
from preprocess import load_image_tensor, normalize_image_id

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]

FIXED_TEST_IDS = ["00001", "00002", "00014", "00022", "00034",
                  "00047", "00048", "00059", "00064", "00070"]
QMAX = 32767.0

QUANT_POINTS = ["in", "c2", "c3", "c4", "c4out", "head"]


def qmaxabs(t: torch.Tensor) -> float:
    return float(t.abs().max().item())


def calibrate(model, ids, image_dir, band_mean, band_std, pre_hooks, post_hooks):
    maxabs = {k: 0.0 for k in QUANT_POINTS}
    handles = []
    for name, mod in pre_hooks:
        handles.append(mod.register_forward_pre_hook(make_hook(name, maxabs)))
    for name, mod in post_hooks:
        handles.append(mod.register_forward_hook(make_post_hook(name, maxabs)))
    with torch.no_grad():
        for image_id in ids:
            x = torch.from_numpy(load_image_tensor(image_dir / f"{image_id}.tiff", band_mean, band_std)[None]).float()
            maxabs["in"] = max(maxabs["in"], qmaxabs(x))
            model(x)
    for h in handles:
        h.remove()
    return maxabs


def make_hook(name, maxabs):
    def hook(module, inputs):
        maxabs[name] = max(maxabs[name], qmaxabs(inputs[0]))
    return hook


def make_post_hook(name, maxabs):
    def hook(module, inputs, output):
        maxabs[name] = max(maxabs[name], qmaxabs(output))
    return hook


def quantize_weights_per_channel(w: torch.Tensor):
    out = w.reshape(w.shape[0], -1)
    s = out.abs().amax(dim=1) / QMAX
    q = torch.round(out / s[:, None]).clamp(-QMAX, QMAX).reshape(w.shape)
    return q.to(torch.int16), s.to(torch.float32)


def int16_forward(sd: dict, x: torch.Tensor, act: dict[str, float]) -> torch.Tensor:
    def q(t, s):
        return torch.round(t / s).clamp(-QMAX, QMAX) * s

    def wdq(name):
        return sd[f"{name}.q"].float() * sd[f"{name}.s"][:, None, None, None]

    x = q(x, act["in"])
    x = F.conv2d(x, wdq("c1"), sd["c1.b"], padding=1)
    x = F.relu(x)
    x = F.max_pool2d(x, (2, 4))
    x = q(x, act["c2"])
    x = F.conv2d(x, wdq("c2"), sd["c2.b"], padding=1)
    x = F.relu(x)
    x = F.max_pool2d(x, 2)
    x = q(x, act["c3"])
    x = F.conv2d(x, wdq("c3"), sd["c3.b"], padding=1)
    x = F.relu(x)
    x = F.max_pool2d(x, 2)
    x = q(x, act["c4"])
    x = F.conv2d(x, wdq("c4"), sd["c4.b"], padding=1)
    x = F.relu(x)
    x = q(x, act["c4out"])
    x = F.adaptive_avg_pool2d(x, 1).flatten(1)
    x = q(x, act["head"])
    x = F.linear(x, sd["h1.w"], sd["h1.b"])
    x = F.relu(x)
    x = F.linear(x, sd["h2.w"], sd["h2.b"])
    return x.squeeze(1)


def metrics(y_true, y_pred) -> dict:
    y_true = np.asarray(y_true, dtype=np.float64).reshape(-1)
    y_pred = np.asarray(y_pred, dtype=np.float64).reshape(-1)
    ss_res = float(np.sum((y_true - y_pred) ** 2))
    ss_tot = float(np.sum((y_true - y_true.mean()) ** 2))
    return {
        "r2": 1.0 - ss_res / (ss_tot + 1e-12),
        "rmse": float(np.sqrt(np.mean((y_true - y_pred) ** 2))),
        "mae": float(np.mean(np.abs(y_true - y_pred))),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folded-checkpoint", type=Path, default=HERE / "checkpoint" / "best_folded.pt")
    parser.add_argument("--split", type=Path, default=PROJECT / "data_contract" / "formal_split_seed42.csv")
    parser.add_argument("--image-dir", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--labels-test", type=Path, required=True)
    parser.add_argument("--calib-n", type=int, default=256)
    parser.add_argument("--out-dir", type=Path, default=HERE / "artifacts_int16")
    args = parser.parse_args()

    model = Masked2DCNNFolded()
    ckpt = torch.load(args.folded_checkpoint, map_location="cpu", weights_only=False)
    model.load_state_dict(ckpt["state_dict"])
    model.eval()

    norm = json.loads((HERE / "run_metadata" / "normalization.json").read_text(encoding="utf-8"))
    band_mean = np.asarray(norm["band_mean"], dtype=np.float32)
    band_std = np.asarray(norm["band_std"], dtype=np.float32)
    mean, std = float(norm["target_mean"]), float(norm["target_std"])

    split = pd.read_csv(args.split)
    roles = {
        role: sorted(split.loc[split["split"].astype(str).eq(role), "norm_id"].astype(int).astype(str).str.zfill(5))
        for role in ("train", "val", "test")
    }
    labels = pd.concat(
        [
            pd.read_csv(args.labels, usecols=["ImageId", "PhiPS2"]),
            pd.read_csv(args.labels_test, usecols=["ImageId", "PhiPS2"]),
        ],
        ignore_index=True,
    )
    labels["ImageId"] = labels["ImageId"].map(normalize_image_id)
    gt = dict(zip(labels["ImageId"], labels["PhiPS2"]))

    pre_hooks = [("c2", model.features[3]), ("c3", model.features[6]),
                 ("c4", model.features[9]), ("head", model.head[0])]
    post_hooks = [("c4out", model.features[10])]
    print(f"calibrating on first {args.calib_n} train images ...")
    maxabs = calibrate(model, roles["train"][: args.calib_n], args.image_dir, band_mean, band_std, pre_hooks, post_hooks)
    act = {k: max(v, 1e-12) / QMAX for k, v in maxabs.items()}
    print(json.dumps({k: {"maxabs": maxabs[k], "scale": act[k]} for k in QUANT_POINTS}, indent=2))

    sd = {}
    for name, mod in [("c1", model.features[0]), ("c2", model.features[3]),
                      ("c3", model.features[6]), ("c4", model.features[9])]:
        q, s = quantize_weights_per_channel(mod.weight.detach())
        sd[f"{name}.q"] = q
        sd[f"{name}.s"] = s
        sd[f"{name}.b"] = mod.bias.detach().float()
    sd["h1.w"] = model.head[0].weight.detach().float()
    sd["h1.b"] = model.head[0].bias.detach().float()
    sd["h2.w"] = model.head[3].weight.detach().float()
    sd["h2.b"] = model.head[3].bias.detach().float()

    def predict(ids, mode):
        rows = []
        with torch.no_grad():
            for image_id in ids:
                x = torch.from_numpy(load_image_tensor(args.image_dir / f"{image_id}.tiff", band_mean, band_std)[None]).float()
                if mode == "fp32":
                    z = model(x).numpy().reshape(-1)[0]
                else:
                    z = int16_forward(sd, x, act).numpy().reshape(-1)[0]
                rows.append(z * std + mean)
        return np.asarray(rows, dtype=np.float64)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    np.savez(args.out_dir / "quant_params.npz", **{k: (v.numpy() if torch.is_tensor(v) else v) for k, v in sd.items()})
    (args.out_dir / "calibration.json").write_text(
        json.dumps({k: {"maxabs": maxabs[k], "scale": act[k]} for k in QUANT_POINTS}, indent=2),
        encoding="utf-8",
    )

    print("evaluating fixed 10 test samples ...")
    p_fp = predict(FIXED_TEST_IDS, "fp32")
    p_i16 = predict(FIXED_TEST_IDS, "int16")
    df10 = pd.DataFrame(
        {
            "ImageId": FIXED_TEST_IDS,
            "GroundTruth": [gt[i] for i in FIXED_TEST_IDS],
            "FP32": p_fp,
            "INT16": p_i16,
            "abs_diff": np.abs(p_fp - p_i16),
        }
    )
    df10.to_csv(args.out_dir / "compare_10samples.csv", index=False)
    print(df10.to_string(index=False))
    print(f"10-sample max |FP32-INT16| = {df10['abs_diff'].max():.3e}")

    print(f"evaluating full test split ({len(roles['test'])}) ...")
    p_fp = predict(roles["test"], "fp32")
    p_i16 = predict(roles["test"], "int16")
    y_test = np.asarray([gt[i] for i in roles["test"]], dtype=np.float64)
    result = {
        "quant": "int16 per-channel weights, per-tensor activations, fp32 accumulate (fake-quant)",
        "calib": f"first {args.calib_n} sorted train images, train only",
        "act_scales": act,
        "fixed10_max_abs_diff": float(df10["abs_diff"].max()),
        "fp32_test": metrics(y_test, p_fp),
        "int16_test": metrics(y_test, p_i16),
    }
    pd.DataFrame(
        {
            "ImageId": roles["test"],
            "GroundTruth": y_test,
            "FP32": p_fp,
            "INT16": p_i16,
            "abs_diff": np.abs(p_fp - p_i16),
        }
    ).to_csv(args.out_dir / "test_predictions_int16_full.csv", index=False)
    (args.out_dir / "metrics.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
