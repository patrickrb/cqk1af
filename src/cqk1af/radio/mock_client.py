from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field

from ..config import RigMode
from ..events import (
    EventBus,
    PttChanged,
    RadioConnected,
    RadioDisconnected,
    SliceUpdated,
    SMeterReading,
)
from ..util.logging import get_logger
from ..util.time import utcnow
from .base import GateToken, KILL_TOKEN, RadioClient, TxNotPermitted
from .slice_model import RadioState, SliceState

log = get_logger(__name__)


@dataclass
class FrequencyActivity:
    """A scripted block of activity at a given frequency window.

    The mock radio uses these to drive realistic S-meter readings and to gate
    the "is the frequency in use?" behaviour during tests.
    """

    low_hz: int
    high_hz: int
    dbm: float = -70.0
    is_voice: bool = True
    # Optional time window relative to mock_client.connect() in seconds
    start_s: float = 0.0
    end_s: float = float("inf")

    def matches(self, freq_hz: int, t_relative: float) -> bool:
        return self.low_hz <= freq_hz <= self.high_hz and self.start_s <= t_relative <= self.end_s


@dataclass
class MockRadioConfig:
    model: str = "FlexRadio 6400 (mock)"
    serial: str = "MOCK-0000-0001"
    version: str = "v3.99.0-mock"
    noise_floor_dbm: float = -110.0
    activity: list[FrequencyActivity] = field(default_factory=list)
    s_meter_period_s: float = 0.25
    initial_slice_freq_hz: int = 14_250_000
    initial_slice_mode: RigMode = "USB"


