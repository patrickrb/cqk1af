"""Deny path: clicking Deny on the TX approval modal must NOT land in ERROR.

Each gated tool has a defined "back-out" transition:
  - check_frequency_in_use → SCANNING (try another freq)
  - call_cq → SCANNING (give up CQ loop)
  - exchange_signal_report / complete_qso → stay in IN_QSO (operator retries)
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from cqk1af.app import App
from cqk1af.config import Settings
from cqk1af.events import TxApprovalRequested
from cqk1af.mcp_server.tools import MCPTools, ToolError
from cqk1af.state.context import HeardCallsign
from cqk1af.state.states import State


@pytest.fixture
async def app_fixture(tmp_path):
    settings = Settings()
    settings.safety.require_manual_tx_approval = True
    # Short auto-deny so a missed subscription doesn't hang the test for 15s.
    settings.safety.approval_timeout_s = 1
    settings.data_dir = tmp_path
    app = App.build(settings)
    yield app
    await app.shutdown()


class ApprovalDenier:
    """Pre-subscribes to the bus so no events are missed during setup."""

    def __init__(self, app: App, *, approve: bool = False) -> None:
        self.app = app
        self.approve = approve
        self._sub = None
        self.req_ids: list[uuid.UUID] = []

    async def __aenter__(self):
        async def on_req(ev):
            if isinstance(ev, TxApprovalRequested):
                self.req_ids.append(ev.req_id)
                # Resolve on next loop tick so the awaiter sees the future flip
                asyncio.create_task(
                    self.app.interlock.resolve_approval(ev.req_id, self.approve, by="operator")
                )

        self._sub = await self.app.bus.subscribe(
            "control.tx_approval_requested", on_req, name="denier"
        )
        return self

    async def __aexit__(self, *_):
        if self._sub is not None:
            await self.app.bus.unsubscribe(self._sub)


@pytest.mark.asyncio
async def test_check_frequency_deny_lands_in_scanning(app_fixture) -> None:
    app = app_fixture
    tools = MCPTools(app)
    await tools.connect_radio()
    await app.interlock.arm(True)
    await tools.tune_slice(14_270_000, "USB")

    async with ApprovalDenier(app, approve=False) as denier:
        result = await tools.check_frequency_in_use(14_270_000, attempts=3)

    assert result.clear is False
    assert result.reason == "tx_denied"
    assert app.fsm.state is State.SCANNING
    assert app.fsm.session.error == ""
    assert len(denier.req_ids) >= 1


@pytest.mark.asyncio
async def test_call_cq_deny_lands_in_scanning(app_fixture) -> None:
    app = app_fixture
    tools = MCPTools(app)
    await tools.connect_radio()
    await app.interlock.arm(True)
    await tools.tune_slice(14_270_000, "USB")

    # First, get to CALLING_CQ without modals.
    app.settings.safety.require_manual_tx_approval = False
    await tools.check_frequency_in_use(14_270_000)
    app.settings.safety.require_manual_tx_approval = True
    assert app.fsm.state is State.CALLING_CQ

    async with ApprovalDenier(app, approve=False):
        result = await tools.call_cq()

    assert result.last_state == "SCANNING"
    assert app.fsm.state is State.SCANNING
    assert app.fsm.session.error == ""


@pytest.mark.asyncio
async def test_signal_report_deny_stays_in_qso(app_fixture) -> None:
    app = app_fixture
    tools = MCPTools(app)
    await tools.connect_radio()
    await app.interlock.arm(True)
    await tools.tune_slice(14_270_000, "USB")
    app.settings.safety.require_manual_tx_approval = False
    await tools.check_frequency_in_use(14_270_000)
    await tools.call_cq()
    app.fsm.session.pileup = [HeardCallsign(callsign="W1AW", confidence=0.95)]
    await tools.listen_for_reply()
    await tools.select_caller("W1AW")
    assert app.fsm.state is State.IN_QSO

    app.settings.safety.require_manual_tx_approval = True
    async with ApprovalDenier(app, approve=False):
        with pytest.raises(ToolError) as exc:
            await tools.exchange_signal_report("59")
    assert exc.value.code == "tx_denied"
    assert app.fsm.state is State.IN_QSO


@pytest.mark.asyncio
async def test_check_frequency_approve_then_clear(app_fixture) -> None:
    """Positive control: with approve=True the same flow completes normally."""
    app = app_fixture
    tools = MCPTools(app)
    await tools.connect_radio()
    await app.interlock.arm(True)
    await tools.tune_slice(14_270_000, "USB")

    async with ApprovalDenier(app, approve=True) as denier:
        result = await tools.check_frequency_in_use(14_270_000, attempts=3)

    assert result.clear is True
    assert len(denier.req_ids) == 3
    assert app.fsm.state is State.CALLING_CQ
