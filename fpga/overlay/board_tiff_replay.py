"""Replay the ten frozen HyperLeaf TIFFs through the AX7020 INT16 Overlay."""
import argparse
import csv
import json
import time
from pathlib import Path

import numpy as np

from pynq_cnn import CNNOverlay, tiff_to_int16
from tiff_int16 import TIFFRawPreprocessor


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--bit', type=Path, required=True)
    parser.add_argument('--pack', type=Path, required=True)
    parser.add_argument('--image-dir', type=Path, required=True)
    parser.add_argument('--norm', type=Path, required=True)
    parser.add_argument('--reference', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--pl-preprocess', action='store_true', help='main v1.4 raw uint16/Q28 input')
    parser.add_argument('--reuse-overlay', action='store_true')
    parser.add_argument('--save-first-layers', action='store_true', help='save the first sample layer outputs and GAP')
    args = parser.parse_args()

    norm = json.loads(args.norm.read_text())
    band_mean = np.asarray(norm['band_mean'], dtype=np.float32)
    band_std = np.asarray(norm['band_std'], dtype=np.float32)
    scale_in = np.float32(json.loads((args.pack / 'scales.json').read_text())['in'])
    with args.reference.open(newline='') as source:
        references = list(csv.DictReader(source))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    cnn = CNNOverlay(args.bit, args.pack, download=not args.reuse_overlay,
                     pl_preprocess=args.pl_preprocess)
    raw_prepare = TIFFRawPreprocessor() if args.pl_preprocess else None
    with args.out.open('w', newline='') as target:
        fields = ['ImageId', 'PhiPS2', 'integer_F48', 'abs_diff',
                  't_tiff_read', 't_preprocess', 't_quantize', 't_predict', 't_total', 'preparation_method',
                  'commands', 'status', 'dma_mm2s', 'dma_s2mm']
        writer = csv.DictWriter(target, fieldnames=fields)
        writer.writeheader()
        for index, reference in enumerate(references):
            image_id = reference['ImageId']
            tic = time.monotonic()
            path = args.image_dir / f'{image_id}.tiff'
            if args.pl_preprocess:
                x, stages = raw_prepare.prepare_file(path)
            else:
                x, stages = tiff_to_int16(path, band_mean, band_std, scale_in)
            output_dir = args.out.parent / 'layers' / image_id if args.save_first_layers and index == 0 else None
            if output_dir is not None:
                output_dir.mkdir(parents=True, exist_ok=True)
            before = cnn.ip.read(0x2c)
            t_predict = time.monotonic()
            prediction = cnn.predict_raw(x, output_dir) if args.pl_preprocess else cnn.predict_int16(x, output_dir)
            finished = time.monotonic()
            actual = prediction['PhiPS2']
            expected = float(reference['integer_F48'])
            row = dict(ImageId=image_id, PhiPS2=actual, integer_F48=expected,
                       abs_diff=abs(actual-expected),
                       **{key: stages[key] for key in ('t_tiff_read', 't_preprocess', 't_quantize')},
                       preparation_method=stages.get('preparation_method', 'fp32_reference_ps'),
                       t_predict=finished-t_predict, t_total=finished-tic,
                       commands=(cnn.ip.read(0x2c)-before) & 0xffff,
                       status=hex(cnn.ip.read(0x24)),
                       dma_mm2s=hex(cnn.dma.mmio.read(0x04)),
                       dma_s2mm=hex(cnn.dma.mmio.read(0x34)))
            writer.writerow(row)
            target.flush()
            print(f'{image_id}: PhiPS2={actual:.12f} diff={row["abs_diff"]:.3g} '
                  f'total={row["t_total"]:.3f}s', flush=True)
    cnn.close()


if __name__ == '__main__':
    main()
