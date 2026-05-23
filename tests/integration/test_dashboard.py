from __future__ import annotations

import json

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


def test_health_endpoint(client_app) -> None:
    app, fastapi_app = client_app
    with TestClient(fastapi_app) as client:
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["ok"] is True


def test_state_endpoint_returns_disconnected_initially(client_app) -> None:
    _app, fastapi_app = client_app
    with TestClient(fastapi_app) as client:
        r = client.get("/api/radio/state")
        assert r.status_code == 200
        data = r.json()
        assert data["state"] == "DISCONNECTED"
        assert data["operator_callsign"] == "K1AF"


def test_radio_connect_then_state_idle(client_app) -> None:
    _app, fastapi_app = client_app
    with TestClient(fastapi_app) as client:
        r = client.post("/api/radio/connect")
        assert r.status_code == 200
        assert r.json()["connected"] is True
        r2 = client.get("/api/radio/state")
        assert r2.json()["state"] == "IDLE"


def test_arm_session_via_rest(client_app) -> None:
    _app, fastapi_app = client_app
    with TestClient(fastapi_app) as client:
        client.post("/api/radio/connect")
        r = client.post("/api/control/arm", json={"armed": True})
        assert r.status_code == 200
        assert r.json()["armed"] is True


def test_kill_endpoint(client_app) -> None:
    _app, fastapi_app = client_app
    with TestClient(fastapi_app) as client:
        client.post("/api/radio/connect")
        r = client.post("/api/control/kill", json={"reason": "unit test"})
        assert r.status_code == 200
        assert r.json()["state"] == "STOPPED"


def test_candidates_endpoint(client_app) -> None:
    _app, fastapi_app = client_app
    with TestClient(fastapi_app) as client:
        client.post("/api/radio/connect")
        r = client.get("/api/radio/candidates/20m?mode=USB")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        assert len(data) > 0
        assert data[0]["band"] == "20m"
        assert data[0]["mode"] == "USB"


def test_settings_get_does_not_leak_api_key(client_app) -> None:
    _app, fastapi_app = client_app
    with TestClient(fastapi_app) as client:
        r = client.get("/api/settings")
        assert r.status_code == 200
        data = r.json()
        # cloudlog dict must not include api_key
        assert "api_key" not in data["cloudlog"]
        assert "operator_license_class_options" in data


def test_settings_patch_persists_to_yaml(client_app, tmp_path, monkeypatch) -> None:
    _app, fastapi_app = client_app
    # Redirect user yaml to tmp
    user_yaml = tmp_path / "config.yaml"
    monkeypatch.setattr(
        "cqk1af.dashboard.routes.settings.Path.home", lambda: tmp_path
    )
    # The route was already built with default user_yaml; re-mount with override
    fastapi_app.router.routes = [
        r for r in fastapi_app.router.routes if not (hasattr(r, "path") and r.path.startswith("/api/settings"))
    ]
    from cqk1af.dashboard.routes.settings import build_settings_router

    fastapi_app.include_router(build_settings_router(lambda: _app.settings, user_yaml=user_yaml))

    with TestClient(fastapi_app) as client:
        r = client.patch(
            "/api/settings",
            json={"operator_name": "Test Op", "cq_max_attempts": 4},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["applied"]["operator"]["name"] == "Test Op"
        assert user_yaml.exists()
        text = user_yaml.read_text()
        assert "Test Op" in text


def test_websocket_hello_message(client_app) -> None:
    _app, fastapi_app = client_app
    with TestClient(fastapi_app) as client:
        client.post("/api/radio/connect")
        with client.websocket_connect("/ws") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "hello"
            assert "session_id" in msg["payload"]
            assert msg["payload"]["state"]["state"] == "IDLE"
            assert msg["payload"]["radio"]["connected"] is True


def test_state_endpoint_includes_valid_events(client_app) -> None:
    _app, fastapi_app = client_app
    with TestClient(fastapi_app) as client:
        r = client.get("/api/radio/state")
        assert r.status_code == 200
        data = r.json()
        # KILL/STOP/ERROR are always allowed; CONNECT is valid from DISCONNECTED.
        assert "connect" in data["valid_events"]
        assert "kill" in data["valid_events"]
        # Not valid from DISCONNECTED.
        assert "start_session" not in data["valid_events"]


def test_dispatch_event_advances_state(client_app) -> None:
    _app, fastapi_app = client_app
    with TestClient(fastapi_app) as client:
        r = client.post("/api/control/dispatch_event", json={"event": "connect"})
        assert r.status_code == 200
        body = r.json()
        assert body["state"] == "IDLE"
        assert "start_session" in body["valid_events"]
        assert "connect" not in body["valid_events"]


def test_dispatch_event_unknown_event_returns_400(client_app) -> None:
    _app, fastapi_app = client_app
    with TestClient(fastapi_app) as client:
        r = client.post("/api/control/dispatch_event", json={"event": "bogus"})
        assert r.status_code == 400
        assert r.json()["detail"]["code"] == "unknown_event"


def test_dispatch_event_illegal_from_state_returns_409(client_app) -> None:
    _app, fastapi_app = client_app
    with TestClient(fastapi_app) as client:
        # From DISCONNECTED, start_session is not legal.
        r = client.post("/api/control/dispatch_event", json={"event": "start_session"})
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "illegal_transition"


def test_state_changed_broadcast_includes_valid_events(client_app) -> None:
    _app, fastapi_app = client_app
    with TestClient(fastapi_app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # hello
            client.post("/api/control/dispatch_event", json={"event": "connect"})
            seen = False
            for _ in range(10):
                msg = ws.receive_json()
                if msg.get("type") == "state_changed" and msg["payload"]["next"] == "IDLE":
                    assert "valid_events" in msg["payload"]
                    assert "start_session" in msg["payload"]["valid_events"]
                    seen = True
                    break
            assert seen


def test_websocket_kill_via_message(client_app) -> None:
    _app, fastapi_app = client_app
    with TestClient(fastapi_app) as client:
        client.post("/api/radio/connect")
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # hello
            ws.send_text(json.dumps({"type": "kill", "payload": {"reason": "ws test"}}))
            # Look for the state_changed -> STOPPED
            seen_stopped = False
            for _ in range(10):
                try:
                    msg = ws.receive_json(mode="binary")
                except Exception:
                    msg = ws.receive_json()
                if msg.get("type") == "state_changed" and msg["payload"]["next"] == "STOPPED":
                    seen_stopped = True
                    break
            assert seen_stopped
