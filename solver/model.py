"""
model.py
CRNN (CNN + BiLSTM + CTC head) for the IRICA captcha.

This is the same proven CRNN geometry used in the persian_ocr project; only the
number of output classes changes (driven by the tiny IRICA vocab). Height is
collapsed to 1 by the conv stack so the RNN sees a pure width sequence; width is
downsampled by 4 (two 2x2 pools) -> T = W/4.

CRNN input : (B, 1, H, W)   H fixed (48), W fixed (192)
CRNN output: (T, B, num_classes) log-probabilities for torch.nn.CTCLoss
"""
from __future__ import annotations
import torch
import torch.nn as nn
import torch.nn.functional as F

from config import CFG
from vocab import VOCAB


class _ConvBlock(nn.Module):
    def __init__(self, cin, cout, bn=False):
        super().__init__()
        self.conv = nn.Conv2d(cin, cout, kernel_size=3, stride=1, padding=1)
        self.bn = nn.BatchNorm2d(cout) if bn else None
        self.act = nn.ReLU(inplace=True)

    def forward(self, x):
        x = self.conv(x)
        if self.bn is not None:
            x = self.bn(x)
        return self.act(x)


class CRNN(nn.Module):
    """VGG-style CRNN adapted for a 48px input height."""

    def __init__(
        self,
        num_classes: int = None,
        img_channels: int = None,
        rnn_hidden: int = None,
        rnn_layers: int = None,
        dropout: float = None,
    ):
        super().__init__()
        num_classes = num_classes or len(VOCAB)
        img_channels = img_channels or CFG.image.channels
        rnn_hidden = rnn_hidden or CFG.model.rnn_hidden
        rnn_layers = rnn_layers or CFG.model.rnn_layers
        dropout = dropout if dropout is not None else CFG.model.dropout

        # --- CNN feature extractor -------------------------------------- #
        # Pooling schedule chosen so that for H=48 the height reaches 1 and the
        # width is divided by 4 (keeps a long time axis for CTC alignment).
        self.cnn = nn.Sequential(
            _ConvBlock(img_channels, 64),
            nn.MaxPool2d(2, 2),                       # H 48->24, W /2
            _ConvBlock(64, 128),
            nn.MaxPool2d(2, 2),                       # H 24->12, W /2
            _ConvBlock(128, 256, bn=True),
            _ConvBlock(256, 256),
            nn.MaxPool2d((2, 1), (2, 1)),             # H 12->6,  W /1
            _ConvBlock(256, CFG.model.cnn_out_channels, bn=True),
            nn.MaxPool2d((2, 1), (2, 1)),             # H 6->3,   W /1
            _ConvBlock(CFG.model.cnn_out_channels, CFG.model.cnn_out_channels, bn=True),
            nn.AdaptiveMaxPool2d((1, None)),          # H ->1, keep W
        )

        # --- Sequence model --------------------------------------------- #
        self.rnn = nn.LSTM(
            input_size=CFG.model.cnn_out_channels,
            hidden_size=rnn_hidden,
            num_layers=rnn_layers,
            bidirectional=True,
            batch_first=False,
            dropout=dropout if rnn_layers > 1 else 0.0,
        )
        self.dropout = nn.Dropout(dropout)
        self.classifier = nn.Linear(rnn_hidden * 2, num_classes)

        self._init_weights()

    def _init_weights(self):
        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                nn.init.kaiming_normal_(m.weight, mode="fan_out", nonlinearity="relu")
                if m.bias is not None:
                    nn.init.zeros_(m.bias)
            elif isinstance(m, nn.Linear):
                nn.init.xavier_uniform_(m.weight)
                nn.init.zeros_(m.bias)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """x: (B, C, H, W) -> log_probs: (T, B, num_classes) for CTCLoss."""
        feat = self.cnn(x)                 # (B, C', 1, W')
        b, c, h, w = feat.shape
        assert h == 1, f"CNN height must collapse to 1, got {h}"
        feat = feat.squeeze(2)             # (B, C', W')
        feat = feat.permute(2, 0, 1)       # (W'=T, B, C')  time-major

        rnn_out, _ = self.rnn(feat)        # (T, B, 2*hidden)
        rnn_out = self.dropout(rnn_out)
        logits = self.classifier(rnn_out)  # (T, B, num_classes)
        return F.log_softmax(logits, dim=2)


def build_crnn() -> CRNN:
    return CRNN()


if __name__ == "__main__":
    net = build_crnn()
    n_params = sum(p.numel() for p in net.parameters())
    x = torch.randn(2, CFG.image.channels, CFG.image.height, CFG.image.width)
    y = net(x)
    print(f"CRNN params: {n_params/1e6:.2f}M")
    print("input :", tuple(x.shape))
    print("output:", tuple(y.shape), "(T, B, num_classes)")
    print("num_classes:", len(VOCAB))
