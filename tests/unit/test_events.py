from __future__ import annotations

import asyncio

import pytest

from cqk1af.events import (
    EventBus,
    KillSwitchTriggered,
    SMeterReading,
    SliceUpdated,
    StateChanged,
)


@pytest.mark.asyncio
async def test_subscriber_receives_matching_event() -> None:
    bus = EventBus()
    received: list[StateChanged] = []

    async def handler(ev):
        received.append(ev)

    await bus.subscribe("state.*", handler, name="t1")
    await bus.publish(StateChanged(prev="IDLE", next="SCANNING"))
    await asyncio.sleep(0.05)
    assert len(received) == 1
    assert received[0].next == "SCANNING"
    await bus.close()


@pytest.mark.asyncio
async def test_pattern_does_not_match_other_topics() -> None:
    bus = EventBus()
    seen_state: list = []
    seen_radio: list = []

    async def h_state(ev):
        seen_state.append(ev)

    async def h_radio(ev):
        seen_radio.append(ev)

    await bus.subscribe("state.*", h_state, name="s")
    await bus.subscribe("radio.*", h_radio, name="r")
    await bus.publish(SliceUpdated(slice_id=0, freq_hz=14250000, mode="USB"))
    await bus.publish(StateChanged(prev="IDLE", next="SCANNING"))
    await asyncio.sleep(0.05)
    assert len(seen_state) == 1
    assert len(seen_radio) == 1
    await bus.close()


@pytest.mark.asyncio
async def test_drop_oldest_for_high_rate_topic() -> None:
    bus = EventBus()
    # Block handler so the queue fills up.
    gate = asyncio.Event()
    received: list = []

    async def handler(ev):
        await gate.wait()
        received.append(ev)

    await bus.subscribe("radio.s_meter", handler, name="m", queue_size=2)
    for dbm in range(-100, -90):
        bus.publish_nowait(SMeterReading(slice_id=0, dbm=float(dbm)))
    gate.set()
    await asyncio.sleep(0.1)
    await bus.close()
    # We sent 10 events into a queue of size 2 with drop-oldest semantics.
    # The two most-recent should be the only ones delivered.
    assert len(received) <= 3  # 2 queued + possibly one mid-handler
    assert received[-1].dbm == -91.0


@pytest.mark.asyncio
async def test_unsubscribe_stops_delivery() -> None:
    bus = EventBus()
    received: list = []

    async def handler(ev):
        received.append(ev)

    sub = await bus.subscribe("control.*", handler, name="k")
    await bus.publish(KillSwitchTriggered(source="test"))
    await asyncio.sleep(0.02)
    assert len(received) == 1

    await bus.unsubscribe(sub)
    await bus.publish(KillSwitchTriggered(source="test2"))
    await asyncio.sleep(0.02)
    assert len(received) == 1
    await bus.close()


@pytest.mark.asyncio
async def test_handler_exception_does_not_kill_subscriber() -> None:
    bus = EventBus()
    received: list = []

    async def handler(ev):
        if len(received) == 0:
            received.append(ev)
            raise RuntimeError("boom")
        received.append(ev)

    await bus.subscribe("state.*", handler, name="x")
    await bus.publish(StateChanged(prev="IDLE", next="SCANNING"))
    await bus.publish(StateChanged(prev="SCANNING", next="LISTENING"))
    await asyncio.sleep(0.05)
    assert len(received) == 2
    await bus.close()


@pytest.mark.asyncio
async def test_wildcard_pattern_catches_all() -> None:
    bus = EventBus()
    received: list = []

    async def handler(ev):
        received.append(ev)

    await bus.subscribe("*", handler, name="all")
    await bus.publish(StateChanged(prev="IDLE", next="SCANNING"))
    await bus.publish(SliceUpdated(slice_id=0, freq_hz=14250000, mode="USB"))
    await bus.publish(KillSwitchTriggered(source="test"))
    await asyncio.sleep(0.05)
    assert len(received) == 3
    await bus.close()
