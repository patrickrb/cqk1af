"""Manual probe: connect, arm, kick check_frequency, watch WS, approve each request."""
from __future__ import annotations

import asyncio
import json

import httpx
import websockets


async def main() -> None:
    base = "http://127.0.0.1:8000"
    ws_url = "ws://127.0.0.1:8000/ws"

    async with httpx.AsyncClient(base_url=base, timeout=30.0) as http:
        print("connect_radio:", (await http.post("/api/radio/connect")).status_code)
        print("arm:", (await http.post("/api/control/arm", json={"armed": True})).status_code)
        print("tune:", (await http.post("/api/radio/tune", json={"freq_hz": 14_270_000, "mode": "USB"})).status_code)

    async with websockets.connect(ws_url) as ws:
        hello = json.loads(await ws.recv())
        print(f"ws.hello state={hello['payload']['state']['state']}")

        async def kick_check() -> None:
            async with httpx.AsyncClient(base_url=base, timeout=120.0) as http2:
                r = await http2.post("/api/operate/check_frequency", json={"freq_hz": 14_270_000, "attempts": 3})
                print(f"\ncheck_frequency RESULT status={r.status_code} body={r.text}")

        check_task = asyncio.create_task(kick_check())
        approve_count = 0
        async with httpx.AsyncClient(base_url=base, timeout=30.0) as http3:
            while True:
                try:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5.0))
                except asyncio.TimeoutError:
                    print("(WS quiet for 5s — done)")
                    break
                if msg["type"] == "tx_approval_requested":
                    req_id = msg["payload"]["req_id"]
                    approve_count += 1
                    print(f"\n[ws]  approval #{approve_count} req_id={req_id}")
                    resp = await http3.post(
                        "/api/control/approve_tx",
                        json={"req_id": req_id, "approved": True},
                    )
                    print(f"[rest] approve_tx -> {resp.status_code} {resp.text}")
                elif msg["type"] == "state_changed":
                    print(f"[ws]  state {msg['payload']['prev']} -> {msg['payload']['next']} ({msg['payload']['reason']})")
                elif msg["type"] == "session_armed":
                    print(f"[ws]  session_armed={msg['payload']['armed']}")
                elif msg["type"] == "radio_slice":
                    p = msg["payload"]
                    if "ptt_on" in p:
                        print(f"[ws]  ptt_on={p['ptt_on']}")
                elif msg["type"] == "s_meter":
                    pass
                else:
                    print(f"[ws]  {msg['type']} {msg.get('payload', {})}")
                if check_task.done():
                    break

        try:
            await asyncio.wait_for(check_task, timeout=2.0)
        except asyncio.TimeoutError:
            check_task.cancel()


asyncio.run(main())
