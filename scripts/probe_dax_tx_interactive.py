"""Interactive DAX TX device probe.

Walks through candidate output devices, plays a 3-second 1kHz tone to each,
and asks you whether the radio's DAX TX meter moved. After all candidates,
prints which combinations worked.

Usage: python scripts\probe_dax_tx_interactive.py

Open SmartSDR's DAX side panel (TX tab) and watch the TX meter / signal level
during each test.
"""
from __future__ import annotations

import math
import sys

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
        if "MIC" in name.upper():
            continue
        host = hostapis[d["hostapi"]]["name"]
        out.append((i, d, host))
    return out


def gen_tone(seconds: float, freq: float, sample_rate: int) -> np.ndarray:
    n = int(sample_rate * seconds)
    t = np.arange(n) / sample_rate
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

    print(f"\nFound {len(candidates)} candidate device(s).")
    print("For each test, watch SmartSDR's DAX TX meter (DAX panel, TX tab).")
    print("Press Enter to start the next test, type 'y' if the meter moved,")
    print("'n' if it didn't, or 'q' to quit.\n")

    # Pre-filter: only test the 24 kHz rate since SmartSDR DAX is 24kHz native.
    # Add 48k only as a fallback if nothing at 24k works.
    primary_rate = 24000
    results: list[tuple[int, str, str, int, str]] = []

    for idx, d, host in candidates:
        label = f"[{idx}] {d['name'][:55]} ({host})"
        prompt = input(f"\nTest: {label} @ {primary_rate} Hz  [Enter to play, q to quit]: ").strip().lower()
        if prompt == "q":
            break
        try:
            tone = gen_tone(3.0, 1000.0, primary_rate)
            print("  playing...", flush=True)
            sd.play(tone, samplerate=primary_rate, device=idx, blocking=True)
            print("  done.")
        except Exception as e:
            print(f"  FAILED: {e}")
            results.append((idx, d["name"], host, primary_rate, "error"))
            continue
        ans = input("  Did the DAX TX meter move? [y/n]: ").strip().lower()
        results.append((idx, d["name"], host, primary_rate, ans))

    print("\n=== Summary ===")
    moved = [r for r in results if r[4] == "y"]
    if moved:
        print("Devices where the meter MOVED:")
        for idx, name, host, sr, _ in moved:
            print(f"  [{idx}] {name} ({host}) @ {sr} Hz")
        first = moved[0]
        print(f"\nUse this device. Set audio.tx_device in config to a substring that")
        print(f"matches '{first[1]}' and the host API will be auto-preferred.")
    else:
        print("No device moved the meter. The DAX TX routing in SmartSDR is broken.")
        print("Open SmartSDR DAX panel → TX → confirm DAX TX is on AND bound to your TX slice.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
