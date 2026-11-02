# PYNQ 2.1旧镜像运行

此方式对应已完成的PYNQ-Z2实板测试：PYNQ 2.1、Python 3.6、NumPy 1.14.2。使用归档的Xlnk适配程序和相同四层计算驱动。

在PC工程根目录执行，输出为工程外的新目录：

```sh
python scripts/prepare_pynq21.py --output ../pynq21_run
```

将生成的pynq21_run目录复制到板卡。可经SSH传输，也可使用串口传输文件；上次测试使用串口。以下命令在板卡的该目录执行，PYTHON替换为实际PYNQ解释器：

```sh
PYTHON -m pip install --target vendor tifffile==2019.7.26
sudo PYTHON run_board_compare.py --stage smoke --confirm-download --out smoke_new
sudo PYTHON run_board_compare.py --stage full --confirm-download --reuse-overlay --out full_new
```

smoke运行00001的两种输入方式；full各运行十张。第二次使用同一路径已经加载的PYNQ-Z2位流，结果写入results。每次采用新的输出目录名。

准备脚本只复制文件，不安装依赖或访问板卡。归档测试程序与适配程序保持原样；准备后的运行目录及TIFF依赖安装需要按上述环境使用。该目录不包含LCD、HDMI或触摸服务。
