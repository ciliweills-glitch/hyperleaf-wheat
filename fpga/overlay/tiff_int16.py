"""冻结 uint16 TIFF 契约的精确 INT16 查表预处理；无 torch 依赖。"""

import time
from io import BytesIO

import numpy as np
import tifffile


def read_uint16_tiff(data):
    cube = tifffile.imread(BytesIO(data))
    if cube.dtype != np.uint16 or cube.ndim != 3:
        raise ValueError('TIFF input must be a 3D uint16 cube')
    if cube.shape == (48, 352, 204):
        cube = np.moveaxis(cube, -1, 0)
    elif cube.shape != (204, 48, 352):
        raise ValueError(f'Expected TIFF shape [204,48,352] or [48,352,204], got {cube.shape}')
    return cube


def foreground_spectrum(cube, mask):
    if not mask.any():
        raise ValueError('TIFF没有有效叶片像素')
    return cube[:, mask].mean(axis=1).tolist()


class TIFFRawPreprocessor:
    output_dtype = np.uint16

    def prepare_file(self, path, output=None):
        started = time.monotonic()
        data = path.read_bytes()
        file_read = time.monotonic()-started
        result, stages = self.prepare(data, output)
        stages['t_tiff_read'] += file_read
        stages['prepare_started_at'] = started
        return result, stages

    def prepare(self, data, output=None, include_spectrum=False):
        started = time.monotonic()
        cube = read_uint16_tiff(data)
        read = time.monotonic()
        if output is None:
            output = np.empty((205, 48, 352), dtype=np.uint16)
        output[:204] = cube
        mask = np.any(cube != 0, axis=0)
        output[204] = mask
        spectrum = {'spectrum': foreground_spectrum(cube, mask)} if include_spectrum else {}
        finished = time.monotonic()
        return output, dict(t_tiff_read=read-started, t_preprocess=finished-read,
                            t_quantize=0.0, prepare_started_at=started,
                            prepare_finished_at=finished, preparation_method='uint16_raw_pl_q28',
                            **spectrum)


class TIFFInt16Preprocessor:
    output_dtype = np.int16
    def __init__(self, band_mean, band_std, scale_in):
        self.table = np.empty((204, 65536), dtype=np.int16)
        values = np.arange(65536, dtype=np.float32)
        scratch = np.empty_like(values)
        for band in range(204):
            # 保留原 float32 减、除、除、rint 顺序，不能合并成近似仿射系数。
            np.subtract(values, band_mean[band], out=scratch)
            np.divide(scratch, band_std[band], out=scratch)
            np.divide(scratch, scale_in, out=scratch)
            np.rint(scratch, out=scratch)
            np.clip(scratch, -32767, 32767, out=scratch)
            self.table[band] = scratch
        self.mask_value = np.int16(np.clip(np.rint(np.float32(1.0)/scale_in), -32767, 32767))

    def prepare(self, data, output=None, include_spectrum=False):
        started = time.monotonic()
        cube = read_uint16_tiff(data)
        read = time.monotonic()
        # 对非负 uint16，任一波段非零与原 |cube|.sum(bands)>0 完全等价。
        mask = np.any(cube != 0, axis=0)
        spectrum = {'spectrum': foreground_spectrum(cube, mask)} if include_spectrum else {}
        masked = time.monotonic()
        if output is None:
            output = np.empty((205, 48, 352), dtype=np.int16)
        for band in range(204):
            # uint16 索引必在 0..65535；clip 模式使 take 直接写入 out。
            np.take(self.table[band], cube[band], out=output[band], mode='clip')
        output[:204, ~mask] = 0
        np.multiply(mask, self.mask_value, out=output[204], casting='unsafe')
        finished = time.monotonic()
        return output, dict(t_tiff_read=read-started, t_preprocess=masked-read,
                            t_quantize=finished-masked,
                            prepare_started_at=started, prepare_finished_at=finished,
                            preparation_method='uint16_exact_lut', **spectrum)
