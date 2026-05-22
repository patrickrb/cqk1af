from __future__ import annotations

import pytest

from cqk1af.config import Settings
from cqk1af.demo import run_demo


@pytest.mark.asyncio
async def test_demo_walks_full_flow() -> None:
    settings = Settings()
    observed = await run_demo(settings)
    transitions = [(ev.prev, ev.next) for ev in observed]
    # First transition: DISCONNECTED -> IDLE
    assert ("DISCONNECTED", "IDLE") in transitions
    # Must visit each major state
    nexts = [n for _, n in transitions]
    for required in (
        "SCANNING",
        "LISTENING",
        "CHECKING_FREQUENCY",
        "CALLING_CQ",
        "RECEIVING_REPLY",
        "HANDLING_PILEUP",
        "IN_QSO",
        "CONFIRMING_LOG",
        "LOGGED",
    ):
        assert required in nexts, f"missing {required} in {nexts}"


@pytest.mark.asyncio
async def test_demo_returns_to_idle() -> None:
    settings = Settings()
    observed = await run_demo(settings)
    assert observed[-1].next == "IDLE"
