"""Connect to the radio's TCP control port and print everything it sends.

Usage:
    python scripts\probe_tcp.py <radio-ip>           # listen for 15s
    python scripts\probe_tcp.py <radio-ip> 30        # listen for 30s

This bypasses the dashboard entirely. It connects to port 4992, sends the
subscription commands, then prints every line for inspection so you can verify
the protocol assumptions in cqk1af.radio.tcp_protocol against your firmware.
"""
from __future__ import annotations

import asyncio
import sys


async def probe(host: str, seconds: float) -> int:
    print(f"Connecting to {host}:4992 ...", flush=True)
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, 4992), timeout=5.0
        )
    except (asyncio.TimeoutError, OSError) as e:
        print(f"CONNECT FAILED: {e}", flush=True)
        return 1
    print("Connected. Sending subscribe commands ...", flush=True)

    seq = 0

    def send(line: str) -> None:
        nonlocal seq
        seq += 1
        writer.write(f"C{seq}|{line}\n".encode("ascii"))

    for cmd in ("sub radio all", "sub slice all", "sub meter all", "sub tx all", "info"):
        send(cmd)
    await writer.drain()
    print(f"Sent {seq} commands. Listening for {seconds:.0f}s ...\n", flush=True)

    buf = b""
    deadline = asyncio.get_event_loop().time() + seconds
    while asyncio.get_event_loop().time() < deadline:
        remaining = deadline - asyncio.get_event_loop().time()
        try:
            chunk = await asyncio.wait_for(reader.read(4096), timeout=remaining)
        except asyncio.TimeoutError:
            break
        if not chunk:
            print("(server closed connection)", flush=True)
            break
        buf += chunk
        while b"\n" in buf:
            line, _, buf = buf.partition(b"\n")
            text = line.decode("ascii", errors="replace").rstrip("\r")
            print(text, flush=True)
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    host = sys.argv[1]
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 15.0
    return asyncio.run(probe(host, seconds))


if __name__ == "__main__":
    raise SystemExit(main())
