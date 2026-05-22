from __future__ import annotations

import pytest

from cqk1af.app import App
from cqk1af.config import Settings
from cqk1af.mcp_server.tools import MCPTools, ToolError
from cqk1af.radio.mock_client import FrequencyActivity, MockRadioClient, MockRadioConfig
from cqk1af.state.context import HeardCallsign
from cqk1af.state.states import State


@pytest.fixture
async def app_fixture():
    settings = Settings()
    # MCP-tool happy-path tests do not exercise the per-action approval modal —
    # the interlock test suite covers that. Disable it here so calls proceed.
    settings.safety.require_manual_tx_approval = False
    app = App.build(settings)
    yield app
    await app.shutdown()


@pytest.fixture
async def tools(app_fixture: App):
    return MCPTools(app_fixture)


@pytest.mark.asyncio
async def test_get_session_state_initial(tools: MCPTools) -> None:
    s = await tools.get_session_state()
    assert s.state == "DISCONNECTED"
    assert s.armed is False
    assert s.operator_callsign == "K1AF"
    assert s.license_class == "Extra"
    assert s.pileup == []


@pytest.mark.asyncio
async def test_connect_radio_advances_to_idle(tools: MCPTools) -> None:
    status = await tools.connect_radio()
    assert status.connected is True
    s = await tools.get_session_state()
    assert s.state == "IDLE"


@pytest.mark.asyncio
async def test_scan_returns_candidates_and_starts_session(tools: MCPTools) -> None:
    await tools.connect_radio()
    cands = await tools.scan_for_open_frequency("20m", "USB")
    assert cands, "expected candidate frequencies for 20m Extra"
    s = await tools.get_session_state()
    assert s.state == "SCANNING"
    assert all(c.band == "20m" and c.mode == "USB" for c in cands)


@pytest.mark.asyncio
async def test_check_frequency_requires_armed(tools: MCPTools) -> None:
    await tools.connect_radio()
    with pytest.raises(ToolError) as exc:
        await tools.check_frequency_in_use(14_250_000)
    assert exc.value.code == "session_not_armed"


@pytest.mark.asyncio
async def test_check_frequency_band_plan_denial(tools: MCPTools) -> None:
    await tools.connect_radio()
    await tools.app.interlock.arm(True)
    # Set mode to USB so we can attempt to TX
    await tools.tune_slice(14_250_000, "USB")
    with pytest.raises(ToolError) as exc:
        await tools.check_frequency_in_use(14_050_000)  # CW segment
    assert exc.value.code == "tx_denied_by_band_plan"


@pytest.mark.asyncio
async def test_full_qso_via_tools(tools: MCPTools) -> None:
    await tools.connect_radio()
    await tools.app.interlock.arm(True)

    cands = await tools.scan_for_open_frequency("20m", "USB")
    target = cands[0]

    # Tune RX
    s = await tools.tune_slice(target.freq_hz, "USB")
    assert s.freq_hz == target.freq_hz
    assert s.mode == "USB"

    # The mock radio's initial freq is 14.250; we just tuned elsewhere — that
    # area is clear by default (no activity windows configured), so the in-use
    # check should pass.
    result = await tools.check_frequency_in_use(target.freq_hz, attempts=3)
    assert result.clear is True

    # CALLING_CQ -> RECEIVING_REPLY
    cq = await tools.call_cq()
    assert cq.last_state == "RECEIVING_REPLY"

    # Populate pileup before listening
    tools.app.fsm.session.pileup = [
        HeardCallsign(callsign="W1AW", confidence=0.95, snr_dbm=-50.0),
        HeardCallsign(callsign="K9XYZ", confidence=0.62, snr_dbm=-70.0),
    ]
    heard = await tools.listen_for_reply()
    assert len(heard) == 2
    assert tools.app.fsm.state is State.HANDLING_PILEUP

    picked = await tools.select_caller("W1AW")
    assert picked.current_qso is not None
    assert picked.current_qso.callsign == "W1AW"
    assert tools.app.fsm.state is State.IN_QSO

    await tools.exchange_signal_report("59")
    await tools.update_qso_field("name", "Joe")
    await tools.update_qso_field("qth", "Newington CT")
    await tools.update_qso_field("rst_rcvd", "57")

    qso_after = await tools.complete_qso(closing_phrase="73")
    assert qso_after.name == "Joe"
    assert tools.app.fsm.state is State.CONFIRMING_LOG

    logged = await tools.confirm_and_log_qso()
    assert logged.callsign == "W1AW"
    assert tools.app.fsm.state is State.IDLE
    assert tools.app.fsm.session.qsos_logged == 1


@pytest.mark.asyncio
async def test_emergency_stop_from_any_state(tools: MCPTools) -> None:
    await tools.connect_radio()
    await tools.app.interlock.arm(True)
    await tools.scan_for_open_frequency("20m", "USB")
    s = await tools.emergency_stop(reason="test")
    assert s.state == "STOPPED"
    assert s.armed is False


@pytest.mark.asyncio
async def test_select_caller_unknown_raises(tools: MCPTools) -> None:
    await tools.connect_radio()
    await tools.app.interlock.arm(True)
    await tools.tune_slice(14_250_000, "USB")
    await tools.check_frequency_in_use(14_250_000)
    await tools.call_cq()
    tools.app.fsm.session.pileup = [HeardCallsign(callsign="W1AW", confidence=0.95)]
    await tools.listen_for_reply()
    with pytest.raises(ToolError) as exc:
        await tools.select_caller("XX1XX")
    assert exc.value.code == "unknown_caller"
