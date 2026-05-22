"""Kill-switch latency drill.

Goal: a kill request must release PTT in well under the 150 ms target. We
measure the time from ``interlock.kill()`` to the radio's ``ptt_on`` going
False AND to ``KillSwitchTriggered`` reaching a bus subscriber.

This runs against the mock radio so it's a measure of our coordination
overhead, not of real hardware. The real hardware target is documented in the
manual smoke checklist.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from cqk1af.app import App
from cqk1af.config import Settings
from cqk1af.events import KillSwitchTriggered


@pytest.mark.asyncio
async def test_kill_releases_ptt_synchronously() -> None:
    settings = Settings()
    settings.safety.require_manual_tx_approval = False
    app = App.build(settings)
    try:
        await app.radio.connect()
        await app.interlock.arm(True)
        token = await app.interlock.request_tx(summary="latency", slice_id=0, purpose="test")
        assert token is not None
        await app.radio.set_ptt(True, gate_token=token)
        assert app.radio.state.ptt_on is True

        seen_kill: list = []
        async def on_kill(ev) -> None:
            if isinstance(ev, KillSwitchTriggered):
                seen_kill.append(time.perf_counter())
        await app.bus.subscribe("control.kill", on_kill, name="t.kill")

        t0 = time.perf_counter()
        app.interlock.kill(source="test_latency")
        ptt_off_at = time.perf_counter()

        # PTT released synchronously, BEFORE the bus has fanned out
        assert app.radio.state.ptt_on is False
        sync_latency_ms = (ptt_off_at - t0) * 1000.0
        assert sync_latency_ms < 50.0, f"sync kill->PTT off was {sync_latency_ms:.2f} ms"

        await asyncio.sleep(0.05)
        assert seen_kill, "KillSwitchTriggered not delivered"
        bus_latency_ms = (seen_kill[0] - t0) * 1000.0
        assert bus_latency_ms < 150.0, f"bus kill latency {bus_latency_ms:.2f} ms exceeds target"
    finally:
        await app.shutdown()


@pytest.mark.asyncio
async def test_kill_invalidates_outstanding_tokens() -> None:
    settings = Settings()
    settings.safety.require_manual_tx_approval = False
    app = App.build(settings)
    try:
        await app.radio.connect()
        await app.interlock.arm(True)
        token = await app.interlock.request_tx(summary="x", slice_id=0, purpose="t")
        assert token is not None
        app.interlock.kill(source="test")
        # Token must no longer be valid
        assert app.interlock.is_token_valid(token) is False
        from cqk1af.radio.base import TxNotPermitted
        with pytest.raises(TxNotPermitted):
            await app.radio.set_ptt(True, gate_token=token)
    finally:
        await app.shutdown()


@pytest.mark.asyncio
async def test_kill_denies_in_flight_approval() -> None:
    settings = Settings()
    settings.safety.require_manual_tx_approval = True
    app = App.build(settings)
    try:
        await app.radio.connect()
        await app.interlock.arm(True)
        task = asyncio.create_task(
            app.interlock.request_tx(summary="x", slice_id=0, purpose="t", timeout_s=2.0)
        )
        await asyncio.sleep(0.05)
        app.interlock.kill(source="test")
        result = await task
        assert result is None
    finally:
        await app.shutdown()
