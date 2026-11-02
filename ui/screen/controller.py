"""AN970 screen-started TIFF session; forwards to the existing CNN service."""
import argparse
import fcntl
import gzip
import json
import mmap
import os
import re
import struct
import threading
import time
import uuid
from http.client import HTTPConnection, HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

from PIL import ImageChops

from views import SpectralView, SLIDER_LEFT, SLIDER_RIGHT
from classification import classify_phips2, LOW_THRESHOLD, HIGH_THRESHOLD

WIDTH, HEIGHT = 800, 480
FRAME_BYTES = WIDTH * HEIGHT * 4
META_BYTES = 80
HEADER_BYTES = 8 + 2*META_BYTES
HERE = Path(__file__).resolve().parent


class Screen:
    def __init__(self, runtime, font, control_uart=None):
        self.runtime = runtime
        runtime.mkdir(parents=True, exist_ok=True)
        (runtime / 'count_cards.argb').write_bytes(gzip.decompress((HERE / 'assets/count_cards.argb.gz').read_bytes()))
        self.lock = threading.RLock()
        self.control_uart = os.open(control_uart, os.O_WRONLY | os.O_NOCTTY) if control_uart else None
        self.state = {'phase': 'idle', 'filename': '', 'image_id': '', 'result': None,
                      'sample_count': 1, 'received_count': 0, 'history': [],
                      'classification_mode': 'phips2_ranges', 'session_id': '',
                      'classification_rule': {'target': 'PhiPS2', 'lower': LOW_THRESHOLD, 'upper': HIGH_THRESHOLD},
                      'progress': [], 'seconds_per_sample': 0.0,
                      'inflight_count': 0,
                      'elapsed_seconds': 0.0,
                      'message': '点击开始检测，电脑自动发送 TIFF'}
        self.view = SpectralView(font)
        self.archive_path = runtime / 'batches.json'
        self.archive = json.loads(self.archive_path.read_text(encoding='utf-8')) if self.archive_path.exists() else []
        for entry in self.archive:
            for row in entry['samples']:
                row['classification'] = classify_phips2(row['PhiPS2'])
        self.batches = [entry['samples'] for entry in self.archive]
        self.state['history'] = [{k: entry[k] for k in ('number', 'count', 'time', 'mode')}
                                 for entry in self.archive]
        self.frame_file = (runtime / 'screen.argb').open('w+b')
        self.frame_file.truncate(HEADER_BYTES + 2 * FRAME_BYTES)
        self.frame = mmap.mmap(self.frame_file.fileno(), HEADER_BYTES + 2 * FRAME_BYTES)
        self.scroll_file = (runtime / 'scroll.argb').open('w+b')
        self.scroll_capacity = 4096
        self.scroll_file.truncate(2*self.scroll_capacity)
        self.scroll_frame = mmap.mmap(self.scroll_file.fileno(), 2*self.scroll_capacity)
        self.scroll_generation = 0
        self.scroll_version = -1
        self.scroll_slots = [None, None]
        self.scroll_publish_bytes = 0
        self.generation = 0
        self.last_canvas = None
        self.last_meta = None
        self.frame_damage = [(0, 0, WIDTH, HEIGHT)]*2
        self.frame_publish_bytes = 0
        self.batch_started = 0
        self.clock_active = False
        self.accepted_count = 0
        self.plan_index = {}
        self.results = {}
        self.scroll_drag = None
        self.history_file = (runtime / 'events.jsonl').open('a', encoding='utf-8', buffering=1)
        self.transition('idle')

    def control_event(self, name):
        event = {'event': name, 'session_id': self.state['session_id'],
                 'sample_count': self.state['sample_count'],
                 'received_count': self.state['received_count']}
        line = 'HYPERLEAF_EVENT ' + json.dumps(event, separators=(',', ':'))
        if self.control_uart is not None:
            os.write(self.control_uart, ('\r\n' + line + '\r\n').encode('ascii'))
        print(line, flush=True)

    def finish_cancel(self):
        for row in self.state['progress']:
            if row['status'] != 'done':
                row['status'] = 'cancelled'
        completed = sum(row['status'] == 'done' for row in self.state['progress'])
        self.state['received_count'] = completed
        self.finish_time()
        self.view.clear()
        self.view.page = -1
        self.view.history_open = False
        self.transition('idle', filename='', image_id='', result=None,
                        received_count=completed,
                        message='本轮检测已取消')

    def reset_progress(self):
        self.state['progress'] = [{'number': i+1, 'image_id': '', 'status': 'pending', 'value': None}
                                  for i in range(self.state['sample_count'])]
        self.state['seconds_per_sample'] = 0.0
        self.state['elapsed_seconds'] = 0.0
        self.view.progress_offset = 0
        self.view.progress_follow = True

    def finish_time(self):
        self.state['elapsed_seconds'] = time.monotonic()-self.batch_started
        self.clock_active = False

    def publish_scroll(self):
        spec = self.view.native_scroll_spec(self.state)
        if spec is None:
            return [0]*15
        kind,(x,y,width,height),content_height,step,offset,limit,bar = spec
        if self.scroll_version != self.view.scroll_version:
            size = width*content_height*4
            if size > self.scroll_capacity:
                self.scroll_frame.close()
                self.scroll_capacity = size
                self.scroll_file.truncate(2*self.scroll_capacity)
                self.scroll_frame = mmap.mmap(self.scroll_file.fileno(),2*self.scroll_capacity)
                self.scroll_slots = [None, None]
            self.scroll_generation += 1
            index = self.scroll_generation & 1
            slot = index*self.scroll_capacity
            layout = (kind, width, content_height, len(self.view.scroll_keys))
            previous = self.scroll_slots[index]
            if previous is None or previous[0] != layout:
                data = self.view.scroll_image.convert('RGBA').tobytes('raw','BGRA')
                self.scroll_frame[slot:slot+size] = data
                self.scroll_publish_bytes += size
            else:
                for row,(old,key) in enumerate(zip(previous[1], self.view.scroll_keys)):
                    if old != key:
                        top = row*step
                        data = self.view.scroll_image.crop((0,top,width,top+step)).convert('RGBA').tobytes('raw','BGRA')
                        start = slot+top*width*4
                        self.scroll_frame[start:start+len(data)] = data
                        self.scroll_publish_bytes += len(data)
            self.scroll_slots[index] = (layout, list(self.view.scroll_keys))
            self.scroll_version = self.view.scroll_version
        return [kind,x,y,width,height,content_height,step,offset,limit,
                self.scroll_capacity,self.scroll_generation,*bar]

    def clock_loop(self):
        while True:
            time.sleep(.2)
            with self.lock:
                if self.clock_active:
                    self.state['elapsed_seconds'] = time.monotonic()-self.batch_started
                    if self.view.page == -1 and not self.view.history_open:
                        self.render(snapshot=False)

    def render(self, snapshot=False):
        self.state.update(page=self.view.page, category=self.view.category,
                          row_offset=self.view.row_offset, selected=self.view.selected)
        canvas, rectangles = self.view.render_updates(self.state)
        fcntl.flock(self.frame_file, fcntl.LOCK_EX)
        try:
            self.publish_frame(canvas, rectangles)
        finally:
            fcntl.flock(self.frame_file, fcntl.LOCK_UN)
        if snapshot:
            canvas.save(self.runtime / 'screen.png')

    def publish_frame(self, canvas, rectangles):
        meta = self.publish_scroll()
        dirty = None
        if rectangles:
            bounds = (min(r[0] for r in rectangles), min(r[1] for r in rectangles),
                      max(r[2] for r in rectangles), max(r[3] for r in rectangles))
            if self.last_canvas is None:
                dirty = bounds
            else:
                changed = ImageChops.difference(canvas.crop(bounds), self.last_canvas.crop(bounds)).getbbox()
                if changed:
                    dirty = (bounds[0]+changed[0], bounds[1]+changed[1],
                             bounds[0]+changed[2], bounds[1]+changed[3])
        count_enabled = (self.view.page == -1 and not self.view.history_open
                         and self.state['phase'] in ('idle', 'done', 'error')
                         and self.state['inflight_count'] == 0)
        if dirty is None and meta == self.last_meta:
            return
        self.last_meta = meta
        if self.last_canvas is None:
            self.last_canvas = canvas.copy()
        elif dirty:
            self.last_canvas.paste(canvas.crop(dirty), dirty[:2])
        if dirty:
            for i, pending in enumerate(self.frame_damage):
                self.frame_damage[i] = dirty if pending is None else (
                    min(dirty[0], pending[0]), min(dirty[1], pending[1]),
                    max(dirty[2], pending[2]), max(dirty[3], pending[3]))
        self.generation += 1
        index = self.generation & 1
        pending = self.frame_damage[index]
        if pending:
            left, top, right, bottom = pending
            pixels = canvas.crop(pending).convert('RGBA').tobytes('raw', 'BGRA')
            row_bytes = (right-left)*4
            offset = HEADER_BYTES + index*FRAME_BYTES
            for row in range(bottom-top):
                start = offset+((top+row)*WIDTH+left)*4
                self.frame[start:start+row_bytes] = pixels[row*row_bytes:(row+1)*row_bytes]
            self.frame_publish_bytes += len(pixels)
            self.frame_damage[index] = None
        left, top, right, bottom = dirty or (0, 0, 0, 0)
        # Spare metadata words carry the delta from the previous generation.
        struct.pack_into('<20I',self.frame,8+index*META_BYTES,
                         self.generation,int(count_enabled),*meta,
                         (left << 16) | top, ((right-left) << 16) | (bottom-top), 0)
        struct.pack_into('<II', self.frame, 0, self.generation, int(count_enabled))

    def transition(self, phase, **updates):
        with self.lock:
            if phase == 'error' and self.clock_active:
                self.finish_time()
            if phase == 'done' and self.state['phase'] != 'done':
                self.view.page = 0
            self.state.update(phase=phase, **updates)
            self.render()
            self.history_file.write(json.dumps({'time': time.time(), **self.state}, ensure_ascii=False) + '\n')
            (self.runtime / 'status.json').write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding='utf-8')
            print('SCREEN_STATE', phase, self.state['image_id'], flush=True)

    def action(self, name, value=0):
        with self.lock:
            phase = self.state['phase']
            if name == 'page':
                self.view.page = value
                self.transition(phase)
            elif name == 'category':
                self.view.category = value
                self.transition(phase)
            elif name == 'scroll':
                self.view.row_offset = value
                self.transition(phase)
            elif name == 'select' and self.view.samples:
                self.view.selected = value
                self.transition(phase)
            elif name == 'count' and phase in ('idle', 'done', 'error') and self.state['inflight_count'] == 0:
                self.state['sample_count'] = value
                self.reset_progress()
                self.transition(phase, sample_count=value)
            elif name == 'start' and phase in ('idle', 'done', 'error') and self.state['inflight_count'] == 0:
                self.view.clear()
                self.view.page = -1
                self.batch_started = time.monotonic()
                self.clock_active = True
                self.accepted_count = 0
                self.plan_index = {}
                self.results = {}
                self.reset_progress()
                self.transition('waiting', filename='', image_id='', result=None, received_count=0,
                                session_id=uuid.uuid4().hex,
                                message='已通知电脑，等待 TIFF 自动传入')
                self.control_event('start')
            elif name == 'cancel' and phase in ('waiting', 'receiving', 'detecting'):
                self.control_event('cancel')
                if self.state['inflight_count'] == 0:
                    self.finish_cancel()
                else:
                    self.transition('cancelling', message='等待在途样本结束后取消')
            elif name in ('clear', 'home') and phase in ('idle', 'done', 'error') and self.state['inflight_count'] == 0:
                self.view.page = -1
                self.view.history_open = False
                self.transition('idle', filename='', image_id='', result=None,
                                message='点击开始检测，电脑自动发送 TIFF')
            elif name == 'history' and phase in ('idle', 'done', 'error') and self.state['inflight_count'] == 0:
                self.view.page = -1
                self.view.history_open = True
                self.transition(phase)
            elif name == 'history_scroll':
                self.view.history_offset = max(0, min(max(0, len(self.batches)-4), self.view.history_offset+value))
                self.transition(phase)
            elif name == 'history_delete' and self.view.history_open and value < len(self.batches):
                self.batches.pop(value)
                self.archive.pop(value)
                self.state['history'].pop(value)
                self.archive_path.write_text(json.dumps(self.archive, ensure_ascii=False), encoding='utf-8')
                self.view.clear()
                self.view.history_offset = min(self.view.history_offset, max(0, len(self.batches)-4))
                self.transition(phase)
            elif name == 'progress_scroll':
                self.view.progress_offset = max(0, min(max(0, len(self.state['progress'])-6),
                                                     self.view.progress_offset+value))
                self.view.progress_follow = False
                self.transition(phase)
            elif name == 'history_select' and value < len(self.batches):
                self.view.samples = self.batches[value]
                self.view.row_offset = self.view.selected = self.view.category = 0
                self.view.page = 0
                self.view.history_open = False
                count = len(self.view.samples)
                self.state['progress'] = [{'number':i+1, 'image_id':row['ImageId'],
                                          'status':'done', 'value':row['PhiPS2']}
                                         for i,row in enumerate(self.view.samples)]
                entry = self.archive[value]
                self.state['elapsed_seconds'] = entry.get('elapsed_seconds', 0.0)
                timings = [row['t_predict'] for row in self.view.samples if 't_predict' in row]
                self.state['seconds_per_sample'] = sum(timings)/len(timings) if timings else 0.0
                self.view.progress_follow = True
                self.transition('done', sample_count=count, received_count=count,
                                image_id=self.view.samples[-1]['ImageId'],
                                result={'PhiPS2': self.view.samples[-1]['PhiPS2']})
            print('SCREEN_ACTION', name, value, 'phase', self.state['phase'], flush=True)

    def touch(self, event):
        with self.lock:
            slider = (self.view.page == -1 and not self.view.history_open
                      and SLIDER_LEFT-12 <= event['x0'] <= SLIDER_RIGHT+12
                      and 279 <= event['y0'] <= 339
                      and self.state['phase'] in ('idle', 'done', 'error'))
            if slider:
                width = SLIDER_RIGHT-SLIDER_LEFT
                value = 1 + round(max(0, min(width, event['x']-SLIDER_LEFT))/width*399)
                if event['drag']:
                    if value != self.state['sample_count']:
                        self.state['sample_count'] = value
                else:
                    self.action('count', value)
                return
            target = self.view.scroll_target(event['x0'], event['y0'], self.state)
            dy, dx = event['y0']-event['y'], event['x0']-event['x']
            if self.scroll_drag or (target and abs(dy) >= 3 and abs(dy) > 1.5*abs(dx)):
                if self.scroll_drag is None:
                    attribute, height, limit = target
                    self.scroll_drag = (attribute, height, limit, getattr(self.view, attribute))
                attribute, height, limit, base = self.scroll_drag
                offset = max(0, min(limit, base+dy/height))
                setattr(self.view, attribute, offset)
                if attribute == 'progress_offset':
                    self.view.progress_follow = False
                if event['drag']:
                    self.render(snapshot=False)
                else:
                    self.scroll_drag = None
                    self.transition(self.state['phase'])
                return
            if event['drag']:
                return
        name, value = self.view.touch_action(event['x'], event['y'], event['x0'], event['y0'])
        self.action(name, value)
        print('SCREEN_TOUCH', event, flush=True)


