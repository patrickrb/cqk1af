"""Approval flow through the dashboard WS.

Split into two halves to avoid mixing the FastAPI event loop with the test's
outer asyncio loop:

  - Test A: a published ``TxApprovalRequested`` event is fanned out to WS clients.
  - Test B: a WS ``approve_tx`` message resolves a pending approval.
"""
from __future__ import annotations

import json
import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from cqk1af.app import App
from cqk1af.config import Settings
from cqk1af.dashboard.api import build_dashboard_app
from cqk1af.events import TxApprovalRequested
from cqk1af.util.time import utcnow


@pytest.fixture
async def client_app():
    settings = Settings()
    settings.safety.require_manual_tx_approval = True
    app = App.build(settings)
    fastapi_app = build_dashboard_app(app)
    yield app, fastapi_app
    await app.shutdown()


def test_published_approval_event_reaches_ws_client(client_app) -> None:
    app, fastapi_app = client_app
    with TestClient(fastapi_app) as client:
        client.post("/api/radio/connect")
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # hello

            # Inject a TxApprovalRequested directly onto the bus from the
            # FastAPI event loop. ``portal.call`` is what TestClient uses
            # internally to schedule coroutines on the app's loop.
            req_id = uuid.uuid4()
            ev = TxApprovalRequested(
                req_id=req_id,
                summary="test approval",
                payload={"foo": "bar"},
                expires_at=utcnow() + timedelta(seconds=2),
            )
            # FastAPI's TestClient exposes the running loop via the underlying
            # portal so we can publish from this synchronous test thread.
            client.portal.call(app.bus.publish, ev)  # type: ignore[attr-defined]

            # Drain WS until we observe the forwarded message.
            seen = None
            for _ in range(20):
                msg = ws.receive_json()
                if msg["type"] == "tx_approval_requested":
                    seen = msg
                    break
            assert seen is not None
            assert seen["payload"]["req_id"] == str(req_id)
            assert seen["payload"]["summary"] == "test approval"


def test_approve_tx_via_ws_resolves_pending(client_app) -> None:
    app, fastapi_app = client_app
    with TestClient(fastapi_app) as client:
        client.post("/api/radio/connect")

        # Pre-arm via REST (also exercises the route).
        r = client.post("/api/control/arm", json={"armed": True})
        assert r.status_code == 200

        token_box: dict = {}

        async def request_then_capture():
            token_box["token"] = await app.interlock.request_tx(
                summary="test tx", slice_id=0, purpose="test", timeout_s=2.0
            )

        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # hello

            # Only NOW that the WS is connected do we kick off the request.
            client.portal.start_task_soon(request_then_capture)  # type: ignore[attr-defined]

            req_id = None
            for _ in range(50):
                msg = ws.receive_json()
                if msg["type"] == "tx_approval_requested":
                    req_id = msg["payload"]["req_id"]
                    break
            assert req_id, "did not see tx_approval_requested"

            ws.send_text(
                json.dumps({"type": "approve_tx", "payload": {"req_id": req_id, "approved": True}})
            )

            # Give the loop a chance to deliver resolution and return the token.
            for _ in range(50):
                if "token" in token_box and token_box["token"] is not None:
                    break
                # Pumping any incoming messages keeps the loop alive.
                try:
                    ws.receive_json()
                except Exception:
                    break
        assert token_box.get("token") is not None
