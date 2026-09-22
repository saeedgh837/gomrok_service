"""Build the panel's seven UI notification WAVs (sfx/) from their CC0 sources.

The panel plays one short cue per UI event. The audio comes from Kenney's
"Interface Sounds" pack, released under CC0 1.0 (public domain dedication), so
it may ship inside the release bundle with no attribution obligation. The pack
is already 44.1 kHz / 16-bit / mono WAV, which is exactly the format the panel
wants; what is left to do is level and length -- the pack ships everything near
full scale, while the panel needs quiet, click-free cues between -18 and
-10 dBFS.

Every source is pinned by sha256 (and by the mirror commit, so the URL itself
is immutable). The processing below is pure arithmetic -- no randomness, no
floating-point ordering surprises -- so re-running the script rewrites the very
same bytes. Sources are cached, and downloaded only when the cache is cold, so
building a release never needs the network: the generated WAVs are committed.

The interface this script implements (asserted by
lab/tests/test_v1_9_0_sfx_assets.py, which is the gate the release relies on):

  * RIFF/WAVE, PCM 16-bit, mono, 44100 Hz;
  * duration inside the per-event window, never above 600 ms;
  * peak level: ui-tab -18, ui-open -15, ui-ok / ui-warn / ui-error /
    ui-login / ui-login-error -10 dBFS;
  * no clipping (|sample| < 0.9 FS), no DC offset, and a 5 ms raised-cosine
    fade at both ends so a cue can never start or stop with a click.

Run:
    "C:/Program Files/Python310/python.exe" lab/scripts/gen_sfx.py
    "C:/Program Files/Python310/python.exe" lab/scripts/gen_sfx.py --src-dir C:\\tmp\\sfx-src

Provenance is recorded for the operator in sfx/README.md.
"""
from __future__ import annotations

import argparse
import hashlib
import math
import struct
import sys
import tempfile
import urllib.request
import wave
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "sfx"

SAMPLE_RATE = 44100
SAMPLE_WIDTH = 2          # bytes -> PCM 16-bit
FADE_MS = 5.0             # inside the 3-8 ms window every cue is bound to
FULL_SCALE = 32768.0      # dBFS reference (symmetric, so -10 dBFS is exact)

PACK_NAME = 'Kenney "Interface Sounds" (1.0)'
PACK_PAGE = "https://kenney.nl/assets/interface-sounds"
PACK_LICENCE = "Creative Commons Zero, CC0 1.0 Universal (public domain)"
PACK_LICENCE_URL = "http://creativecommons.org/publicdomain/zero/1.0/"
MIRROR_REPO = "Calinou/kenney-interface-sounds"
MIRROR = f"https://github.com/{MIRROR_REPO}"
MIRROR_COMMIT = "4596a49eaf5a533948d49a47467f606bcdea70ff"
MIRROR_DIR = "addons/kenney_interface_sounds"
RAW_BASE = f"https://raw.githubusercontent.com/{MIRROR_REPO}/{MIRROR_COMMIT}/{MIRROR_DIR}"

# out name, source file, source sha256, target peak dBFS, longest allowed take.
# The pack's own lengths are used as-is; only the two error cues are cut, and
# only through their trailing silence (both have gone quiet by ~280 ms, so the
# cut is inaudible). Every peak is well below the source, hence the reset gain.
EVENTS = [
    dict(out="ui-open.wav", src="tick_001.wav", peak_db=-15.0, max_ms=90,
         sha256="fee217e21731da4be1f0347fd2632296259b8bfdca343c936fe60dc64bce280e",
         use="a form, modal or dialog opens"),
    dict(out="ui-tab.wav", src="tick_004.wav", peak_db=-18.0, max_ms=70,
         sha256="7cf0ab362d64d3cc2d25a89528662ed21abd5c67a425dc0f4d1013871a481bdd",
         use="tab / page switch (quietest cue of the set)"),
    dict(out="ui-ok.wav", src="confirmation_001.wav", peak_db=-10.0, max_ms=300,
         sha256="f9d0ef5a5c740c2ac250ac3876db9e4340669044a5bf78ae09192918db3e2ebc",
         use="a form, case, passenger or setting was saved"),
    dict(out="ui-warn.wav", src="bong_001.wav", peak_db=-10.0, max_ms=220,
         sha256="f5d5f83b13edc5321352c2ba57972d1400151e3f27f1c18fcda07d261358511a",
         use="warning"),
    dict(out="ui-error.wav", src="error_005.wav", peak_db=-10.0, max_ms=380,
         sha256="8e685ba1d408d3029c1a084a238265fa0a2b439d374fbfc8ba6cfef6edfefb7b",
         use="validation error, server error, failed action"),
    dict(out="ui-login.wav", src="confirmation_003.wav", peak_db=-10.0, max_ms=450,
         sha256="d9b3719731a409c8109c5a049aceffbff9ff6c0ffe074a02fe52664393139b79",
         use="successful login"),
    dict(out="ui-login-error.wav", src="error_006.wav", peak_db=-10.0, max_ms=480,
         sha256="7ed3f06867e213edcb7b314c693e30a6ffe5ea5c694c19b130c4727810a5729a",
         use="failed login (slightly longer than ui-error)"),
]


