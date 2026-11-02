"""Export INT16 PTQ development package for the FPGA teammate.

Inputs: artifacts_int16/quant_params.npz + calibration.json (from
quantize_int16.py). Output root: golden/int16_ptq/

  weights/                 int16 conv weights (OIHW raw bin), per-channel
                           fp32 scales, fp32 biases, fp32 head
  scales.json              activation scales + weight scale ranges
  golden_10samples.csv     end-to-end PhiPS2, FP32 vs INT16, fixed 10 IDs
  00001/                   per-layer golden for the layer sample
                           (int16 activations + fp32 reference + scales)
  int16_spec.md            format / requant formula / accumulator note
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
from preprocess import load_image_tensor

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
QMAX = 32767.0

FIXED_TEST_IDS = ["00001", "00002", "00014", "00022", "00034",
                  "00047", "00048", "00059", "00064", "00070"]

SPEC_TEMPLATE = """# INT16 PTQ 开发包接口说明（int16_spec）

来源：`artifacts_int16/quant_params.npz`（`quantize_int16.py` 产出，校准集 = 排序前 256 张 train 图）。
量化方式：**权重 per-output-channel 对称 INT16；激活 per-tensor 对称 INT16；累加 FP32/宽位定点**。
层样本：{layer_id}。端到端 golden 覆盖固定 10 样本。

## 0. 快速答疑（12 问）

1. **Conv1 输入 shape？** [205,48,352]，CHW，无 batch 维（见 §2 `in`）。
2. **CHW 还是 HWC？** 一律 **CHW**（第 0 维 = 通道）。权重是 OIHW（见 §1）。`.int16.npy` 直接就是该形状。
3. **权重排列？** OIHW，原始 int16 小端、C 连续，conv1=[32,205,3,3]（见 §1）。
4. **padding / stride？** 所有 conv 相同：kernel 3×3、**stride 1、padding 1（零填充）**、dilation 1；conv 不改变空间尺寸，缩小只靠 pool。
5. **输入/权重/输出 scale？** 输入与各级激活 = §2 表（6 个量化点）；权重 = per-channel `conv{{i}}_w_scale.f32.bin`（`w_fp = w_int16 * scale[o]`）；conv 输出 scale = 下一量化点 scale（对照：conv1→c2、conv2→c3、conv3→c4、conv4→c4out、GAP→head）。
6. **bias 怎么处理？** fp32，BN 折叠后的值；在 acc 反量化后相加：`y_fp = acc*s_in*s_w[o] + b[o]`（§3）。
7. **accumulator 至少多少 bit？** **≥42 bit** 有符号（conv1 累加 1845 项 int16×int16；见 §4）。
8. **什么时候 requantize？** 每个 conv 输出在 **ReLU 后、写入 int16 存储前**，重量化到下一量化点 scale；GAP 输出在进 head 前量化到 head scale（§3）。
9. **rounding 怎么做？** **round-half-to-even**（四舍六入五成双，torch.round / np.rint 语义）。硬件用四舍五入时与 golden 差 ≤1 LSB，在 §6 容差内。
10. **saturation 怎么做？** clamp 到 **[-32767, 32767]**（对称量化不用 -32768）；ReLU 后实际只到 [0, 32767]。
11. **Pool 怎么处理？** MaxPool 直接在 int16 整数上比较取最大，**无缩放、无舍入**；pool1=(2,4) 非对称（H÷2、W÷4），pool2/pool3=(2,2)（§5）。
12. **Conv4→GAP→FC 为什么换 scale？** conv4 输出幅值 max≈{c4out_max:.1f}，GAP 均值后 |x|≤{head_max:.2f}。统一用 head scale 则 conv4 输出全部饱和截断（实测曾致 PhiPS2 误差 3e-2）；统一用 c4out scale 则 head 分辨率粗 ~{ratio:.0f} 倍。故 conv4 输出按 c4out 存，GAP 后再重量化到 head（§5）。

## 1. 文件布局

- `weights/conv{{1..4}}_w.int16.bin`：原始 int16 小端、C 连续、OIHW 形状 [{wshapes}]
- `weights/conv{{1..4}}_w_scale.f32.bin`：float32 小端，per-channel 缩放 [{sshapes}]，`conv_w_fp = conv_w_int16 * scale[o]`
- `weights/conv{{1..4}}_b.f32.bin`：float32 bias（BN 折叠后）
- `weights/head_fc1_w.f32.bin` [96,160]、`head_fc1_b.f32.bin`、`head_fc2_w.f32.bin` [1,96]、`head_fc2_b.f32.bin`：head 保持 FP32
- `scales.json`：各激活量化点 scale
- 所有 conv 公共参数：kernel 3×3、stride 1、padding 1（零填充）、dilation 1；输出空间尺寸 = 输入。

