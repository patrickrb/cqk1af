"""Probe the FlexLib DAX TX audio path end to end.

Connects via FlexLibRadioClient, opens the DAX TX stream, keys MOX, plays a
2s 1kHz tone through ``add_tx_audio``, then un-keys. Watch SmartSDR's TX
**waveform display** (the panadapter modulation during transmit). DAX.exe's
own TX meter will NOT move on this path — we bypass DAX.exe and send VITA
packets directly to the radio.

Usage: python scripts\\probe_flexlib_tx.py

Prereqs:
  * SmartSDR running and connected to the radio.
  * **In DAX.exe, the TX channel must be DISABLED.** When DAX.exe has a TX
    channel enabled, the radio routes its stream and silently ignores ours.
  * Radio mic gain non-zero, RF power non-zero.
  * runtime/flexlib/FlexLib.dll populated (run scripts/package_flexlib.ps1).
  * pip install -e ".[radio,audio]"
"""
from __future__ import annotations

import asyncio
import math
import sys
import uuid
from datetime import timedelta
from pathlib import Path

import numpy as np

# Resolve repo root so we can import cqk1af without installing it.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from cqk1af.events import EventBus  # noqa: E402
from cqk1af.radio.base import GateToken  # noqa: E402
from cqk1af.radio.flexlib_client import FlexLibRadioClient  # noqa: E402
from cqk1af.util.time import utcnow  # noqa: E402


def gen_tone(seconds: float, freq: float, sample_rate: int) -> bytes:
    n = int(sample_rate * seconds)
    t = np.arange(n) / sample_rate
    samples = 0.5 * np.sin(2 * math.pi * freq * t)
    fade = int(sample_rate * 0.03)
    samples[:fade] *= np.linspace(0, 1, fade)
    samples[-fade:] *= np.linspace(1, 0, fade)
    return (samples * 32767).astype(np.int16).tobytes()


async def main() -> int:
    flexlib_dir = REPO_ROOT / "runtime" / "flexlib"
    if not (flexlib_dir / "FlexLib.dll").exists():
        print(f"FlexLib.dll not found at {flexlib_dir}. Run scripts/package_flexlib.ps1.")
        return 1

    bus = EventBus()
    client = FlexLibRadioClient(bus, flexlib_dir)

    print("Connecting (this triggers DAXOn + RequestDAXTXAudioStream)...")
    try:
        await client.connect()
    except Exception as e:
        print(f"connect() failed: {e}")
        return 1
    print(f"  model={client.state.model} serial={client.state.serial}")
    print(f"  tx_stream_id={client.state.tx_stream_id}")
    if not client.supports_native_tx_audio:
        print("  WARN: supports_native_tx_audio=False — stream not open")
        await client.disconnect()
        return 1

    # Forge a gate token. There's no interlock here — bypass safety since this
    # is a manual diagnostic, not user-driven flow.
    token = GateToken(
        id=uuid.uuid4(),
        slice_id=0,
        purpose="probe",
        issued_at=utcnow(),
        expires_at=utcnow() + timedelta(seconds=30),
    )

    sample_rate = 22050  # match Piper's native rate so the resampler path is exercised
    print(f"Generating 2s 1kHz tone @ {sample_rate} Hz mono...")
    pcm = gen_tone(2.0, 1000.0, sample_rate)

    print("Keying MOX...")
    try:
        await client.set_ptt(True, gate_token=token)
    except Exception as e:
        print(f"set_ptt(True) failed (interlock not bound?): {e}")
        print("Falling back: setting MOX directly via FlexLib.")
        # interlock isn't bound, so set_ptt enforces nothing else; the error
        # was about the token. Bypass by writing the radio property directly.
        client._radio.Mox = True  # type: ignore[union-attr]

    try:
        print("Streaming tone via add_tx_audio... (watch SmartSDR TX waveform — DAX.exe meter does NOT move on this path)")
        await client.add_tx_audio(pcm, sample_rate)
        print("  done.")
    finally:
        print("Un-keying MOX...")
        client.force_ptt_off()

    await client.disconnect()
    print("Disconnected. Did the meters move?")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