def make_handler(screen, cnn_host, cnn_port):
    class Handler(BaseHTTPRequestHandler):
        def reply(self, value, status=200, content_type='application/json; charset=utf-8'):
            body = json.dumps(value, ensure_ascii=False).encode() if isinstance(value, dict) else value
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True

        def do_GET(self):
            if self.path == '/status':
                with screen.lock:
                    self.reply(dict(screen.state))
            elif self.path == '/screen.png':
                with screen.lock:
                    screen.render(snapshot=True)
                    self.reply((screen.runtime / 'screen.png').read_bytes(), content_type='image/png')
            else:
                self.send_error(404)

        def do_POST(self):
            if self.path in ('/touch', '/action'):
                if self.client_address[0] != '127.0.0.1':
                    self.reply({'error': '请在板载屏幕上启动检测'}, 403)
                    return
                event = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if self.path == '/touch':
                    screen.touch(event)
                else:
                    screen.action(event['name'], event.get('value', 0))
                self.reply({'phase': screen.state['phase']})
                return
            if self.path == '/plan':
                plan = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                with screen.lock:
                    if (screen.state['phase'] != 'waiting' or plan['session_id'] != screen.state['session_id']
                            or len(plan['image_ids']) != screen.state['sample_count']):
                        self.reply({'error': '本轮检测已结束或样本数量不符'}, 409)
                        return
                    for row, image_id in zip(screen.state['progress'], plan['image_ids']):
                        row['image_id'] = image_id
                    screen.plan_index = {image_id:i for i,image_id in enumerate(plan['image_ids'])}
                    screen.transition('waiting')
                self.reply({'accepted': True})
                return
            if self.path != '/predict':
                self.send_error(404)
                return
            with screen.lock:
                session_id = self.headers.get('X-Session-Id', '')
                if (screen.state['phase'] not in ('waiting', 'receiving', 'detecting')
                        or session_id != screen.state['session_id']):
                    self.reply({'error': '请先在板载屏幕点击“启动检测”'}, 409)
                    return
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                except ValueError:
                    self.reply({'error': '无效的文件长度'}, 400)
                    return
                image_id = self.headers.get('X-Image-Id', '')
                if not 0 < length <= 64 * 1024 * 1024 or not re.fullmatch(r'\d{5}', image_id):
                    self.reply({'error': '请选择有效 TIFF 和五位样本编号'}, 400)
                    return
                if screen.state['inflight_count'] >= 3:
                    self.reply({'error': '已有三个样本在途'}, 429)
                    return
                if screen.plan_index:
                    if image_id not in screen.plan_index:
                        self.reply({'error': '样本编号不在本轮名单中'}, 409)
                        return
                    index = screen.plan_index[image_id]
                else:
                    index = screen.accepted_count
                if index >= screen.state['sample_count'] or screen.state['progress'][index]['status'] != 'pending':
                    self.reply({'error': '样本已提交或超过本轮数量'}, 409)
                    return
                progress_row = screen.state['progress'][index]
                progress_row.update(image_id=image_id, status='running')
                screen.accepted_count += 1
                screen.state['inflight_count'] += 1
                screen.state.update(phase='receiving', image_id=image_id,
                                    filename=Path(unquote(self.headers.get('X-Filename', image_id + '.tiff'))).name,
                                    message='正在接收并检测本轮样本')
            self.connection.settimeout(30)
            started = time.monotonic()
            try:
                data = self.rfile.read(length)
                with screen.lock:
                    forward = screen.state['phase'] not in ('cancelling', 'error')
                    if forward:
                        screen.state['phase'] = 'detecting'
                if forward:
                    conn = HTTPConnection(cnn_host, cnn_port, timeout=30)
                    conn.request('POST', '/predict', data,
                                 {'Content-Type': 'image/tiff', 'X-Image-Id': image_id,
                                  'X-Spectrum': 'mean'})
                    del data
                    response = conn.getresponse()
                    result = json.loads(response.read())
                    conn.close()
                    if response.status != 200:
                        raise ValueError(f'检测服务返回 {response.status}')
                    spectrum = result.pop('spectrum')
                    result['classification'] = classify_phips2(result['PhiPS2'])
                    result['t_screen_session'] = time.monotonic()-started
            except (OSError, HTTPException, ValueError) as error:
                with screen.lock:
                    if screen.state['phase'] not in ('cancelling', 'error'):
                        progress_row['status'] = 'error'
                        screen.transition('error', message=str(error)[:80])
                        screen.control_event('error')
            with screen.lock:
                screen.state['inflight_count'] -= 1
                if screen.state['phase'] == 'cancelling':
                    if screen.state['inflight_count'] == 0:
                        screen.finish_cancel()
                    self.reply({'cancelled': True, 'session_id': session_id}, 409)
                    return
                if screen.state['phase'] == 'error':
                    if progress_row['status'] == 'running':
                        progress_row['status'] = 'cancelled'
                    if screen.state['inflight_count'] == 0:
                        for row in screen.state['progress']:
                            if row['status'] in ('pending', 'running'):
                                row['status'] = 'cancelled'
                        screen.transition('error')
                    self.reply({'error': screen.state['message'], 'session_id': session_id}, 422)
                    return
                screen.results[index] = {'ImageId': image_id, 'PhiPS2': result['PhiPS2'],
                                         'classification': result['classification'], 'spectrum': spectrum,
                                         't_predict': result['t_predict']}
                screen.view.samples = [screen.results[i] for i in sorted(screen.results)]
                count = len(screen.results)
                screen.state['received_count'] = count
                progress_row.update(status='done', value=result['PhiPS2'])
                screen.state['seconds_per_sample'] = sum(row['t_predict'] for row in screen.results.values())/count
                if count == screen.state['sample_count']:
                    screen.finish_time()
                    screen.batches.insert(0, list(screen.view.samples))
                    entry = {'number': max((entry['number'] for entry in screen.archive), default=0)+1, 'count': count,
                             'elapsed_seconds': screen.state['elapsed_seconds'],
                             'seconds_per_sample': screen.state['seconds_per_sample'],
                             'time': time.strftime('%m-%d %H:%M'), 'mode': '板端检测'}
                    screen.state['history'].insert(0, entry)
                    screen.archive.insert(0, {**entry, 'samples': list(screen.view.samples)})
                    screen.archive_path.write_text(json.dumps(screen.archive, ensure_ascii=False), encoding='utf-8')
                    (screen.runtime / 'last_result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
                    screen.transition('done', received_count=count, result=result, message='检测完成')
                    screen.control_event('done')
                else:
                    screen.state.update(phase='detecting' if screen.state['inflight_count'] else 'waiting',
                                        received_count=count, result=result, image_id=image_id,
                                        message=f'已完成{count}个')
            self.reply(result)
    return Handler


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--runtime', type=Path, required=True)
    p.add_argument('--font', type=Path, default=HERE / 'assets/hyperleafsans.ttf')
    p.add_argument('--host', default='0.0.0.0')
    p.add_argument('--port', type=int, default=8766)
    p.add_argument('--cnn-host', default='127.0.0.1')
    p.add_argument('--cnn-port', type=int, default=8765)
    p.add_argument('--control-uart', type=Path, help='Linux UART connected to the PC control monitor')
    args = p.parse_args()
    screen = Screen(args.runtime, args.font, args.control_uart)
    threading.Thread(target=screen.clock_loop, daemon=True).start()
    with ThreadingHTTPServer((args.host, args.port), make_handler(screen, args.cnn_host, args.cnn_port)) as server:
        print('SCREEN_UI_LISTENING', args.port, flush=True)
        server.serve_forever()


if __name__ == '__main__':
    main()
