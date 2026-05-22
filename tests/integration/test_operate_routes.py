"""REST routes that drive the FSM workflow from the dashboard."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from cqk1af.app import App
from cqk1af.config import Settings
from cqk1af.dashboard.api import build_dashboard_app
from cqk1af.state.context import HeardCallsign


@pytest.fixture
async def client_app():
    settings = Settings()
    settings.safety.require_manual_tx_approval = False
    app = App.build(settings)
    fastapi_app = build_dashboard_app(app)
    yield app, fastapi_app
    await app.shutdown()


def test_full_workflow_via_rest(client_app, tmp_path) -> None:
    app, fastapi_app = client_app
    app.settings.data_dir = tmp_path
    app.qso_store = type(app.qso_store)(tmp_path / "qsos.sqlite")
    with TestClient(fastapi_app) as client:
        assert client.post("/api/radio/connect").status_code == 200
        assert client.post("/api/control/arm", json={"armed": True}).status_code == 200

        # Tune somewhere clear (mock radio has no activity)
        r = client.post("/api/radio/tune", json={"freq_hz": 14_270_000, "mode": "USB"})
        assert r.status_code == 200

        # Run the in-use check
        r = client.post("/api/operate/check_frequency", json={"freq_hz": 14_270_000, "attempts": 3})
        assert r.status_code == 200
        assert r.json()["clear"] is True

        # Should now be CALLING_CQ
        st = client.get("/api/radio/state").json()
        assert st["state"] == "CALLING_CQ"

        # Call CQ
        r = client.post("/api/operate/call_cq", json={"attempts": 1})
        assert r.status_code == 200
        assert r.json()["last_state"] == "RECEIVING_REPLY"

        # Inject a caller and listen
        app.fsm.session.pileup = [HeardCallsign(callsign="W1AW", confidence=0.95)]
        r = client.post("/api/operate/listen_for_reply")
        assert r.status_code == 200

        # Select
        r = client.post("/api/control/select_caller", json={"callsign": "W1AW"})
        assert r.status_code == 200

        # Signal report
        r = client.post("/api/operate/exchange_signal_report", json={"rst_sent": "59"})
        assert r.status_code == 200

        # Edit QSO
        client.post("/api/control/qso/field", json={"field": "name", "value": "Joe"})

        # Close
        r = client.post("/api/operate/complete_qso", json={"closing_phrase": "73"})
        assert r.status_code == 200

        # Confirm log
        r = client.post("/api/operate/confirm_log", json={"edits": {"qth": "Newington"}})
        assert r.status_code == 200
        assert r.json()["callsign"] == "W1AW"

        # ADIF export now contains the row
        adif = client.get("/api/qsos/export.adi").text
        assert "<CALL:4>W1AW" in adif


def test_check_frequency_requires_armed(client_app) -> None:
    app, fastapi_app = client_app
    with TestClient(fastapi_app) as client:
        client.post("/api/radio/connect")
        # NOT armed
        r = client.post("/api/operate/check_frequency", json={"freq_hz": 14_270_000})
        assert r.status_code == 400
        body = r.json()
        assert body["detail"]["code"] == "session_not_armed"


def test_check_frequency_band_plan_denial(client_app) -> None:
    app, fastapi_app = client_app
    with TestClient(fastapi_app) as client:
        client.post("/api/radio/connect")
        client.post("/api/control/arm", json={"armed": True})
        client.post("/api/radio/tune", json={"freq_hz": 14_050_000, "mode": "USB"})
        r = client.post("/api/operate/check_frequency", json={"freq_hz": 14_050_000})
        assert r.status_code == 400
        assert r.json()["detail"]["code"] == "tx_denied_by_band_plan"
