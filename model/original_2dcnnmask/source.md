# Original 2DCNNMask / Masked2DCNN

Competition migration of the confirmed formal HyperLeaf PhiPSII spatial CNN.

## Model

- Class: `Masked2DCNN`
- Input: `205 × 48 × 352` (204 spectral bands + 1 leaf mask)
- Output: scalar PhiPSII (via inverse of train-target standardization)
- Parameters: **351,841** trainable

Architecture:

```text
Conv 205→32, BN, ReLU, MaxPool(2,4)
Conv 32→64, BN, ReLU, MaxPool(2,2)
Conv 64→128, BN, ReLU, MaxPool(2,2)
Conv 128→160, BN, ReLU
AdaptiveAvgPool2d(1) → Flatten
FC 160→96 → ReLU → Dropout(0.15) → FC 96→1
```

## Provenance

```text
Model source (extracted):
  src/hyperleaf/models/spatial/masked_2d_cnn.py
  extracted with: git show 089b81b:src/hyperleaf/models/spatial/masked_2d_cnn.py
  verified identical to current HEAD file (empty diff)

Checkpoint source:
  artifacts/runs/masked_2d_cnn/20260812_005434_seed42/best.pt

Run metadata:
  artifacts/runs/masked_2d_cnn/20260812_005434_seed42/

Split:
  tiff_spatial/splits/formal_split_seed42.csv
  100% agreement with run_metadata/splits_used.csv

Best epoch: 52
Selection metric: minimum validation RMSE (original train_regressor)

Validation (best):
  R² = 0.7824, RMSE = 0.0514, MAE = 0.0362

Test (best, same checkpoint):
  R² = 0.7492, RMSE = 0.0538, MAE = 0.0379

Training-time nearest known commit by timestamp:
  089b81b (2026-08-11 23:49 +0800)

Important:
  The checkpoint does not contain an embedded Git commit hash.
  089b81b is only the nearest known repository commit by timestamp,
  not a cryptographically verified training commit.
```

## Normalization

- Spectral bands: train-only per-band mean/std stored in
  `run_metadata/normalization.json` (copied from the formal run `config.json`
  `cache_manifest`, which came from `all_2410_split_seed42_f16`).
- Background: pixels with `|cube|.sum(bands) == 0` are masked to 0 after
  standardization; mask channel is binary foreground.
- Target: `z = (PhiPS2 - target_mean) / target_std` during training;
  inference returns `PhiPS2 = z * target_std + target_mean`
  (`target_mean=0.5497305393`, `target_std=0.1063743606`；全精度见 run_metadata/normalization.json)。

## Files

| Path | Role |
|---|---|
| `masked_2d_cnn.py` | Model definition extracted from git history |
| `preprocess.py` | Minimal TIFF → `205×48×352` contract |
| `inference.py` | FP32 checkpoint inference CLI |
| `checkpoint/best.pt` | Formal best checkpoint (epoch 52) |
| `run_metadata/` | Original config, split, metrics and predictions; normalization.json freezes preprocessing |
