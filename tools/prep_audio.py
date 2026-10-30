# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy", "scipy", "soundfile", "pyloudnorm"]
# ///
"""Build the show corpus (assets/sounds/) from the raw downloads (assets/raw/).

    uv run tools/prep_audio.py           # rebuild every output
    uv run tools/prep_audio.py --check   # measure and report, write nothing

tools/audio_prep.toml is the recipe: one [[sound]] per output file, naming
its raw source (or several raw layers mixed together), trim window, fades,
whether it loops, and its loudness tier. Every output goes through the same
chain:

  1. stereo 44.1kHz — the Pi's dmix format, so nothing resamples at show time
  2. DC removal + high-pass (default 30Hz): energy the Polk PSW10 sub can't
     play but still strains its cone trying to (HARDWARE.md § 6)
  3. trim, then either a loop crossfade (the tail blends into the head, so
     the bed restarts without a seam) or fade in/out
  4. normalize to the tier's integrated loudness (LUFS) plus the master
     trim — the show gets louder as it builds, every file in a pool matches
     its neighbours, and stacked layers keep headroom
  5. peak-limit to the ceiling, so stacked layers keep headroom in dmix

Raw files are never modified. Outputs are regenerated from scratch each run,
and any .wav under assets/sounds/ the recipe no longer produces is removed —
so assets/sounds/ is always exactly the recipe's output. The extra deps are
declared inline above: uv runs this in its own environment, and the show
(and the Pi) never install them.
"""

import argparse
import sys
import tomllib
from math import gcd
from pathlib import Path

import numpy as np
import pyloudnorm as pyln
import soundfile as sf
from scipy.signal import butter, resample_poly, sosfilt

ROOT = Path(__file__).resolve().parents[1]
RECIPE = Path(__file__).with_name("audio_prep.toml")
RAW = ROOT / "assets" / "raw"
OUT = ROOT / "assets" / "sounds"
RATE = 44100

# Limiter look-ahead/release window. Long enough not to distort bass (a 30Hz
# cycle is 33ms); the gain dips only around the peaks that need it.
LIMIT_WINDOW = 0.040

# More gain reduction than this means the tier target is unrealistic for the
# material — the limiter would be squashing it audibly.
LIMIT_WARN_DB = 6.0