def source_url(name: str) -> str:
    return f"{RAW_BASE}/{name}"


def fetch_source(name: str, cache: Path) -> Path:
    """Return a cached source WAV, downloading it once on a cold cache."""
    cache.mkdir(parents=True, exist_ok=True)
    dest = cache / name
    if not dest.exists():
        url = source_url(name)
        print(f"  fetch {name} <- {url}")
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                payload = response.read()
        except OSError as exc:  # offline, blocked, mirror gone
            raise SystemExit(
                f"cannot fetch {url}: {exc}\n"
                "Every source is sha256-pinned and the generated WAVs are committed; "
                "pass --src-dir pointing at a copy of the pack to work offline."
            ) from exc
        dest.write_bytes(payload)
    return dest


def read_mono(path: Path) -> list[int]:
    """Read a source WAV, refusing anything but mono 16-bit 44.1 kHz."""
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        frames = handle.getnframes()
        payload = handle.readframes(frames)
    if (channels, width, rate) != (1, SAMPLE_WIDTH, SAMPLE_RATE):
        raise SystemExit(
            f"{path.name}: expected mono 16-bit {SAMPLE_RATE} Hz, "
            f"got {channels}ch {width * 8}-bit {rate} Hz"
        )
    samples = list(struct.unpack(f"<{frames}h", payload[: frames * SAMPLE_WIDTH]))
    if len(samples) != frames:
        raise SystemExit(f"{path.name}: truncated sample data")
    return samples


def render(source: list[int], peak_db: float, max_ms: float) -> list[int]:
    """Trim, DC-block, fade and level one source into mono int16 samples.

    Fading happens BEFORE the level reset so the requested peak is the peak of
    the finished file: a percussive attack sits inside the fade-in window, and
    levelling first would let the fade pull the measured peak below target.
    """
    keep = min(len(source), int(round(max_ms * SAMPLE_RATE / 1000.0)))
    fade = int(round(FADE_MS * SAMPLE_RATE / 1000.0))
    if keep <= 2 * fade:
        raise SystemExit(f"take of {keep} frames is too short for a {FADE_MS} ms fade")

    samples = [float(x) for x in source[:keep]]

    mean = sum(samples) / len(samples)
    samples = [x - mean for x in samples]

    for i in range(fade):
        weight = 0.5 - 0.5 * math.cos(math.pi * i / (fade - 1))
        samples[i] *= weight
        samples[-1 - i] *= weight

    peak = max(abs(x) for x in samples)
    if peak == 0.0:
        raise SystemExit("take is silent")
    scale = (FULL_SCALE * 10.0 ** (peak_db / 20.0)) / peak
    return [max(-32768, min(32767, int(round(x * scale)))) for x in samples]


def write_wav(path: Path, samples: list[int]) -> None:
    payload = struct.pack(f"<{len(samples)}h", *samples)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(SAMPLE_WIDTH)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(payload)


def peak_dbfs(samples: list[int]) -> float:
    peak = max(abs(x) for x in samples)
    return 20.0 * math.log10(peak / FULL_SCALE) if peak else float("-inf")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--src-dir",
        type=Path,
        default=None,
        help="directory holding the packaged source WAVs (cached downloads "
             "otherwise, under the system temp directory)",
    )
    args = parser.parse_args(argv)

    cache = args.src_dir or (Path(tempfile.gettempdir()) / "irica-sfx-src")
    if args.src_dir is not None and not args.src_dir.is_dir():
        raise SystemExit(f"--src-dir {args.src_dir} is not a directory")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"sources : {cache}")
    print(f"output  : {OUT_DIR}")

    total = 0
    rows = []
    for event in EVENTS:
        path = fetch_source(event["src"], cache)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != event["sha256"]:
            raise SystemExit(
                f"{event['src']}: sha256 mismatch, refusing to use it\n"
                f"  expected {event['sha256']}\n  actual   {digest}"
            )
        samples = render(read_mono(path), event["peak_db"], event["max_ms"])
        out = OUT_DIR / event["out"]
        write_wav(out, samples)

        size = out.stat().st_size
        total += size
        rows.append((
            event["out"],
            event["src"],
            len(samples) * 1000.0 / SAMPLE_RATE,
            peak_dbfs(samples),
            size,
        ))

    print(f"\n{'file':19s} {'source':19s} {'ms':>7s} {'peak dBFS':>10s} {'bytes':>7s}")
    for name, src, ms, peak, size in rows:
        print(f"{name:19s} {src:19s} {ms:7.1f} {peak:10.2f} {size:7d}")
    print(f"{'TOTAL':19s} {'':19s} {'':7s} {'':10s} {total:7d} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
