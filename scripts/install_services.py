"""Install service definitions for this expanded project; activation is explicit."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    for template in (ROOT / 'board/services').glob('*.service.in'):
        text = template.read_text().replace('@PROJECT_ROOT@', str(ROOT))
        (Path('/etc/systemd/system') / template.name.removesuffix('.in')).write_text(text)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    print('Installed four service definitions. Start hyperleaf-display only after approving the overlay load.')


if __name__ == '__main__':
    main()
