# PYNQ-Z2 四层2DCNN接口说明

整理日期：2026-10-31。依据本次PYNQ-Z2生成的HWH、实际使用的RTL/Python驱动和十样本原始板测记录。

**软件交接目标为PYNQ 3.1.1；本次归档的20次真实板测运行在PYNQ 2.1。二者的版本标识分别保留。** 模型、权重和这一对PYNQ-Z2 bit/hwh保持一致。

## 1. 模型及PS/PL分工

模型为四层3×3 2DCNN，输入205×48×352，通道205→32→64→128→160。四层池化分别为(2,4)、(2,2)、(2,2)、(1,1)。每张最终输出一个PhiPSII浮点预测值。

| 部分 | 工作 |
|---|---|
| PS（板载ARM/Linux） | TIFF解码、全204波段前景掩码、分块打包、DMA与寄存器调度、GAP、160→96→1 FP32回归头及目标反标准化 |
| PL（FPGA电路） | 四层INT16卷积、F48重定标、ReLU及池化；RAW路径另外执行Q28输入预处理和冻结修正ROM |
| INT16入口 | PS按原float32顺序标准化/量化为INT16，再交给PL |
| RAW入口 | PS读取uint16光谱及生成0/1掩码，PL完成标准化/量化后继续同一CNN |

## 2. 板级和连接

| 项目 | 当前工程配置 |
|---|---|
| 器件 | PYNQ-Z2，xc7z020clg400-1 |
| DDR | 512MB，16位数据总线 |
| PS物理参考 | 50MHz |
| CNN、DMA及AXI时钟 | PS FCLK0，100MHz；同一时钟域 |
| 复位 | FCLK_RESET0_N经proc_sys_reset，CNN/DMA/AXI使用低有效复位 |
| 控制路径 | PS M_AXI_GP0 → AXI interconnect → CNN AXI-Lite及DMA S_AXI_LITE |
| 存储路径 | DMA MM2S/S2MM → memory interconnect → PS S_AXI_HP0 → DDR |
| 流路径 | DMA M_AXIS_MM2S → CNN s_axis；CNN m_axis → DMA S_AXIS_S2MM |

实例名固定为`processing_system7_0`、`cnn_axis_accel_0`和`axi_dma_0`。CPU/DDR的初始化沿用SD卡原系统；加载程序不运行ps7_init。

## 3. CNN AXI-Lite寄存器

基址**0x40000000**，HWH分配范围0x40000000～0x4000FFFF；实际RTL使用低8位寄存器偏移。以下地址均为字节地址，32位访问。

| 偏移 | 绝对地址 | 名称 | 读写与含义 |
|---|---|---|---|
| 0x00 | 0x40000000 | CTRL | 写bit0=1启动一条命令；读bit1为DONE（读后清除），bit2和bit3均反映idle。不是通用HLS auto-restart寄存器 |
| 0x10 | 0x40000010 | WORDS | 当前输入包的32位word数；不是字节数，也不是INT16个数 |
| 0x14 | 0x40000014 | COMMAND | 本四层流程使用1=LOAD INT16、3=批RUN、4=LOAD RAW |
| 0x18 | 0x40000018 | DIMS | tile宽tw放低位，硬件取bits[5:0]；tile高th放高16位，硬件取bits[18:16]。写入`tw | (th << 16)` |
| 0x1C | 0x4000001C | GROUPS | 3×3路径输入通道组数G=ceil(Cin/2)，硬件取bits[6:0] |
| 0x20 | 0x40000020 | POOL | ph在bits[1:0]、pw在bits[10:8]、K=Cout/2在bits[31:20]；bit16为pointwise标志，本四层模型保持0 |
| 0x24 | 0x40000024 | STATUS | 只读：bit0=busy，bit1=frame_error（包长/TLAST/TKEEP不匹配）；正常完成后0 |
| 0x28 | 0x40000028 | VERSION | 只读，期望**0x00010004** |
| 0x2C | 0x4000002C | CMPL | 只读16位命令完成计数，模65536回绕 |

写寄存器支持WSTRB；AXI-Lite地址/数据通道可以错开到达。完整命令执行以CMPL计数和DMA状态为准，避免反复读DONE清除状态而遗漏完成事件。当前调度按LOAD完成再RUN，不依赖排队容量扩展。

| 层 | Cin → Cout | G | K | ph,pw | 池化后形状 |
|---|---|---:|---:|---|---|
| 1 | 205 → 32 | 103 | 16 | 2,4 | 32×24×88 |
| 2 | 32 → 64 | 16 | 32 | 2,2 | 64×12×44 |
| 3 | 64 → 128 | 32 | 64 | 2,2 | 128×6×22 |
| 4 | 128 → 160 | 64 | 80 | 1,1 | 160×6×22 |

## 4. AXI DMA寄存器及状态

基址**0x41E00000**，地址范围0x41E00000～0x41E0FFFF。Simple模式，Scatter-Gather关闭；AXIS和存储数据宽度32位；长度字段23位，单次上限8388607字节。

| 偏移 | 寄存器 | 说明 |
|---|---|---|
| 0x00 | MM2S_DMACR | 发送通道控制，bit0 Run/Stop、bit2 Reset |
| 0x04 | MM2S_DMASR | 发送状态 |
| 0x18/0x1C | MM2S_SA | 发送物理地址低/高32位 |
| 0x28 | MM2S_LENGTH | 发送长度，单位字节，写入发起传输 |
| 0x30 | S2MM_DMACR | 接收通道控制 |
| 0x34 | S2MM_DMASR | 接收状态 |
| 0x48/0x4C | S2MM_DA | 接收物理地址低/高32位 |
| 0x58 | S2MM_LENGTH | 接收缓冲长度，单位字节，写入发起接收 |

