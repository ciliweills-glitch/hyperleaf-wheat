---
name: hyperleaf-zynq-reproduce
description: 在AX7020与PYNQ-Z2上复现四层高光谱2DCNN，保持模型、INT16/F48数值和DMA接口一致，按目标板及PYNQ版本选择构建、部署和十样本比较方法。
metadata:
  short-description: AX7020与PYNQ-Z2四层CNN跨板部署
---

# 四层CNN跨板复现

本技能合并AX7020原工程与PYNQ-Z2移植中的共同方法：固定同一个模型和输入处理，复用CNN计算IP，替换目标板的板级工程和运行接口，再用相同十张TIFF比较结果。随完整工程hyperleaf_original4使用。

## 共同模型与计算规则

- 任务为小麦叶片PhiPSII回归，四层3×3卷积通道为205→32→64→128→160，输入205×48×352。204个光谱波段标准化后将背景置零，再加入前景Mask。
- 输入和权重采用INT16，48位累加，F48重量化，半整数取偶舍入。PL执行四层卷积、ReLU和池化；PS执行GAP、FP32回归头与反标准化。
- 计算接口v1.4，VERSION为0x00010004。每张158个分块、316条命令；32位AXIS字打包两路INT16，一条命令一个包，末字置TLAST。
- 批RUN先挂S2MM接收，再启动计算并由MM2S发送权重。按CMPL和DMA状态等待完成，再读取有效输出。
- 权重入口为golden/int16_ptq，模型和标准化参数位于model，十张TIFF位于images。详细数值及协议见[计算约定](references/contracts.md)。

## 选择对应的板卡与运行环境

| 板卡与环境 | 入口 | 已完成情况 |
|---|---|---|
| AX7020，PYNQ 3.1.2 | [AX7020运行](references/original4.md) | 独立目录十张板端与网口回放通过 |
| PYNQ-Z2，PYNQ 2.1 | [旧镜像运行](references/pynq21.md) | RAW和INT16各十张，预测与AX7020一致 |
| PYNQ-Z2，PYNQ 3.1.1 | [标准运行接口](references/run-pynq311.md) | 本地代码及API检查通过，实板记录来自2.1入口 |

先读取实际PYNQ安装版本。PYNQ-Z2的3.1.1入口严格匹配该版本，不能在2.1或3.1.2中直接使用。PYNQ-Z2使用自己的-1器件、512 MB DDR与板文件；AX7020使用-2器件、1 GB DDR与黑金板级配置。两套bit/HWH分别使用。

## 构建与部署

AX7020构建入口为build.tcl，PYNQ-Z2入口为build_pynq_z2.tcl；均使用Vivado 2024.1。板卡步骤见[板级适配](references/pynq-z2.md)。沿用厂商DDR、MIO、引脚和时钟配置，只接入所需计算IP与DMA。

下载位流需要人工确认；已明确授权的操作按该授权执行。仅阅读Skill、比较文件或整理目录时不访问PL。显示与输入设备按各自板卡配置，核心十样本比较可在无屏条件下完成。

## 使用相同样本比较

十个固定编号为00001、00002、00014、00022、00034、00047、00048、00059、00064、00070。先跑00001，再跑完整十张。RAW表示PL预处理，INT16表示PS生成量化输入，两种方式共用四层计算核。

按ImageId对齐预测。与整数参考比较使用技能scripts/compare_replay.py，与AX7020实测比较使用工程scripts/compare_pynq311.py。分别说明参考来源。00001逐层比较四层输出和GAP；有两种输入方式时，还可比较两条路径各样本的中间数组。

PYNQ-Z2已完成20次推理，最终预测与AX7020实测最大差0；00001逐层最大差0 LSB，十张RAW与INT16逐层结果一致。实际环境、时间和资源见[复测结果](references/verified-reuse.md)。新运行应记录本次环境和结果，计时分别说明推理与文件读取、预处理、传输和等待。

本技能复现对象为上述四层模型。