## 2. 激活量化点与 scale（对称，grid = scale；激活一律 CHW）

| 点 | 含义 | 形状 | scale |
|---|---|---|---|
| in | 预处理后输入 | [205,48,352] | {s_in} |
| c2 | pool1 输出 / conv2 输入 | [32,24,88] | {s_c2} |
| c3 | pool2 输出 / conv3 输入 | [64,12,44] | {s_c3} |
| c4 | pool3 输出 / conv4 输入 | [128,6,22] | {s_c4} |
| c4out | conv4 输出（ReLU 后、GAP 前） | [160,6,22] | {s_c4out} |
| head | GAP 输出 | [160] | {s_head} |

量化：`q = clamp(round(x / scale), -32767, 32767)`；反量化 `x̂ = q * scale`。
mask 通道 {{0,1}} 经 in-scale 量化后有 ~{mask_err:.1e} 误差，属预期。

## 3. 逐层计算、重量化、rounding、saturation（conv 通用）

对输出通道 o：
`acc = Σ (x_int16 * w_int16)`（x、w 均为 int16 grid 上的整数）
`y_fp = acc * s_act_in * s_w[o] + b[o]`
`y_int16 = clamp(round(y_fp / s_act_next), 0, 32767)`

- **requant 时点**：上式在 **ReLU 后、写入 int16 存储前**执行；`s_act_next` 即下一量化点 scale（§0 第 5 条对照）。
- **rounding**：round-half-to-even（四舍六入五成双，torch.round/np.rint 语义）。硬件四舍五入与 golden 差 ≤1 LSB。
- **saturation**：clamp 到 [-32767, 32767]（不用 -32768）；ReLU 后实际 [0, 32767]。超过即饱和，属预期行为。
- **bias**：fp32 在 acc 反量化后相加；硬件可预先把 b[o] 折算进累加器定标。

## 4. 累加器位宽提醒

每输出元素乘加项数 = in_ch×3×3：conv1=1845、conv2=288、conv3=576、conv4=1152。
int16×int16 积 ≤2^30，**conv1 累加需 ≥42 bit**（其余 ≥41 bit）；用 DSP48E1 级联或分块定点实现，勿用纯 int32。

## 5. 空间链、Pool 与 GAP

48×352 --conv1--> 48×352 --pool1(2,4)--> 24×88 --conv2--> 24×88 --pool2(2,2)--> 12×44
--conv3--> 12×44 --pool3(2,2)--> 6×22 --conv4--> 6×22 --GAP(÷132)--> 160 --head(fp32)--> 1

- **Pool**：MaxPool 在 int16 整数上直接比较取最大，无缩放无舍入；pool1=(2,4) 非对称（H÷2、W÷4），勿按 (2,2)。
- **GAP**：int16 grid 值求和（132 项）乘 1/132，重量化到 head scale 后进 head（head 保持 FP32，
  可在 PS 侧跑：消费 GAP 输出的反量化 FP32 值即可）。
- **为什么 c4out/head 双刻度**：conv4 输出幅值 max≈{c4out_max:.1f}，GAP 后 |x|≤{head_max:.2f}。
  统一 head scale → conv4 输出上限 32767×{s_head}≈{head_qmax:.2f}，全部饱和（历史实测端到端误差 3e-2）；
  统一 c4out scale → head grid 步长粗 ~{ratio:.0f} 倍。故 conv4 输出按 c4out 存储，GAP 后重量化到 head。

## 6. golden 与验收

- `golden_10samples.csv`：10 固定样本端到端 PhiPS2（FP32 vs INT16），max|Δ|={e2e_max:.3e}
- `{layer_id}/`：逐层 int16 激活（`.int16.npy` + 各自 scale）与 FP32 参考（`.f32.npy`），
  取样点：conv 输出 = **ReLU 后、pool 前**；另有 pool 输出（= 下一层输入 grid）、GAP 输出
- 逐层验收：int16 激活反量化后与 FP32 参考逐元素比，容差 = 2×该层 scale（量化网格 + 权重量化影响 +
  rounding 模式差异 ≤1 LSB）；本次导出实测最大偏差见下表（单位：该层 scale 的倍数）
{layer_table}

## 7. 固定 10 样本 ID

