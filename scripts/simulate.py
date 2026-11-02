"""Run the released-core transaction TB using the archived 15-case vectors."""
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ('pe_array.v', 'axil_regs.v', 'requant_pipe.v', 'cnn_tile_core.v',
           'standardize_mac.v', 'tiff_preprocess_lane.v', 'raw_tile_preprocess.v', 'cnn_axis_accel.v')


def main():
    work = ROOT / 'build/simulation'
    work.mkdir(parents=True, exist_ok=True)
    for name in SOURCES:
        shutil.copy2(ROOT / 'fpga/rtl'/name, work/name)
    shutil.copytree(ROOT / 'fpga/rtl/preprocess_rom', work / 'preprocess_rom', dirs_exist_ok=True)
    shutil.copy2(ROOT / 'fpga/rtl/tb_release.sv', work / 'tb.sv')
    for name in ('vectors.txt', 'mac.txt', 'normalized.txt'):
        shutil.copy2(ROOT / 'sim/fixtures'/name, work/name)
    (work / 'vectors').mkdir(exist_ok=True)
    (work / 'vectors/run.cfg').write_text('vectors.txt\nmac.txt\nout.hex\n')
    (work / 'run.tcl').write_text('run all\nquit\n')
    suffix = '.bat' if os.name=='nt' else ''
    commands = [('xvlog', '-sv', *SOURCES, 'tb.sv'),
                ('xelab', 'tb_cnn_axis_accel', '-s', 'check', '-mt', '4'),
                ('xsim', 'check', '-tclbatch', 'run.tcl')]
    for name, *args in commands:
        with (work/(name+'.stdout.log')).open('w') as stream:
            subprocess.run([name+suffix, *args], cwd=work, stdout=stream, stderr=subprocess.STDOUT, check=True)
    log = (work / 'xsim.stdout.log').read_text()
    assert 'TEST_PASS overlay' in log, log[-3000:]
    print(next(line for line in log.splitlines() if 'TEST_PASS overlay' in line))


if __name__ == '__main__':
    main()
