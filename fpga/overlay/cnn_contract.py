"""Shared host packing for the INT16 RTL IP; no torch or PYNQ dependency."""
import json
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
PACK = ROOT / 'golden/int16_ptq'
SHAPES = [(32, 205, 3, 3), (64, 32, 3, 3), (128, 64, 3, 3), (160, 128, 3, 3)]
SCALE_IN = ['in', 'c2', 'c3', 'c4']
SCALE_OUT = ['c2', 'c3', 'c4', 'c4out']
POOLS = [(2, 4), (2, 2), (2, 2), (1, 1)]
TILE_H, TILE_W = 4, 32


def load_layer(layer, pack=PACK):
    scales = json.loads((pack / 'scales.json').read_text())
    name = f'conv{layer+1}'
    w = np.fromfile(pack / f'weights/{name}_w.int16.bin', '<i2').reshape(SHAPES[layer])
    sw = np.fromfile(pack / f'weights/{name}_w_scale.f32.bin', '<f4')
    b = np.fromfile(pack / f'weights/{name}_b.f32.bin', '<f4')
    sq = np.rint(scales[SCALE_IN[layer]] * sw.astype(np.float64) / scales[SCALE_OUT[layer]] * 2**48).astype(np.int64)
    bq = np.rint(b.astype(np.float64) / scales[SCALE_OUT[layer]] * 2**48).astype(np.int64)
    return w, sq, bq


def weight_words(w, sq, bq, output_channel):
    cin = w.shape[1]
    wp = np.pad(w[output_channel:output_channel+2], ((0, 0), (0, cin % 2), (0, 0), (0, 0)))
    g = (cin+1)//2
    packed = wp.reshape(2, g, 2, 9).transpose(1, 0, 2, 3).copy().ravel().view('<u4')
    coeff = []
    for o in range(output_channel, output_channel+2):
        for v in (int(sq[o]), int(bq[o])):
            coeff.extend([v & 0xffffffff, (v >> 32) & 0xffffffff])
    return np.concatenate([packed, np.array(coeff, dtype='<u4')])


def activation_words(x, row, col, height, width):
    cin, h, w = x.shape
    tile = np.zeros((cin+cin%2, height+2, width+2), dtype='<i2')
    y0, y1 = max(0, row-1), min(h, row+height+1)
    x0, x1 = max(0, col-1), min(w, col+width+1)
    tile[:cin, y0-row+1:y1-row+1, x0-col+1:x1-col+1] = x[:, y0:y1, x0:x1]
    return tile.reshape(-1, 2, height+2, width+2).transpose(0, 2, 3, 1).copy().ravel().view('<u4')


def activation_words_into(x, row, col, height, width, buffer):
    """直接填充 DMA 缓冲，保留 group,row,col,{cin1,cin0} 和 halo/tail 语义。"""
    cin, h, w = x.shape
    groups = (cin+1)//2
    n = groups*(height+2)*(width+2)
    tile = buffer[:n].view('<i2').reshape(groups, height+2, width+2, 2)
    tile.fill(0)
    y0, y1 = max(0, row-1), min(h, row+height+1)
    x0, x1 = max(0, col-1), min(w, col+width+1)
    ys, xs = slice(y0-row+1, y1-row+1), slice(x0-col+1, x1-col+1)
    tile[:, ys, xs, 0] = x[::2, y0:y1, x0:x1]
    tile[:cin//2, ys, xs, 1] = x[1::2, y0:y1, x0:x1]
    return n


def output_values(words, height, width):
    return np.asarray(words, dtype='<u4').view('<i2').reshape(height, width, 2).transpose(2, 0, 1).copy()


def raw_activation_words_into(x, row, col, height, width, buffer):
    """x=[204 raw uint16 bands, full-band 0/1 mask]; command 4 mask prefix + pairs."""
    hh, ww = height+2, width+2
    spatial = hh*ww
    prefix = (spatial+31)//32
    n = prefix+103*spatial
    buffer[:n].fill(0)
    tile = buffer[prefix:n].view('<u2').reshape(103, hh, ww, 2)
    _, h, w = x.shape
    y0, y1 = max(0, row-1), min(h, row+height+1)
    x0, x1 = max(0, col-1), min(w, col+width+1)
    ys, xs = slice(y0-row+1, y1-row+1), slice(x0-col+1, x1-col+1)
    tile[:102, ys, xs, 0] = x[:204:2, y0:y1, x0:x1]
    tile[:102, ys, xs, 1] = x[1:204:2, y0:y1, x0:x1]
    # Mask/tail group pixels are placeholders; PL creates quantized mask and zero tail.
    mask = np.zeros((hh, ww), dtype=np.uint8)
    mask[ys, xs] = x[204, y0:y1, x0:x1]
    packed = np.packbits(mask.ravel(), bitorder='little')
    buffer[:prefix].view(np.uint8)[:len(packed)] = packed
    return n


def requant_exact(acc, sq, bq):
    """Python integers keep the complete signed 112-bit product and F48 remainder."""
    z = acc.astype(object) * int(sq) + int(bq)
    q = z >> 48
    rem = z & (2**48-1)
    q += (rem > 2**47) | ((rem == 2**47) & ((q & 1) != 0))
    return np.clip(q, 0, 32767).astype(np.int16)


def integer_conv(x, w, sq, bq):
    padded = np.pad(x.astype(np.int64), ((0, 0), (1, 1), (1, 1)))
    windows = np.lib.stride_tricks.sliding_window_view(padded, (3, 3), axis=(1, 2))
    acc = np.einsum('cyxij,ocij->oyx', windows, w.astype(np.int64), optimize=True)
    y = np.stack([requant_exact(a, sc, bi) for a, sc, bi in zip(acc, sq, bq)])
    return y, acc


def maxpool(x, pool):
    ph, pw = pool
    c, h, w = x.shape
    return x.reshape(c, h//ph, ph, w//pw, pw).max(axis=(2, 4))


class FP32Head:
    def __init__(self, pack=PACK, target_mean=0.5497305393218994,
                 target_std=0.1063743606209755):
        self.scales = json.loads((pack / 'scales.json').read_text())
        self.f1w = np.fromfile(pack / 'weights/head_fc1_w.f32.bin', '<f4').reshape(96, 160)
        self.f1b = np.fromfile(pack / 'weights/head_fc1_b.f32.bin', '<f4')
        self.f2w = np.fromfile(pack / 'weights/head_fc2_w.f32.bin', '<f4')
        self.f2b = np.fromfile(pack / 'weights/head_fc2_b.f32.bin', '<f4')[0]
        self.target_mean, self.target_std = target_mean, target_std

    def __call__(self, c4):
        scales = self.scales
        gap = np.rint(c4.astype(np.int64).sum(axis=(1, 2)) *
                      (scales['c4out'] / (c4.shape[1]*c4.shape[2]*scales['head'])))
        gap = np.clip(gap, -32767, 32767).astype(np.int16)
        hidden = np.maximum(self.f1w @ (gap.astype(np.float32)*np.float32(scales['head']))
                            + self.f1b, 0)
        z = float(self.f2w @ hidden + self.f2b)
        return gap, z*self.target_std + self.target_mean


def finish_phips2(c4, pack=PACK, target_mean=0.5497305393218994,
                  target_std=0.1063743606209755):
    return FP32Head(pack, target_mean, target_std)(c4)
