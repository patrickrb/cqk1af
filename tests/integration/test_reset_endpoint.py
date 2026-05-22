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


def test_reset_from_stopped_to_idle(client_app) -> None:
    _app, fastapi_app = client_app
    with TestClient(fastapi_app) as client:
        client.post("/api/radio/connect")
        client.post("/api/control/arm", json={"armed": True})
        client.post("/api/control/kill", json={"reason": "test"})
        st = client.get("/api/radio/state").json()
        assert st["state"] == "STOPPED"
        r = client.post("/api/control/reset")
        assert r.status_code == 200
        assert r.json()["state"] == "IDLE"
        # Reset does NOT re-arm.
        assert r.json()["armed"] is False


def test_reset_from_error_to_idle(client_app) -> None:
    app, fastapi_app = client_app
    from cqk1af.state.states import Event
    with TestClient(fastapi_app) as client:
        client.post("/api/radio/connect")
        # Force ERROR via FSM directly (no public path makes this easy)
        client.portal.call(app.fsm.dispatch, Event.ERROR)  # type: ignore[attr-defined]
        st = client.get("/api/radio/state").json()
        assert st["state"] == "ERROR"
        r = client.post("/api/control/reset")
        assert r.json()["state"] == "IDLE"


def test_reset_noop_from_idle(client_app) -> None:
    _app, fastapi_app = client_app
    with TestClient(fastapi_app) as client:
        client.post("/api/radio/connect")
        r = client.post("/api/control/reset")
        assert r.status_code == 200
        assert r.json()["state"] == "IDLE"
