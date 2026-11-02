"""Replay the ten included TIFFs on the CPU using the Original integer/F48 reference."""
import csv
import json
from pathlib import Path
import sys
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'fpga/overlay'))
sys.path.insert(0, str(ROOT / 'model/original_2dcnnmask'))
from cnn_contract import PACK, POOLS, integer_conv, load_layer, maxpool, finish_phips2
from tiff_int16 import TIFFInt16Preprocessor
from preprocess import load_image_tensor


def main():
    norm = json.loads((ROOT / 'model/original_2dcnnmask/run_metadata/normalization.json').read_text())
    mean, std = (np.asarray(norm[key], np.float32) for key in ('band_mean', 'band_std'))
    scale = np.float32(json.loads((PACK / 'scales.json').read_text())['in'])
    prepare = TIFFInt16Preprocessor(mean, std, scale)
    layers = [load_layer(i) for i in range(4)]
    with (ROOT / 'data/integer_chain_10samples.csv').open(newline='') as stream:
        references = list(csv.DictReader(stream))
    output = ROOT / 'results/local_tiff_10'
    output.mkdir(parents=True, exist_ok=True)
    rows = []
    for reference in references:
        started = time.monotonic()
        image_id = reference['ImageId']
        path = ROOT / 'images' / (image_id + '.tiff')
        x, _ = prepare.prepare(path.read_bytes())
        oracle = np.clip(np.rint(load_image_tensor(path, mean, std) / scale), -32767, 32767).astype(np.int16)
        delta = np.abs(x.astype(np.int32) - oracle.astype(np.int32))
        mismatches, max_lsb = int(np.count_nonzero(delta)), int(delta.max())
        assert mismatches == 0, f'{image_id}: LUT preprocessing differs from FP32 preprocessing'
        for (w, sq, bq), pool in zip(layers, POOLS):
            x, _ = integer_conv(x, w, sq, bq)
            x = maxpool(x, pool)
        _, actual = finish_phips2(x)
        expected = float(reference['integer_F48'])
        row = dict(ImageId=image_id, PhiPS2=actual, integer_F48=expected,
                   abs_diff=abs(actual - expected), input_mismatches=mismatches,
                   input_max_lsb=max_lsb, local_seconds=time.monotonic() - started)
        rows.append(row)
        print(f'{image_id}: diff={row["abs_diff"]:.3g}, input_max_lsb={max_lsb}', flush=True)
        assert row['abs_diff'] <= 1e-7, f'{image_id}: prediction differs from integer reference'
    with (output / 'predictions.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary = dict(model='Original four-layer', samples=len(rows),
                   max_prediction_abs_diff=max(row['abs_diff'] for row in rows),
                   input_mismatches=sum(row['input_mismatches'] for row in rows),
                   input_max_lsb=max(row['input_max_lsb'] for row in rows),
                   execution='local CPU: real TIFF -> exact LUT -> integer Conv1-4 -> GAP -> FP32 Head',
                   timing_scope='local verification duration, not board inference performance')
    (output / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
