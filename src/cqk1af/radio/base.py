from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..config import RigMode
from ..util.time import utcnow
from .slice_model import RadioState, SliceState


class RadioError(Exception):
    pass


class TxNotPermitted(RadioError):
    """Raised when set_ptt is called without a valid GateToken."""


@dataclass(frozen=True)
class GateToken:
    """Single-use, time-limited token authorizing a TX action.

    Issued by the SafetyInterlock. The radio adapter validates the token
    before flipping PTT. Tokens are bound to a specific slice + intent and
    expire quickly to bound the blast radius.
    """

    id: uuid.UUID
    slice_id: int
    purpose: str
    issued_at: datetime
    expires_at: datetime

    def is_expired(self, *, now: datetime | None = None) -> bool:
        now = now or utcnow()
        return now >= self.expires_at


# A sentinel token reserved for the kill path. The radio adapter accepts this
# when forcing PTT off — kill must work even when the interlock is disarmed.
KILL_TOKEN = GateToken(
    id=uuid.UUID(int=0),
    slice_id=-1,
    purpose="kill",
    issued_at=datetime(1970, 1, 1, tzinfo=timezone.utc),
    expires_at=datetime(9999, 12, 31, tzinfo=timezone.utc),
)


class RadioClient(ABC):
    """Async control surface for a transceiver.

    Implementations: ``MockRadioClient`` (tests/dev), ``FlexLibRadioClient``
    (real FlexRadio 6400 via pythonnet, added in Phase 9).
    """

    # ---- lifecycle --------------------------------------------------

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @property
    @abstractmethod
    def state(self) -> RadioState: ...

    # ---- slice control ---------------------------------------------

    @abstractmethod
    async def list_slices(self) -> list[SliceState]: ...

    @abstractmethod
    async def tune(self, slice_id: int, freq_hz: int) -> SliceState: ...

    @abstractmethod
    async def set_mode(self, slice_id: int, mode: RigMode) -> SliceState: ...

    @abstractmethod
    async def set_filter(self, slice_id: int, low_hz: int, high_hz: int) -> SliceState: ...

    @abstractmethod
    async def read_s_meter(self, slice_id: int) -> float: ...

    # ---- TX ---------------------------------------------------------

    @abstractmethod
    async def set_ptt(self, on: bool, *, gate_token: GateToken | None = None) -> None:
        """Engage/release PTT.

        - ``on=True`` requires a non-expired ``gate_token`` for the target slice.
        - ``on=False`` may be called without a token (release is always safe).
        - The kill path passes ``KILL_TOKEN`` (which only forces PTT *off*).
        """
        ...

    @abstractmethod
    def force_ptt_off(self) -> None:
        """Synchronous, no-await PTT release for the kill path."""
        ...

    # ---- native TX audio (FlexLib DAX) -----------------------------

    # Backends that own the radio-side DAX TX audio stream (i.e. the FlexLib
    # backend on SmartSDR v4) override these. AudioPipeline checks the flag
    # and routes Piper PCM through ``add_tx_audio`` instead of writing to a
    # Windows audio device.
    @property
    def supports_native_tx_audio(self) -> bool:
        return False

    async def add_tx_audio(self, pcm: bytes, sample_rate: int) -> None:
        """Push int16 mono PCM into the radio's TX audio stream.

        ``pcm`` is little-endian int16 samples at ``sample_rate`` Hz.
        Implementations resample and reformat to whatever the underlying
        stream wants. Caller has already keyed PTT.
        """
        raise NotImplementedError
