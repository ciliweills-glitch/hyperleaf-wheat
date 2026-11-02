# AX7020显示、触摸与网口界面

当前Original四层整机使用本目录controller.py、views.py、classification.py、kms_screen及assets。完整运行说明见项目根docs/run.md，服务模板见board/services。历史界面部署过程另存evidence/screen_source_notes.md。

controller在8766管理会话、原始TIFF上传、光谱、取消和历史；8765提供实际CNN推理。UART传开始/取消等控制，TIFF经网口传输。PC入口为根start_pc_sender.cmd，默认提供10张真实TIFF；400模式使用data/stress400_order.json及完整图像目录。

界面为800×480，KMS双缓冲显示，GT911经evdev输入。完成批次保存到runtime/batches.json；新工程默认无历史，不复制历史预测作为本次结果。数值分档为低<0.45、中含0.45–0.70、高>0.70。

字体、稻穗图标、数量卡片素材均在assets；C源码位于fpga/linux/lcd_touch/kms_touch_demo.c，编译入口scripts/compile_display.sh。

