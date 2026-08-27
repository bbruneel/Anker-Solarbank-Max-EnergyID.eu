"""Tests for the offline reading queue (no live network)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import aiosqlite
import pytest

from energyid_monitor import reading_store, token_store


@pytest.fixture
def db_path() -> str:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as handle:
        path = handle.name
    yield path
    Path(path).unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_ensure_db_creates_readings_and_sync_state(db_path: str) -> None:
    await token_store.ensure_db(db_path)

    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row[0] for row in await cursor.fetchall()}
        await cursor.close()

    assert "readings" in tables
    assert "sync_state" in tables
    assert "tokens" in tables

    state = await reading_store.get_sync_state(db_path)
    assert state["last_successful_upload_at"] is None
    assert state["hello_upload_interval_seconds"] is None


@pytest.mark.asyncio
async def test_enqueue_and_list_pending(db_path: str) -> None:
    await token_store.ensure_db(db_path)

    first = {"ts": 100, "pv": 1.0}
    second = {"ts": 200, "pv": 2.0}
    id1 = await reading_store.enqueue(first, db_path, created_at=1000)
    id2 = await reading_store.enqueue(second, db_path, created_at=1001)

    pending = await reading_store.list_pending(db_path)
    assert [row["id"] for row in pending] == [id1, id2]
    assert pending[0]["payload"] == first
    assert pending[0]["sent_at"] is None
    assert pending[1]["payload"] == second


@pytest.mark.asyncio
async def test_mark_sent_removes_from_pending_but_keeps_row(db_path: str) -> None:
    await token_store.ensure_db(db_path)
    id1 = await reading_store.enqueue({"ts": 100, "bat": 1.0}, db_path)
    id2 = await reading_store.enqueue({"ts": 200, "bat": 2.0}, db_path)

    await reading_store.mark_sent([id1], db_path, sent_at=5000)

    pending = await reading_store.list_pending(db_path)
    assert [row["id"] for row in pending] == [id2]

    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute(
            "SELECT id, sent_at FROM readings ORDER BY id"
        )
        rows = await cursor.fetchall()
        await cursor.close()

    assert rows[0][0] == id1
    assert rows[0][1] == 5000
    assert rows[1][0] == id2
    assert rows[1][1] is None


@pytest.mark.asyncio
async def test_prune_expired_by_ts(db_path: str) -> None:
    await token_store.ensure_db(db_path)
    now = 1_700_000_000
    await reading_store.enqueue({"ts": now - 1000, "pv": 1.0}, db_path)
    keep_id = await reading_store.enqueue({"ts": now - 10, "pv": 2.0}, db_path)

    deleted = await reading_store.prune_expired(
        retention_seconds=100, db_path=db_path, now_seconds=now
    )
    assert deleted == 1

    pending = await reading_store.list_pending(db_path)
    assert [row["id"] for row in pending] == [keep_id]


@pytest.mark.asyncio
async def test_sync_state_hello_interval_and_last_upload(db_path: str) -> None:
    await token_store.ensure_db(db_path)

    await reading_store.set_hello_upload_interval(900, db_path)
    state = await reading_store.get_sync_state(db_path)
    assert state["hello_upload_interval_seconds"] == 900
    assert state["last_successful_upload_at"] is None

    await reading_store.set_last_successful_upload(1_700_000_100, db_path)
    state = await reading_store.get_sync_state(db_path)
    assert state["last_successful_upload_at"] == 1_700_000_100
    assert state["hello_upload_interval_seconds"] == 900


@pytest.mark.asyncio
async def test_enqueue_stores_exact_json(db_path: str) -> None:
    await token_store.ensure_db(db_path)
    payload = {"ts": 42, "pv": 6.1, "bat-soc": 69.0}
    await reading_store.enqueue(payload, db_path)

    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute("SELECT payload_json FROM readings")
        row = await cursor.fetchone()
        await cursor.close()

    assert json.loads(row[0]) == payload


@pytest.mark.asyncio
async def test_mark_sent_empty_ids_is_noop(db_path: str) -> None:
    await token_store.ensure_db(db_path)
    await reading_store.mark_sent([], db_path)
    assert await reading_store.list_pending(db_path) == []
