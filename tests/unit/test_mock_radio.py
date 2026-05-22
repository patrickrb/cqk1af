from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from cqk1af.events import (
    EventBus,
    PttChanged,
    RadioConnected,
    SliceUpdated,
)
from cqk1af.radio.base import GateToken, KILL_TOKEN, TxNotPermitted
from cqk1af.radio.mock_client import FrequencyActivity, MockRadioClient, MockRadioConfig
from cqk1af.util.time import utcnow


def _token(slice_id: int = 0, *, expired: bool = False) -> GateToken:
    import uuid

    now = utcnow()
    exp = now - timedelta(seconds=1) if expired else now + timedelta(seconds=30)
    return GateToken(id=uuid.uuid4(), slice_id=slice_id, purpose="test", issued_at=now, expires_at=exp)


@pytest.mark.asyncio
async def test_connect_publishes_radio_connected() -> None:
    bus = EventBus()
    seen: list = []

    async def collect(ev):
        seen.append(ev)

    await bus.subscribe("radio.*", collect, name="r")
    radio = MockRadioClient(bus)
    await radio.connect()
    await asyncio.sleep(0.05)
    assert any(isinstance(e, RadioConnected) for e in seen)
    assert any(isinstance(e, SliceUpdated) for e in seen)
    await radio.disconnect()
    await bus.close()


@pytest.mark.asyncio
async def test_tune_publishes_slice_update() -> None:
    bus = EventBus()
    radio = MockRadioClient(bus)
    await radio.connect()
    seen: list[SliceUpdated] = []

    async def collect(ev):
        if isinstance(ev, SliceUpdated):
            seen.append(ev)

    await bus.subscribe("radio.slice", collect, name="r")
    await radio.tune(0, 7_200_000)
    await asyncio.sleep(0.05)
    assert any(e.freq_hz == 7_200_000 for e in seen)
    await radio.disconnect()
    await bus.close()


@pytest.mark.asyncio
async def test_s_meter_busy_vs_clear() -> None:
    bus = EventBus()
    cfg = MockRadioConfig(
        initial_slice_freq_hz=14_250_000,
        activity=[FrequencyActivity(low_hz=14_249_000, high_hz=14_251_000, dbm=-60.0)],
    )
    radio = MockRadioClient(bus, cfg)
    await radio.connect()
    busy = await radio.read_s_meter(0)
    await radio.tune(0, 14_260_000)
    clear = await radio.read_s_meter(0)
    assert busy > -90.0
    assert clear < -90.0
    await radio.disconnect()
    await bus.close()


@pytest.mark.asyncio
async def test_set_ptt_requires_valid_token() -> None:
    bus = EventBus()
    radio = MockRadioClient(bus)
    await radio.connect()
    with pytest.raises(TxNotPermitted):
        await radio.set_ptt(True)
    with pytest.raises(TxNotPermitted):
        await radio.set_ptt(True, gate_token=_token(expired=True))
    with pytest.raises(TxNotPermitted):
        await radio.set_ptt(True, gate_token=KILL_TOKEN)
    await radio.set_ptt(True, gate_token=_token())
    assert radio.state.ptt_on is True
    await radio.set_ptt(False)
    assert radio.state.ptt_on is False
    await radio.disconnect()
    await bus.close()


@pytest.mark.asyncio
async def test_force_ptt_off_synchronous() -> None:
    bus = EventBus()
    seen_ptt: list[PttChanged] = []

    async def collect(ev):
        if isinstance(ev, PttChanged):
            seen_ptt.append(ev)

    await bus.subscribe("radio.ptt", collect, name="ptt")
    radio = MockRadioClient(bus)
    await radio.connect()
    await radio.set_ptt(True, gate_token=_token())
    radio.force_ptt_off()  # synchronous
    await asyncio.sleep(0.05)
    assert radio.state.ptt_on is False
    assert any(p.on is False for p in seen_ptt)
    await radio.disconnect()
    await bus.close()
