"""Real PYNQ-Z2 four-layer test: original arithmetic, legacy runtime adapter only."""
import sys, os, csv, json, time, traceback, platform, argparse
from pathlib import Path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT/'vendor'))
sys.path.insert(0, str(ROOT/'fpga/overlay'))
import numpy as np
import pynq_legacy_compat as compat

def compare_arrays(actual, expected):
    if actual.shape != expected.shape or actual.dtype != expected.dtype:
        raise ValueError('Reference array schema mismatch')
    diff = np.abs(actual.astype(np.int64)-expected.astype(np.int64))
    return dict(shape=list(actual.shape), max_lsb=int(diff.max()),
                mismatches=int(np.count_nonzero(diff)), total_values=int(diff.size),
                exact=bool(np.all(diff==0)))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage', choices=['smoke','full'], required=True)
    parser.add_argument('--confirm-download', action='store_true')
    parser.add_argument('--reuse-overlay', action='store_true')
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    if not args.confirm_download:
        raise PermissionError('Explicit --confirm-download required for board access')
    out = ROOT/'results'/args.out
    out.mkdir(parents=True, exist_ok=False)
    report = dict(stage=args.stage, pass_test=False, board='PYNQ-Z2',
                  python=sys.version, kernel=platform.release(), numpy=np.__version__,
                  board_test=True, architecture='Original four-layer v1.4 + RAW/Q28',
                  samples=[], layers_vs_ax7020_00001={}, raw_vs_int16={},
                  source_ax7020='reference/ax7020_direct_10.csv',
                  numerical_tolerance=1e-6, no_file_integrity_seal=True)
    cnn = None
    try:
        compat.install_numpy_compat()
        compat.install(confirm_download=True)
        from pynq_cnn import CNNOverlay, tiff_to_int16
        from tiff_int16 import TIFFRawPreprocessor
        pack = ROOT/'golden/int16_ptq'
        norm = json.loads((ROOT/'model/original_2dcnnmask/run_metadata/normalization.json').read_text())
        mean, std = np.asarray(norm['band_mean'], np.float32), np.asarray(norm['band_std'], np.float32)
        scale = np.float32(json.loads((pack/'scales.json').read_text())['in'])
        with (ROOT/'reference/ax7020_direct_10.csv').open() as source:
            references = list(csv.DictReader(source))
        expected_ids = ['00001','00002','00014','00022','00034','00047','00048','00059','00064','00070']
        if len(references)!=10 or [r['ImageId'] for r in references] != expected_ids:
            raise ValueError('AX7020 reference does not contain the frozen ordered ten samples')
        selected = references[:1] if args.stage=='smoke' else references
        bit = ROOT/'overlay/pynq_z2_original4_v14_raw_100mhz.bit'
        cnn = CNNOverlay(bit, pack=pack, download=not args.reuse_overlay, pl_preprocess=True)
        report['hardware'] = dict(compat.ENVIRONMENT)
        raw_prepare = TIFFRawPreprocessor()
        for mode in ('int16','raw'):
            for reference in selected:
                ident = reference['ImageId']
                sample_out = out/mode/ident
                sample_out.mkdir(parents=True)
                started = time.monotonic()
                if mode=='raw':
                    x, preparation = raw_prepare.prepare_file(ROOT/'images'/(ident+'.tiff'))
                else:
                    x, preparation = tiff_to_int16(ROOT/'images'/(ident+'.tiff'), mean, std, scale)
                before = int(cnn.ip.read(0x2c)) & 0xffff
                tic = time.monotonic()
                prediction = cnn.predict_raw(x, sample_out) if mode=='raw' else cnn.predict_int16(x, sample_out)
                finished = time.monotonic()
                status = int(cnn.ip.read(0x24))
                dm = int(cnn.dma.mmio.read(4)); ds = int(cnn.dma.mmio.read(0x34))
                count = (int(cnn.ip.read(0x2c))-before) & 0xffff
                actual, other = float(prediction['PhiPS2']), float(reference['PhiPS2'])
                row = dict(mode=mode, ImageId=ident, PhiPS2=actual, ax7020_PhiPS2=other,
                           signed_diff=actual-other, abs_diff=abs(actual-other),
                           integer_F48=float(reference['integer_F48']),
                           exact_prediction=(actual==other), close_prediction=(abs(actual-other)<=1e-6),
                           commands=count, status=status, dma_mm2s=dm, dma_s2mm=ds,
                           t_predict=finished-tic, t_total=finished-started,
                           ax7020_t_predict=float(reference['t_predict']),
                           layer_seconds=prediction['layer_seconds'])
                report['samples'].append(row)
                print('SAMPLE_COMPLETE', json.dumps(row), flush=True)
                if count != 316 or status != 0 or dm & 0x70 or ds & 0x70:
                    raise RuntimeError('Command/frame/DMA validation failed for '+ident)
                if not row['close_prediction']:
                    raise AssertionError('Prediction differs from AX7020 by more than 1e-6 for '+ident)
                if ident=='00001':
                    checks = {}
                    for name in ['layer%d_output.npy'%i for i in range(1,5)]+['gap.int16.npy']:
                        check = compare_arrays(np.load(str(sample_out/name)), np.load(str(ROOT/'reference/ax7020_layers_00001'/name)))
                        checks[name] = check
                        if not check['exact']: raise AssertionError('AX7020 integer feature mismatch: '+name)
                    report['layers_vs_ax7020_00001'][mode] = checks
                if mode=='raw':
                    checks = {}
                    for name in ['layer%d_output.npy'%i for i in range(1,5)]+['gap.int16.npy']:
                        check = compare_arrays(np.load(str(sample_out/name)), np.load(str(out/'int16'/ident/name)))
                        checks[name] = check
                        if not check['exact']: raise AssertionError('RAW vs INT16 feature mismatch: '+ident+'/'+name)
                    report['raw_vs_int16'][ident] = checks
        report['fclk_after'] = compat.clock_snapshot()
        report['pass_test'] = True
        report['summary'] = dict(samples_per_mode=len(selected), modes=2,
             max_abs_diff_vs_ax7020=max(r['abs_diff'] for r in report['samples']),
             mean_abs_diff_vs_ax7020=sum(r['abs_diff'] for r in report['samples'])/len(report['samples']),
             exact_predictions=sum(r['exact_prediction'] for r in report['samples']),
             close_predictions=sum(r['close_prediction'] for r in report['samples']),
             host_timing_not_pure_compute=True)
    except BaseException:
        report['failure'] = traceback.format_exc()
        print(report['failure'], flush=True)
    finally:
        halted = [dma.stop_safely(reset=not report['pass_test']) for dma in compat.DMA_OBJECTS]
        safe = all(halted)
        report['dma_halted_before_free'] = safe
        if not safe: report['pass_test'] = False
        if safe and cnn is not None: cnn.close()
        report['hardware'] = dict(compat.ENVIRONMENT)
        (out/'result.json').write_text(json.dumps(report, indent=2))
        rows = report['samples']
        if rows:
            with (out/'comparison.csv').open('w', newline='') as target:
                fields = [k for k in rows[0] if k!='layer_seconds']
                writer = csv.DictWriter(target, fieldnames=fields)
                writer.writeheader();writer.writerows([{k:r[k] for k in fields} for r in rows])
        print('BOARD_RESULT', str(out/'result.json'), 'PASS', report['pass_test'], flush=True)
        if not safe:
            print('DMA_RESET_NOT_CONFIRMED: allocations retained; operator recovery required', flush=True)
            while True: time.sleep(30)
    if not report['pass_test']: raise SystemExit(1)

if __name__=='__main__': main()
