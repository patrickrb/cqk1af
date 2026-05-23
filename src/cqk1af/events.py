from __future__ import annotations

import asyncio
import fnmatch
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from .util.logging import get_logger
from .util.time import utcnow

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------


class Event(BaseModel):
    """Base event. Subclasses set a class-level `topic`."""

    model_config = ConfigDict(frozen=True)

    topic: ClassVar[str] = "event"
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    ts: datetime = Field(default_factory=utcnow)


# --- Radio ---------------------------------------------------------------


class RadioConnected(Event):
    topic: ClassVar[str] = "radio.connected"
    model: str
    version: str = ""
    serial: str = ""


class RadioDisconnected(Event):
    topic: ClassVar[str] = "radio.disconnected"
    reason: str = ""


class SliceUpdated(Event):
    topic: ClassVar[str] = "radio.slice"
    slice_id: int
    freq_hz: int
    mode: str
    filter_low_hz: int = 0
    filter_high_hz: int = 0
    rxant: str = "ANT1"


class SMeterReading(Event):
    topic: ClassVar[str] = "radio.s_meter"
    slice_id: int
    dbm: float


class PttChanged(Event):
    topic: ClassVar[str] = "radio.ptt"
    on: bool


# --- Audio / VAD ---------------------------------------------------------


class VoiceActivityStarted(Event):
    topic: ClassVar[str] = "audio.voice_started"
    channel: str = "rx"


class VoiceActivityStopped(Event):
    topic: ClassVar[str] = "audio.voice_stopped"
    channel: str = "rx"
    duration_ms: int = 0


class AudioPipelineStatus(Event):
    """Snapshot of the audio pipeline's capability flags.

    Published after pipeline start, and again whenever a leg flips (e.g. STT
    engine first loads, RX capture recovers). The dashboard mirrors this into
    its session store so the RX/STT indicator reflects reality.
    """

    topic: ClassVar[str] = "audio.status"
    status: dict[str, Any] = Field(default_factory=dict)


class TranscriptDropped(Event):
    """STT produced text but a downstream filter dropped it (hallucination,
    empty, too short). Surfaced so the operator can see *why* the transcript
    feed is empty despite voice activity.
    """

    topic: ClassVar[str] = "transcript.dropped"
    reason: str = ""
    text: str = ""
    no_speech_prob: float = 0.0


# --- STT -----------------------------------------------------------------


class TranscriptPartial(Event):
    topic: ClassVar[str] = "transcript.partial"
    text: str
    confidence: float = 0.0


class TranscriptFinal(Event):
    topic: ClassVar[str] = "transcript.final"
    text: str
    confidence: float = 0.0
    direction: Literal["rx", "tx"] = "rx"


class CallsignHeard(Event):
    topic: ClassVar[str] = "transcript.callsign"
    callsign: str
    confidence: float
    snr_dbm: float | None = None
    source_text: str = ""


# --- State machine -------------------------------------------------------


class StateChanged(Event):
    topic: ClassVar[str] = "state.changed"
    prev: str
    next: str
    reason: str = ""


class StateError(Event):
    topic: ClassVar[str] = "state.error"
    state: str
    message: str


# --- Control / safety ----------------------------------------------------


class TxApprovalRequested(Event):
    topic: ClassVar[str] = "control.tx_approval_requested"
    req_id: uuid.UUID
    summary: str
    payload: dict[str, Any] = Field(default_factory=dict)
    expires_at: datetime


class TxApprovalResolved(Event):
    topic: ClassVar[str] = "control.tx_approval_resolved"
    req_id: uuid.UUID
    approved: bool
    by: Literal["operator", "auto-deny", "system"] = "operator"


class KillSwitchTriggered(Event):
    topic: ClassVar[str] = "control.kill"
    source: str = "dashboard"


class SessionArmed(Event):
    topic: ClassVar[str] = "control.armed"
    armed: bool


# --- QSO -----------------------------------------------------------------


class PileupChanged(Event):
    """Full pileup-list sync after a manual edit/add/remove.

    Distinct from ``CallsignHeard`` which is a single-entry append fired by STT
    or simulation. ``PileupChanged`` carries the complete current list so the
    dashboard can replace its view atomically.
    """

    topic: ClassVar[str] = "pileup.changed"
    entries: list[dict[str, Any]] = Field(default_factory=list)


