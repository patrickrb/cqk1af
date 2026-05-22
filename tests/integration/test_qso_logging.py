"""End-to-end: tool path stores a QSO to SQLite, marks it for sync, exports ADIF."""
from __future__ import annotations

import pytest

from cqk1af.app import App
from cqk1af.config import Settings
from cqk1af.mcp_server.tools import MCPTools
from cqk1af.state.context import HeardCallsign


@pytest.fixture
async def tools_with_app(tmp_path):
    settings = Settings()
    settings.safety.require_manual_tx_approval = False
    settings.data_dir = tmp_path  # isolate the SQLite file
    app = App.build(settings)
    yield MCPTools(app), app
    await app.shutdown()


@pytest.mark.asyncio
async def test_full_qso_writes_to_sqlite_and_exports_adif(tools_with_app) -> None:
    tools, app = tools_with_app
    await tools.connect_radio()
    await app.interlock.arm(True)
    await tools.tune_slice(14_250_000, "USB")
    await tools.check_frequency_in_use(14_250_000)
    await tools.call_cq()
    app.fsm.session.pileup = [HeardCallsign(callsign="W1AW", confidence=0.95)]
    await tools.listen_for_reply()
    picked = await tools.select_caller("W1AW")
    assert picked.current_qso is not None
    await tools.exchange_signal_report("59")
    await tools.update_qso_field("name", "Joe")
    await tools.update_qso_field("qth", "Newington CT")
    await tools.update_qso_field("rst_rcvd", "57")
    await tools.complete_qso(closing_phrase="73")
    logged = await tools.confirm_and_log_qso()
    assert logged.callsign == "W1AW"
    recent = await tools.get_recent_qsos(limit=10)
    assert any(q.callsign == "W1AW" for q in recent)
    adif = await tools.export_adif()
    assert "<CALL:4>W1AW" in adif
    assert "<EOH>" in adif and "<EOR>" in adif
    # Operator callsign appears as STATION_CALLSIGN
    assert "K1AF" in adif


@pytest.mark.asyncio
async def test_logged_qso_persists_across_app_restart(tools_with_app, tmp_path) -> None:
    tools, app = tools_with_app
    await tools.connect_radio()
    await app.interlock.arm(True)
    await tools.tune_slice(14_250_000, "USB")
    await tools.check_frequency_in_use(14_250_000)
    await tools.call_cq()
    app.fsm.session.pileup = [HeardCallsign(callsign="K9XYZ", confidence=0.9)]
    await tools.listen_for_reply()
    await tools.select_caller("K9XYZ")
    await tools.complete_qso()
    await tools.confirm_and_log_qso()
    await app.shutdown()

    settings = Settings()
    settings.data_dir = app.settings.data_dir
    app2 = App.build(settings)
    try:
        tools2 = MCPTools(app2)
        recent = await tools2.get_recent_qsos()
        assert any(q.callsign == "K9XYZ" for q in recent)
    finally:
        await app2.shutdown()
