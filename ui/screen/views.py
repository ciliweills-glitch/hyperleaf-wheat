"""800x480 start screen, three result sections, and batch spectrum details."""
import io
import math
from pathlib import Path
import numpy as np
import tifffile
from PIL import Image, ImageDraw, ImageFont
from classification import classify_phips2, LOW_THRESHOLD, HIGH_THRESHOLD

BLUE = '#183d45'
TEAL = '#287d68'
MUTED = '#778a86'
SLIDER_LEFT, SLIDER_RIGHT = 64, 452
CARD_BOX = (170, 181, 346, 270)
CASES = (
    ('low', '低', '#b7832e', '#fbf1de', f'PhiPS2 < {LOW_THRESHOLD:.3f}',
     ('复核采样与光照条件。', '结合现场长势核查，再同条件复测。')),
    ('medium', '中', '#287d68', '#e7f2ec', f'{LOW_THRESHOLD:.3f} ≤ PhiPS2 ≤ {HIGH_THRESHOLD:.3f}',
     ('记录区间内样本并持续跟踪。', '保持采集条件一致，定期复测。')),
    ('high', '高', '#b96048', '#f8ebe4', f'PhiPS2 > {HIGH_THRESHOLD:.3f}',
     ('保留高值样本，核对采集条件。', '记录长势表现，在同条件下复测。')),
)
PHASES = {'idle': '准备开始', 'waiting': '等待数据', 'receiving': '正在接收',
          'detecting': '正在检测', 'cancelling': '正在取消', 'done': '检测完成', 'error': '检测失败'}


