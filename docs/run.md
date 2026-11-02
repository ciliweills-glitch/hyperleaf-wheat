# AX7020运行说明

适用原AX7020 PYNQ 3.1.2 SD系统、AN970显示触摸和当前Linux驱动。已有模块对应内核`6.6.10-xilinx-v2024.1-g9299000c9490`。PYNQ-Z2使用自己的板级工程和驱动。

## 文件放置与服务

将整个展开目录传至板端，例如`/home/xilinx/cnn/hyperleaf_original4/`。板端安装步骤在该目录执行。安装时先停止此前的显示/控制器/推理与硬件服务，等待原KMS程序退出后再卸驱动；已有历史数据留在旧目录。

```sh
sudo systemctl stop hyperleaf-hardware.service
sudo sh scripts/compile_display.sh
sudo python3 scripts/install_services.py
```

安装器按本目录实际位置生成四个service并执行daemon-reload，不启动服务。Linux模块、修正后的DTBO和位流在`fpga/overlay/ax7020_cnn_lcd_drc_20261016/`。

**操作者确认使用本包位流后**，启动：

```sh
sudo systemctl start hyperleaf-display.service
```

依赖顺序为hardware→cnn→controller→display。硬件服务通过PYNQ加载bit/HWH，随后装载显示触摸驱动；CNN服务使用`--reuse-overlay`，保持一个推理进程与一个数据准备进程，最多同时提交三个请求。显示使用`/dev/dri/card1`及`/dev/input/event0`，800×480。

希望之后开机自动启动时，执行`sudo systemctl enable hyperleaf-display.service`。原SD启动方式继续使用。

```sh
systemctl status hyperleaf-hardware hyperleaf-cnn hyperleaf-controller hyperleaf-display
journalctl -b -u hyperleaf-hardware -u hyperleaf-cnn -u hyperleaf-controller -u hyperleaf-display
```

源码和Makefile位于`fpga/linux/lcd_touch/`。更换Linux内核后需使用该内核的配置/头文件和ARM交叉工具链重新编译模块；现有ko保留其原内核版本。

## 网口与PC发送

当前板端地址192.168.137.2/24，PC网卡192.168.137.1/24；串口COM11、115200。原网络别名配置保存为`ui/screen/systemd/eth0_hyperleaf`，按现有网络实际安装，不覆盖原网络配置。

PC需Python、NumPy、tifffile、Pillow、pyserial。在本工程根目录双击`start_pc_sender.cmd`；屏幕选择1–10个样本并开始检测。默认发送`images/`中的10张真实TIFF，顺序见`data/demo_samples.json`。

TIFF经过网口送至8766；UART只传开始/取消等控制事件。8766管理批次并转发8765推理服务。屏幕显示本次真实回归结果、204波段前景均值光谱、批次耗时与历史。

400样本模式需电脑已有相应原始TIFF，在工程根目录运行：

```sh
python ui/screen/pc_uart_sender.py --com COM11 --host 192.168.137.2 --source-ip 192.168.137.1 --inflight 3 --samples data/stress400_order.json --image-dir <HyperLeaf2024图像目录> --log results/pc_sender/events.jsonl
```

默认十样本清单与400样本清单只提供ImageId，旧缓存预测不参与推理。400张TIFF未重复放入本工程；选择记录和既有400结果已随附。

单张手动上传可在屏幕开始批次后打开`http://192.168.137.2:8766/`。取消时停止续传，等待在途请求结束，未完成批次不归档。低/中/高采用固定0.45/0.70数值分档。

## 不经界面的十样本回放

无板卡时，可先在PC执行`python scripts/replay_local.py`，从随包十张真实TIFF运行CPU整数参考，并与原多步FP32输入量化及十张整数预测比较。2026-10-28已在独立英文目录运行，最大输入差0LSB、预测差0。10月29日已执行以下板端回放并验证新service路径，见文末复测记录。

已加载同版硬件、暂停显示/控制器/CNN服务以释放计算核后，在工程根目录执行：

```sh
export XILINX_XRT=/usr
sudo -E /usr/local/share/pynq-venv/bin/python3 fpga/overlay/board_tiff_replay.py \
  --bit fpga/overlay/ax7020_cnn_lcd_drc_20261016/ax7020_cnn.bit \
  --pack golden/int16_ptq \
  --norm model/original_2dcnnmask/run_metadata/normalization.json \
  --image-dir images --reference data/integer_chain_10samples.csv \
  --pl-preprocess --reuse-overlay --out results/board_10.csv
```

回放使用已加载Overlay，不重复下载。完成后重新启动原常驻软件服务。该操作是独立测试，避免与常驻推理同时占用DMA。

## 结果比较

十张直接回放和十张网口回放均完成，预测与整数参考最大差约2.54e-8。00001四层和GAP与参考逐元素一致。取消后等待已提交请求完成，再返回首页；重启任务管理服务后可恢复完整批次的历史记录。
