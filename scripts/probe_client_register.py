"""Probe whether registering as a GUI client unlocks mic source control.

SmartSDR clients identify themselves with ``client gui`` (and friends) shortly
after connecting. Without that, the radio may treat us as a read-only telemetry
client and reject TX-property writes.

Usage: python scripts\probe_client_register.py <radio-ip>
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
                # Echo any client-related status updates
                if "client" in text.lower() and text.startswith("S"):
                    print(f"  [STATUS] {text}", flush=True)

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

    await asyncio.sleep(0.4)

    print("\n--- Register as GUI client ---", flush=True)
    register_attempts = [
        "client gui",
        "client gui cqk1af",
        "client program cqk1af",
        "client station cqk1af",
        "client local_ptt 1",
        "client takeoff",
    ]
    for cmd in register_attempts:
        code, msg = await send(cmd)
        print(f"  {cmd:<35s} -> code={code:>10x} msg={msg!r}", flush=True)

    await send("sub tx all")
    await asyncio.sleep(0.5)
    print(f"\nmic_selection after registration = {last_mic[0]!r}\n", flush=True)

    print("--- Retry mic source switch (now as GUI client) ---", flush=True)
    retry_cmds = [
        "mic input DAX",
        "transmit set mic_selection=DAX",
        "radio set mic_input=DAX",
    ]
    for cmd in retry_cmds:
        before = last_mic[0]
        code, msg = await send(cmd)
        await asyncio.sleep(0.5)
        after = last_mic[0]
        changed = " CHANGED!" if before != after else ""
        print(
            f"  {cmd:<40s}  code={code:>10x} msg={msg!r:<25s}  mic={after!r}{changed}",
            flush=True,
        )

    # Reset
    if last_mic[0] != "MIC":
        await send("mic input MIC")
        await send("transmit set mic_selection=MIC")

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
