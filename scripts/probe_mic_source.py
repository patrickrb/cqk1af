"""Probe which command syntax actually changes the TX modulation source on a
SmartSDR v4 radio.

Tries each candidate command, then queries the transmit-subsystem status and
reports whether mic_selection changed.

Usage:
    python scripts\probe_mic_source.py <radio-ip>

No PTT keying — this only changes the source selection. Safe with antenna
connected.
"""
from __future__ import annotations

import asyncio
import re
import sys

_REPLY = re.compile(r"^R(\d+)\|([0-9A-Fa-f]+)\|(.*)$")
_STATUS_MIC = re.compile(r"\bmic_selection=(\S+)")


async def probe(host: str) -> int:
    reader, writer = await asyncio.open_connection(host, 4992)

    seq = 0
    pending: dict[int, asyncio.Future] = {}
    last_mic: list[str | None] = [None]
    mic_event = asyncio.Event()

    async def reader_loop() -> None:
        buf = b""
        while True:
            chunk = await reader.read(4096)
            if not chunk:
                return
            buf += chunk
            while b"\n" in buf:
                line, _, buf = buf.partition(b"\n")
                text = line.decode("ascii", errors="replace").rstrip("\r")
                m = _REPLY.match(text)
                if m:
                    s = int(m.group(1))
                    fut = pending.pop(s, None)
                    if fut and not fut.done():
                        fut.set_result((int(m.group(2), 16), m.group(3)))
                    continue
                mm = _STATUS_MIC.search(text)
                if mm:
                    last_mic[0] = mm.group(1)
                    mic_event.set()

    loop = asyncio.get_event_loop()
    rt = asyncio.create_task(reader_loop())

    async def send(cmd: str) -> tuple[int, str]:
        nonlocal seq
        seq += 1
        s = seq
        fut = loop.create_future()
        pending[s] = fut
        writer.write(f"C{s}|{cmd}\n".encode("ascii"))
        await writer.drain()
        try:
            return await asyncio.wait_for(fut, timeout=2.0)
        except asyncio.TimeoutError:
            pending.pop(s, None)
            return (-1, "(timeout)")

    # Settle, then subscribe so we get transmit status updates
    await asyncio.sleep(0.4)
    await send("sub tx all")
    await send("sub radio all")

    # Wait for the initial transmit status (sometimes immediate, sometimes after subscribe)
    try:
        await asyncio.wait_for(mic_event.wait(), timeout=2.0)
    except asyncio.TimeoutError:
        pass
    print(f"\nInitial mic_selection = {last_mic[0]!r}\n", flush=True)

    candidates = [
        "mic input DAX",
        "mic source DAX",
        "mic select DAX",
        "transmit set mic_selection=DAX",
        "transmit set mic_input=DAX",
        "transmit set mic_source=DAX",
        "transmit set mic=DAX",
        "radio set mic_input=DAX",
        "dax audio set 1 1",
        "dax audio set DAX 1",
        "dax audio enable 1",
        "transmit set dax=1",  # already known to be 1
    ]
    for cmd in candidates:
        before = last_mic[0]
        mic_event.clear()
        code, msg = await send(cmd)
        # Wait briefly for a status update
        try:
            await asyncio.wait_for(mic_event.wait(), timeout=0.5)
        except asyncio.TimeoutError:
            pass
        after = last_mic[0]
        changed = "  ← CHANGED" if before != after else ""
        print(
            f"  {cmd:<40s}  reply: code={code:>10x} msg={msg!r:<20s}  mic={after!r}{changed}",
            flush=True,
        )

    # Reset to MIC if we accidentally changed something
    print("\nResetting mic_selection back to MIC ...", flush=True)
    for reset_cmd in ("mic input MIC", "transmit set mic_selection=MIC"):
        code, msg = await send(reset_cmd)
        if code == 0:
            print(f"  reset via: {reset_cmd}", flush=True)
            break

    writer.close()
    rt.cancel()
    try:
        await rt
    except asyncio.CancelledError:
        pass
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(asyncio.run(probe(sys.argv[1])))
