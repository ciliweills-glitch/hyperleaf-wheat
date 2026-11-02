"""原始 TIFF 服务：一个 PS 准备 worker 与一个独占 DMA/PL 的推理 worker。"""

import argparse
import json
import multiprocessing
import os
import threading
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from multiprocessing.shared_memory import SharedMemory
from pathlib import Path

import numpy as np

from pynq_cnn import CNNOverlay
from tiff_int16 import TIFFInt16Preprocessor, TIFFRawPreprocessor


def init_prepare(band_mean, band_std, scale_in, pl_preprocess=False):
    global preprocessor
    preprocessor = TIFFRawPreprocessor() if pl_preprocess else TIFFInt16Preprocessor(band_mean, band_std, scale_in)


def prepare_tiff(data, output=None, include_spectrum=False):
    x, stages = preprocessor.prepare(data, output, include_spectrum)
    stages['prepare_pid'] = os.getpid()
    return x, stages


def prepare_shared(input_name, input_size, output_name, include_spectrum=False):
    source = SharedMemory(name=input_name)
    target = SharedMemory(name=output_name)
    try:
        output = np.ndarray((205, 48, 352), dtype=preprocessor.output_dtype, buffer=target.buf)
        output, stages = prepare_tiff(source.buf[:input_size], output, include_spectrum)
        stages['t_output_share'] = 0.0  # 准备直接写共享区，无整图输出复制。
        del output
        return stages
    finally:
        source.close()
        target.close()


class TIFFPipeline:
    def __init__(self, cnn, band_mean, band_std, scale_in, inflight=3,
                 prep_executor='process', pl_preprocess=False):
        self.cnn = cnn
        self.band_mean = band_mean
        self.band_std = band_std
        self.scale_in = scale_in
        self.inflight = inflight
        self.prep_executor = prep_executor
        self.pl_preprocess = pl_preprocess
        self.input_dtype = np.uint16 if pl_preprocess else np.int16
        self.preparation_method = 'uint16_raw_pl_q28' if pl_preprocess else 'uint16_exact_lut'
        self.slots = threading.BoundedSemaphore(inflight)
        if prep_executor == 'process':
            self.prep = ProcessPoolExecutor(
                max_workers=1, mp_context=multiprocessing.get_context('spawn'),
                initializer=init_prepare, initargs=(band_mean, band_std, scale_in, pl_preprocess))
        else:
            self.prep = ThreadPoolExecutor(max_workers=1, thread_name_prefix='prepare',
                                           initializer=init_prepare,
                                           initargs=(band_mean, band_std, scale_in, pl_preprocess))
        self.infer = ThreadPoolExecutor(max_workers=1, thread_name_prefix='overlay')

    @contextmanager
    def prepared(self, data, include_spectrum=False):
        if self.prep_executor == 'thread':
            yield self.prep.submit(prepare_tiff, data, None, include_spectrum).result()
            return
        # 进程队列只传名称/尺度/计时；TIFF 与 16-bit 张量留在共享内存。
        source = SharedMemory(create=True, size=len(data))
        target = SharedMemory(create=True, size=205*48*352*2)
        try:
            copied = time.monotonic()
            source.buf[:len(data)] = data
            input_share = time.monotonic()-copied
            stages = self.prep.submit(prepare_shared, source.name, len(data),
                                     target.name, include_spectrum).result()
            stages['t_input_share'] = input_share
            x = np.ndarray((205, 48, 352), dtype=self.input_dtype, buffer=target.buf)
            yield x, stages
            del x
        finally:
            source.close()
            source.unlink()
            target.close()
            target.unlink()

    def predict(self, x):
        started = time.monotonic()
        prediction = self.cnn.predict_raw if self.pl_preprocess else self.cnn.predict_int16
        result = prediction(x)
        finished = time.monotonic()
        return dict(**result, predict_started_at=started,
                    predict_finished_at=finished, t_predict=finished-started,
                    predict_pid=os.getpid())

    def health(self):
        return dict(version=hex(self.cnn.ip.read(0x28)),
                    completions=self.cnn.ip.read(0x2c),
                    status=hex(self.cnn.ip.read(0x24)),
                    dma_mm2s=hex(self.cnn.dma.mmio.read(0x04)),
                    dma_s2mm=hex(self.cnn.dma.mmio.read(0x34)))

    def close(self):
        self.prep.shutdown(wait=True)
        self.infer.shutdown(wait=True)


