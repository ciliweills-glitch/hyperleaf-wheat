# 数值、模型和接口

## 固定的算法


原始TIFF为uint16，204×48×352或48×352×204。前景由全204波段非零判定；先按训练集波段mean/std标准化，再将背景置零，最后追加0/1前景通道。不要只根据一个波段生成Mask。目标反标准化参数在normalization.json。

INT16输入为205×48×352；缩放采用float32运算顺序、np.rint与[-32767,32767]裁剪，规则来源golden/int16_ptq。F48的系数、偏置、舍入与ReLU饱和依据cnn_contract.py/relu_requant.v等实际代码。fake-quant INT16、整数/F48、FP32和真实标签是不同的参照。

v1.4的原始入口在PS解码TIFF并生成Mask，PL用Q28和稀疏修正得到冻结输入，不是任意近似仿射变换。系数ROM与卷积RTL一同导出。当前参考00001整数/F48 PhiPSII=0.6990（全精度见golden参考与verify脚本）；参考值不代替实际目标板运行。

## 软件和PL对应

- 源驱动：fpga/overlay/pynq_cnn.py、cnn_contract.py。仓库仅含四层2DCNN模型，不携带1×1权重。
- HWH实例名：cnn_axis_accel_0、axi_dma_0；使目标工程实例名与驱动一致，或明确同步修改驱动，不照抄绝对地址。
- DMA：simple mode，长度字段23位。INT16输入tile最大84048字节，raw入口加Mask前缀后最大84076字节，批RUN权重载荷更大，不能沿用14位约16KB的长度上限。
- 每次LOAD输入为独立AXIS包并正确给出TLAST；批RUN同时MM2S权重/S2MM结果，不能在输出无人接收时阻塞等待输入完成。依当前pynq_cnn.py启动顺序操作。
- VERSION寄存器0x28，当前v1.4为0x10004；CMPL位于0x2c，16位可回绕。完成计数不等于CPU轮询次数。命令字段查axil_regs.v和打包代码。

导出的板端代码不需要torch；NumPy、tifffile及目标板PYNQ环境即可运行数值链。复跑FP32模型时另用已安装torch的CPU环境，不在板卡临时训练或重新量化。
