"""Masked 2D-CNN copied from the legacy spatial comparison."""

import torch
from torch import nn


class Masked2DCNN(nn.Module):
    def __init__(self):
        super().__init__()
        self.features = nn.Sequential(
            nn.Conv2d(205, 32, 3, padding=1, bias=False),
            nn.BatchNorm2d(32), nn.ReLU(inplace=True), nn.MaxPool2d((2, 4)),
            nn.Conv2d(32, 64, 3, padding=1, bias=False),
            nn.BatchNorm2d(64), nn.ReLU(inplace=True), nn.MaxPool2d((2, 2)),
            nn.Conv2d(64, 128, 3, padding=1, bias=False),
            nn.BatchNorm2d(128), nn.ReLU(inplace=True), nn.MaxPool2d((2, 2)),
            nn.Conv2d(128, 160, 3, padding=1, bias=False),
            nn.BatchNorm2d(160), nn.ReLU(inplace=True),
            nn.AdaptiveAvgPool2d(1), nn.Flatten(),
        )
        self.head = nn.Sequential(
            nn.Linear(160, 96), nn.ReLU(inplace=True),
            nn.Dropout(0.15), nn.Linear(96, 1),
        )

    def forward(self, x):
        return self.head(self.features(x)).squeeze(1)


class Masked2DCNNFolded(nn.Module):
    """Conv+BN folded variant: every Conv2d carries a real bias, no BatchNorm."""

    def __init__(self):
        super().__init__()
        self.features = nn.Sequential(
            nn.Conv2d(205, 32, 3, padding=1, bias=True),
            nn.ReLU(inplace=True), nn.MaxPool2d((2, 4)),
            nn.Conv2d(32, 64, 3, padding=1, bias=True),
            nn.ReLU(inplace=True), nn.MaxPool2d((2, 2)),
            nn.Conv2d(64, 128, 3, padding=1, bias=True),
            nn.ReLU(inplace=True), nn.MaxPool2d((2, 2)),
            nn.Conv2d(128, 160, 3, padding=1, bias=True),
            nn.ReLU(inplace=True),
            nn.AdaptiveAvgPool2d(1), nn.Flatten(),
        )
        self.head = nn.Sequential(
            nn.Linear(160, 96), nn.ReLU(inplace=True),
            nn.Dropout(0.15), nn.Linear(96, 1),
        )

    def forward(self, x):
        return self.head(self.features(x)).squeeze(1)
