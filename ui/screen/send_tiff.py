"""Send one original TIFF after the operator starts the session on the LCD.

与 fpga/overlay/send_tiff.py 区分：后者直连 CNN 推理服务（:8765），本脚本走会话服务（:8766）。"""
import argparse
import json
import time
from http.client import HTTPConnection
from pathlib import Path
from urllib.parse import quote

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--tiff', type=Path, required=True)
p.add_argument('--host', default='192.168.137.2')
p.add_argument('--port', type=int, default=8766)
p.add_argument('--image-id')
p.add_argument('--source-ip', help='PC wired address, when routing requires an explicit source')
args = p.parse_args()
image_id = args.image_id or args.tiff.stem
if len(image_id) != 5 or not image_id.isascii() or not image_id.isdigit():
    p.error('Use a five-digit TIFF filename or --image-id')
data = args.tiff.read_bytes()
connection = HTTPConnection(args.host, args.port, timeout=40,
                            source_address=(args.source_ip, 0) if args.source_ip else None)
started = time.perf_counter()
connection.request('GET', '/status')
session = json.loads(connection.getresponse().read())
connection.close()
connection.request('POST', '/predict', data, {'Content-Type': 'image/tiff',
                   'X-Image-Id': image_id, 'X-Filename': quote(args.tiff.name),
                   'X-Session-Id': session['session_id']})
response = connection.getresponse()
result = json.loads(response.read())
connection.close()
result['pc_roundtrip_seconds'] = time.perf_counter() - started
print(json.dumps(result, ensure_ascii=False, indent=2))
raise SystemExit(0 if response.status == 200 else 1)
