"""Local SQLite store for completed QSOs.

Schema:
  qsos(id, qso_id, callsign, start_ts, end_ts, freq_hz, mode, band,
       rst_sent, rst_rcvd, name, qth, grid, comment, ai_assisted,
       synced_at, cloudlog_id, sync_error)

We write the QSO durably to SQLite first, then enqueue background sync to
Cloudlog/Wavelog. A row is "logged" the moment it lands in SQLite; cloudlog_id
fills in (or sync_error gets populated) on the next sync round.
"""
from __future__ import annotations

import asyncio
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from ..state.context import QSOContext
from ..util.logging import get_logger
from ..util.time import utcnow

log = get_logger(__name__)


SCHEMA = """
CREATE TABLE IF NOT EXISTS qsos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    qso_id TEXT NOT NULL UNIQUE,
    callsign TEXT NOT NULL,
    start_ts TEXT NOT NULL,
    end_ts TEXT NOT NULL DEFAULT '',
    freq_hz INTEGER NOT NULL DEFAULT 0,
    mode TEXT NOT NULL DEFAULT '',
    band TEXT NOT NULL DEFAULT '',
    rst_sent TEXT NOT NULL DEFAULT '',
    rst_rcvd TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL DEFAULT '',
    qth TEXT NOT NULL DEFAULT '',
    grid TEXT NOT NULL DEFAULT '',
    comment TEXT NOT NULL DEFAULT '',
    ai_assisted INTEGER NOT NULL DEFAULT 1,
    logged_at TEXT NOT NULL,
    synced_at TEXT NOT NULL DEFAULT '',
    cloudlog_id TEXT NOT NULL DEFAULT '',
    sync_error TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_qsos_logged_at ON qsos(logged_at DESC);
CREATE INDEX IF NOT EXISTS idx_qsos_unsynced ON qsos(synced_at) WHERE synced_at = '';
"""


@dataclass
class QSORecord:
    qso_id: str
    callsign: str
    start_ts: str
    end_ts: str
    freq_hz: int
    mode: str
    band: str
    rst_sent: str
    rst_rcvd: str
    name: str
    qth: str
    grid: str
    comment: str
    ai_assisted: bool
    logged_at: str
    synced_at: str = ""
    cloudlog_id: str = ""
    sync_error: str = ""

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "QSORecord":
        return cls(
            qso_id=row["qso_id"],
            callsign=row["callsign"],
            start_ts=row["start_ts"],
            end_ts=row["end_ts"],
            freq_hz=int(row["freq_hz"]),
            mode=row["mode"],
            band=row["band"],
            rst_sent=row["rst_sent"],
            rst_rcvd=row["rst_rcvd"],
            name=row["name"],
            qth=row["qth"],
            grid=row["grid"],
            comment=row["comment"],
            ai_assisted=bool(row["ai_assisted"]),
            logged_at=row["logged_at"],
            synced_at=row["synced_at"],
            cloudlog_id=row["cloudlog_id"],
            sync_error=row["sync_error"],
        )

    def to_dict(self) -> dict:
        return {
            "qso_id": self.qso_id,
            "callsign": self.callsign,
            "start_ts": self.start_ts,
            "end_ts": self.end_ts,
            "freq_hz": self.freq_hz,
            "mode": self.mode,
            "band": self.band,
            "rst_sent": self.rst_sent,
            "rst_rcvd": self.rst_rcvd,
            "name": self.name,
            "qth": self.qth,
            "grid": self.grid,
            "comment": self.comment,
            "ai_assisted": self.ai_assisted,
            "logged_at": self.logged_at,
            "synced_at": self.synced_at,
            "cloudlog_id": self.cloudlog_id,
            "sync_error": self.sync_error,
        }


class SQLiteQSOStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            conn.commit()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=5.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
        finally:
            conn.close()

    async def insert(self, ctx: QSOContext) -> QSORecord:
        now = utcnow().isoformat()

        def _do() -> QSORecord:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO qsos(
                        qso_id, callsign, start_ts, end_ts, freq_hz, mode, band,
                        rst_sent, rst_rcvd, name, qth, grid, comment,
                        ai_assisted, logged_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        str(ctx.qso_id),
                        ctx.callsign.upper(),
                        ctx.start_ts,
                        ctx.end_ts or now,
                        ctx.freq_hz,
                        ctx.mode,
                        ctx.band,
                        ctx.rst_sent,
                        ctx.rst_rcvd,
                        ctx.name,
                        ctx.qth,
                        ctx.grid,
                        ctx.comment,
                        1 if ctx.ai_assisted else 0,
                        now,
                    ),
                )
                row = conn.execute(
                    "SELECT * FROM qsos WHERE qso_id = ?", (str(ctx.qso_id),)
                ).fetchone()
                return QSORecord.from_row(row)

        async with self._lock:
            return await asyncio.to_thread(_do)

    async def list_recent(self, limit: int = 50) -> list[QSORecord]:
        def _do() -> list[QSORecord]:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM qsos ORDER BY logged_at DESC LIMIT ?", (limit,)
                ).fetchall()
                return [QSORecord.from_row(r) for r in rows]

        return await asyncio.to_thread(_do)

    async def list_unsynced(self, limit: int = 50) -> list[QSORecord]:
        def _do() -> list[QSORecord]:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM qsos WHERE synced_at = '' ORDER BY logged_at ASC LIMIT ?",
                    (limit,),
                ).fetchall()
                return [QSORecord.from_row(r) for r in rows]

        return await asyncio.to_thread(_do)

    async def mark_synced(self, qso_id: str, cloudlog_id: str = "") -> None:
        now = utcnow().isoformat()

        def _do() -> None:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE qsos SET synced_at = ?, cloudlog_id = ?, sync_error = '' WHERE qso_id = ?",
                    (now, cloudlog_id, qso_id),
                )

        await asyncio.to_thread(_do)

    async def mark_sync_failed(self, qso_id: str, error: str) -> None:
        def _do() -> None:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE qsos SET sync_error = ? WHERE qso_id = ?",
                    (error[:500], qso_id),
                )

        await asyncio.to_thread(_do)
