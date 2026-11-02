# 环境与来源

## 开发环境

- 硬件：AX7020/AC7Z020，xc7z020clg400-2，1GB DDR，AN970 800×480 LCD与GT911触摸。
- FPGA工具：Vivado 2024.1。
- 板端：PYNQ 3.1.2，现有Linux内核6.6.10-xilinx-v2024.1-g9299000c9490，PYNQ虚拟环境Python，XILINX_XRT=/usr。
- 离线参考：Python、NumPy、tifffile；PC发送另需pyserial，界面绘制需Pillow。
- FP32模型/量化导出：使用torch、NumPy、pandas、tifffile；torch仅使用已配置的WSL anaconda dl环境，golden在CPU执行。部署与整数对照无需torch。
- LCD程序编译：gcc、libdrm开发头文件，编译命令见`scripts/compile_display.sh`。内核模块使用匹配内核的ARM构建环境与随附Makefile。

部署时的卷积和Head参数统一从`golden/int16_ptq/weights/`读取；`scales.json`给出激活刻度。checkpoint/best.pt是训练来源，best_folded.pt及artifacts_int16保存BN折叠和PTQ导出来源，不由板端运行时加载。

## 第三方来源

| 内容 | 来源与处理 |
|---|---|
| PS/DDR/DMA母工程及system.xdc | 黑金AX7020_2023.1资料的course_s4_linux/09_sg_dma_test，保留原约束与版权 |
| LCD/触摸引脚与LCD IP | 同资料course_s4_linux/08_lcd_touch，使用原lcd7.xdc、pwm_lcd和axi_dynclk |
| 动态时钟IP | 随黑金例程提供的Digilent axi_dynclk，保留原版权和IP元数据 |
| Linux显示触摸驱动 | 原已部署ALINX/Xilinx/Digilent驱动及队内修改，源码各文件保留原许可与声明 |
| 中文字体 | Noto Sans SC子集，文件名hyperleafsans.ttf，OFL许可随assets/ofl.txt提供 |
| 数据与模型 | 原HyperLeaf小麦叶片PhiPSII数据、冻结seed42划分和2DCNN训练结果，见source.md；随附十张研究样本 |
| 原创工作 | CNN定点IP、整数参考、DMA调度、服务与交互、测试及复现Skill |


