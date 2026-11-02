# 性能与数值说明 / Performance and accuracy

## 连续检测与同核优化

AX7020的400张连续网口检测全部完成，批次529.094秒；板端推理平均1.321秒/张，输入准备平均0.227秒/张，两者可重叠。板端推理包含PS调度、DMA、PL卷积与池化、PS GAP和FP32回归头。

同权重、INT16/F48语义、并行度及100 MHz下，Conv1代表tile的RUN仿真耗时由4.639 ms降至2.550 ms，提速1.819倍。这是事务级仿真；整张图耗时采用上述实板测量。

## 十样本CPU与PL历史对照

2026年9月28日，AX7020使用相同十个样本和四层INT16/F48计算规则。输入已量化后，CPU NumPy int64卷积与Python精确F48到预测的平均`t_predict`为222.807秒；100 MHz PL服务的DMA、调度、卷积、GAP与回归头平均`t_predict`为1.372秒。前者为NumPy/Python精确整数实现，后者使用当时v1.3硬件；两者逐张预测相同。笔记本Ryzen 9 9955HX采用与板端CPU相同的NumPy/Python整数参考，Windows NumPy 2.4.1，平均4.108秒/张；同一批样本的PL平均1.372秒/张，按这两组预测时间相除约为2.99倍。该对照展示这些实现的耗时，CPU数据不代表针对各处理器优化的推理性能上限。CPU从板端SD读取TIFF，PL经PC网口接收；输入处理和传输不计入以上两项预测时间。

## 精度与硬件一致性

完整测试集上，FP32 RMSE为0.0538、MAE为0.0379；INT16伪量化 RMSE为0.0538、MAE为0.0379。该伪量化使用FP32累加，表示量化实验结果。独立板端四层整数链在相同482张测试样本上的RMSE为0.0538、MAE为0.0379；实板预测在0.45和0.70阈值下分档准确率为78.22%。

硬件数值检查：样本00001的四层与GAP相对整数参考为0 LSB；AX7020十张原始TIFF预测与整数参考最大绝对差2.54×10⁻⁸；PYNQ-Z2上RAW及INT16各十次预测与AX7020记录最大差为0。

## 资料来源

- 模型伪量化指标：`model/original_2dcnnmask/artifacts_int16/metrics.json`。
- AX7020资源、时序及连续检测：[results.md](results.md)。
- 板端完整测试与历史十样本性能数据来自工程历史运行记录；测试设置和计算脚本应随原记录一起核对。

## English summary

The 400-image AX7020 run completed in 529.094 s; mean board inference was 1.321 s/image, including PS scheduling, DMA, PL, GAP and the FP32 head. Preparation averaged 0.227 s/image and overlaps inference.

For the same core and 100 MHz clock, prefetch reduced the Conv1 representative tile RUN simulation from 4.639 ms to 2.550 ms, a 1.819× transaction-level speedup.

A historical AX7020 ten-sample comparison measured 222.807 s/image for a NumPy int64 and Python exact-F48 CPU implementation and 1.372 s/image for the v1.3 PL service. These prediction times exclude input preparation and network transfer. The Ryzen 9 9955HX laptop ran the same NumPy/Python integer reference with Windows NumPy 2.4.1 and averaged 4.108 s/image; the ratio to the 1.372 s PL measurement is approximately 2.99×. The CPU implementation is an exact integer reference rather than an optimized inference kernel.

On the same 482 test samples, FP32 RMSE/MAE were 0.0538/0.0379; INT16 fake-quantization with FP32 accumulation gave 0.0538/0.0379; four-layer board integer inference gave 0.0538/0.0379. Board grade accuracy at thresholds 0.45 and 0.70 was 78.22%. Sample 00001 matched the integer reference to 0 LSB for all four layers and GAP.
