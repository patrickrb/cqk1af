from __future__ import annotations

import uuid

import pytest

from cqk1af.logging_qso.sqlite_store import SQLiteQSOStore
from cqk1af.state.context import QSOContext


@pytest.mark.asyncio
async def test_insert_and_list(tmp_path) -> None:
    store = SQLiteQSOStore(tmp_path / "qsos.sqlite")
    ctx = QSOContext(
        qso_id=uuid.uuid4(),
        callsign="W1AW",
        freq_hz=14_250_000,
        mode="USB",
        band="20m",
        rst_sent="59",
        rst_rcvd="57",
        name="Joe",
        qth="Newington CT",
    )
    rec = await store.insert(ctx)
    assert rec.callsign == "W1AW"
    assert rec.freq_hz == 14_250_000
    assert rec.synced_at == ""
    rows = await store.list_recent()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_list_unsynced(tmp_path) -> None:
    store = SQLiteQSOStore(tmp_path / "qsos.sqlite")
    for call in ("AA1A", "AA2A", "AA3A"):
        await store.insert(QSOContext(qso_id=uuid.uuid4(), callsign=call))
    pending = await store.list_unsynced()
    assert len(pending) == 3
    await store.mark_synced(pending[0].qso_id, cloudlog_id="cloud-1")
    pending2 = await store.list_unsynced()
    assert len(pending2) == 2


@pytest.mark.asyncio
async def test_mark_sync_failed_keeps_unsynced(tmp_path) -> None:
    store = SQLiteQSOStore(tmp_path / "qsos.sqlite")
    rec = await store.insert(QSOContext(qso_id=uuid.uuid4(), callsign="W1AW"))
    await store.mark_sync_failed(rec.qso_id, "boom")
    pending = await store.list_unsynced()
    assert len(pending) == 1
    assert pending[0].sync_error == "boom"


@pytest.mark.asyncio
async def test_unique_constraint_on_qso_id(tmp_path) -> None:
    store = SQLiteQSOStore(tmp_path / "qsos.sqlite")
    qid = uuid.uuid4()
    await store.insert(QSOContext(qso_id=qid, callsign="W1AW"))
    with pytest.raises(Exception):
        await store.insert(QSOContext(qso_id=qid, callsign="W1AW"))
