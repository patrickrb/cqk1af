from __future__ import annotations

import asyncio
from typing import Any

from ..events import EventBus, StateChanged, StateError
from ..util.logging import get_logger
from .context import SessionContext
from .states import Event, State

log = get_logger(__name__)


# Allowed transitions: (from_state, event) -> to_state
# `Event.KILL` is a universal edge; handled outside this table.
TRANSITIONS: dict[tuple[State, Event], State] = {
    (State.DISCONNECTED, Event.CONNECT): State.IDLE,
    (State.IDLE, Event.DISCONNECT): State.DISCONNECTED,
    (State.IDLE, Event.START_SESSION): State.SCANNING,
    (State.SCANNING, Event.CANDIDATE_PICKED): State.LISTENING,
    (State.LISTENING, Event.LISTEN_DONE_BUSY): State.SCANNING,
    (State.LISTENING, Event.LISTEN_DONE_CLEAR): State.CHECKING_FREQUENCY,
    (State.CHECKING_FREQUENCY, Event.CHECK_DONE_BUSY): State.SCANNING,
    (State.CHECKING_FREQUENCY, Event.CHECK_DONE_CLEAR): State.CALLING_CQ,
    (State.CALLING_CQ, Event.CQ_SENT): State.RECEIVING_REPLY,
    (State.CALLING_CQ, Event.CQ_MAX_REACHED): State.SCANNING,
    (State.RECEIVING_REPLY, Event.REPLY_HEARD): State.HANDLING_PILEUP,
    (State.RECEIVING_REPLY, Event.REPLY_TIMEOUT): State.CALLING_CQ,
    (State.HANDLING_PILEUP, Event.PILEUP_SELECTED): State.IN_QSO,
    (State.IN_QSO, Event.QSO_COMPLETED): State.CONFIRMING_LOG,
    (State.CONFIRMING_LOG, Event.LOG_CONFIRMED): State.LOGGED,
    (State.CONFIRMING_LOG, Event.LOG_FAILED): State.ERROR,
    (State.LOGGED, Event.ACK): State.IDLE,
    # ERROR recovery
    (State.ERROR, Event.ACK): State.IDLE,
    # STOPPED requires explicit operator action to leave
    (State.STOPPED, Event.START_SESSION): State.IDLE,
}

# Events that always force STOPPED from any state.
GLOBAL_STOP_EVENTS = {Event.KILL, Event.STOP}


class IllegalTransition(Exception):
    pass


class StateMachine:
    """Async state machine with strict, table-driven transitions.

    - All transitions go through ``dispatch()``.
    - ``KILL`` and ``STOP`` are honoured from every state.
    - ``ERROR`` is reached via ``dispatch(Event.ERROR, reason=...)`` from any state.
    - State changes are published on the event bus as ``StateChanged``.
    """

    def __init__(self, bus: EventBus, *, initial: State = State.DISCONNECTED) -> None:
        self.bus = bus
        self._state = initial
        self._lock = asyncio.Lock()
        self.session = SessionContext()

    @property
    def state(self) -> State:
        return self._state

    async def dispatch(self, event: Event, *, reason: str = "", **payload: Any) -> State:
        """Apply ``event`` to the current state. Returns the new state."""
        async with self._lock:
            prev = self._state

            if event in GLOBAL_STOP_EVENTS:
                new = State.STOPPED
            elif event is Event.ERROR:
                new = State.ERROR
                self.session.error = reason
            else:
                key = (prev, event)
                if key not in TRANSITIONS:
                    raise IllegalTransition(
                        f"No transition from {prev} on {event}"
                    )
                new = TRANSITIONS[key]

            if new is prev:
                return prev

            self._state = new
            await self.bus.publish(StateChanged(prev=str(prev), next=str(new), reason=reason))
            if new is State.ERROR:
                await self.bus.publish(StateError(state=str(prev), message=reason or "unknown"))
            return new

    def can(self, event: Event) -> bool:
        if event in GLOBAL_STOP_EVENTS or event is Event.ERROR:
            return True
        return (self._state, event) in TRANSITIONS

    async def reset(self, *, to: State = State.DISCONNECTED) -> None:
        async with self._lock:
            prev = self._state
            self._state = to
            self.session = SessionContext()
            if prev is not to:
                await self.bus.publish(StateChanged(prev=str(prev), next=str(to), reason="reset"))
