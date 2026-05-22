"""Arming the session via REST should fan a session_armed message out over WS."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from cqk1af.app import App
from cqk1af.config import Settings
from cqk1af.dashboard.api import build_dashboard_app


@pytest.fixture
async def client_app():
    settings = Settings()
    settings.safety.require_manual_tx_approval = False
    app = App.build(settings)
    fastapi_app = build_dashboard_app(app)
    yield app, fastapi_app
    await app.shutdown()


def test_arm_via_rest_emits_session_armed_ws(client_app) -> None:
    app, fastapi_app = client_app
    with TestClient(fastapi_app) as client:
        client.post("/api/radio/connect")
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # hello

            r = client.post("/api/control/arm", json={"armed": True})
            assert r.status_code == 200
            assert r.json()["armed"] is True

            saw_armed = False
            for _ in range(30):
                msg = ws.receive_json()
                if msg["type"] == "session_armed" and msg["payload"]["armed"] is True:
                    saw_armed = True
                    break
            assert saw_armed, "did not see session_armed=true on WS"


def test_disarm_via_rest_emits_session_armed_false(client_app) -> None:
    app, fastapi_app = client_app
    with TestClient(fastapi_app) as client:
        client.post("/api/radio/connect")
        client.post("/api/control/arm", json={"armed": True})
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # hello

            client.post("/api/control/arm", json={"armed": False})
            saw_disarmed = False
            for _ in range(30):
                msg = ws.receive_json()
                if msg["type"] == "session_armed" and msg["payload"]["armed"] is False:
                    saw_disarmed = True
                    break
            assert saw_disarmed


def test_kill_emits_session_armed_false(client_app) -> None:
    app, fastapi_app = client_app
    with TestClient(fastapi_app) as client:
        client.post("/api/radio/connect")
        client.post("/api/control/arm", json={"armed": True})
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # hello
            client.post("/api/control/kill", json={"reason": "test"})
            saw_disarmed = False
            for _ in range(30):
                msg = ws.receive_json()
                if msg["type"] == "session_armed" and msg["payload"]["armed"] is False:
                    saw_disarmed = True
                    break
            assert saw_disarmed
