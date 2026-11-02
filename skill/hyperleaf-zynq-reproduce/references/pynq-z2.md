# 板级适配

共同计算核保持模型、权重、标准化、寄存器及包布局一致。DDR、PS参考时钟、引脚和器件速度等级使用目标板厂商配置。

AX7020保持根build.tcl及board/ax7020工程。PYNQ-Z2使用build_pynq_z2.tcl及board/pynq-z2工程，不能交换两板位流。

PYNQ-Z2构建方法见[构建说明](../../../docs/build_pynq_z2.md)，寄存器、DMA地址和运行顺序见[接口说明](../../../docs/pynq_z2_interfaces.md)。

PYNQ 2.1使用[旧镜像运行方法](pynq21.md)，PYNQ 3.1.1使用[标准运行方法](run-pynq311.md)。其他版本需先确认运行接口，已有结果不代替新版本板测。
