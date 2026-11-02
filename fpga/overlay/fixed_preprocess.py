"""Q28 uint16 -> INT16 乘加；稀疏 ±1 修正保持冻结 FP32 舍入语义。"""

import numpy as np

FRACTION = 28
CORRECTION_SLOTS = 9
BUCKET_BITS = 10
BANK_DEPTH = 1024


def round_even_fixed(total, fraction=FRACTION):
    floor = total >> fraction
    remainder = total - (floor << fraction)
    return floor + ((remainder > 2**(fraction-1)) |
                    ((remainder == 2**(fraction-1)) & ((floor & 1) != 0)))


def affine_quantize(values, multiplier, offset, fraction=FRACTION):
    total = values.astype(np.int64)*multiplier + offset
    return np.clip(round_even_fixed(total, fraction), -32767, 32767).astype(np.int16)


class FixedInt16Preprocessor:
    def __init__(self, band_mean, band_std, scale_in):
        mean = np.asarray(band_mean, dtype=np.float32)
        std = np.asarray(band_std, dtype=np.float32)
        scale = np.float32(scale_in)
        factor = 1.0/(std.astype(np.float64)*float(scale))
        self.multiplier = np.rint(factor*2**FRACTION).astype(np.int64)
        self.offset = np.rint(-mean.astype(np.float64)*factor*2**FRACTION).astype(np.int64)
        self.mask_value = np.int16(np.clip(np.rint(np.float32(1)/scale), -32767, 32767))
        self.exceptions = []
        values = np.arange(65536, dtype=np.uint16)
        for band in range(204):
            original = np.clip(np.rint(((values.astype(np.float32)-mean[band])/std[band])/scale),
                               -32767, 32767).astype(np.int16)
            base = self.quantize_band(values, band, corrected=False)
            bad = np.flatnonzero(base != original)
            delta = original[bad].astype(np.int32)-base[bad].astype(np.int32)
            assert np.all(np.abs(delta) == 1)
            assert np.bincount(bad >> BUCKET_BITS, minlength=64).max() <= CORRECTION_SLOTS
            self.exceptions.append((bad.astype(np.uint16), delta.astype(np.int8)))

    def quantize_band(self, values, band, corrected=True):
        output = affine_quantize(values, self.multiplier[band], self.offset[band])
        if corrected:
            keys, delta = self.exceptions[band]
            if keys.size:
                # 软件参考用有序稀疏表；RTL 为每个 1024 值桶并行比较最多 9 项。
                index = np.searchsorted(keys, values)
                index = np.minimum(index, keys.size-1)
                correction = np.where(keys[index] == values, delta[index], 0)
                output = (output.astype(np.int32)+correction).astype(np.int16)
        return output

    def prepare_cube(self, cube, corrected=True, output=None):
        if cube.shape == (48, 352, 204):
            cube = np.moveaxis(cube, -1, 0)
        elif cube.shape != (204, 48, 352):
            raise ValueError(f'Expected [204,48,352] or [48,352,204], got {cube.shape}')
        if cube.dtype != np.uint16:
            raise ValueError('Fixed preprocessing requires uint16 pixels')
        mask = np.any(cube != 0, axis=0)
        if output is None:
            output = np.empty((205, 48, 352), dtype=np.int16)
        for band in range(204):
            output[band] = self.quantize_band(cube[band], band, corrected)
        output[:204, ~mask] = 0
        np.multiply(mask, self.mask_value, out=output[204], casting='unsafe')
        return output

    def export_roms(self, directory, suffix='.hex'):
        directory.mkdir(parents=True, exist_ok=True)
        # 78-bit coefficient word = {signed offset[47:0], signed multiplier[29:0]}.
        with (directory/f'coefficients{suffix}').open('w') as out:
            for multiplier, offset in zip(self.multiplier, self.offset):
                assert 0 <= int(multiplier) < 2**29
                assert -2**47 <= int(offset) < 2**47
                word = ((int(offset) & (2**48-1)) << 30) | int(multiplier)
                out.write(f'{word:020x}\n')
        # 每个 1024 值桶记录全局稀疏表起点：{count[3:0], remainder[3:0], quotient[9:0]}。
        # 第 j 个全局条目位于 bank=j%9, address=j//9，最多 9 项同拍读出。
        banks = np.zeros((CORRECTION_SLOTS, BANK_DEPTH), dtype=np.uint16)
        cursor = 0
        with (directory/f'correction_directory{suffix}').open('w') as out:
            for keys, delta in self.exceptions:
                for bucket in range(64):
                    selected = np.flatnonzero((keys >> BUCKET_BITS) == bucket)
                    quotient, remainder = divmod(cursor, CORRECTION_SLOTS)
                    word = (len(selected) << 14) | (remainder << 10) | quotient
                    out.write(f'{word:05x}\n')
                    for index in selected:
                        entry = ((int(delta[index]) & 3) << 10) | (int(keys[index]) & 1023)
                        banks[cursor%CORRECTION_SLOTS, cursor//CORRECTION_SLOTS] = entry
                        cursor += 1
        assert cursor <= CORRECTION_SLOTS*BANK_DEPTH
        for bank in range(CORRECTION_SLOTS):
            np.savetxt(directory/f'correction_bank_{bank}{suffix}', banks[bank], fmt='%03x')
