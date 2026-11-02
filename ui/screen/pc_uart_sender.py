"""Monitor the LCD's UART start/cancel events and send raw TIFFs over Ethernet."""
import argparse
import json
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from http.client import HTTPConnection, HTTPException
from pathlib import Path
from urllib.parse import quote

import serial

HERE = Path(__file__).resolve().parent
PREFIX = 'HYPERLEAF_EVENT '


class Sender:
    def __init__(self, args):
        self.args = args
        samples = json.loads(args.samples.read_text(encoding='utf-8'))['samples']
        self.image_ids = [row['ImageId'] for row in samples]
        self.session_id = ''
        self.stop = threading.Event()
        self.worker = None
        self.log_lock = threading.Lock()
        args.log.parent.mkdir(parents=True, exist_ok=True)
        self.log_file = args.log.open('a', encoding='utf-8', buffering=1)

    def log(self, event, **fields):
        line = json.dumps({'time': time.strftime('%Y-%m-%d %H:%M:%S'),
                           'event': event, **fields}, ensure_ascii=False)
        with self.log_lock:
            self.log_file.write(line + '\n')
            print(line, flush=True)

    def connection(self):
        return HTTPConnection(self.args.host, self.args.port, timeout=40,
                              source_address=(self.args.source_ip, 0) if self.args.source_ip else None)

    def status(self):
        conn = self.connection()
        conn.request('GET', '/status')
        response = conn.getresponse()
        state = json.loads(response.read())
        conn.close()
        return state

    def event(self, event):
        self.log('uart', control=event)
        if event['event'] == 'start':
            if event['session_id'] == self.session_id:
                return
            self.stop.set()
            self.stop = threading.Event()
            self.session_id = event['session_id']
            self.worker = threading.Thread(target=self.send_batch, args=(event, self.stop))
            self.worker.start()
        elif event['session_id'] == self.session_id and event['event'] in ('cancel', 'error'):
            self.stop.set()

    def send_batch(self, event, stop):
        session_id = event['session_id']
        count = event['sample_count']
        if not 1 <= count <= min(400, len(self.image_ids)):
            self.log('error', session_id=session_id, message='样本数量超过本机清单范围')
            return
        self.log('batch_start', session_id=session_id, count=count)
        try:
            conn = self.connection()
            conn.request('POST', '/plan', json.dumps({'session_id': session_id,
                         'image_ids': self.image_ids[:count]}).encode(),
                         {'Content-Type': 'application/json'})
            response = conn.getresponse()
            response.read()
            conn.close()
            if response.status != 200:
                self.log('batch_stop', session_id=session_id, status=response.status)
                return
            indices = iter(range(event['received_count'], count))
            with ThreadPoolExecutor(max_workers=self.args.inflight) as clients:
                pending = set()
                for _ in range(min(self.args.inflight, count-event['received_count'])):
                    pending.add(clients.submit(self.send_one, session_id, next(indices), count, stop))
                while pending:
                    finished, pending = wait(pending, return_when=FIRST_COMPLETED)
                    for future in finished:
                        if future.exception() is not None:
                            stop.set()
                        if not future.result():
                            stop.set()
                    if not stop.is_set():
                        for _ in finished:
                            index = next(indices, None)
                            if index is not None:
                                pending.add(clients.submit(self.send_one, session_id, index, count, stop))
            self.log('batch_cancelled' if stop.is_set() else 'batch_done', session_id=session_id)
        except (OSError, HTTPException, ValueError) as error:
            self.log('error', session_id=session_id, message=str(error))


    def send_one(self, session_id, index, count, stop):
        if stop.is_set():
            return False
        image_id = self.image_ids[index]
        path = self.args.image_dir / (image_id + '.tiff')
        read_started = time.monotonic()
        data = path.read_bytes()
        read_seconds = time.monotonic()-read_started
        if stop.is_set():
            return False
        started = time.monotonic()
        conn = self.connection()
        self.log('sending', session_id=session_id, number=index+1,
                 count=count, image_id=image_id, bytes=len(data))
        conn.request('POST', '/predict', data,
                     {'Content-Type': 'image/tiff', 'X-Image-Id': image_id,
                      'X-Session-Id': session_id, 'X-Filename': quote(path.name)})
        response = conn.getresponse()
        result = json.loads(response.read())
        conn.close()
        self.log('response', session_id=session_id, number=index+1,
                 image_id=image_id, status=response.status,
                 seconds=time.monotonic()-started, t_pc_read=read_seconds,
                 t_total_pc=time.monotonic()-read_started, result=result)
        if response.status != 200:
            stop.set()
        return response.status == 200


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--com', default='COM11')
    parser.add_argument('--baud', type=int, default=115200)
    parser.add_argument('--host', default='192.168.137.2')
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--inflight', type=int, choices=(1, 2, 3), default=3,
                        help='最多在途请求；默认3，使传输/准备与单核推理重叠')
    parser.add_argument('--source-ip', default='192.168.137.1')
    parser.add_argument('--image-dir', type=Path, required=True,
                        help='目录含 {ImageId}.tiff 原始高光谱样本')
    parser.add_argument('--samples', type=Path, default=HERE / 'pc_preview/assets/recorded_results.json',
                        help='JSON samples list; ImageId order defines the sample selection')
    parser.add_argument('--log', type=Path,
                        default=HERE.parents[1] / 'fpga/vivado/build/pc_uart_sender/events.jsonl')
    args = parser.parse_args()
    sender = Sender(args)
    # UART carries only control events. TIFF bytes use the existing HTTP interface.
    with serial.Serial(args.com, args.baud, timeout=.5) as uart:
        sender.log('monitor_ready', com=args.com, baud=args.baud,
                   host=args.host, inflight=args.inflight,
                   sample_count=len(sender.image_ids), image_dir=str(args.image_dir))
        state = sender.status()
        if state['phase'] == 'waiting':
            sender.event({'event': 'start', 'session_id': state['session_id'],
                          'sample_count': state['sample_count'], 'received_count': state['received_count']})
        try:
            while True:
                line = uart.readline().decode('ascii', errors='replace').strip()
                if line.startswith(PREFIX):
                    try:
                        event = json.loads(line[len(PREFIX):])
                    except json.JSONDecodeError:
                        sender.log('uart_invalid', line=line)
                        continue
                    sender.event(event)
        except KeyboardInterrupt:
            sender.stop.set()
            if sender.worker:
                sender.worker.join()
            sender.log('monitor_stopped')


if __name__ == '__main__':
    main()
