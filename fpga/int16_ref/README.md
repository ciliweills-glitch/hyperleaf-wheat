# INT16 软件算子参考

按 `golden/int16_ptq/` 开发包，用 numpy 复现与 `export_int16_weights.py` 一致的 **FP32 fake-quant** 推理链，供 B（硬件）/ C（运行时）做数值对照。不依赖 torch，不依赖板卡。

硬件按 `int16_spec.md` §3 的纯整数累加实现时，与本参考/golden 允许 **≤1 LSB** 差异。

## 运行

```bash
python fpga/int16_ref/int16_runner.py
```

Windows 宿主 Python + numpy 即可。

## 验收含义

对样本 `00001`，自 `input.int16.npy` 前向：

| 结果 | 含义 |
|---|---|
| `PASS exact` | 与 golden int16 网格完全一致 |
| `PASS within 1 LSB` | max\|Δ\|≤1（numpy/torch 累加顺序差，可接受） |
| `FAIL` | max\|Δ\|≥2 或 shape 不对，退出码 1 |

stdout 另打印反标准化后的 PhiPSII，应接近 `golden_10samples.csv` 中 00001 的 INT16 列（≈0.6990）。

## 文件

| 文件 | 作用 |
|---|---|
| `int16_ops.py` | conv / pool / GAP / head（fake-quant 路径） |
| `int16_runner.py` | 加载 pack + 逐层对拍 |

权重与 golden 只读 `golden/int16_ptq/`；目标反标准化读 `model/original_2dcnnmask/run_metadata/normalization.json`。