class QSOStarted(Event):
    topic: ClassVar[str] = "qso.started"
    qso_id: uuid.UUID
    callsign: str
    freq_hz: int
    mode: str


class QSOUpdated(Event):
    topic: ClassVar[str] = "qso.updated"
    qso_id: uuid.UUID
    fields: dict[str, Any]


class QSOLogged(Event):
    topic: ClassVar[str] = "qso.logged"
    qso_id: uuid.UUID
    cloudlog_id: str | None = None


# ---------------------------------------------------------------------------
# Event bus
# ---------------------------------------------------------------------------


# Topics for which we prefer to drop the oldest event when a subscriber queue
# is full, rather than block the publisher. These are high-rate / informational.
DROP_OLDEST_TOPICS = {
    "radio.s_meter",
    "audio.voice_started",
    "audio.voice_stopped",
    "transcript.partial",
}

EventHandler = Callable[[Event], Awaitable[None]]


class _Subscription:
    __slots__ = ("pattern", "queue", "task", "name")

    def __init__(self, pattern: str, name: str, maxsize: int) -> None:
        self.pattern = pattern
        self.name = name
        self.queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=maxsize)
        self.task: asyncio.Task[None] | None = None

    def matches(self, topic: str) -> bool:
        return fnmatch.fnmatchcase(topic, self.pattern)


class EventBus:
    """Asyncio pub/sub with topic patterns and bounded subscriber queues.

    Subscribers register `(pattern, handler)`. Patterns are fnmatch-style
    (e.g. ``radio.*``, ``state.changed``, ``*``). Each subscription has a bounded
    queue; high-rate topics drop the oldest, control topics block-with-warn.
    """

    def __init__(self, *, default_queue_size: int = 256) -> None:
        self._subs: list[_Subscription] = []
        self._default_queue_size = default_queue_size
        self._closed = False
        self._lock = asyncio.Lock()

    async def subscribe(
        self,
        pattern: str,
        handler: EventHandler,
        *,
        name: str | None = None,
        queue_size: int | None = None,
    ) -> _Subscription:
        async with self._lock:
            sub = _Subscription(
                pattern=pattern,
                name=name or handler.__qualname__,
                maxsize=queue_size or self._default_queue_size,
            )
            self._subs.append(sub)
            sub.task = asyncio.create_task(self._drain(sub, handler), name=f"bus:{sub.name}")
            return sub

    async def unsubscribe(self, sub: _Subscription) -> None:
        async with self._lock:
            try:
                self._subs.remove(sub)
            except ValueError:
                return
            if sub.task is not None:
                sub.task.cancel()
                try:
                    await sub.task
                except (asyncio.CancelledError, Exception):
                    pass

    def publish_nowait(self, event: Event) -> None:
        """Fan out to subscribers without awaiting. Safe from sync contexts."""
        if self._closed:
            return
        topic = event.topic
        for sub in list(self._subs):
            if not sub.matches(topic):
                continue
            try:
                sub.queue.put_nowait(event)
            except asyncio.QueueFull:
                if topic in DROP_OLDEST_TOPICS:
                    try:
                        sub.queue.get_nowait()
                        sub.queue.put_nowait(event)
                    except asyncio.QueueEmpty:
                        pass
                else:
                    log.warning(
                        "event_bus.subscriber_lag",
                        subscriber=sub.name,
                        topic=topic,
                        dropped_event_id=str(event.id),
                    )

    async def publish(self, event: Event) -> None:
        """Awaitable variant. Currently equivalent to ``publish_nowait``."""
        self.publish_nowait(event)

    async def close(self) -> None:
        async with self._lock:
            self._closed = True
            for sub in list(self._subs):
                if sub.task is not None:
                    sub.task.cancel()
            for sub in list(self._subs):
                if sub.task is not None:
                    try:
                        await sub.task
                    except (asyncio.CancelledError, Exception):
                        pass
            self._subs.clear()

    async def _drain(self, sub: _Subscription, handler: EventHandler) -> None:
        while True:
            event = await sub.queue.get()
            try:
                await handler(event)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception(
                    "event_bus.handler_error",
                    subscriber=sub.name,
                    topic=event.topic,
                )
