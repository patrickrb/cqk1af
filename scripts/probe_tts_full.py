"""End-to-end TTS smoke test through the full AudioPipeline.speak() path.

Builds the App with the FlexLib backend, generates TTS via Piper, pushes the
PCM through AudioPipeline.speak() which routes via radio.add_tx_audio().
Exercises the same code path the MCP `_narrate_tx` tool uses, minus the
interlock token validation.

Prereqs (see scripts/probe_flexlib_tx.py docstring for the full list):
  * runtime/flexlib/FlexLib.dll built and unblocked
  * DAX.exe TX channel DISABLED
  * radio.backend == "flexlib" and radio.mock_mode == false in config

Usage: python scripts\\probe_tts_full.py "your text here"
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from cqk1af.app import App  # noqa: E402
from cqk1af.config import load_settings  # noqa: E402


async def main() -> int:
    text = " ".join(sys.argv[1:]) or "This is a test of K1 Alpha Foxtrot."
    settings = load_settings()
    settings.radio.mock_mode = False
    # Honor whatever backend is in config (default.yaml). Don't force flexlib.

    app = App.build(settings)
    print(f"Backend: {settings.radio.backend}")

    print(f"Connecting to radio...")
    await app.radio.connect()
    print(f"  model={app.radio.state.model} serial={app.radio.state.serial}")
    print(f"  supports_native_tx_audio={app.radio.supports_native_tx_audio}")

    print("Starting audio pipeline (Piper TTS init)...")
    await app.pipeline.start()
    print(f"  tts_engine={app.pipeline.status.tts_engine}")
    print(f"  tx_device={app.pipeline.status.tx_device}")
    print(f"  can_tx={app.pipeline.can_tx}")
    for note in app.pipeline.status.notes:
        print(f"  note: {note}")

    if not app.pipeline.can_tx:
        print("Pipeline cannot TX — fix the notes above and retry.")
        await app.shutdown()
        return 1

    # Arm the interlock and have it issue a real token for us. Using the
    # private _issue_token to skip the approval await — this is a probe.
    await app.interlock.arm(True, source="probe")
    token = app.interlock._issue_token(slice_id=0, purpose="probe")

    print(f"\nKeying PTT, synthesizing + sending: {text!r}")
    try:
        await app.radio.set_ptt(True, gate_token=token)
        if not app.radio.supports_native_tx_audio:
            await asyncio.sleep(0.3)  # WASAPI device path needs settle
        duration = await app.pipeline.speak(text)
        print(f"  spoke for {duration:.2f}s")
        await asyncio.sleep(0.3)
    finally:
        await app.radio.set_ptt(False)
        print("Un-keyed PTT.")

    await app.shutdown()
    print("Done. Watch SmartSDR's TX waveform.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
