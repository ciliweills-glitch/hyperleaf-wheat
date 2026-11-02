"""Prepare exact count-card glyphs and slider graphics for native touch updates."""
import gzip
from pathlib import Path

from PIL import Image, ImageDraw
from views import SpectralView, TEAL, CARD_BOX, SLIDER_LEFT, SLIDER_RIGHT

HERE = Path(__file__).resolve().parent


def bgra(image):
    return image.convert('RGBA').tobytes('raw', 'BGRA')


def main():
    view = SpectralView(HERE / 'assets/hyperleafsans.ttf')
    state = {'phase': 'idle', 'sample_count': 1, 'history': [], 'classification_mode': 'unassigned'}
    with gzip.open(HERE / 'assets/count_cards.argb.gz', 'wb') as output:
        for count in range(1, 401):
            state['sample_count'] = count
            output.write(bgra(view.render(state).crop(CARD_BOX)))
        strip = Image.new('RGB', (SLIDER_RIGHT-SLIDER_LEFT+27, 30), 'white')
        ImageDraw.Draw(strip).line((13, 14, SLIDER_RIGHT-SLIDER_LEFT+13, 14), fill='#dce8df', width=8)
        output.write(bgra(strip))
        thumb = Image.new('RGBA', (25, 25))
        ImageDraw.Draw(thumb).ellipse((0, 0, 24, 24), fill=TEAL)
        output.write(bgra(thumb))
    print('COUNT_CARDS', (HERE / 'assets/count_cards.argb.gz').stat().st_size)


if __name__ == '__main__':
    main()
