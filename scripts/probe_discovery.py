"""Listen for SmartSDR discovery beacons.

FlexRadio radios broadcast discovery packets on UDP 4992. Each is a VITA-49
packet whose payload is an ASCII string of key=value pairs (the same format the
TCP control protocol uses).

Run this with the radio powered on and on the same LAN; it'll print the first
few beacons it sees. Hit Ctrl-C when done.
"""
from __future__ import annotations

import socket
import sys


def main(seconds: float = 10.0) -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    except Exception:
        pass
    sock.bind(("0.0.0.0", 4992))
    sock.settimeout(seconds)
    print(f"Listening on UDP 4992 for {seconds:.0f}s...")
    seen: set[str] = set()
    try:
        while True:
            try:
                data, addr = sock.recvfrom(2048)
            except socket.timeout:
                break
            print(f"\n--- packet from {addr} ({len(data)} bytes) ---")
            # Skip VITA-49 header (most flex beacons: 28 bytes header, payload is ASCII)
            # We'll try a few offsets to find printable text.
            for offset in (28, 16, 8, 0):
                tail = data[offset:]
                try:
                    text = tail.decode("ascii", errors="replace")
                except Exception:
                    continue
                if any(c.isprintable() for c in text[:64]):
                    print(f"@offset {offset}: {text[:400]}")
                    break
            key = f"{addr[0]}:{len(data)}"
            seen.add(key)
    finally:
        sock.close()
    if not seen:
        print("No beacons. Is the radio powered on and on this LAN? Is anything firewalled on UDP 4992?")
    return 0


if __name__ == "__main__":
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 10.0
    raise SystemExit(main(secs))
