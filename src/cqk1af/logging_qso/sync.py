"""Background sync: pulls unsynced QSOs from SQLite and pushes to Cloudlog.

Runs as an asyncio task. Backs off on failures. Honours an `enqueue` event so
freshly-logged QSOs are picked up immediately without polling.
"""
from __future__ import annotations

import asyncio
import random

from ..util.logging import get_logger
from .cloudlog_client import CloudlogClient, CloudlogError
from .sqlite_store import SQLiteQSOStore

log = get_logger(__name__)


class QSOSyncer:
    def __init__(
        self,
        store: SQLiteQSOStore,
        client: CloudlogClient,
        *,
        operator_callsign: str = "",
        idle_interval_s: float = 300.0,
        max_backoff_s: float = 600.0,
    ) -> None:
        self.store = store
        self.client = client
        self.operator_callsign = operator_callsign
        self.idle_interval_s = idle_interval_s
        self.max_backoff_s = max_backoff_s
        self._wake = asyncio.Event()
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    def enqueue(self) -> None:
        self._wake.set()

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="qso_syncer")

    async def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=2.0)
            except asyncio.TimeoutError:
                self._task.cancel()
            self._task = None

    async def _run(self) -> None:
        backoff = 0.0
        while not self._stop.is_set():
            try:
                if not self.client.enabled:
                    # Long idle when Cloudlog isn't configured
                    await self._wait(self.idle_interval_s)
                    continue
                pending = await self.store.list_unsynced(limit=50)
                if not pending:
                    backoff = 0.0
                    await self._wait(self.idle_interval_s)
                    continue
                any_failed = False
                for qso in pending:
                    if self._stop.is_set():
                        return
                    try:
                        cloud_id = await self.client.upload(
                            qso, station_callsign=self.operator_callsign
                        )
                        await self.store.mark_synced(qso.qso_id, cloud_id)
                        log.info("qso_sync.ok", qso_id=qso.qso_id, call=qso.callsign, cloud_id=cloud_id)
                    except CloudlogError as e:
                        any_failed = True
                        await self.store.mark_sync_failed(qso.qso_id, str(e))
                        log.warning("qso_sync.failed", qso_id=qso.qso_id, error=str(e))
                    except Exception as e:
                        any_failed = True
                        await self.store.mark_sync_failed(qso.qso_id, repr(e))
                        log.exception("qso_sync.error")
                if any_failed:
                    backoff = min(self.max_backoff_s, max(5.0, backoff * 2 if backoff else 5.0))
                    await self._wait(backoff + random.uniform(0, 1.5))
                else:
                    backoff = 0.0
            except asyncio.CancelledError:
                return
            except Exception:
                log.exception("qso_sync.loop_error")
                await self._wait(30.0)

    async def _wait(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass
        self._wake.clear()
