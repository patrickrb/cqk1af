from __future__ import annotations

from dataclasses import dataclass

from ..config import RigMode


@dataclass
class SliceState:
    slice_id: int
    freq_hz: int
    mode: RigMode
    filter_low_hz: int = 200
    filter_high_hz: int = 2900
    rxant: str = "ANT1"
    active: bool = True
    s_meter_dbm: float = -110.0


@dataclass
class RadioState:
    connected: bool = False
    model: str = ""
    version: str = ""
    serial: str = ""
    ptt_on: bool = False
    # Current TX modulation source on the radio (MIC, DAX, ACC, BAL, ...).
    # Tracked from transmit-subsystem status updates. Surfaced so the operator
    # knows whether Piper TTS will actually modulate.
    mic_source: str = ""
    # FlexLib DAX TX audio stream id, when the FlexLib backend has one open.
    # None on other backends. Surfaced for dashboard / debug visibility.
    tx_stream_id: int | None = None
    slices: dict[int, SliceState] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.slices is None:
            self.slices = {}