def load(path: Path, highpass: float) -> np.ndarray:
    """Read a raw file as float stereo at RATE, DC-free and high-passed."""
    data, sr = sf.read(path, always_2d=True)
    if data.shape[1] == 1:
        data = np.repeat(data, 2, axis=1)
    data = data[:, :2]
    if sr != RATE:
        g = gcd(RATE, sr)
        data = resample_poly(data, RATE // g, sr // g, axis=0)
    data = data - data.mean(axis=0)  # before filtering: a DC step rings
    sos = butter(4, highpass, btype="highpass", fs=RATE, output="sos")
    return sosfilt(sos, data, axis=0)


def trim(data: np.ndarray, start: float, end: float | None) -> np.ndarray:
    a = int(start * RATE)
    b = len(data) if end is None else min(len(data), int(end * RATE))
    if b <= a:
        raise ValueError(f"empty trim window {start}-{end}s")
    return data[a:b]


def speed(data: np.ndarray, factor: float) -> np.ndarray:
    """Varispeed: >1 is faster and higher, like tape."""
    if factor == 1.0:
        return data
    up, down = 1000, round(1000 * factor)
    g = gcd(up, down)
    return resample_poly(data, up // g, down // g, axis=0)


def fade(data: np.ndarray, fade_in: float, fade_out: float) -> np.ndarray:
    out = data.copy()
    n_in = min(len(out), int(fade_in * RATE))
    n_out = min(len(out), int(fade_out * RATE))
    if n_in:
        out[:n_in] *= np.sin(np.linspace(0, np.pi / 2, n_in))[:, None] ** 2
    if n_out:
        out[-n_out:] *= np.cos(np.linspace(0, np.pi / 2, n_out))[:, None] ** 2
    return out


def loop_crossfade(data: np.ndarray, seconds: float) -> np.ndarray:
    """Blend the last `seconds` into the head (equal power): the file's end
    now runs straight on into its start, so a respawned bed has no seam."""
    n = int(seconds * RATE)
    if 2 * n >= len(data):
        raise ValueError("loop too short for its crossfade")
    t = np.linspace(0, np.pi / 2, n)[:, None]
    head = data[:n] * np.sin(t) + data[-n:] * np.cos(t)
    return np.concatenate([head, data[n:-n]])


def limit(data: np.ndarray, ceiling_db: float) -> tuple[np.ndarray, float]:
    """Look-ahead peak limiter; returns (audio, max gain reduction in dB).

    Needed gain per sample, then a sliding minimum, then a moving average no
    wider than that minimum — every averaged gain is still at or below what
    each sample in reach needs, so no sample can exceed the ceiling."""
    ceiling = 10 ** (ceiling_db / 20)
    peak = np.max(np.abs(data), axis=1)
    need = np.minimum(1.0, ceiling / np.maximum(peak, 1e-12))
    if need.min() >= 1.0:
        return data, 0.0
    w = int(LIMIT_WINDOW * RATE) | 1
    pad = np.pad(need, w, mode="edge")
    windows = np.lib.stride_tricks.sliding_window_view(pad, 2 * w + 1)
    held = np.pad(windows.min(axis=1), w // 2, mode="edge")
    gain = np.convolve(held, np.ones(w) / w, mode="valid")
    return data * gain[:, None], float(-20 * np.log10(gain.min()))


def build(entry: dict, defaults: dict, tiers: dict) -> tuple[np.ndarray, float]:
    """Render one [[sound]] entry; returns (audio, limiter reduction dB)."""
    opt = {**defaults, **entry}
    layers = entry.get("layers") or [
        {k: entry[k] for k in ("src", "start", "end") if k in entry}
    ]
    parts = []
    for layer in layers:
        data = load(RAW / layer["src"], opt["highpass"])
        data = trim(data, layer.get("start", 0.0), layer.get("end"))
        data = speed(data, layer.get("speed", 1.0)) * 10 ** (layer.get("gain", 0.0) / 20)
        parts.append((int(layer.get("at", 0.0) * RATE), data))
    mix = np.zeros((max(at + len(d) for at, d in parts), 2))
    for at, data in parts:
        mix[at : at + len(data)] += data

    if opt.get("loop"):
        mix = loop_crossfade(mix, opt["loop_crossfade"])
        mix = fade(mix, 0.005, 0.005)  # the player restart is a hard edge anyway
    else:
        mix = fade(mix, opt["fade_in"], opt["fade_out"])

    target = tiers[opt["tier"]] + opt["master"]
    meter = pyln.Meter(RATE)
    mix = mix * 10 ** ((target - meter.integrated_loudness(mix)) / 20)
    return limit(mix, opt["ceiling"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="report only")
    args = parser.parse_args()

    recipe = tomllib.loads(RECIPE.read_text())
    defaults, tiers, entries = recipe["defaults"], recipe["tiers"], recipe["sound"]

    outs = [e["out"] for e in entries]
    dupes = {o for o in outs if outs.count(o) > 1}
    if dupes:
        sys.exit(f"duplicate outputs in recipe: {', '.join(sorted(dupes))}")

    problems = 0
    meter = pyln.Meter(RATE)
    for entry in entries:
        audio, reduction = build(entry, defaults, tiers)
        lufs = meter.integrated_loudness(audio)
        peak = 20 * np.log10(np.max(np.abs(audio)) + 1e-12)
        flag = ""
        if reduction > LIMIT_WARN_DB:
            flag = f"  ⚠ limiter -{reduction:.1f}dB: tier too hot for this material"
            problems += 1
        print(
            f"{entry['out']:48s} {len(audio) / RATE:5.1f}s  {lufs:6.1f} LUFS  "
            f"peak {peak:5.1f}{flag}"
        )
        if not args.check:
            dest = OUT / entry["out"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            sf.write(dest, audio, RATE, subtype="PCM_16")

    if not args.check:
        for stale in sorted(set(OUT.rglob("*.wav")) - {OUT / o for o in outs}):
            print(f"removed stale {stale.relative_to(OUT)}")
            stale.unlink()
    if problems:
        sys.exit(f"{problems} output(s) flagged")


if __name__ == "__main__":
    main()