{ids}
"""


def to_int16_grid(t: torch.Tensor, s: float) -> torch.Tensor:
    return torch.round(t / s).clamp(-QMAX, QMAX)


def wdq(sd, name):
    return sd[f"{name}.q"].float() * sd[f"{name}.s"][:, None, None, None]


def int16_layers(sd, x, act):
    """Return ordered dict of (name, int16 tensor on its grid, scale, dequantized fp32 value).

    The chain propagates quantized tensors (grid-aligned), matching the
    hardware dataflow and quantize_int16.py:int16_forward.
    """
    out = {}
    q_in = to_int16_grid(x, act["in"])
    out["input"] = (q_in.to(torch.int16), act["in"], q_in * act["in"])
    x = q_in * act["in"]

    def conv_block(name, x, s_next, pool):
        y = F.conv2d(x, wdq(sd, name), sd[f"{name}.b"], padding=1)
        y = F.relu(y)
        yq = to_int16_grid(y, s_next) * s_next
        out[name] = (to_int16_grid(y, s_next).to(torch.int16), s_next, yq)
        z = yq
        if pool is not None:
            z = F.max_pool2d(yq, pool)
            zq = to_int16_grid(z, s_next) * s_next
            out[f"pool_after_{name[-1]}"] = (to_int16_grid(z, s_next).to(torch.int16), s_next, zq)
            z = zq
        return z

    x = conv_block("c1", x, act["c2"], (2, 4))
    x = conv_block("c2", x, act["c3"], 2)
    x = conv_block("c3", x, act["c4"], 2)
    y = F.conv2d(x, wdq(sd, "c4"), sd["c4.b"], padding=1)
    y = F.relu(y)
    yq = to_int16_grid(y, act["c4out"]) * act["c4out"]
    out["c4"] = (to_int16_grid(y, act["c4out"]).to(torch.int16), act["c4out"], yq)
    g = F.adaptive_avg_pool2d(yq, 1).flatten(1)
    gq = to_int16_grid(g, act["head"]) * act["head"]
    out["gap"] = (to_int16_grid(g, act["head"]).to(torch.int16), act["head"], gq)
    z = F.linear(gq, sd["h1.w"], sd["h1.b"])
    z = F.relu(z)
    z = F.linear(z, sd["h2.w"], sd["h2.b"])
    return out, z.reshape(-1)


def fp32_layers(model, x):
    feats = {}
    handles = []

    def cap(name):
        def h(m, i, o):
            feats[name] = o.detach()
        return h

    handles.append(model.features[1].register_forward_hook(cap("c1")))
    handles.append(model.features[4].register_forward_hook(cap("c2")))
    handles.append(model.features[7].register_forward_hook(cap("c3")))
    handles.append(model.features[10].register_forward_hook(cap("c4")))
    handles.append(model.features[11].register_forward_hook(cap("gap_flat")))
    with torch.no_grad():
        z = model(x)
    for h in handles:
        h.remove()
    feats["gap"] = feats.pop("gap_flat").flatten(1)
    feats["input"] = x
    return feats, z.reshape(-1)


def save_bin(arr: np.ndarray, path: Path) -> None:
    arr.tofile(path)
    back = np.fromfile(path, dtype=arr.dtype).reshape(arr.shape)
    d = float(np.abs(back.astype(np.float64) - arr.astype(np.float64)).max())
    print(f"{path.name}: shape={arr.shape} dtype={arr.dtype} roundtrip_max|diff|={d}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folded-checkpoint", type=Path, default=HERE / "checkpoint" / "best_folded.pt")
    parser.add_argument("--quant", type=Path, default=HERE / "artifacts_int16" / "quant_params.npz")
    parser.add_argument("--calibration", type=Path, default=HERE / "artifacts_int16" / "calibration.json")
    parser.add_argument("--image-id", default="00001", help="layer-wise golden sample")
    parser.add_argument("--image-dir", type=Path, required=True)
    parser.add_argument("--out-root", type=Path, default=PROJECT / "golden" / "int16_ptq")
    args = parser.parse_args()

    npz = np.load(args.quant)
    sd = {k: torch.from_numpy(npz[k]) for k in npz.files}
    calib = json.loads(args.calibration.read_text(encoding="utf-8"))
    act = {k: float(v["scale"]) for k, v in calib.items()}

    norm = json.loads((HERE / "run_metadata" / "normalization.json").read_text(encoding="utf-8"))
    band_mean = np.asarray(norm["band_mean"], dtype=np.float32)
    band_std = np.asarray(norm["band_std"], dtype=np.float32)
    mean, std = float(norm["target_mean"]), float(norm["target_std"])

    model = Masked2DCNNFolded()
    ckpt = torch.load(args.folded_checkpoint, map_location="cpu", weights_only=False)
    model.load_state_dict(ckpt["state_dict"])
    model.eval()

    out_root = args.out_root
    wdir = out_root / "weights"
    wdir.mkdir(parents=True, exist_ok=True)
    shapes_w, shapes_s = [], []
    for i, name in enumerate(["c1", "c2", "c3", "c4"], start=1):
        q = npz[f"{name}.q"]
        s = npz[f"{name}.s"]
        shapes_w.append(list(q.shape))
        shapes_s.append(list(s.shape))
        save_bin(q, wdir / f"conv{i}_w.int16.bin")
        save_bin(npz[f"{name}.s"], wdir / f"conv{i}_w_scale.f32.bin")
        save_bin(npz[f"{name}.b"], wdir / f"conv{i}_b.f32.bin")
    save_bin(npz["h1.w"], wdir / "head_fc1_w.f32.bin")
    save_bin(npz["h1.b"], wdir / "head_fc1_b.f32.bin")
    save_bin(npz["h2.w"], wdir / "head_fc2_w.f32.bin")
    save_bin(npz["h2.b"], wdir / "head_fc2_b.f32.bin")
    (out_root / "scales.json").write_text(json.dumps(act, indent=2), encoding="utf-8")

    def predict_i16(image_id):
        x = torch.from_numpy(load_image_tensor(args.image_dir / f"{image_id}.tiff", band_mean, band_std)[None]).float()
        _, z = int16_layers(sd, x, act)
        return x, float(z) * std + mean

    def predict_fp32(image_id):
        x = torch.from_numpy(load_image_tensor(args.image_dir / f"{image_id}.tiff", band_mean, band_std)[None]).float()
        with torch.no_grad():
            z = model(x)
        return x, float(z.reshape(-1)[0]) * std + mean

    rows = []
    for image_id in FIXED_TEST_IDS:
        _, p16 = predict_i16(image_id)
        _, pfp = predict_fp32(image_id)
        rows.append({"ImageId": image_id, "FP32": pfp, "INT16": p16, "abs_diff": abs(pfp - p16)})
    df10 = pd.DataFrame(rows)
    df10.to_csv(out_root / "golden_10samples.csv", index=False)
    e2e_max = float(df10["abs_diff"].max())
    print(df10.to_string(index=False))
    print(f"10-sample max |FP32-INT16| = {e2e_max:.3e}")

    layer_id = args.image_id
    ldir = out_root / layer_id
    ldir.mkdir(parents=True, exist_ok=True)
    x16, _ = predict_i16(layer_id)
    xfp, _ = predict_fp32(layer_id)
    int_layers, _ = int16_layers(sd, x16, act)
    ref_layers, _ = fp32_layers(model, xfp)
    pool_kernel = {"pool_after_1": (2, 4), "pool_after_2": 2, "pool_after_3": 2}
    lines = []
    for name, (q, s, grid_f32) in int_layers.items():
        if name in ref_layers:
            ref = ref_layers[name]
        else:
            ref = F.max_pool2d(ref_layers[f"c{name[-1]}"], pool_kernel[name])
        np.save(ldir / f"{name}.int16.npy", q[0].numpy())
        np.save(ldir / f"{name}.f32ref.npy", ref[0].numpy() if ref.dim() == q.dim() else ref.numpy())
        d = float((grid_f32 - ref).abs().max())
        lines.append(f"| {name} | {list(q.shape[1:])} | {s:.6e} | {d / s:.2f} |")
        print(f"layer {name}: max|int16-fp32| = {d:.4e} ({d / s:.2f} grid steps)")
    (ldir / "layer_scales.json").write_text(
        json.dumps({k: v[1] for k, v in int_layers.items()}, indent=2), encoding="utf-8"
    )

    mask_err = abs(round(1.0 / act["in"]) * act["in"] - 1.0)
    c4out_max = float(calib["c4out"]["maxabs"])
    head_max = float(calib["head"]["maxabs"])
    head_qmax = QMAX * act["head"]
    spec = SPEC_TEMPLATE.format(
        layer_id=layer_id,
        wshapes=", ".join(str(s) for s in shapes_w),
        sshapes=", ".join(str(s) for s in shapes_s),
        s_in=f"{act['in']:.6e}", s_c2=f"{act['c2']:.6e}", s_c3=f"{act['c3']:.6e}",
        s_c4=f"{act['c4']:.6e}", s_c4out=f"{act['c4out']:.6e}", s_head=f"{act['head']:.6e}",
        mask_err=mask_err, e2e_max=e2e_max,
        c4out_max=c4out_max, head_max=head_max, head_qmax=head_qmax,
        ratio=act["c4out"] / act["head"],
        layer_table="\n".join(["| 层 | 形状 | scale | max偏差(grid 倍数) |", "|---|---|---|---|"] + lines),
        ids=", ".join(FIXED_TEST_IDS),
    )
    (out_root / "int16_spec.md").write_text(spec, encoding="utf-8")
    print(f"wrote {out_root}")


if __name__ == "__main__":
    main()
