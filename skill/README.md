# 四层CNN跨板复现Skill

入口为[SKILL.md](hyperleaf-zynq-reproduce/SKILL.md)。共同部分包括四层模型、INT16/F48计算、DMA包布局、逐层比较和固定十样本。

AX7020与PYNQ-Z2各自保留板级工程和运行方法。AX7020采用PYNQ 3.1.2；PYNQ-Z2已跑通的记录来自PYNQ 2.1，另提供PYNQ 3.1.1标准接口适配。使用时按实际板卡和安装版本选择入口。

技能随完整工程提供模型、权重、RTL、目标板bit/HWH、构建脚本、十张TIFF和参考结果。显示设备按板卡另行配置。