class MockRadioClient(RadioClient):
    """In-memory radio for tests and dev. Emits the same bus events as the real one.

    Scripted activity windows let tests assert behaviours like "abandon a busy
    frequency" or "find the frequency clear and proceed to CQ".
    """

    def __init__(self, bus: EventBus, cfg: MockRadioConfig | None = None) -> None:
        self.bus = bus
        self.cfg = cfg or MockRadioConfig()
        self._state = RadioState(model=self.cfg.model, version=self.cfg.version, serial=self.cfg.serial)
        self._connected_at: float | None = None
        self._meter_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._interlock = None  # type: ignore[assignment]

    def bind_interlock(self, interlock) -> None:  # type: ignore[no-untyped-def]
        """Late-bind the SafetyInterlock so set_ptt(True) can consume tokens."""
        self._interlock = interlock

    @property
    def state(self) -> RadioState:
        return self._state

    # ---- lifecycle --------------------------------------------------

    async def connect(self) -> None:
        async with self._lock:
            if self._state.connected:
                return
            self._state.connected = True
            self._connected_at = asyncio.get_event_loop().time()
            initial = SliceState(
                slice_id=0,
                freq_hz=self.cfg.initial_slice_freq_hz,
                mode=self.cfg.initial_slice_mode,
            )
            self._state.slices[0] = initial
            await self.bus.publish(
                RadioConnected(model=self._state.model, version=self._state.version, serial=self._state.serial)
            )
            await self.bus.publish(
                SliceUpdated(
                    slice_id=initial.slice_id,
                    freq_hz=initial.freq_hz,
                    mode=initial.mode,
                    filter_low_hz=initial.filter_low_hz,
                    filter_high_hz=initial.filter_high_hz,
                    rxant=initial.rxant,
                )
            )
            self._meter_task = asyncio.create_task(self._emit_meter_loop(), name="mock_radio.s_meter")

    async def disconnect(self) -> None:
        async with self._lock:
            if not self._state.connected:
                return
            self._state.connected = False
            if self._meter_task is not None:
                self._meter_task.cancel()
                try:
                    await self._meter_task
                except (asyncio.CancelledError, Exception):
                    pass
                self._meter_task = None
            await self.bus.publish(RadioDisconnected(reason="requested"))

    # ---- slice control ---------------------------------------------

    async def list_slices(self) -> list[SliceState]:
        return list(self._state.slices.values())

    async def tune(self, slice_id: int, freq_hz: int) -> SliceState:
        self._require_connected()
        s = self._require_slice(slice_id)
        s.freq_hz = freq_hz
        await self._publish_slice(s)
        return s

    async def set_mode(self, slice_id: int, mode: RigMode) -> SliceState:
        self._require_connected()
        s = self._require_slice(slice_id)
        s.mode = mode
        await self._publish_slice(s)
        return s

    async def set_filter(self, slice_id: int, low_hz: int, high_hz: int) -> SliceState:
        self._require_connected()
        s = self._require_slice(slice_id)
        s.filter_low_hz = low_hz
        s.filter_high_hz = high_hz
        await self._publish_slice(s)
        return s

    async def read_s_meter(self, slice_id: int) -> float:
        s = self._require_slice(slice_id)
        dbm = self._s_meter_for(s.freq_hz)
        s.s_meter_dbm = dbm
        return dbm

    # ---- TX ---------------------------------------------------------

    async def set_ptt(self, on: bool, *, gate_token: GateToken | None = None) -> None:
        if on:
            if gate_token is None:
                raise TxNotPermitted("set_ptt(True) requires a GateToken")
            if gate_token is KILL_TOKEN:
                raise TxNotPermitted("KILL_TOKEN is only valid for force_ptt_off")
            if gate_token.is_expired():
                raise TxNotPermitted("GateToken expired")
            if self._interlock is not None:
                if not self._interlock.is_token_valid(gate_token):
                    raise TxNotPermitted("GateToken not issued by interlock or already consumed")
                self._interlock.consume_token(gate_token)
        self._state.ptt_on = on
        await self.bus.publish(PttChanged(on=on))

    def force_ptt_off(self) -> None:
        if self._state.ptt_on:
            self._state.ptt_on = False
            # Synchronous: use publish_nowait so the kill path doesn't await.
            self.bus.publish_nowait(PttChanged(on=False))

    # ---- test helpers ----------------------------------------------

    def set_activity(self, activity: list[FrequencyActivity]) -> None:
        self.cfg.activity = activity

    def add_activity(self, act: FrequencyActivity) -> None:
        self.cfg.activity.append(act)

    # ---- internals --------------------------------------------------

    def _require_connected(self) -> None:
        if not self._state.connected:
            raise RuntimeError("Radio not connected")

    def _require_slice(self, slice_id: int) -> SliceState:
        s = self._state.slices.get(slice_id)
        if s is None:
            raise KeyError(f"No such slice: {slice_id}")
        return s

    def _t_relative(self) -> float:
        if self._connected_at is None:
            return 0.0
        return asyncio.get_event_loop().time() - self._connected_at

    def _s_meter_for(self, freq_hz: int) -> float:
        t = self._t_relative()
        for act in self.cfg.activity:
            if act.matches(freq_hz, t):
                # Add small jitter
                return act.dbm + random.uniform(-2.0, 2.0)
        return self.cfg.noise_floor_dbm + random.uniform(-2.0, 2.0)

    async def _publish_slice(self, s: SliceState) -> None:
        await self.bus.publish(
            SliceUpdated(
                slice_id=s.slice_id,
                freq_hz=s.freq_hz,
                mode=s.mode,
                filter_low_hz=s.filter_low_hz,
                filter_high_hz=s.filter_high_hz,
                rxant=s.rxant,
            )
        )

    async def _emit_meter_loop(self) -> None:
        try:
            while self._state.connected:
                await asyncio.sleep(self.cfg.s_meter_period_s)
                for s in list(self._state.slices.values()):
                    dbm = self._s_meter_for(s.freq_hz)
                    s.s_meter_dbm = dbm
                    self.bus.publish_nowait(SMeterReading(slice_id=s.slice_id, dbm=dbm))
        except asyncio.CancelledError:
            return
