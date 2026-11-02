# AX7020四层工程

使用四层3×3 2DCNN，INT16/F48、v1.4；AX7020采用xc7z020clg400-2、1 GB DDR3、Vivado 2024.1和PYNQ 3.1.2。

在工程根目录执行本地比较、仿真及构建：

```sh
python scripts/verify_outputs.py
python scripts/replay_local.py
python scripts/simulate.py
vivado -mode batch -source build.tcl -tclargs bd
```

完整综合实现省略bd参数。运行与服务安装见[AX7020运行说明](../../../docs/run.md)，计算比较见[复测结果](verified-reuse.md)。十张原始TIFF、权重、参数和参考结果随工程提供。

直接回放使用fpga/overlay/board_tiff_replay.py，先加载配套AX7020位流，再以--reuse-overlay运行。PS执行GAP及FP32回归头。板端十张直接回放和十张网口回放均已完成，00001逐层最大差0 LSB。

显示采用AN970和GT911；核心十样本计算可在无屏状态下完成。迁移至PYNQ-Z2时保留计算IP和数值规则，使用目标板专用工程与位流。
