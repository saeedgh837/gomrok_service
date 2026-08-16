"""
config.py
Central configuration for the Persian OCR project.
All hyper-parameters, paths and image geometry live here so the rest of the
code stays free of magic numbers.
"""
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
IMAGES_DIR = DATA_DIR / "images"          # raw full images
LABELS_CSV = DATA_DIR / "labels.csv"      # columns: filename,text
CKPT_DIR = ROOT / "checkpoints"
CKPT_DIR.mkdir(exist_ok=True, parents=True)


@dataclass
class ImageConfig:
    """Geometry the CRNN expects. Line images are resized to (H, W)."""
    height: int = 48           # fixed height fed to the CNN
    width: int = 512           # fixed width (pad/resize). T = width/4 = 128 time steps.
                               # Must satisfy T >= 2*max_label_len+1; long spelled-out
                               # numbers reach ~30 chars, so keep this generous.
    channels: int = 1          # grayscale after preprocessing
    keep_aspect: bool = True   # resize by height, then pad width to `width`


@dataclass
class TrainConfig:
    epochs: int = 150
    batch_size: int = 16          # smaller batch -> more gradient steps/epoch (helps
                                  # CTC escape the initial all-blank phase faster)
    lr: float = 1e-3              # CTC needs a healthy LR to leave the blank phase;
                                  # 3e-4 was too slow. grad_clip keeps this stable.
    weight_decay: float = 1e-5
    num_workers: int = 4
    val_split: float = 0.1
    seed: int = 42
    grad_clip: float = 5.0
    # Early-stopping / checkpoint selection metric.
    # IMPORTANT: use "val_loss", not "cer". During the early "all-blank" phase CER
    # is pinned at 1.0 for many epochs while val_loss keeps dropping — monitoring
    # CER would trigger early stopping before the model ever emits characters.
    monitor: str = "val_loss"
    patience: int = 30           # generous: the blank phase can last 50-120 epochs


@dataclass
class ModelConfig:
    cnn_out_channels: int = 256    # channels of the final conv feature map
    rnn_hidden: int = 256          # v2 geometry (best model). v3's 384 + full BN
                                   # + scribble-aug regressed real accuracy, reverted.
    rnn_layers: int = 2
    dropout: float = 0.1


@dataclass
class DecoderConfig:
    """Confidence gate for the number decoder (tools/calibrate_decoder.py).
    accept -> return the snapped integer; else return "hard" (refresh captcha).
    Calibrated on real_test.csv @ v4 (60 samples): this point gave 90.3%
    precision / 51.7% coverage (28/31). Raw numeric accuracy w/o gate = 71.7%.
    Small-sample — re-run calibration as the test set grows."""
    min_word_conf: float = 0.7     # min over spelled words of mean top-1 softmax
    d_max: int = 1                 # snap distance: 1 = allow 1-char snap to canonical
    min_margin: float = 0.2        # min over chars of (top1 - top2) softmax
    require_exact: bool = False
    precision_target: float = 0.90


@dataclass
class Config:
    image: ImageConfig = field(default_factory=ImageConfig)
    train: TrainConfig = field(default_factory=TrainConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    decoder: DecoderConfig = field(default_factory=DecoderConfig)


CFG = Config()
