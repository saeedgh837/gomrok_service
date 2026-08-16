"""
config.py
Central configuration for the IRICA captcha solver.

The IRICA captcha (https://epl.irica.gov.ir/ImeiRegister) is a FIXED-length,
lowercase alphanumeric string:
    * alphabet: a-z + 0-9  (36 symbols, all lowercase)
    * length  : exactly 5 characters
    * image    : RGB, ~386x151, light-gray background, dark glyphs, a faint red
                 noise line drawn over the text, mild wave distortion.

This is a much smaller problem than the hamta Persian-word OCR: tiny vocab,
short fixed-length string, no number->words decoding layer. All hyper-params
and image geometry live here so the rest of the code stays free of magic
numbers.
"""
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------- #
# The captcha's ground truth alphabet (order is FIXED — never reorder after
# training a model, the class indices depend on it).
# --------------------------------------------------------------------------- #
ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"   # 36 symbols
CAPTCHA_LEN = 5                                      # every string is 5 chars

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
IMAGES_DIR = DATA_DIR / "images"          # synthetic training images
LABELS_CSV = DATA_DIR / "labels.csv"      # columns: filename,text
CKPT_DIR = ROOT / "checkpoints"
CKPT_DIR.mkdir(exist_ok=True, parents=True)

# Real captcha images collected by the scraper (for fine-tune + eval).
REAL_DIR = ROOT.parent / "captchas_irica"
REAL_LABELS_CSV = REAL_DIR / "labels_irica.csv"


@dataclass
class ImageConfig:
    """Geometry the CRNN expects. Images are resized to (H, W).

    Width is modest because the string is only 5 chars — a wide canvas just
    slows training with no benefit. T = width/4 time steps must satisfy
    T >= 2*len+1 = 11; width=192 -> T=48, comfortably generous.
    """
    height: int = 48           # fixed height fed to the CNN
    width: int = 192           # fixed width (resize + pad). T = width/4 = 48.
    channels: int = 1          # grayscale after preprocessing
    keep_aspect: bool = True   # resize by height, then pad width to `width`


@dataclass
class TrainConfig:
    epochs: int = 60
    batch_size: int = 64          # synthetic set is large + uniform -> big batch OK
    lr: float = 1e-3              # CTC needs a healthy LR to leave the blank phase
    weight_decay: float = 1e-5
    num_workers: int = 4
    val_split: float = 0.05
    seed: int = 42
    grad_clip: float = 5.0
    # Early-stopping / checkpoint selection metric. During the early "all-blank"
    # CTC phase, accuracy is pinned at 0 while val_loss keeps dropping — monitor
    # val_loss so we don't stop before the model emits characters.
    monitor: str = "val_loss"
    patience: int = 15


@dataclass
class ModelConfig:
    cnn_out_channels: int = 256    # channels of the final conv feature map
    rnn_hidden: int = 256
    rnn_layers: int = 2
    dropout: float = 0.1


@dataclass
class Config:
    image: ImageConfig = field(default_factory=ImageConfig)
    train: TrainConfig = field(default_factory=TrainConfig)
    model: ModelConfig = field(default_factory=ModelConfig)


CFG = Config()
