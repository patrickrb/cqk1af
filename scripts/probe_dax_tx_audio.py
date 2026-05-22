"""Play test tones to candidate DAX TX devices to find the one that actually
reaches the radio's modulator.

This script does NOT key PTT — it just plays audio. Watch SmartSDR's TX audio
waveform / "DAX" meter to see which device shows activity.

Usage: python scripts\probe_dax_tx_audio.py
"""
from __future__ import annotations

import math
import struct
import sys
import time

import numpy as np
import sounddevice as sd


def find_candidates() -> list[tuple[int, dict, str]]:
    hostapis = sd.query_hostapis()
    out = []
    for i, d in enumerate(sd.query_devices()):
        name = d["name"]
        if d["max_output_channels"] <= 0:
            continue
        if "DAX" not in name:
            continue
        if "RESERVED" in name.upper():
            continue
        if "RX" in name.upper() and "TX" not in name.upper():
            continue
        if "IQ" in name.upper():
            continue
        host = hostapis[d["hostapi"]]["name"]
        out.append((i, d, host))
    return out


def gen_tone(seconds: float, freq: float, sample_rate: int) -> np.ndarray:
    n = int(sample_rate * seconds)
    t = np.arange(n) / sample_rate
    # Half-amplitude to avoid clipping, fade in/out 30 ms.
    samples = 0.5 * np.sin(2 * math.pi * freq * t)
    fade = int(sample_rate * 0.03)
    samples[:fade] *= np.linspace(0, 1, fade)
    samples[-fade:] *= np.linspace(1, 0, fade)
    return (samples * 32767).astype(np.int16)


def main() -> int:
    candidates = find_candidates()
    if not candidates:
        print("No DAX TX output devices found.")
        return 1
    print(f"Found {len(candidates)} candidate device(s):")
    for idx, d, host in candidates:
        print(f"  [{idx}] {d['name']}  ({host})  channels={d['max_output_channels']}")

    rates_to_try = [24000, 48000, 22050]
    for idx, d, host in candidates:
        for sr in rates_to_try:
            print(f"\n>>> Playing 1s 1kHz tone to [{idx}] {d['name']!r} @ {sr} Hz ({host})...", flush=True)
            try:
                tone = gen_tone(1.0, 1000.0, sr)
                sd.play(tone, samplerate=sr, device=idx, blocking=True)
                print("    done.", flush=True)
            except Exception as e:
                print(f"    FAILED: {e}", flush=True)
            time.sleep(0.5)

    print("\nWatch SmartSDR's DAX TX meter or TX waveform display while each played.")
    print("Whichever device + sample rate caused activity is the right one.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
