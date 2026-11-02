"""Export Conv1 FP32 development package for the FPGA accelerator teammate.

Source: checkpoint/best_folded.pt (Masked2DCNNFolded, BN already folded into
conv bias). For one fixed sample (default 00001) writes into
golden/conv1_fp32/:
  input.{{npy,bin}}   preprocessed tensor [205,48,352] (what the FPGA receives)
  weight.{{npy,bin}}  folded conv1 weight [32,205,3,3] OIHW
  bias.{{npy,bin}}    folded conv1 bias [32]
  output.{{npy,bin}}  golden conv1 output [32,48,352], taken AFTER ReLU,
                    BEFORE MaxPool
  conv1_spec.md     interface contract for the accelerator
.bin = float32 little-endian, C-contiguous.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import torch

from masked_2d_cnn import Masked2DCNNFolded
from preprocess import load_image_tensor

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]

FIXED_TEST_IDS = ["00001", "00002", "00014", "00022", "00034",
                  "00047", "00048", "00059", "00064", "00070"]

SPEC_TEMPLATE = """# Conv1 FP32 开发包接口说明（conv1_spec）

来源：`model/original_2dcnnmask/checkpoint/best_folded.pt`（BN 已折叠进 Conv bias）。
导出样本：{image_id}。框架：PyTorch 2.11（WSL dl 环境），CPU FP32。

## 1. 算子定义

- Conv2d：in_channels=205, out_channels=32, kernel=3×3, stride=1, dilation=1
- padding=1（H、W 两侧各补 1 圈**零**），输出空间尺寸 = 输入：48×352
- 后接 ReLU（golden 取样点在 **ReLU 之后、MaxPool 之前**）

## 2. 文件与格式

| 文件 | 形状 | 布局 | 说明 |
|---|---|---|---|
| input.{{npy,bin}} | [205,48,352] | CHW | 预处理后输入张量（见第 3 节） |
| weight.{{npy,bin}} | [32,205,3,3] | OIHW | 折叠后权重，O=输出通道 |
| bias.{{npy,bin}} | [32] | — | 折叠后 bias |
| output.{{npy,bin}} | [32,48,352] | CHW | golden：ReLU 后、pool 前 |

`.bin` 一律 **float32 小端、C 连续顺序**（`np.tofile` 产物，用 `np.fromfile(..., dtype='<f4').reshape(形状)` 读回）。

## 3. 输入张量契约（重要）

input 是**预处理后的张量，不是原始 TIFF cube**。预处理（`preprocess.py`）流程：

1. 前景 mask = |cube|.sum(波段) > 0；
2. 204 个波段按 train-only 逐波段 mean/std 标准化（normalization.json）；
3. **标准化之后**再把背景置零；
4. 拼接二值 mask 通道 → 共 205 通道，第 205 通道（0 基索引 204）取值为 {{0.0, 1.0}}。

## 4. 数值参考（用于排查读序/排布错误）

{stats}

## 5. 验收方法

对固定样本重算 conv1（FP32）：`relu(conv2d(input, weight, bias, stride=1, padding=1))`，
与 output.npy 逐元素比，max |diff| 应 < 1e-4（FP32 累加顺序差异）。

## 6. 后续空间尺寸链（pool 不在本开发包内）

48×352 --pool1(2,4)--> 24×88 --pool2(2,2)--> 12×44 --pool3(2,2)--> 6×22 --GAP--> 160

注意 pool1 是**非对称** MaxPool(2,4)：H 方向 ÷2、W 方向 ÷4，勿按 (2,2) 实现。

## 7. 固定 10 样本 golden ID

{ids}
"""


def save_pair(arr: np.ndarray, stem: Path) -> None:
    np.save(stem.with_suffix(".npy"), arr)
    arr.astype("<f4", copy=False).tofile(stem.with_suffix(".bin"))
    back = np.fromfile(stem.with_suffix(".bin"), dtype="<f4").reshape(arr.shape)
    d = float(np.abs(back - arr.astype(np.float32)).max())
    print(f"{stem.name}: shape={arr.shape} dtype={arr.dtype} bin_roundtrip_max|diff|={d:.3e}")


def stat_line(name: str, arr: np.ndarray) -> str:
    return (f"- {name}: shape={list(arr.shape)}, min={arr.min():.6e}, "
            f"max={arr.max():.6e}, mean={arr.mean():.6e}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folded-checkpoint", type=Path, default=HERE / "checkpoint" / "best_folded.pt")
    parser.add_argument("--image-id", default="00001")
    parser.add_argument("--image-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, default=PROJECT / "golden" / "conv1_fp32")
    args = parser.parse_args()

    model = Masked2DCNNFolded()
    ckpt = torch.load(args.folded_checkpoint, map_location="cpu", weights_only=False)
    model.load_state_dict(ckpt["state_dict"])
    model.eval()
    conv1 = model.features[0]

    norm = json.loads((HERE / "run_metadata" / "normalization.json").read_text(encoding="utf-8"))
    band_mean = np.asarray(norm["band_mean"], dtype=np.float32)
    band_std = np.asarray(norm["band_std"], dtype=np.float32)

    image_id = args.image_id
    x = load_image_tensor(args.image_dir / f"{image_id}.tiff", band_mean, band_std)
    w = conv1.weight.detach().numpy().astype(np.float32)
    b = conv1.bias.detach().numpy().astype(np.float32)
    with torch.no_grad():
        y = torch.relu(conv1(torch.from_numpy(x[None]).float()))[0].numpy().astype(np.float32)

    out_dir = args.out_dir / image_id
    out_dir.mkdir(parents=True, exist_ok=True)
    save_pair(x, out_dir / "input")
    save_pair(w, out_dir / "weight")
    save_pair(b, out_dir / "bias")
    save_pair(y, out_dir / "output")

    stats = "\n".join(
        [stat_line("input", x), stat_line("weight", w), stat_line("bias", b), stat_line("output", y)]
    )
    spec = SPEC_TEMPLATE.format(image_id=image_id, stats=stats, ids=", ".join(FIXED_TEST_IDS))
    (out_dir / "conv1_spec.md").write_text(spec, encoding="utf-8")
    print(f"wrote {out_dir}")


if __name__ == "__main__":
    main()
