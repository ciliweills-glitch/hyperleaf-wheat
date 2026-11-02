# PYNQ-Z2硬件重建

在Vivado2024.1命令环境，从新工程根目录执行（PC仿真Python3.8及以上）：

```text
python scripts/simulate.py
vivado -mode batch -source build_pynq_z2.tcl -tclargs create
vivado -mode batch -source build_pynq_z2.tcl -tclargs build
vivado -mode batch -source build_pynq_z2.tcl -tclargs cleanup
vivado -mode batch -source build_pynq_z2.tcl -tclargs release
```

PYNQ-Z2使用根目录build_pynq_z2.tcl，AX7020继续使用build.tcl。Tcl入口和TUL板文件位于board/pynq-z2/，RTL在fpga/rtl/，打包IP及ROM在ip/cnn_axis_accel/，仿真向量在sim/fixtures/。

生成工程、工作报告和新bit/hwh输出在build/pynq_z2/下。create拒绝覆盖已有vivado目录；cleanup保留首轮报告并用等价逻辑FIFO清理重新实现。发布脚本只生成文件，不编程设备。

随包已经配套的PYNQ-Z2制品在fpga/overlay/pynq_z2_original4_v14_100mhz/。历史时序/资源/DRC/CDC及警告在fpga/reports/pynq_z2_20261031/，构建过程记录在board/results/pynq_z2_20261031/build_logs/。

历史100MHz：WNS+0.121ns、WHS+0.018ns，DRC0；保留PSU-1～4及3条LUTAR-1。历史构建audit里的programmed_device=false只表示构建脚本不下载；实际板测有独立记录。

AX7020入口保留为根build.tcl；不用AX7020位流替代PYNQ-Z2位流。
