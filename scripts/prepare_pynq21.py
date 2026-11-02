"""Assemble the recorded PYNQ 2.1 runtime layout without accessing hardware."""
import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    recorded = ROOT / 'board/pynq-z2/pynq21'
    for name in ['model', 'golden', 'images']:
        shutil.copytree(ROOT / name, out / name)
    overlay = out / 'fpga/overlay'
    overlay.mkdir(parents=True)
    for source in (ROOT / 'fpga/overlay').glob('*.py'):
        shutil.copy2(source, overlay / source.name)
    shutil.copy2(recorded / 'preprocess.py', overlay / 'preprocess.py')
    for name in ['run_board_compare.py', 'pynq_legacy_compat.py']:
        shutil.copy2(recorded / name, out / name)
    shutil.copytree(ROOT / 'fpga/overlay/pynq_z2_original4_v14_100mhz', out / 'overlay')
    reference = out / 'reference'
    reference.mkdir()
    shutil.copy2(ROOT / 'data/reference/ax7020_10.csv', reference / 'ax7020_direct_10.csv')
    shutil.copytree(ROOT / 'data/reference/00001', reference / 'ax7020_layers_00001')
    (out / 'vendor').mkdir()
    print(out)


if __name__ == '__main__':
    main()
