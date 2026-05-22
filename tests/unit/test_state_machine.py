from __future__ import annotations

import asyncio

import pytest

from cqk1af.events import EventBus, StateChanged
from cqk1af.state.machine import IllegalTransition, StateMachine
from cqk1af.state.states import Event, State


@pytest.mark.asyncio
async def test_initial_state_disconnected() -> None:
    bus = EventBus()
    fsm = StateMachine(bus)
    assert fsm.state is State.DISCONNECTED
    await bus.close()


@pytest.mark.asyncio
async def test_happy_path_transitions() -> None:
    bus = EventBus()
    fsm = StateMachine(bus)
    seq = [
        (Event.CONNECT, State.IDLE),
        (Event.START_SESSION, State.SCANNING),
        (Event.CANDIDATE_PICKED, State.LISTENING),
        (Event.LISTEN_DONE_CLEAR, State.CHECKING_FREQUENCY),
        (Event.CHECK_DONE_CLEAR, State.CALLING_CQ),
        (Event.CQ_SENT, State.RECEIVING_REPLY),
        (Event.REPLY_HEARD, State.HANDLING_PILEUP),
        (Event.PILEUP_SELECTED, State.IN_QSO),
        (Event.QSO_COMPLETED, State.CONFIRMING_LOG),
        (Event.LOG_CONFIRMED, State.LOGGED),
        (Event.ACK, State.IDLE),
    ]
    for event, expected in seq:
        new_state = await fsm.dispatch(event)
        assert new_state is expected, f"{event} -> expected {expected}, got {new_state}"
    await bus.close()


@pytest.mark.asyncio
async def test_kill_from_any_state() -> None:
    for start_event_seq in (
        [Event.CONNECT],
        [Event.CONNECT, Event.START_SESSION],
        [Event.CONNECT, Event.START_SESSION, Event.CANDIDATE_PICKED],
        [Event.CONNECT, Event.START_SESSION, Event.CANDIDATE_PICKED, Event.LISTEN_DONE_CLEAR],
        [
            Event.CONNECT,
            Event.START_SESSION,
            Event.CANDIDATE_PICKED,
            Event.LISTEN_DONE_CLEAR,
            Event.CHECK_DONE_CLEAR,
            Event.CQ_SENT,
            Event.REPLY_HEARD,
            Event.PILEUP_SELECTED,
        ],
    ):
        bus = EventBus()
        fsm = StateMachine(bus)
        for ev in start_event_seq:
            await fsm.dispatch(ev)
        new_state = await fsm.dispatch(Event.KILL, reason="operator")
        assert new_state is State.STOPPED, f"kill from {fsm.state} failed"
        await bus.close()


@pytest.mark.asyncio
async def test_illegal_transition_raises() -> None:
    bus = EventBus()
    fsm = StateMachine(bus)
    # Cannot start a session from DISCONNECTED
    with pytest.raises(IllegalTransition):
        await fsm.dispatch(Event.START_SESSION)
    await bus.close()


@pytest.mark.asyncio
async def test_error_event_from_any_state() -> None:
    bus = EventBus()
    fsm = StateMachine(bus)
    await fsm.dispatch(Event.CONNECT)
    await fsm.dispatch(Event.START_SESSION)
    new_state = await fsm.dispatch(Event.ERROR, reason="boom")
    assert new_state is State.ERROR
    assert fsm.session.error == "boom"
    await bus.close()


@pytest.mark.asyncio
async def test_state_changes_published() -> None:
    bus = EventBus()
    observed: list[StateChanged] = []

    async def collect(ev):
        observed.append(ev)

    await bus.subscribe("state.changed", collect, name="t")
    fsm = StateMachine(bus)
    await fsm.dispatch(Event.CONNECT)
    await fsm.dispatch(Event.START_SESSION)
    await asyncio.sleep(0.05)
    assert len(observed) == 2
    assert observed[0].prev == "DISCONNECTED"
    assert observed[0].next == "IDLE"
    assert observed[1].next == "SCANNING"
    await bus.close()


@pytest.mark.asyncio
async def test_stopped_requires_explicit_restart() -> None:
    bus = EventBus()
    fsm = StateMachine(bus)
    await fsm.dispatch(Event.CONNECT)
    await fsm.dispatch(Event.KILL)
    assert fsm.state is State.STOPPED
    # ACK from STOPPED is illegal — we want explicit START_SESSION.
    with pytest.raises(IllegalTransition):
        await fsm.dispatch(Event.ACK)
    new_state = await fsm.dispatch(Event.START_SESSION)
    assert new_state is State.IDLE
    await bus.close()


@pytest.mark.asyncio
async def test_listening_busy_returns_to_scanning() -> None:
    bus = EventBus()
    fsm = StateMachine(bus)
    await fsm.dispatch(Event.CONNECT)
    await fsm.dispatch(Event.START_SESSION)
    await fsm.dispatch(Event.CANDIDATE_PICKED)
    await fsm.dispatch(Event.LISTEN_DONE_BUSY)
    assert fsm.state is State.SCANNING
    await bus.close()


@pytest.mark.asyncio
async def test_can_returns_true_for_valid_transitions() -> None:
    bus = EventBus()
    fsm = StateMachine(bus)
    assert fsm.can(Event.CONNECT) is True
    assert fsm.can(Event.START_SESSION) is False
    assert fsm.can(Event.KILL) is True  # global
    await bus.close()