DMASR的bit0=Halted，bit1=Idle，bits[6:4]为错误位，bit12=IOC完成事件。历史正式20次推理结束时两通道均为**0x1002**，表示Idle和IOC置位、错误位清零。不能只看程序无异常就认定DMA正确。

PYNQ3.1.1用`pynq.allocate`分配可供DMA访问的连续缓冲，传入缓冲的物理地址。32位数据按4字节对齐；实际传输长度取有效范围。直接轮询寄存器而不调用接收wait时，完成后对有效接收范围执行`invalidate()`。停止DMA后再释放缓冲。

## 5. 数据格式与包长

原始TIFF为uint16的204×48×352或48×352×204。前景掩码依据全部204波段非零判定；掩码作为第205通道。INT16张量为CHW、int16；RAW张量为CHW、uint16，前204通道是原始光谱，第205通道是0/1掩码。

tile最大th=4、tw=32，3×3卷积带一圈halo，边界补零。令S=(th+2)(tw+2)、G=ceil(Cin/2)、K=Cout/2：

| 包 | 输入word数 | 说明 |
|---|---|---|
| COMMAND=1 | G×S | 通道按两两一组，顺序为group、row、col，每word低/高16位装相邻两个通道；末尾补齐通道为0 |
| COMMAND=4 | ceil(S/32)+103×S | 前缀为掩码bit流，小端bit序；其后为原始光谱的通道对，量化与掩码值由PL生成 |
| COMMAND=3 | K×(18G+8) | 批量权重及系数；每输出通道对18G个权重word和8个系数word。打包以`weight_words`为准 |
| RUN输出 | K×(th/ph)×(tw/pw) | 每word两个int16输出值，低16位为通道2k，高16位为通道2k+1；按输出通道对及空间位置排列 |

一个命令一个完整AXIS输入包，TKEEP=0xF，TLAST只在最后word有效。输出包也在最后word给TLAST。权重重定标系数sq、偏置bq采用F48规则并各占64位，按低/高32位拆分；不要自行改变舍入、饱和、字节序或预处理运算顺序。

第一层最大INT16 LOAD为21012word=84048字节；RAW LOAD为21019word=84076字节；第一层批RUN输入29792word=119168字节。不能沿用约16KB的14位DMA长度设置，也不能把整张图直接当成一个tile包发送。

## 6. 正确调用顺序

1. 在DMA空闲时准备本层权重及输入tile，写DIMS/GROUPS/POOL。
2. 写LOAD的WORDS及COMMAND，写CTRL启动，再发MM2S输入包；等待MM2S空闲及LOAD完成。
3. 计算RUN输出长度，写RUN的WORDS/COMMAND；**先挂S2MM接收**，再写CTRL启动，最后MM2S发送批权重包。
4. 按`target=(CMPL+1)&0xffff`等待RUN完成，检查两通道状态、STATUS和有效接收数据，invalidate后按通道/空间重排。
5. 全图四层共158个tile、316条LOAD/RUN命令。PS计算GAP和FP32回归头并返回PhiPSII。

等待使用有界超时；发生超时或错误时保存原始状态，停止/复位DMA并确认Halted后释放内存。不要把先发权重后等待发送完成、最后才接收输出作为替代顺序。

## 7. Python运行与交付文件

更新skill入口为`skill/hyperleaf-zynq-reproduce/SKILL.md`。完整工程直接使用`scripts/run_pynq311.py`；驱动在`fpga/overlay/pynq311/`，位流在`fpga/overlay/pynq_z2_original4_v14_100mhz/`；模型、标准化、参考和十张TIFF沿用model、golden、data/reference和images目录。`pynq_cnn.py`保留A的计算流程，仅将加载入口接到3.1.1后端。

程序使用标准Overlay、allocate和DMA；从HWH核对地址、Simple模式、长度和时钟。bit旁边不放同名XSA，防止PYNQ3.1.1切换到XSA解析分支。XSA可放在独立开发目录，不作为运行依赖。

提供`--stage smoke`和`--stage full`，以及`--confirm-download`、`--reuse-overlay`、`--out`。full按INT16和RAW各十张，保存每张四层输出/GAP、comparison.csv及result.json。

本v1.4的0x28/0x2C分别是VERSION/CMPL，不能套用此前五层PERF1性能计数器。计时中的predict包括打包/搬运、PL计算、PS回归及逐层文件保存；total另含TIFF读取和预处理。

## 8. 实测核查依据

PYNQ2.1正式十样本×两路径，20个最终PhiPSII与A归档AX7020实测严格一致，最大差0；00001四层及GAP对AX7020为0LSB，十张RAW/INT16逐层互比全部一致。每次316命令，STATUS0，DMA两通道0x1002，FCLK0实读100MHz。

这些是带真实版本标识的历史结果。当前交接软件目标3.1.1在运行时现场记录版本；新结果保存到`results/pynq311_runs/NAME/`。原始结果位置、困难、保留警告和时序/资源见docs/results.md的PYNQ-Z2一节和board/results/pynq_z2_20261031/。
