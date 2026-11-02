"""Recompute four-layer integer/F48 outputs against the saved 2026-10-27 board run."""
import csv
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'fpga/overlay'))
from cnn_contract import PACK, POOLS, integer_conv, maxpool, load_layer, finish_phips2


def main():
    saved = ROOT / 'data/reference/00001'
    output = ROOT / 'results/integer_board_00001'
    output.mkdir(parents=True, exist_ok=True)
    x = np.load(PACK / '00001/input.int16.npy')
    rows = []
    for i, pool in enumerate(POOLS):
        w, scale, bias = load_layer(i)
        x, _ = integer_conv(x, w, scale, bias)
        x = maxpool(x, pool)
        board = np.load(saved/f'layer{i+1}_output.npy')
        delta = np.abs(x.astype(np.int64)-board.astype(np.int64))
        rows.append(dict(layer=f'conv{i+1}', elements=x.size,
                         mismatches=int(np.count_nonzero(delta)), max_lsb=int(delta.max())))
        np.save(output/f'layer{i+1}_integer.npy', x)
    gap, value = finish_phips2(x)
    delta = np.abs(gap.astype(np.int64)-np.load(saved / 'gap.int16.npy').astype(np.int64))
    rows.append(dict(layer='gap', elements=gap.size,
                     mismatches=int(np.count_nonzero(delta)), max_lsb=int(delta.max())))
    with (ROOT / 'data/reference/ax7020_10.csv').open(newline='') as stream:
        actual = next(row for row in csv.DictReader(stream) if row['ImageId']=='00001')
    summary = dict(ImageId='00001', model='Original four-layer', integer_F48=value,
                   archived_board=float(actual['PhiPS2']),
                   abs_diff=abs(value-float(actual['PhiPS2'])), layers=rows,
                   reference='saved board arrays from 2026-10-27')
    with (output / 'layers.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (output / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps(summary, indent=2))
    assert all(row['mismatches']==0 for row in rows), 'Saved board layers differ from integer reference'
    assert summary['abs_diff'] <= 1e-7, 'Saved FP32 head differs from the reference'


if __name__ == '__main__':
    main()
