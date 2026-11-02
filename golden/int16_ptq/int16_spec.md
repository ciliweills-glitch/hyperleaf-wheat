# INT16 PTQ 开发包接口说明（int16_spec）

来源：`artifacts_int16/quant_params.npz`（`quantize_int16.py` 产出，校准集 = 排序前 256 张 train 图）。
量化方式：**权重 per-output-channel 对称 INT16；激活 per-tensor 对称 INT16；累加 FP32/宽位定点**。
层样本：00001。端到端 golden 覆盖固定 10 样本。

## 0. 快速答疑（12 问）

1. **Conv1 输入 shape？** [205,48,352]，CHW，无 batch 维（见 §2 `in`）。
2. **CHW 还是 HWC？** 一律 **CHW**（第 0 维 = 通道）。权重是 OIHW（见 §1）。`.int16.npy` 直接就是该形状。
3. **权重排列？** OIHW，原始 int16 小端、C 连续，conv1=[32,205,3,3]（见 §1）。
4. **padding / stride？** 所有 conv 相同：kernel 3×3、**stride 1、padding 1（零填充）**、dilation 1；conv 不改变空间尺寸，缩小只靠 pool。
5. **输入/权重/输出 scale？** 输入与各级激活 = §2 表（6 个量化点）；权重 = per-channel `conv{i}_w_scale.f32.bin`（`w_fp = w_int16 * scale[o]`）；conv 输出 scale = 下一量化点 scale（对照：conv1→c2、conv2→c3、conv3→c4、conv4→c4out、GAP→head）。
6. **bias 怎么处理？** fp32，BN 折叠后的值；在 acc 反量化后相加：`y_fp = acc*s_in*s_w[o] + b[o]`（§3）。
7. **accumulator 至少多少 bit？** **≥42 bit** 有符号（conv1 累加 1845 项 int16×int16；见 §4）。
8. **什么时候 requantize？** 每个 conv 输出在 **ReLU 后、写入 int16 存储前**，重量化到下一量化点 scale；GAP 输出在进 head 前量化到 head scale（§3）。
9. **rounding 怎么做？** **round-half-to-even**（四舍六入五成双，torch.round / np.rint 语义）。硬件用四舍五入时与 golden 差 ≤1 LSB，在 §6 容差内。
10. **saturation 怎么做？** clamp 到 **[-32767, 32767]**（对称量化不用 -32768）；ReLU 后实际只到 [0, 32767]。
11. **Pool 怎么处理？** MaxPool 直接在 int16 整数上比较取最大，**无缩放、无舍入**；pool1=(2,4) 非对称（H÷2、W÷4），pool2/pool3=(2,2)（§5）。
12. **Conv4→GAP→FC 为什么换 scale？** conv4 输出幅值 max≈12.6，GAP 均值后 |x|≤1.64。统一用 head scale 则 conv4 输出全部饱和截断（实测曾致 PhiPSII 误差 3e-2）；统一用 c4out scale 则 head 分辨率粗 ~8 倍。故 conv4 输出按 c4out 存，GAP 后再重量化到 head（§5）。

## 1. 文件布局

- `weights/conv{1..4}_w.int16.bin`：原始 int16 小端、C 连续、OIHW 形状 [[32, 205, 3, 3], [64, 32, 3, 3], [128, 64, 3, 3], [160, 128, 3, 3]]
- `weights/conv{1..4}_w_scale.f32.bin`：float32 小端，per-channel 缩放 [[32], [64], [128], [160]]，`conv_w_fp = conv_w_int16 * scale[o]`
- `weights/conv{1..4}_b.f32.bin`：float32 bias（BN 折叠后）
- `weights/head_fc1_w.f32.bin` [96,160]、`head_fc1_b.f32.bin`、`head_fc2_w.f32.bin` [1,96]、`head_fc2_b.f32.bin`：head 保持 FP32
- `scales.json`：各激活量化点 scale
- 所有 conv 公共参数：kernel 3×3、stride 1、padding 1（零填充）、dilation 1；输出空间尺寸 = 输入。

## 2. 激活量化点与 scale（对称，grid = scale；激活一律 CHW）

| 点 | 含义 | 形状 | scale |
|---|---|---|---|
| in | 预处理后输入 | [205,48,352] | 3.690991e-04 |
| c2 | pool1 输出 / conv2 输入 | [32,24,88] | 9.375000e-04 |
| c3 | pool2 输出 / conv3 输入 | [64,12,44] | 4.611965e-04 |
| c4 | pool3 输出 / conv4 输入 | [128,6,22] | 4.801732e-04 |
| c4out | conv4 输出（ReLU 后、GAP 前） | [160,6,22] | 3.857373e-04 |
| head | GAP 输出 | [160] | 5.004975e-05 |

量化：`q = clamp(round(x / scale), -32767, 32767)`；反量化 `x̂ = q * scale`。
mask 通道 {0,1} 经 in-scale 量化后有 ~1.1e-04 误差，属预期。

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
- **为什么 c4out/head 双刻度**：conv4 输出幅值 max≈12.6，GAP 后 |x|≤1.64。
  统一 head scale → conv4 输出上限 32767×5.004975e-05≈1.64，全部饱和（历史实测端到端误差 3e-2）；
  统一 c4out scale → head grid 步长粗 ~8 倍。故 conv4 输出按 c4out 存储，GAP 后重量化到 head。

## 6. golden 与验收

- `golden_10samples.csv`：10 固定样本端到端 PhiPSII（FP32 vs INT16），max|Δ|=1.953e-04
- `00001/`：逐层 int16 激活（`.int16.npy` + 各自 scale）与 FP32 参考（`.f32.npy`），
  取样点：conv 输出 = **ReLU 后、pool 前**；另有 pool 输出（= 下一层输入 grid）、GAP 输出
- 逐层验收：int16 激活反量化后与 FP32 参考逐元素比，容差 = 2×该层 scale（量化网格 + 权重量化影响 +
  rounding 模式差异 ≤1 LSB）；本次导出实测最大偏差见下表（单位：该层 scale 的倍数）
| 层 | 形状 | scale | max偏差(grid 倍数) |
|---|---|---|---|
| input | [205, 48, 352] | 3.690991e-04 | 0.50 |
| c1 | [32, 48, 352] | 9.375000e-04 | 0.94 |
| pool_after_1 | [32, 24, 88] | 9.375000e-04 | 0.75 |
| c2 | [64, 24, 88] | 4.611965e-04 | 1.97 |
| pool_after_2 | [64, 12, 44] | 4.611965e-04 | 1.81 |
| c3 | [128, 12, 44] | 4.801732e-04 | 3.69 |
| pool_after_3 | [128, 6, 22] | 4.801732e-04 | 3.27 |
| c4 | [160, 6, 22] | 3.857373e-04 | 13.52 |
| gap | [160] | 5.004975e-05 | 21.81 |

## 7. 固定 10 样本 ID

00001, 00002, 00014, 00022, 00034, 00047, 00048, 00059, 00064, 00070