def make_handler(pipeline, model):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def reply(self, result):
            body = json.dumps(result).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Image-Id, X-Spectrum')
            self.send_header('Content-Length', '0')
            self.end_headers()

        def do_GET(self):
            if self.path != '/health':
                self.send_error(404)
                return
            health = pipeline.infer.submit(pipeline.health).result()
            self.reply(dict(model=model, pipeline=pipeline.inflight > 1,
                            inflight=pipeline.inflight,
                            preparation_method=pipeline.preparation_method,
                            prep_executor=pipeline.prep_executor, **health))

        def do_POST(self):
            if self.path != '/predict':
                self.send_error(404)
                return
            if self.headers.get('Content-Length') is None:
                self.send_error(411)
                return
            try:
                length = int(self.headers['Content-Length'])
            except ValueError:
                self.send_error(400, 'Invalid Content-Length')
                return
            if not 0 < length <= 64 * 1024 * 1024:
                self.send_error(413)
                return
            tic = time.monotonic()
            # 在读入 TIFF 之前占用槽位，限制驻留图片数量。
            with pipeline.slots:
                admitted = time.monotonic()
                data = self.rfile.read(length)
                received = time.monotonic()
                with pipeline.prepared(data, self.headers.get('X-Spectrum') == 'mean') as (x, stages):
                    del data
                    result = pipeline.infer.submit(pipeline.predict, x).result()
                    del x
                self.reply(dict(model=model, pipeline=pipeline.inflight > 1,
                                ImageId=self.headers.get('X-Image-Id'),
                                **result, **stages, request_received_at=tic,
                                t_admission_wait=admitted-tic,
                                t_receive=received-admitted,
                                t_prepare_wait=stages['prepare_started_at']-received,
                                t_prepare=stages['prepare_finished_at']-stages['prepare_started_at'],
                                t_queue_wait=result['predict_started_at']-stages['prepare_finished_at'],
                                t_server=result['predict_finished_at']-tic))

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--bit', type=Path, required=True)
    parser.add_argument('--pack', type=Path, required=True)
    parser.add_argument('--norm', type=Path, required=True)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--inflight', type=int, choices=(1, 2, 3), default=3,
                        help='1: 串行对照；2/3: PS 准备与推理重叠')
    parser.add_argument('--prep-executor', choices=('process', 'thread'), default='process')
    parser.add_argument('--reuse-overlay', action='store_true',
                        help='复用已加载的同名 bit/hwh，使用 download=False')
    parser.add_argument('--pl-preprocess', action='store_true', help='main v1.4: PS only decodes TIFF and computes full-band mask')
    args = parser.parse_args()
    norm = json.loads(args.norm.read_text())
    band_mean = np.asarray(norm['band_mean'], dtype=np.float32)
    band_std = np.asarray(norm['band_std'], dtype=np.float32)
    scale_in = np.float32(json.loads((args.pack / 'scales.json').read_text())['in'])
    cnn = CNNOverlay(args.bit, args.pack,
                     download=not args.reuse_overlay, pl_preprocess=args.pl_preprocess)
    pipeline = TIFFPipeline(cnn, band_mean, band_std, scale_in,
                            args.inflight, args.prep_executor, args.pl_preprocess)
    pipeline.prep.submit(os.getpid).result()  # 子进程启动计入服务初始化，不混入单片时间。
    handler = make_handler(pipeline, 'main')
    try:
        with ThreadingHTTPServer((args.host, args.port), handler) as server:
            print(f'main listening on {args.host}:{args.port}; '
                  f'inflight={args.inflight}, prepare={args.prep_executor}/1, overlay=1',
                  flush=True)
            server.serve_forever()
    finally:
        pipeline.close()
        cnn.close()


if __name__ == '__main__':
    main()
