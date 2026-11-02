# 构建说明

环境为Vivado 2024.1、目标xc7z020clg400-2。Windows构建前将完整工程复制到短英文路径；不需要原电脑的F盘厂家目录、L盘映射或隐藏worktree。

## 工程来源

`board/ax7020/ax7020_cnn_lcd_bd.tcl`由`ax7020_cnn_lcd_drc_20261016`工程导出。`cnn.bd`保留原设计快照。其PS参数包含实际1GB DDR地址范围、MIO、EMIO I2C、FCLK和中断连接。

`board/ax7020/system.xdc`与`lcd7.xdc`来自黑金官方S4例程；`ip/vendor/`带入使用的axi_dynclk和pwm_lcd。`vendor_ps_config_reference.tcl`仅保存厂家母例程来源，构建使用BD导出的参数。

`ip/cnn_axis_accel/`和`fpga/rtl/`中的8份计算RTL、11份ROM取自当前位流配套发布IP；两处提供相同源码，前者供Vivado集成，后者供阅读和仿真。修改计算逻辑后，应将对应源文件同步到IP的src目录再重新构建。

## 命令

在工程根目录执行：

```sh
vivado -mode batch -source build.tcl -tclargs bd
vivado -mode batch -source build.tcl
```

第一条只创建BD、接入随包约束、Validate并输出配置。第二条生成目标、综合、实现和bitstream，产物位于`build/ax7020/release/`，新报告位于`build/ax7020/reports/`。也可将第二个Tcl参数设为另一个英文生成目录：

```sh
vivado -mode batch -source build.tcl -tclargs bd C:/fpga_build/original4
vivado -mode batch -source build.tcl -tclargs impl C:/fpga_build/original4
```

两条构建命令均不连接硬件。后续实际加载新位流仍使用同次生成的bit/HWH。

实现前处理`board/ax7020/repair_netlist.tcl`沿用原最终工程：FIFO BRAM使用READ_FIRST，对已确认的等价LUT作压缩，清理无接收端的死逻辑；保留OPT_DESIGN PRE挂接。显示AXI S02寄存器级已在冻结BD中。没有另写DDR、Bank电压或引脚设置。

## 验证入口

```sh
python scripts/simulate.py
python scripts/make_validation_vectors.py
python scripts/verify_outputs.py
python scripts/replay_local.py
```

仿真入口运行`tb_release.sv`及随包15组向量，结果位于`build/simulation/`。向量生成器按原整数参考重新生成到`build/generated_fixtures/`；逐层比较和十张真实TIFF离线回放分别输出到`results/integer_board_00001/`与`results/local_tiff_10/`。

