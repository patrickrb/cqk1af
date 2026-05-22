from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from ..util.time import utcnow


@dataclass
class HeardCallsign:
    callsign: str
    confidence: float
    snr_dbm: float | None = None
    raw_text: str = ""


@dataclass
class QSOContext:
    qso_id: uuid.UUID = field(default_factory=uuid.uuid4)
    callsign: str = ""
    freq_hz: int = 0
    mode: str = ""
    band: str = ""
    rst_sent: str = "59"
    rst_rcvd: str = ""
    name: str = ""
    qth: str = ""
    grid: str = ""
    comment: str = ""
    start_ts: str = ""
    end_ts: str = ""
    ai_assisted: bool = True

    def __post_init__(self) -> None:
        if not self.start_ts:
            self.start_ts = utcnow().isoformat()

    def to_dict(self) -> dict:
        return {
            "qso_id": str(self.qso_id),
            "callsign": self.callsign,
            "freq_hz": self.freq_hz,
            "mode": self.mode,
            "band": self.band,
            "rst_sent": self.rst_sent,
            "rst_rcvd": self.rst_rcvd,
            "name": self.name,
            "qth": self.qth,
            "grid": self.grid,
            "comment": self.comment,
            "start_ts": self.start_ts,
            "end_ts": self.end_ts,
            "ai_assisted": self.ai_assisted,
        }


@dataclass
class SessionContext:
    armed: bool = False
    current_qso: QSOContext | None = None
    current_slice_id: int = 0
    current_freq_hz: int = 0
    current_mode: str = ""
    current_band: str = ""
    pileup: list[HeardCallsign] = field(default_factory=list)
    cq_attempts: int = 0
    freq_check_attempts: int = 0
    qsos_completed: int = 0
    qsos_logged: int = 0
    last_tx_ts: str = ""
    last_id_ts: str = ""
    error: str = ""

    def reset_session(self) -> None:
        self.current_qso = None
        self.pileup.clear()
        self.cq_attempts = 0
        self.freq_check_attempts = 0
        self.error = ""

    def to_dict(self) -> dict:
        return {
            "armed": self.armed,
            "current_qso": self.current_qso.to_dict() if self.current_qso else None,
            "current_slice_id": self.current_slice_id,
            "current_freq_hz": self.current_freq_hz,
            "current_mode": self.current_mode,
            "current_band": self.current_band,
            "pileup": [
                {"callsign": h.callsign, "confidence": h.confidence, "snr_dbm": h.snr_dbm}
                for h in self.pileup
            ],
            "cq_attempts": self.cq_attempts,
            "freq_check_attempts": self.freq_check_attempts,
            "qsos_completed": self.qsos_completed,
            "qsos_logged": self.qsos_logged,
            "last_tx_ts": self.last_tx_ts,
            "last_id_ts": self.last_id_ts,
            "error": self.error,
        }
