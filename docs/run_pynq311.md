# PYNQ-Z2 / PYNQ 3.1.1运行方法

从完整工程根目录操作，使用Jupyter中sys.executable显示的PYNQ Python解释器。依赖NumPy和tifffile。
PYTHON替换成该解释器绝对路径，sudo时仍用同一个解释器：

```bash
PYTHON scripts/collect_environment.py --output environment.json
sudo PYTHON -B scripts/run_pynq311.py --stage smoke --confirm-download --out smoke_01
sudo PYTHON -B scripts/run_pynq311.py --stage full --confirm-download --reuse-overlay --out full_01
```

Jupyter中可用%run scripts/run_pynq311.py及相同参数（内核需要硬件权限）。首次替换PL，第二次复用同路径已加载位流；每次用新的结果目录名。程序严格核对PYNQ软件包3.1.1，不自动安装或升级系统。

结果位于results/pynq311_runs/NAME/，包含result.json、comparison.csv和INT16/RAW每张四层及GAP数组。正式full每条路径各十张，按AX7020参考比较最终预测，00001对AX7020逐层数组，十张两路径逐层互比。

驱动位于fpga/overlay/pynq311/，采用标准Overlay、allocate及Simple DMA。bit/hwh位于fpga/overlay/pynq_z2_original4_v14_100mhz/，bit旁不放同名XSA。程序核对HWH与实际时钟，只配置FCLK0分频，保持PLL/CPU/DDR和FCLK1～3；DMA停止后释放内存。

先确认板型、供电、没有占用PL/DMA的其他任务，下载位流前确认所用工程与板卡匹配。凭据由操作人在本地输入，不写入脚本和交接包。

环境版本读取使用importlib.metadata，不根据缺少__version__或镜像日期猜测。串口先辨认Linux提示符与Zynq>启动提示符。

PYNQ 2.1已完成20次推理。PYNQ 3.1.1入口完成本地接口检查，尚未进行该入口的上板测试。

软件接口依据：[Overlay](https://github.com/Xilinx/PYNQ/blob/3.1.1/pynq/overlay.py)、[DMA](https://github.com/Xilinx/PYNQ/blob/3.1.1/pynq/lib/dma.py)、[HWH/XSA解析](https://github.com/Xilinx/PYNQ/blob/3.1.1/pynq/pl_server/embedded_device.py)。
