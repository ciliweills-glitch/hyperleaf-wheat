"""PYNQ 3.1.2 driver: batched-RUN INT16 RTL (3x3), PS GAP/FP32 head.

v3 protocol: one RUN per tile sweeps all K output-channel pairs. words = total
weight words (K*(18*groups+8)) streamed on MM2S while results stream on S2MM;
K rides in pool[31:20].
"""
import argparse
import json
import time
from pathlib import Path
import numpy as np
from cnn_contract import (PACK, POOLS, TILE_H, TILE_W, load_layer, activation_words,
                          activation_words_into, raw_activation_words_into, FP32Head,
                          output_values, finish_phips2, weight_words)

MAX_PAIRS = 80            # conv4: 160 channels
WEIGHT_WORDS = 1862       # 18*103 + 8, worst case 3x3
MAX_RUN_WORDS = MAX_PAIRS * (TILE_H*TILE_W)  # worst-case RUN output words


class CNNOverlay:
    def __init__(self, bitfile, pack=PACK, download=True, pl_preprocess=False):
        from pynq import Overlay, allocate
        self.overlay = Overlay(str(bitfile), download=download)
        self.ip = self.overlay.cnn_axis_accel_0
        self.dma = self.overlay.axi_dma_0
        self.pack = Path(pack)
        self.pl_preprocess = pl_preprocess
        self.pools = POOLS
        self.head = FP32Head(self.pack)
        version = self.ip.read(0x28)
        if version not in (0x00010003, 0x00010004):
            raise ValueError(f'Expected CNN interface 1.3/1.4, got {version:#x}; use the CNN bit/hwh pair')
        if pl_preprocess and version != 0x00010004:
            raise ValueError('PL preprocessing requires main model and interface 1.4 bit/hwh')
        self.tx = allocate((7+103*(TILE_H+2)*(TILE_W+2),), dtype=np.uint32)
        self.txw = allocate((MAX_PAIRS*WEIGHT_WORDS,), dtype=np.uint32)
        self.rx = allocate((MAX_PAIRS*(TILE_H*TILE_W),), dtype=np.uint32)
        self.layers = [load_layer(i, self.pack) for i in range(len(self.pools))]
        self.weights_cat = []
        for i, (w, sq, bq) in enumerate(self.layers):
            words = [weight_words(w, sq, bq, o) for o in range(0, w.shape[0], 2)]
            self.weights_cat.append(np.concatenate(words))

    def wait_done(self):
        deadline = time.monotonic()+5
        while not self.ip.read(0x00) & 2:
            if time.monotonic() > deadline:
                raise TimeoutError(f'CNN command timeout, status={self.ip.read(0x24):#x}')
        status = self.ip.read(0x24)
        if status & 2:
            raise RuntimeError('CNN received a packet with inconsistent words/TLAST/TKEEP')

    def wait_cmpl(self, target):
        deadline = time.monotonic()+5
        while True:
            cur = self.ip.read(0x2c)
            if ((cur-target) & 0xffff) < 0x8000:
                return
            if time.monotonic() > deadline:
                raise TimeoutError(f'CNN completion timeout, cmpl={cur:#x} target={target:#x}')

    def wait_dma_idle(self, status_offset):
        deadline = time.monotonic()+5
        while True:
            st = self.dma.mmio.read(status_offset)
            if st & 2:
                if st & 0x70:
                    raise RuntimeError(f'DMA error, DMASR={st:#x}')
                return
            if time.monotonic() > deadline:
                raise TimeoutError(f'DMA idle timeout, DMASR={st:#x}')

    def load(self, command, data):
        n = len(data)
        self.tx[:n] = data
        self.load_words(command, n)

    def load_words(self, command, n):
        self.ip.write(0x10, n)  # count of 32-bit words, never bytes or int16 values
        self.ip.write(0x14, command)
        self.ip.write(0x00, 1)
        self.dma.sendchannel.transfer(self.tx[:n], nbytes=n*4)
        self.wait_dma_idle(0x04)  # MM2S_DMASR
        self.wait_done()

    def run_batch(self, wlen, K, th, tw, ph, pw):
        # one RUN sweeps K output-channel pairs for the resident activation tile
        oh, ow = th//ph, tw//pw
        n_words = K*oh*ow
        target = (self.ip.read(0x2c)+1) & 0xffff
        self.ip.write(0x10, wlen)
        self.ip.write(0x14, 3)
        received = self.rx[:n_words]
        self.dma.recvchannel.transfer(received, nbytes=n_words*4)
        self.ip.write(0x00, 1)
        self.dma.sendchannel.transfer(self.txw[:wlen], nbytes=wlen*4)
        self.wait_cmpl(target)
        self.wait_dma_idle(0x04)
        self.wait_dma_idle(0x34)
        # We poll registers rather than calling recvchannel.wait(), so perform
        # its receive-cache synchronization explicitly on the written range.
        received.invalidate()
        flat = np.asarray(received, dtype='<u4').view('<i2')
        return flat.reshape(K, oh*ow, 2).transpose(0, 2, 1).reshape(K, 2, oh, ow)

    def predict_int16(self, x, output_dir=None):
        if x.shape != (205, 48, 352) or x.dtype != np.int16:
            raise ValueError('Input must be the quantized CHW int16 tensor [205,48,352]')
        return self._predict(x, output_dir, raw=False)

    def predict_raw(self, x, output_dir=None):
        if not self.pl_preprocess:
            raise ValueError('Enable pl_preprocess with a main v1.4 Overlay for raw prediction')
        if x.shape != (205, 48, 352) or x.dtype != np.uint16:
            raise ValueError('Raw input must be uint16 [205,48,352]: 204 spectral bands + 0/1 mask')
        return self._predict(x, output_dir, raw=True)

    def _predict(self, x, output_dir, raw):
        timings = []
        for layer, ((w, _, _), pool) in enumerate(zip(self.layers, self.pools)):
            tic = time.monotonic()
            _, h, width = x.shape
            ph, pw = pool
            K = w.shape[0]//2
            y = np.empty((w.shape[0], h//ph, width//pw), dtype=np.int16)
            self.ip.write(0x1c, (x.shape[0]+1)//2)
            self.ip.write(0x20, ph | pw << 8 | (K << 20))
            self.txw[:len(self.weights_cat[layer])] = self.weights_cat[layer]
            wlen = len(self.weights_cat[layer])
            for row in range(0, h, TILE_H):
                for col in range(0, width, TILE_W):
                    th, tw = min(TILE_H, h-row), min(TILE_W, width-col)
                    self.ip.write(0x18, tw | th << 16)
                    if raw and layer == 0:
                        n = raw_activation_words_into(x, row, col, th, tw, self.tx)
                        self.load_words(4, n)
                    else:
                        n = activation_words_into(x, row, col, th, tw, self.tx)
                        self.load_words(1, n)
                    tiles = self.run_batch(wlen, K, th, tw, ph, pw)
                    y.reshape(K, 2, h//ph, width//pw)[:, :, row//ph:(row+th)//ph,
                                                      col//pw:(col+tw)//pw] = tiles
            x = y
            timings.append(time.monotonic()-tic)
            print(f'PL conv{layer+1}+pool {x.shape}: {timings[-1]:.3f}s', flush=True)
            if output_dir is not None:
                np.save(output_dir / f'layer{layer+1}_output.npy', x)
        gap, phips2 = self.head(x)
        if output_dir is not None:
            np.save(output_dir / 'gap.int16.npy', gap)
        return dict(PhiPS2=phips2, layer_seconds=timings)

    def close(self):
        self.tx.freebuffer()
        self.txw.freebuffer()
        self.rx.freebuffer()


def tiff_to_int16(path, band_mean, band_std, scale_in):
    from preprocess import read_cube_chw, build_205_tensor
    tic = time.monotonic()
    cube = read_cube_chw(path)
    t_read = time.monotonic()
    x_fp32 = build_205_tensor(cube, band_mean, band_std)
    t_preprocess = time.monotonic()
    x = np.clip(np.rint(x_fp32 / scale_in), -32767, 32767).astype(np.int16)
    t_quantize = time.monotonic()
    return x, dict(t_tiff_read=t_read-tic,
                   t_preprocess=t_preprocess-t_read,
                   t_quantize=t_quantize-t_preprocess)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--bit', type=Path, required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--input', type=Path, help='preprocessed input.int16.npy')
    source.add_argument('--tiff', type=Path, help='raw HyperLeaf TIFF on the board filesystem')
    parser.add_argument('--norm', type=Path, help='frozen normalization.json for --tiff')
    parser.add_argument('--pack', type=Path)
    parser.add_argument('--out', type=Path, default=Path('cnn_result'))
    parser.add_argument('--save-layers', action='store_true')
    parser.add_argument('--pl-preprocess', action='store_true', help='main v1.4: PS TIFF/mask, PL Q28 standardization/quantization')
    parser.add_argument('--reuse-overlay', action='store_true')
    args = parser.parse_args()
    if args.tiff is not None and args.norm is None:
        parser.error('--norm is required with --tiff')
    if args.pl_preprocess and args.tiff is None:
        parser.error('--pl-preprocess requires --tiff and the main model')
    pack = args.pack or PACK
    args.out.mkdir(parents=True, exist_ok=True)
    if args.tiff is not None:
        norm = json.loads(args.norm.read_text())
        band_mean = np.asarray(norm['band_mean'], dtype=np.float32)
        band_std = np.asarray(norm['band_std'], dtype=np.float32)
        scale_in = np.float32(json.loads((pack / 'scales.json').read_text())['in'])
    cnn = CNNOverlay(args.bit, pack, download=not args.reuse_overlay,
                     pl_preprocess=args.pl_preprocess)
    if args.tiff is None:
        result = cnn.predict_int16(np.load(args.input), args.out)
    else:
        tic = time.monotonic()
        if args.pl_preprocess:
            from tiff_int16 import TIFFRawPreprocessor
            x, stages = TIFFRawPreprocessor().prepare_file(args.tiff)
        else:
            x, stages = tiff_to_int16(args.tiff, band_mean, band_std, scale_in)
        t_predict = time.monotonic()
        prediction = cnn.predict_raw if args.pl_preprocess else cnn.predict_int16
        result = prediction(x, args.out if args.save_layers else None)
        t_total = time.monotonic()
        result.update(stages)
        result.update(t_predict=t_total-t_predict, t_total=t_total-tic)
    cnn.close()
    (args.out / 'result.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
