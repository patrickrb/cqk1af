"""FlexLib-only RX audio probe. Connects via FlexLib (no sounddevice / DAX
device opened), opens a DAX RX 1 stream, and reports peak / RMS for each
DataReady event. Use this to verify the FlexLib path delivers real audio
without touching SmartSDR's PC Audio routing.

Run:  .\\.venv\\Scripts\\python.exe scripts/probe_flexlib_rx_audio.py
"""
from __future__ import annotations

import math
import sys
import threading
import time
from pathlib import Path


def main() -> None:
    from pythonnet import load
    try:
        load("coreclr")
    except Exception:
        pass
    import clr

    flex = Path(__file__).resolve().parent.parent / "runtime" / "flexlib"
    for dll in ("FlexLib", "Util", "Vita", "Flex.UiWpfFramework"):
        p = flex / f"{dll}.dll"
        if p.exists():
            clr.AddReference(str(p))

    from Flex.Smoothlake.FlexLib import API
    import numpy as np

    API.ProgramName = "cqk1af-rxprobe"
    API.IsGUI = True
    API.Init()
    time.sleep(2.0)
    radios = list(API.RadioList)
    if not radios:
        sys.exit("No FlexRadio discovered on the network")
    r = radios[0]
    r.Connect()
    time.sleep(2.0)

    print(f"Connected to {r.Model} {r.Serial}, DAXOn={r.DAXOn}")
    print("Existing DAX RX streams on radio:")
    for st in list(r.DAXRXAudioStreamList):
        print(f"  StreamID={int(st.StreamID):#x} DAXChannel={st.DAXChannel} BytesPerSec={st.BytesPerSecFromRadio}")
    for s in list(r.SliceList):
        print(f"  Slice {s.Index} ({s.Letter}): freq={s.Freq} MHz, mode={s.DemodMode}, DAXChannel={s.DAXChannel}, mute={s.Mute}, gain={s.AudioGain}")
    print()

    stream_holder = {"s": None}
    ready = threading.Event()

    def on_added(stream):
        stream_holder["s"] = stream
        ready.set()

    r.DAXRXAudioStreamAdded += on_added
    r.RequestDAXRXAudioStream(1)
    if not ready.wait(timeout=5.0):
        sys.exit("Timed out waiting for DAXRXAudioStreamAdded")
    stream = stream_holder["s"]
    stream.RXGain = 50
    print(f"Got stream: StreamID={int(stream.StreamID):#x} DAXChannel={stream.DAXChannel}")
    print()

    cb_count = [0]
    max_peak = [0.0]
    sum_sq = [0.0]
    sum_n = [0]
    last_report = [time.time()]

    def on_data(_s, data):
        cb_count[0] += 1
        # L channel from stereo-interleaved Single[]
        arr = np.asarray(list(data), dtype=np.float32)
        mono = arr[0::2]
        peak = float(np.max(np.abs(mono))) if mono.size else 0.0
        max_peak[0] = max(max_peak[0], peak)
        sum_sq[0] += float(np.sum(mono * mono))
        sum_n[0] += int(mono.size)
        now = time.time()
        if now - last_report[0] >= 0.5:
            last_report[0] = now
            rms = math.sqrt(sum_sq[0] / max(1, sum_n[0]))
            dbfs = 20.0 * math.log10(rms + 1e-12)
            print(
                f"  callbacks={cb_count[0]:4d}  rms={dbfs:7.1f} dBFS  peak={max_peak[0]:.4f}  "
                f"bytesPerSec={stream.BytesPerSecFromRadio}"
            )
            sum_sq[0] = 0.0
            sum_n[0] = 0
            max_peak[0] = 0.0

    stream.DataReady += on_data
    print("Listening for 15 seconds. Talk into the radio or tune to a busy frequency.")
    time.sleep(15.0)
    print()
    print(f"Total callbacks: {cb_count[0]}")

    stream.Close()
    r.Disconnect()


if __name__ == "__main__":
    main()
