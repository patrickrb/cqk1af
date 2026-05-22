"""FCC §97.119 station identification timer.

Track the last time the operator's callsign was transmitted. ``due()`` returns
True when an identification is required (default: every 10 minutes during a
QSO, and at the end of any communication).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from ..util.time import utcnow


class IdTimer:
    def __init__(self, interval_seconds: int = 600) -> None:
        self.interval = timedelta(seconds=interval_seconds)
        self._last_id_ts: datetime | None = None

    def mark_id_sent(self, *, when: datetime | None = None) -> None:
        self._last_id_ts = when or utcnow()

    @property
    def last_id_ts(self) -> datetime | None:
        return self._last_id_ts

    def time_until_due(self, *, now: datetime | None = None) -> float:
        now = now or utcnow()
        if self._last_id_ts is None:
            return 0.0  # never IDed — due immediately
        delta = (self._last_id_ts + self.interval) - now
        return max(0.0, delta.total_seconds())

    def due(self, *, now: datetime | None = None) -> bool:
        return self.time_until_due(now=now) <= 0.0
