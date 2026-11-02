"""Regenerate the released integer/F48 transaction fixtures for the four-layer model."""
import argparse
import io
import json
from pathlib import Path
import sys
import numpy as np
import tifffile
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'fpga/overlay'))
from cnn_contract import (PACK, POOLS, activation_words, integer_conv, load_layer,
                          maxpool, raw_activation_words_into, weight_words)
from tiff_int16 import TIFFRawPreprocessor, TIFFInt16Preprocessor

def vectors(directory):
    rng = np.random.default_rng(1015)
    cases = []
    for layer, (height, width) in enumerate(((4, 32), (4, 32), (4, 22), (3, 22))):
        w, sq, bq = load_layer(layer)
        for border in (True, False):
            x = rng.integers(-8192, 8192, (w.shape[1], height+3, width+4), dtype=np.int16)
            cases.append((f'conv{layer+1}_'+('border' if border else 'interior'), x,
                          w, sq, bq, POOLS[layer], height, width, 0 if border else 1, 0 if border else 2, False, None))
    for cin in (1, 2, 31):
        x = rng.integers(-32768, 32768, (cin, 2, 4), dtype=np.int16)
        w = rng.integers(-128, 128, (4, cin, 3, 3), dtype=np.int16)
        sq = np.array([2**42, 2**43, 2**46, 2**47], np.int64)
        bq = np.array([2**47, -2**47, 0, 2**48], np.int64)
        cases.append((f'groups_tail_cin{cin}', x, w, sq, bq, (1, 1), 2, 4, 0, 0, False, None))
    norm = json.loads((ROOT / 'model/original_2dcnnmask/run_metadata/normalization.json').read_text())
    mean, std = (np.asarray(norm[k], np.float32) for k in ('band_mean', 'band_std'))
    scale = np.float32(json.loads((PACK / 'scales.json').read_text())['in'])
    cube = np.zeros((204, 48, 352), np.uint16)
    cube[:, :6, :35] = rng.integers(1, 65536, (204, 6, 35), dtype=np.uint16)
    cube[:, 1, 2] = 0
    stream = io.BytesIO()
    tifffile.imwrite(stream, cube)
    raw, _ = TIFFRawPreprocessor().prepare(stream.getvalue())
    normalized, _ = TIFFInt16Preprocessor(mean, std, scale).prepare(stream.getvalue())
    w, sq, bq = load_layer(0)
    cases.append(('conv1_raw_q28', normalized[:, :5, :33], w, sq, bq, POOLS[0], 4, 32, 0, 0, False, raw))
    metadata = []
    with (directory / 'vectors.txt').open('w') as out, (directory / 'mac.txt').open('w') as mac, (directory / 'normalized.txt').open('w') as normal:
        for tag, (name, x, w, sq, bq, pool, th, tw, row, col, pw, raw) in enumerate(cases):
            y, acc = integer_conv(x, w, sq, bq)
            groups = (x.shape[0]+1)//2
            pairs = w.shape[0]//2
            pool_reg = pool[0] | pool[1]<<8 | pairs<<20
            if raw is not None:
                buffer = np.empty(7+103*6*34, np.uint32)
                n = raw_activation_words_into(raw, row, col, th, tw, buffer)
                payload, cmd = buffer[:n], 4
                normal.writelines(f'{int(v):08x}\n' for v in activation_words(x, row, col, th, tw))
            else:
                payload, cmd = activation_words(x, row, col, th, tw), 1
            out.write(f'{cmd:x} {len(payload):x} {(th<<16)|tw:x} {groups:x} {pool_reg:x} 0 {tag:x}\n')
            out.writelines(f'{int(v):08x}\n' for v in payload)
            weights, expected = [], []
            for o in range(0, w.shape[0], 2):
                weights.append(weight_words(w, sq, bq, o))
                result = maxpool(y[o:o+2, row:row+th, col:col+tw], pool)
                expected.extend(result.transpose(1, 2, 0).copy().ravel().view('<u4'))
                for a0, a1, v0, v1 in zip(*acc[o:o+2, row:row+th, col:col+tw].reshape(2, -1),
                                         *y[o:o+2, row:row+th, col:col+tw].reshape(2, -1)):
                    mac.write(f'{int(a1)&((1<<48)-1):012x}{int(a0)&((1<<48)-1):012x}{int(v1):04x}{int(v0):04x}\n')
            weights = np.concatenate(weights)
            out.write(f'3 {len(weights):x} {(th<<16)|tw:x} {groups:x} {pool_reg:x} {len(expected):x} {tag:x}\n')
            out.writelines(f'{int(v):08x}\n' for v in weights)
            out.writelines(f'{int(v):08x}\n' for v in expected)
            metadata.append(dict(tag=tag, name=name, groups=groups, pairs=pairs, height=th, width=tw,
                                 prepool_values=pairs*2*th*tw, output_words=len(expected)))
        out.write('0 0 0 0 0 0 0\n')
    return metadata


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, default=ROOT / 'build/generated_fixtures')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    cases = vectors(args.out)
    (args.out / 'cases.json').write_text(json.dumps(cases, indent=2))
    print(f'Generated {len(cases)} cases in {args.out}')
