from __future__ import annotations

import asyncio
import uuid

import pytest

from cqk1af.config import Settings
from cqk1af.events import EventBus, KillSwitchTriggered, TxApprovalRequested, TxApprovalResolved
from cqk1af.radio.mock_client import MockRadioClient, MockRadioConfig
from cqk1af.safety.interlock import SafetyInterlock


@pytest.fixture
async def interlock_fixture():
    settings = Settings()
    bus = EventBus()
    radio = MockRadioClient(bus, MockRadioConfig())
    interlock = SafetyInterlock(bus, radio, settings, token_lifetime_s=2.0)
    radio.bind_interlock(interlock)
    await radio.connect()
    yield bus, radio, interlock, settings
    await radio.disconnect()
    await bus.close()


@pytest.mark.asyncio
async def test_request_tx_denied_when_not_armed(interlock_fixture) -> None:
    _, _, interlock, _ = interlock_fixture
    token = await interlock.request_tx(summary="test", slice_id=0, purpose="test", timeout_s=0.5)
    assert token is None


@pytest.mark.asyncio
async def test_request_tx_succeeds_when_armed_and_no_approval(interlock_fixture) -> None:
    _, _, interlock, settings = interlock_fixture
    settings.safety.require_manual_tx_approval = False
    await interlock.arm(True)
    token = await interlock.request_tx(summary="test", slice_id=0, purpose="test")
    assert token is not None
    assert interlock.is_token_valid(token)


@pytest.mark.asyncio
async def test_approval_required_publishes_event_and_awaits(interlock_fixture) -> None:
    bus, _, interlock, settings = interlock_fixture
    settings.safety.require_manual_tx_approval = True
    await interlock.arm(True)

    seen: list[TxApprovalRequested] = []
    async def collect(ev):
        if isinstance(ev, TxApprovalRequested):
            seen.append(ev)
    await bus.subscribe("control.tx_approval_requested", collect, name="t")

    async def approve_after_delay():
        await asyncio.sleep(0.05)
        # Approve the most-recent request
        assert seen, "expected an approval request to be published"
        await interlock.resolve_approval(seen[-1].req_id, True)

    asyncio.create_task(approve_after_delay())
    token = await interlock.request_tx(
        summary="test", slice_id=0, purpose="test", timeout_s=1.0
    )
    assert token is not None
    assert interlock.is_token_valid(token)


@pytest.mark.asyncio
async def test_approval_denied_returns_none(interlock_fixture) -> None:
    bus, _, interlock, settings = interlock_fixture
    settings.safety.require_manual_tx_approval = True
    await interlock.arm(True)

    seen_req: list[uuid.UUID] = []
    async def collect(ev):
        if isinstance(ev, TxApprovalRequested):
            seen_req.append(ev.req_id)
    await bus.subscribe("control.tx_approval_requested", collect, name="t")

    async def deny_after_delay():
        await asyncio.sleep(0.05)
        assert seen_req
        await interlock.resolve_approval(seen_req[-1], False)

    asyncio.create_task(deny_after_delay())
    token = await interlock.request_tx(
        summary="test", slice_id=0, purpose="test", timeout_s=1.0
    )
    assert token is None


@pytest.mark.asyncio
async def test_approval_timeout_auto_denies(interlock_fixture) -> None:
    bus, _, interlock, settings = interlock_fixture
    settings.safety.require_manual_tx_approval = True
    await interlock.arm(True)

    seen: list[TxApprovalResolved] = []
    async def collect(ev):
        if isinstance(ev, TxApprovalResolved):
            seen.append(ev)
    await bus.subscribe("control.tx_approval_resolved", collect, name="t")

    token = await interlock.request_tx(
        summary="test", slice_id=0, purpose="test", timeout_s=0.1
    )
    assert token is None
    await asyncio.sleep(0.05)
    assert any(r.by == "auto-deny" for r in seen)


@pytest.mark.asyncio
async def test_token_is_single_use(interlock_fixture) -> None:
    bus, radio, interlock, settings = interlock_fixture
    settings.safety.require_manual_tx_approval = False
    await interlock.arm(True)
    token = await interlock.request_tx(summary="x", slice_id=0, purpose="t")
    assert token is not None
    await radio.set_ptt(True, gate_token=token)
    await radio.set_ptt(False)
    # Second use must fail
    from cqk1af.radio.base import TxNotPermitted
    with pytest.raises(TxNotPermitted):
        await radio.set_ptt(True, gate_token=token)


@pytest.mark.asyncio
async def test_token_expires(interlock_fixture) -> None:
    bus, radio, interlock, settings = interlock_fixture
    settings.safety.require_manual_tx_approval = False
    await interlock.arm(True)
    # token_lifetime_s=2.0 from fixture; request a token then wait it out
    token = await interlock.request_tx(summary="x", slice_id=0, purpose="t")
    assert token is not None
    await asyncio.sleep(2.1)
    from cqk1af.radio.base import TxNotPermitted
    with pytest.raises(TxNotPermitted):
        await radio.set_ptt(True, gate_token=token)


@pytest.mark.asyncio
async def test_kill_is_synchronous_and_disarms(interlock_fixture) -> None:
    bus, radio, interlock, settings = interlock_fixture
    settings.safety.require_manual_tx_approval = False
    await interlock.arm(True)
    token = await interlock.request_tx(summary="x", slice_id=0, purpose="t")
    assert token is not None
    await radio.set_ptt(True, gate_token=token)
    assert radio.state.ptt_on is True

    seen_kill: list[KillSwitchTriggered] = []
    async def collect(ev):
        if isinstance(ev, KillSwitchTriggered):
            seen_kill.append(ev)
    await bus.subscribe("control.kill", collect, name="k")

    interlock.kill(source="unit_test")
    assert radio.state.ptt_on is False
    assert interlock.armed is False
    await asyncio.sleep(0.05)
    assert seen_kill


@pytest.mark.asyncio
async def test_kill_denies_pending_approval(interlock_fixture) -> None:
    bus, _, interlock, settings = interlock_fixture
    settings.safety.require_manual_tx_approval = True
    await interlock.arm(True)

    async def request():
        return await interlock.request_tx(
            summary="x", slice_id=0, purpose="t", timeout_s=2.0
        )

    task = asyncio.create_task(request())
    await asyncio.sleep(0.05)
    interlock.kill(source="unit_test")
    result = await task
    assert result is None


@pytest.mark.asyncio
async def test_disarm_denies_pending_approval(interlock_fixture) -> None:
    bus, _, interlock, settings = interlock_fixture
    settings.safety.require_manual_tx_approval = True
    await interlock.arm(True)

    task = asyncio.create_task(
        interlock.request_tx(summary="x", slice_id=0, purpose="t", timeout_s=2.0)
    )
    await asyncio.sleep(0.05)
    await interlock.arm(False)
    result = await task
    assert result is None


@pytest.mark.asyncio
async def test_resolve_unknown_approval_returns_false(interlock_fixture) -> None:
    _, _, interlock, _ = interlock_fixture
    ok = await interlock.resolve_approval(uuid.uuid4(), True)
    assert ok is False
