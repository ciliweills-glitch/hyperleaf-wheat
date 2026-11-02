"""PC 端经网口发送原始 TIFF，直连 CNN 推理服务（默认 :8765 /predict）。

与 ui/screen/send_tiff.py 区分：后者提交会话服务（:8766），由界面批次驱动。
"""

import argparse
import json
import time
from pathlib import Path
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--tiff', type=Path, required=True)
    parser.add_argument('--host', default='192.168.137.2')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()

    request = Request(f'http://{args.host}:{args.port}/predict',
                      data=args.tiff.read_bytes(),
                      headers={'Content-Type': 'image/tiff'}, method='POST')
    started = time.monotonic()
    with urlopen(request, timeout=120) as response:
        result = json.load(response)
    result['t_pc_roundtrip'] = time.monotonic() - started
    result['source_tiff'] = str(args.tiff)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
