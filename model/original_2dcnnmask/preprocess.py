"""Minimal HyperLeaf TIFF preprocessing for Original 2DCNNMask inference.

Self-contained: no dependency on the research repository package tree.
Contract:
  TIFF (H,W,204) or (204,H,W)
  -> foreground mask = |cube|.sum(over bands) > 0
  -> per-band standardization with train-only mean/std
  -> background zeroed
  -> tensor [205, 48, 352] = 204 standardized bands + 1 mask channel
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import tifffile

SPECTRAL_BANDS = 204
TARGET_H, TARGET_W = 48, 352


def normalize_image_id(value: object) -> str:
    text = str(value)
    match = re.search(r"\d+", text)
    return f"{int(match.group(0)):05d}" if match else Path(text).stem


def read_cube_chw(path: str | Path) -> np.ndarray:
    cube = tifffile.imread(path)
    if cube.ndim != 3:
        raise ValueError(f"Expected a 3D TIFF, got shape {cube.shape} for {path}")
    if cube.shape[0] == SPECTRAL_BANDS:
        chw = cube
    elif cube.shape[-1] == SPECTRAL_BANDS:
        chw = np.moveaxis(cube, -1, 0)
    else:
        raise ValueError(f"Cannot infer spectral axis for shape {cube.shape} in {path}")
    return chw.astype(np.float32)


def build_205_tensor(cube_chw: np.ndarray, band_mean: np.ndarray, band_std: np.ndarray) -> np.ndarray:
    if cube_chw.shape[0] != SPECTRAL_BANDS:
        raise ValueError(f"Expected {SPECTRAL_BANDS} bands, got {cube_chw.shape[0]}")
    if cube_chw.shape[-2:] != (TARGET_H, TARGET_W):
        raise ValueError(f"Expected spatial {(TARGET_H, TARGET_W)}, got {cube_chw.shape[-2:]}")
    mean = band_mean[:, None, None]
    std = band_std[:, None, None]
    mask = (np.abs(cube_chw).sum(axis=0) > 0).astype(np.float32)
    cube = (cube_chw - mean) / std
    cube[:, mask == 0] = 0.0
    return np.concatenate([cube, mask[None]], axis=0).astype(np.float32)


def load_image_tensor(image_path: str | Path, band_mean: np.ndarray, band_std: np.ndarray) -> np.ndarray:
    return build_205_tensor(read_cube_chw(image_path), band_mean, band_std)