class SpectralView:
    def __init__(self, font):
        self.fonts = {n: ImageFont.truetype(str(font), n)
                      for n in (12, 14, 16, 18, 20, 22, 24, 28, 32, 42, 56, 64)}
        self.text_cache = {}
        self.start_background = None
        icon_path = Path(__file__).resolve().parent / 'assets/hyperspectral_wheat_icon_48.png'
        self.icon = Image.open(icon_path).convert('RGBA').resize((48, 48), Image.LANCZOS)
        self.page = -1
        self.category = 0
        self.row_offset = 0
        self.selected = 0
        self.history_open = False
        self.history_offset = 0
        self.progress_offset = 0
        self.progress_follow = True
        self.details_cache = None
        self.details_key = None
        self.scroll_image = None
        self.scroll_kind = 0
        self.scroll_keys = []
        self.scroll_version = 0
        self.samples = []
        self.mean = None
        self.rgb = None
        self.update_canvas = None
        self.update_keys = None
        self.spectrum_key = None
        self.spectrum_background = None

    def render_updates(self, state):
        """Keep the screen image; redraw only the components whose inputs changed."""
        layout = (self.page, self.history_open)
        busy = (state['phase'] in ('waiting', 'receiving', 'detecting', 'cancelling')
                or state.get('inflight_count', 0) > 0)
        rows = tuple((r['image_id'], r['status'], r['value']) for r in state.get('progress', []))
        keys = dict(layout=layout, count=state['sample_count'], busy=busy,
                    progress=(rows, state['sample_count'], state['phase'],
                              self.progress_offset, self.progress_follow,
                              state.get('seconds_per_sample', 0)),
                    elapsed=f"{state.get('elapsed_seconds', 0):.1f}",
                    samples=(id(self.samples), len(self.samples)), selected=self.selected,
                    offset=self.row_offset, category=self.category,
                    history=(tuple((r['number'], r['count'], r['time'], r['mode'])
                                   for r in state['history']), self.history_offset),
                    classification=state['classification_mode'])
        previous = self.update_keys
        rectangles = []
        if previous is None or previous['layout'] != layout or previous['classification'] != keys['classification']:
            self.update_canvas = self.render(state).copy()
            rectangles.append((0, 0, 800, 480))
        else:
            self.canvas = self.update_canvas
            d = ImageDraw.Draw(self.canvas)
            if self.history_open:
                if keys['history'] != previous['history']:
                    self.history(self.canvas, d, state, footer=False)
                    rectangles.append((24, 88, 777, 435))
            elif self.page == -1:
                if keys['count'] != previous['count']:
                    # Erase the previous thumb/card before drawing the new selection.
                    for box in ((169, 179, 348, 273), (51, 287, 467, 318)):
                        d.rectangle((box[0], box[1], box[2]-1, box[3]-1), fill='white')
                    self.draw_selection(d, state['sample_count'])
                    rectangles.append((51, 179, 467, 318))
                if keys['busy'] != previous['busy']:
                    self.button(d, (48, 381, 292, 416), '开始检测', secondary=busy)
                    rectangles.append((48, 381, 293, 417))
                if keys['progress'] != previous['progress']:
                    d.rectangle((508, 88, 776, 434), fill='#f3f5ef')
                    self.progress(d, state)
                    rectangles.append((508, 88, 777, 435))
                elif keys['elapsed'] != previous['elapsed']:
                    d.rectangle((602, 418, 753, 433), fill='white')
                    elapsed = state.get('elapsed_seconds', 0)
                    d.text((602, 414), f'{elapsed:.1f} 秒' if elapsed else '— 秒',
                           font=self.fonts[14], fill=TEAL)
                    rectangles.append((602, 418, 754, 434))
            elif self.page == 0:
                if keys['category'] != previous['category'] or keys['samples'] != previous['samples']:
                    self.results(self.canvas, d, state)
                    rectangles.append((24, 134, 777, 435))
            elif keys['samples'] != previous['samples'] or keys['selected'] != previous['selected']:
                self.details(self.canvas, d, state)
                rectangles.append((24, 134, 777, 435))
            elif keys['offset'] != previous['offset']:
                self.detail_rows(d)
                rectangles.append((24, 134, 337, 435))
        # progress() can advance the automatically followed viewport.
        keys['progress'] = (rows, state['sample_count'], state['phase'],
                            self.progress_offset, self.progress_follow,
                            state.get('seconds_per_sample', 0))
        self.update_keys = keys
        return self.update_canvas, rectangles

    def load(self, data):
        cube = tifffile.imread(io.BytesIO(data))
        if cube.shape == (48, 352, 204):
            cube = np.moveaxis(cube, -1, 0)
        if cube.shape != (204, 48, 352) or cube.dtype != np.uint16:
            raise ValueError('请选择204波段、48×352的uint16 TIFF')
        foreground = np.any(cube != 0, axis=0)
        if not foreground.any():
            raise ValueError('TIFF没有有效叶片像素')
        self.mean = cube[:, foreground].mean(axis=1)
        wavelengths = np.linspace(400, 1000, 204)
        bands = [int(np.argmin(abs(wavelengths - nm))) for nm in (650, 550, 460)]
        values = np.moveaxis(cube[bands], 0, -1).astype(np.float32)
        high = max(1, float(np.percentile(values[foreground], 99)))
        rgb = (np.sqrt(np.clip(values / high, 0, 1)) * 255).astype(np.uint8)
        rgb[~foreground] = 0
        self.rgb = Image.fromarray(rgb)
        return self.rgb

    def clear(self):
        self.samples = []
        self.mean = self.rgb = None
        self.row_offset = self.selected = self.category = 0
        self.progress_offset = 0
        self.progress_follow = True
        self.details_key = None
        self.details_cache = None
        self.spectrum_key = None
        self.spectrum_background = None

    def scroll_image_for(self, kind, state):
        if kind == 1:
            rows = state.get('progress', []) or [{'image_id':'','status':'pending','value':None}
                                                for _ in range(state['sample_count'])]
            width, height, step, padding = 241, 168, 28, 0
            keys = [(r['image_id'],r['status'],r['value']) for r in rows]
        elif kind == 2:
            rows = self.samples
            width, height, step, padding = 278, 184, 30, 4
            keys = [(r['ImageId'],r['PhiPS2'],r['classification'],i==self.selected) for i,r in enumerate(rows)]
        else:
            rows = state['history']
            width, height, step, padding = 704, 220, 55, 0
            keys = [(r['number'],r['count'],r['time'],r['mode']) for r in rows]
        changed = kind != self.scroll_kind or len(keys) != len(self.scroll_keys)
        if changed:
            self.scroll_image = Image.new('RGB',(width,max(height,len(rows)*step+padding)),'white')
            self.scroll_keys = [None]*len(keys)
            self.scroll_kind = kind
        outer = self.canvas
        for i,(row,key) in enumerate(zip(rows,keys)):
            if key == self.scroll_keys[i]:
                continue
            changed = True
            tile = Image.new('RGB',(width,step),'white')
            self.canvas = tile
            d = ImageDraw.Draw(tile)
            if kind == 1:
                if row['status'] == 'running':
                    d.rounded_rectangle((0,0,width,27),7,fill='#eef4ec')
                self.text(d,(8,2),row['image_id'] or f'第{i+1:03d}个',16)
                labels = {'pending':'待检测','running':'检测中','cancelled':'已取消','error':'失败'}
                value = f"{row['value']:.3f}" if row['status']=='done' else labels[row['status']]
                color = (next(c[2] for c in CASES if c[0]==classify_phips2(row['value']))
                         if row['status']=='done' else TEAL if row['status']=='running' else MUTED)
                self.text(d,(145,2),value,16,color)
            elif kind == 2:
                if i == self.selected:
                    d.rounded_rectangle((0,-1,width,27),7,fill='#e7f2ec')
                color = next(c[2] for c in CASES if c[0]==row['classification'])
                d.ellipse((7,9,13,15),fill=color)
                self.text(d,(26,2),row['ImageId'],18)
                self.text(d,(180,2),f"{row['PhiPS2']:.3f}",18,color)
            else:
                d.rounded_rectangle((0,0,width,47),10,fill='#eef4ec')
                self.text(d,(17,8),f"第 {row['number']:02d} 批 · {row['count']} 个样本",18)
                self.text(d,(340,11),row['time']+' · '+row['mode'],14,MUTED)
                d.rounded_rectangle((632,8,688,38),8,fill='#f8ebe4')
                self.center(d,(632,8,688,38),'删除',16,'#b96048')
            self.scroll_image.paste(tile,(0,i*step))
        self.canvas = outer
        self.scroll_keys = keys
        if changed:
            self.scroll_version += 1
        return self.scroll_image

    def native_scroll_spec(self, state):
        if self.history_open:
            kind, box, attr, step, bar = 3,(48,187,704,220),'history_offset',55,(760,187,220,3)
        elif self.page == -1:
            kind, box, attr, step, bar = 1,(520,219,241,168),'progress_offset',28,(765,219,165,3)
        elif self.page == 1:
            kind, box, attr, step, bar = 2,(38,209,278,184),'row_offset',30,(323,209,184,4)
        else:
            return None
        image = self.scroll_image_for(kind,state)
        offset = round(getattr(self,attr)*step)
        return kind,box,image.height,step,offset,image.height-box[3],bar

    def label(self, value, size, color):
        value = str(value)
        key = (value, size, color)
        if key not in self.text_cache:
            bounds = self.fonts[size].getbbox(value)
            tile = Image.new('RGBA', (bounds[2]-bounds[0], bounds[3]-bounds[1]))
            ImageDraw.Draw(tile).text((-bounds[0], -bounds[1]), value,
                                     font=self.fonts[size], fill=color)
            self.text_cache[key] = (bounds, tile)
        return self.text_cache[key]

    def text(self, d, xy, value, size=18, color=BLUE):
        bounds, tile = self.label(value, size, color)
        self.canvas.paste(tile, (int(xy[0])+bounds[0], int(xy[1])+bounds[1]), tile)

    def center(self, d, box, value, size=18, color=BLUE):
        bounds, _ = self.label(value, size, color)
        x = (box[0] + box[2] - bounds[2]) / 2
        y = (box[1] + box[3] - bounds[3] - bounds[1]) / 2
        self.text(d, (x, y), value, size, color)

    def button(self, d, box, title, secondary=False):
        d.rounded_rectangle(box, 12, fill='#e9efea' if secondary else TEAL)
        self.center(d, box, title, 20, BLUE if secondary else 'white')

    def render(self, state):
        details_key = (id(self.samples), len(self.samples), self.selected)
        if self.page == 1 and self.details_key == details_key:
            canvas = self.details_cache.copy()
            self.canvas = canvas
            self.detail_rows(ImageDraw.Draw(canvas))
            return canvas
        selecting = (self.page == -1 and not self.history_open
                     and state['phase'] in ('idle', 'done', 'error'))
        if selecting and self.start_background is not None:
            canvas = self.start_background.copy()
            self.canvas = canvas
            self.draw_selection(ImageDraw.Draw(canvas), state['sample_count'])
            self.progress(ImageDraw.Draw(canvas), state)
            return canvas
        canvas = Image.new('RGB', (800, 480), '#f3f5ef')
        self.canvas = canvas
        d = ImageDraw.Draw(canvas)
        canvas.paste(self.icon, (24, 18), self.icon)
        self.text(d, (88, 15), '小麦叶片叶绿素荧光参数检测系统', 24)
        self.text(d, (88, 45), 'PhiPS2 · 光系统Ⅱ实际量子效率', 14, MUTED)
        d.rounded_rectangle((622, 23, 776, 61), 12, fill='#e5ece5')
        self.center(d, (622, 23, 776, 61),
                    '返回起始页' if self.page != -1 or self.history_open else '历史记录', 16)
        if self.page == -1:
            if self.history_open:
                self.history(canvas, d, state)
            else:
                self.start(canvas, d, state)
        else:
            for i, label in enumerate(('01  检测结果', '02  样本详情')):
                x = 24 + 384*i
                d.rounded_rectangle((x, 84, x+368, 118), 10,
                                    fill=BLUE if self.page == i else '#e5ece5')
                self.center(d, (x, 84, x+368, 118), label, 16,
                            'white' if self.page == i else MUTED)
            if self.page == 0:
                self.results(canvas, d, state)
            else:
                self.details(canvas, d, state)
            self.text(d, (28, 450), '左右滑动：结果 / 详情', 14, MUTED)
            self.text(d, (664, 450), f'{self.page+1} / 2', 16, MUTED)
            if self.page == 1:
                self.details_key = details_key
                self.details_cache = canvas.copy()
        if selecting:
            self.start_background = canvas.copy()
            base = ImageDraw.Draw(self.start_background)
            base.rectangle((169, 179, 347, 272), fill='white')
            base.rectangle((51, 287, 466, 317), fill='white')
            base.rectangle((508, 88, 776, 434), fill='#f3f5ef')
        return canvas

    def start(self, canvas, d, state):
        n = state['sample_count']
        d.rounded_rectangle((24, 88, 492, 434), 20, fill='white')
        self.text(d, (54, 110), '选择本轮检测样本数', 24)
        self.draw_selection(d, n)
        self.text(d, (58, 317), '1 个', 14, MUTED)
        self.text(d, (403, 317), '400 个', 14, MUTED)
        self.center(d, (48, 342, 468, 371), '点击开始检测，电脑自动传入样本。', 14, MUTED)
        busy = (state['phase'] in ('waiting', 'receiving', 'detecting', 'cancelling')
                or state.get('inflight_count', 0) > 0)
        self.button(d, (48, 381, 292, 416), '开始检测', secondary=busy)
        self.button(d, (308, 381, 468, 416), '取消检测', secondary=True)
        self.progress(d, state)

    def progress(self, d, state):
        rows = state.get('progress', [])
        n = len(rows) or state['sample_count']
        completed = sum(row['status'] == 'done' for row in rows)
        phase = state['phase']
        caption = ('检测中' if phase in ('waiting', 'receiving', 'detecting')
                   else '正在取消' if phase == 'cancelling'
                   else '检测完成' if completed == n
                   else '检测失败' if phase == 'error'
                   else '已取消' if rows and rows[-1]['status'] == 'cancelled'
                   else '等待开始')
        d.rounded_rectangle((508, 88, 776, 434), 20, fill='white')
        self.text(d, (528, 108), '样本回归进度', 20)
        self.text(d, (528, 148), caption, 14, TEAL)
        self.text(d, (674, 148), f'{completed}/{n}', 14, MUTED)
        d.rounded_rectangle((528, 178, 754, 184), 3, fill='#e7ede7')
        if completed:
            d.rounded_rectangle((528, 178, 528+226*completed/n, 184), 3, fill=TEAL)
        self.text(d, (528, 196), '样本号', 12, MUTED)
        self.text(d, (654, 196), '状态 / 结果', 12, MUTED)
        if self.progress_follow:
            active = next((i for i, row in enumerate(rows) if row['status'] == 'running'), completed)
            self.progress_offset = max(0, min(max(0, n-6), active-4))
        labels = {'pending': '待检测', 'running': '检测中', 'cancelled': '已取消', 'error': '失败'}
        offset = round(self.progress_offset*28)
        tile = self.scroll_image_for(1,state).crop((0,offset,241,offset+168))
        self.canvas.paste(tile,(520,219))
        if n > 6:
            thumb = max(12, 165*6/n)
            y = 219+self.progress_offset/(n-6)*(165-thumb)
            d.rounded_rectangle((765, 219, 768, 384), 2, fill='#e7ede7')
            d.rounded_rectangle((765, y, 768, y+thumb), 2, fill='#90b6a2')
        d.line((528, 390, 754, 390), fill='#e7ece6')
        self.text(d, (528, 398), '检测速度', 14, MUTED)
        speed = state.get('seconds_per_sample', 0)
        self.text(d, (602, 396), f'{speed:.2f} 秒/样本' if speed else '— 秒/样本', 16, TEAL)
        self.text(d, (528, 414), '检测时间', 14, MUTED)
        elapsed = state.get('elapsed_seconds', 0)
        d.text((602, 414), f'{elapsed:.1f} 秒' if elapsed else '— 秒', font=self.fonts[14], fill=TEAL)

    def draw_selection(self, d, n):
        d.rounded_rectangle(CARD_BOX, 18, fill='#eef4ec')
        self.center(d, (170, 180, 299, 267), str(n), 64, TEAL)
        self.text(d, (307, 227), '个', 20, TEAL)
        left, right, y = SLIDER_LEFT, SLIDER_RIGHT, 301
        d.line((left, y, right, y), fill='#dce8df', width=8)
        x = left + (n-1)/399*(right-left)
        d.line((left, y, x, y), fill=TEAL, width=8)
        d.ellipse((x-12, y-12, x+12, y+12), fill=TEAL)

    def results(self, canvas, d, state):
        key, title, color, tint, summary, actions = CASES[self.category]
        rows = [row for row in self.samples if row.get('classification') == key]
        total = len(self.samples)
        assigned = state['classification_mode'] != 'unassigned'
        d.rounded_rectangle((24, 134, 776, 434), 20, fill='white')
        caption = 'PhiPS2 数值分级' if assigned else '分类待接入'
        self.text(d, (48, 149), f'本批 {total} 个样本 · {caption}', 16, MUTED)
        d.rounded_rectangle((48, 190, 302, 368), 18, fill=tint)
        self.text(d, (70, 202), title, 42, color)
        self.text(d, (70, 267), str(len(rows)) if assigned else '—', 56, color)
        self.text(d, (201, 307), '个样本', 18, color)
        self.text(d, (71, 344), f'占本批 {len(rows)/total:.1%}' if total and assigned else '等待分类规则接入', 14, color)
        self.text(d, (331, 190), summary, 22, color)
        self.text(d, (331, 233), '对应措施', 18)
        for i, text in enumerate(actions):
            y = 267+i*41
            d.ellipse((331, y+9, 337, y+15), fill=color)
            self.text(d, (347, y), text, 18)
        self.text(d, (331, 353), '分级按原始 PhiPS2 预测值计算。', 14, MUTED)
        self.text(d, (48, 396), '上下滑动查看：低 / 中 / 高', 14, MUTED)
        for i in range(3):
            y = 219+i*30
            d.ellipse((748, y, 756, y+8), fill=color if self.category == i else '#dce5df')
        self.text(d, (670, 392), f'{self.category+1} / 3', 16, MUTED)

    def detail_rows(self, d):
        rows = self.samples
        n = len(rows)
        d.rounded_rectangle((24, 134, 336, 434), 18, fill='white')
        self.text(d, (44, 147), f'回归结果 · {n} 个样本', 18)
        self.text(d, (44, 181), '样本号', 14, MUTED)
        self.text(d, (214, 181), 'PhiPS2', 14, MUTED)
        start = math.floor(self.row_offset)
        offset = round(self.row_offset*30)
        tile = self.scroll_image_for(2,{}).crop((0,offset,278,offset+184))
        self.canvas.paste(tile,(38,209))
        self.text(d, (44, 407), f'{start+1 if n else 0}–{min(n,start+6)} / {n} · 上下滑动', 12, MUTED)
        if n > 6:
            d.rounded_rectangle((323, 209, 327, 393), 2, fill='#e7ede7')
            thumb = max(12, 184*6/n)
            y = 209+self.row_offset/max(1,n-6)*(184-thumb)
            d.rounded_rectangle((323, y, 327, y+thumb), 2, fill='#90b6a2')

    def details(self, canvas, d, state):
        rows = self.samples
        n = len(rows)
        self.detail_rows(d)
        spectrum_key = (id(rows), n, state['classification_mode'])
        if self.spectrum_key == spectrum_key:
            canvas.paste(self.spectrum_background, (348, 134))
            self.selected_spectrum(d)
            return
        d.rounded_rectangle((348, 134, 776, 434), 18, fill='white')
        self.text(d, (368, 147), f'全波段光谱 · {n} 条', 18)
        if state['classification_mode'] != 'unassigned':
            for i, (_, label, color, _, _, _) in enumerate(CASES):
                x = 376+i*115
                d.line((x, 183, x+14, 183), fill=color, width=2)
                self.text(d, (x+20, 173), label, 12, MUTED)
        else:
            self.text(d, (368, 173), '全部样本 · 分类规则待接入', 12, MUTED)
        self.text(d, (368, 196), '前景平均信号（×1000）', 12, MUTED)
        left, right, top, bottom = 404, 751, 224, 372
        spectra = np.asarray([r['spectrum'] for r in rows]) if n else np.zeros((1,204))
        maximum = max(1, float(spectra.max())/1000)
        step = 10**math.floor(math.log10(maximum))
        ceiling = math.ceil(maximum/step)*step
        for f in (0, .25, .5, .75, 1):
            y = bottom-f*(bottom-top)
            d.line((left, y, right, y), fill='#e7ece6')
            self.text(d, (368, y-8), f'{ceiling*f:g}', 12, MUTED)
        for band in (1, 50, 100, 150, 204):
            x = left+(band-1)/203*(right-left)
            d.line((x, top, x, bottom), fill='#eef2ec')
            self.text(d, (x-10, bottom+6), str(band), 12, MUTED)
        palette = {'medium':'#86bda4','low':'#ddbf81','high':'#d99b89'}
        for row in rows:
            points = [(left+i/203*(right-left), bottom-value/1000/ceiling*(bottom-top))
                      for i, value in enumerate(row['spectrum'])]
            d.line(points, fill=palette.get(row.get('classification'), '#a2c4b3'), width=1)
        self.center(d, (435, 390, 732, 414), '波段（1–204）', 12, MUTED)
        self.spectrum_ceiling = ceiling
        self.spectrum_key = spectrum_key
        self.spectrum_background = canvas.crop((348, 134, 777, 435))
        self.selected_spectrum(d)

    def selected_spectrum(self, d):
        if self.samples:
            row = self.samples[self.selected]
            left, right, top, bottom = 404, 751, 224, 372
            ceiling = self.spectrum_ceiling
            points = [(left+i/203*(right-left), bottom-value/1000/ceiling*(bottom-top))
                      for i, value in enumerate(row['spectrum'])]
            color = next((c[2] for c in CASES if c[0] == row.get('classification')), TEAL)
            d.line(points, fill=color, width=2)
            self.text(d, (368, 408), f"选中 {row['ImageId']} · PhiPS2 {row['PhiPS2']:.3f}", 12, color)

    def history(self, canvas, d, state, footer=True):
        entries = state['history']
        d.rounded_rectangle((24, 88, 776, 434), 20, fill='white')
        self.text(d, (48, 108), '检测批次记录', 24)
        self.text(d, (48, 145), '点击一条记录，查看该批结果与详情。', 16, MUTED)
        if not entries:
            self.center(d, (48, 205, 750, 350), '暂无记录，开始一次检测后会显示在这里。', 20, MUTED)
        offset = round(self.history_offset*55)
        tile = self.scroll_image_for(3,state).crop((0,offset,704,offset+220))
        self.canvas.paste(tile,(48,187))
        if footer:
            self.text(d, (28, 450), '上下滑动浏览历史批次', 14, MUTED)

    def scroll_target(self, x0, y0, state):
        if self.history_open and 48 <= x0 <= 752 and 187 <= y0 <= 406:
            return 'history_offset', 55, max(0, len(state['history'])-4)
        if self.page == -1 and x0 >= 508 and 213 <= y0 <= 386:
            return 'progress_offset', 28, max(0, len(state['progress'])-6)
        if self.page == 1 and 38 <= x0 <= 336 and 209 <= y0 <= 393:
            return 'row_offset', 30, max(0, len(self.samples)-6)
        return None

    def touch_action(self, x, y, x0, y0):
        if 622 <= x <= 776 and 18 <= y <= 66 and abs(x-x0) < 30 and abs(y-y0) < 30:
            return ('home', 0) if self.page != -1 or self.history_open else ('history', 0)
        if self.history_open:
            if abs(y-y0) >= 35 and abs(y-y0) > abs(x-x0):
                return 'history_scroll', 1 if y < y0 else -1
            if 48 <= x <= 752 and 187 <= y <= 406:
                name = 'history_delete' if 680 <= x <= 736 else 'history_select'
                return name, math.floor(self.history_offset+(y-187)/55)
            return 'none', 0
        if self.page == -1:
            if x0 >= 508 and 213 <= y0 <= 386 and abs(y-y0) >= 28:
                return 'progress_scroll', round((y0-y)/28)
            if 52 <= x0 <= 464 and 279 <= y0 <= 339:
                return 'count', 1+round(max(0,min(SLIDER_RIGHT-SLIDER_LEFT,x-SLIDER_LEFT))/(SLIDER_RIGHT-SLIDER_LEFT)*399)
            if 48 <= x <= 292 and 381 <= y <= 416:
                return 'start', 0
            if 308 <= x <= 468 and 381 <= y <= 416:
                return 'cancel', 0
            return 'none', 0
        if abs(x-x0) >= 90 and abs(x-x0) > 1.5*abs(y-y0):
            return 'page', max(0,min(1,self.page+(1 if x<x0 else -1)))
        if abs(y-y0) >= 35 and abs(y-y0) > 1.5*abs(x-x0):
            if self.page == 0:
                return 'category', max(0,min(2,self.category+(1 if y<y0 else -1)))
            if x0 < 336:
                return 'scroll', max(0,min(max(0,len(self.samples)-6),self.row_offset+round((y0-y)/30)))
        if 84 <= y <= 118:
            return 'page', int(x>=408)
        if self.page == 1 and 38 <= x <= 316 and 208 <= y <= 390:
            return 'select', min(len(self.samples)-1,math.floor(self.row_offset+(y-209)/30))
        return 'none', 0
