# AX7020当前四层LCD＋CNN制品

本目录为已部署的Original四层/v1.4配套文件，AX7020 xc7z020clg400-2、100MHz。bit与HWH来自同次最终构建，XSA包含同版bit，计算IP源码与其配套。

包含6个Linux显示触摸模块、修正140MHz时钟的DTBO、位流加载和驱动加载/卸载脚本。ko对应内核6.6.10-xilinx-v2024.1-g9299000c9490。


完整工程历史结果：LUT11372、FF14176、BRAM78、DSP62，WNS+1.039ns、WHS+0.042ns，DRC0。外部LCD输出约束与Methodology/CDC范围见docs/results.md。
