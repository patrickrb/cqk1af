"""Probe which PTT command syntax the radio accepts.

Tries each candidate, records the reply (0=success, non-zero=error), keeps
each TX cycle under 100ms. Use a dummy load.

Usage: python scripts\probe_ptt_command.py <radio-ip>
"""
from __future__ import annotations

import asyncio
import sys


async def probe(host: str) -> int:
    print(f"Connecting to {host}:4992 ...", flush=True)
    reader, writer = await asyncio.open_connection(host, 4992)

    seq = 0
    pending: dict[int, asyncio.Future[str]] = {}

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
                if text.startswith("R"):
                    head, _, body = text[1:].partition("|")
                    try:
                        s = int(head)
                    except ValueError:
                        continue
                    fut = pending.pop(s, None)
                    if fut and not fut.done():
                        fut.set_result(body)

    loop = asyncio.get_event_loop()
    reader_task = asyncio.create_task(reader_loop())

    async def send(cmd: str, *, expect_reply: bool = True) -> str:
        nonlocal seq
        seq += 1
        s = seq
        fut: asyncio.Future[str] | None = None
        if expect_reply:
            fut = loop.create_future()
            pending[s] = fut
        writer.write(f"C{s}|{cmd}\n".encode("ascii"))
        await writer.drain()
        if fut is None:
            return ""
        try:
            return await asyncio.wait_for(fut, timeout=2.0)
        except asyncio.TimeoutError:
            pending.pop(s, None)
            return "(timeout)"

    # Settle and subscribe so we get a clean state.
    await asyncio.sleep(0.5)
    await send("sub tx all")
    await send("sub radio all")

    candidates_on = ["xmit 1", "transmit set mox=1", "mixer tx mox 1"]
    candidates_off = ["xmit 0", "transmit set mox=0", "mixer tx mox 0"]

    print("\n--- DUMMY LOAD CHECK BEFORE PROCEEDING ---", flush=True)
    print("Each candidate keys PTT for ~100ms. Antenna disconnected?\n", flush=True)

    for on, off in zip(candidates_on, candidates_off):
        print(f"\n> ON  : {on}", flush=True)
        reply_on = await send(on)
        print(f"  R: {reply_on!r}", flush=True)
        await asyncio.sleep(0.15)
        print(f"> OFF : {off}", flush=True)
        reply_off = await send(off)
        print(f"  R: {reply_off!r}", flush=True)
        await asyncio.sleep(0.3)

    writer.close()
    reader_task.cancel()
    try:
        await reader_task
    except asyncio.CancelledError:
        pass
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(asyncio.run(probe(sys.argv[1])))
