# Panel UI notification sounds (`sfx/`)

Seven short cues the CRM panel plays for UI events. They ship inside the release
bundle (`lab/scripts/build_windows_release.py`, `APP_FILES`) and are served
offline from the app folder — nothing is loaded from the network at runtime.

| File | Event |
| --- | --- |
| `ui-open.wav` | a modal / form opens |
| `ui-tab.wav` | tab or page switch (quietest cue of the set) |
| `ui-ok.wav` | a form, case, passenger or setting was saved |
| `ui-warn.wav` | warning |
| `ui-error.wav` | validation error, server error, failed action |
| `ui-login.wav` | successful login |
| `ui-login-error.wav` | failed login |

## Origin and licence

All seven files are **derived** (trimmed, re-levelled, faded) from the free
**Kenney "Interface Sounds" (1.0)** pack, released under **CC0 1.0 Universal**
(public domain dedication): free to use and redistribute in personal,
educational and commercial projects, attribution not mandatory, no copyleft.

Licence text as shipped with the pack (`LICENSE.txt` next to the WAV files):

```
Interface Sounds (1.0)

Created/distributed by Kenney (www.kenney.nl)
Creation date: 11-02-2020

License: (Creative Commons Zero, CC0)
http://creativecommons.org/publicdomain/zero/1.0/

This content is free to use in personal, educational and commercial projects.
Support us by crediting Kenney or www.kenney.nl (this is not mandatory)
```

* Pack page: <https://kenney.nl/assets/interface-sounds>
* Licence: <http://creativecommons.org/publicdomain/zero/1.0/>
* Copy the bytes were taken from (mirror that ships the pack's WAVs, pinned to
  an immutable commit):
  `https://github.com/Calinou/kenney-interface-sounds` @
  `4596a49eaf5a533948d49a47467f606bcdea70ff`,
  directory `addons/kenney_interface_sounds` —
  e.g. <https://raw.githubusercontent.com/Calinou/kenney-interface-sounds/4596a49eaf5a533948d49a47467f606bcdea70ff/addons/kenney_interface_sounds/tick_001.wav>

Each source is sha256-pinned in `lab/scripts/gen_sfx.py`, and the script
refuses to use a file whose hash does not match, so the exact bytes below are
the only ones that can produce the shipped output.

## Per-file provenance

Everything is mono, PCM 16-bit, 44100 Hz. Levels are peaks, not loudness; the
pack ships near full scale and the panel needs quiet cues, so every file is
re-levelled downwards.

| File | Origin (source file) | Source sha256 | Duration | Peak | Size |
| --- | --- | --- | --- | --- | --- |
| `ui-open.wav` | `tick_001.wav` | `fee217e21731da4be1f0347fd2632296259b8bfdca343c936fe60dc64bce280e` | 45.0 ms | -15.0 dBFS | 4 012 B |
| `ui-tab.wav` | `tick_004.wav` | `7cf0ab362d64d3cc2d25a89528662ed21abd5c67a425dc0f4d1013871a481bdd` | 55.1 ms | -18.0 dBFS | 4 908 B |
| `ui-ok.wav` | `confirmation_001.wav` | `f9d0ef5a5c740c2ac250ac3876db9e4340669044a5bf78ae09192918db3e2ebc` | 294.6 ms | -10.0 dBFS | 26 028 B |
| `ui-warn.wav` | `bong_001.wav` | `f5d5f83b13edc5321352c2ba57972d1400151e3f27f1c18fcda07d261358511a` | 132.1 ms | -10.0 dBFS | 11 692 B |
| `ui-error.wav` | `error_005.wav` (cut to 380 ms) | `8e685ba1d408d3029c1a084a238265fa0a2b439d374fbfc8ba6cfef6edfefb7b` | 380.0 ms | -10.0 dBFS | 33 560 B |
| `ui-login.wav` | `confirmation_003.wav` | `d9b3719731a409c8109c5a049aceffbff9ff6c0ffe074a02fe52664393139b79` | 323.6 ms | -10.0 dBFS | 28 588 B |
| `ui-login-error.wav` | `error_006.wav` (cut to 480 ms) | `7ed3f06867e213edcb7b314c693e30a6ffe5ea5c694c19b130c4727810a5729a` | 480.0 ms | -10.0 dBFS | 42 380 B |

Total 151 168 bytes (147.6 KB). The two cuts remove trailing silence only —
both error cues have decayed into the noise floor by ~280 ms — so nothing
audible was dropped.

Sample rate 44100 Hz for all seven; nothing is longer than 600 ms.

Processing applied to every file (`lab/scripts/gen_sfx.py`):

1. take the source as-is, or its first N ms for the two trimmed cues;
2. remove the DC offset (subtract the mean);
3. apply a 5 ms raised-cosine fade in and fade out (no clicks);
4. reset the peak to the level in the table above;
5. write mono PCM 16-bit 44100 Hz.

No compression, no resampling, no channel mixing (every source is already
mono).

## Regenerating

```
"C:/Program Files/Python310/python.exe" lab/scripts/gen_sfx.py
```

The script is deterministic — no randomness — and every source is sha256-pinned,
so it rewrites byte-identical files (verify with the sha256 column above). It
downloads each source once into a cache under the system temp directory and
reuses it afterwards; on a machine with no network, pass a directory holding the
pack's WAVs:

```
"C:/Program Files/Python310/python.exe" lab/scripts/gen_sfx.py --src-dir <pack-dir>
```

Releases never need this: the generated WAVs are committed and the release build
only copies them.

## Content note

These are synthetic UI cues from a public-domain pack. They contain no speech,
no music, no personal data and no secrets — nothing here is confidential, and
the files carry no metadata beyond the 44-byte RIFF/WAVE header (the `wave`
module emits no `LIST`/`INFO` chunk).
