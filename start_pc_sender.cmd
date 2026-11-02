@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 屏幕开始检测后，PC经网口发送随工程提供的10张原始TIFF。
echo 样本数请选择1到10；请保持COM11和网口连接，保持此窗口开启。
python -u "ui\screen\pc_uart_sender.py" --com COM11 --host 192.168.137.2 --source-ip 192.168.137.1 --inflight 3 --image-dir images --samples data/demo_samples.json --log results/pc_sender/events.jsonl %*
pause
