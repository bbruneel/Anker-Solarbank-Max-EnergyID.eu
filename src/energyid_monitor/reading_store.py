from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, TypedDict

import aiosqlite

from energyid_monitor import token_store

DEFAULT_DB_PATH = token_store.DEFAULT_DB_PATH


class StoredReading(TypedDict):
    id: int
    ts: int
    payload: dict[str, Any]
    created_at: int
    sent_at: int | None


class SyncState(TypedDict):
    last_successful_upload_at: int | None
    hello_upload_interval_seconds: int | None
    updated_at: int


def _normalize_db_path(db_path: str | Path) -> tuple[str, bool]:
    db_path_str, is_uri, _ = token_store._normalize_db_path(db_path)
    return db_path_str, is_uri


async def enqueue(
    payload: dict[str, Any],
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    created_at: int | None = None,
) -> int:
    """Insert a webhook payload row with sent_at NULL. Returns the new row id."""
    await token_store.ensure_db(db_path)
    db_path_str, is_uri = _normalize_db_path(db_path)
    now = created_at if created_at is not None else int(time.time())
    ts = int(payload["ts"])
    payload_json = json.dumps(payload, separators=(",", ":"))

    async with aiosqlite.connect(db_path_str, uri=is_uri) as conn:
        cursor = await conn.execute(
            """
            INSERT INTO readings (ts, payload_json, created_at, sent_at)
            VALUES (?, ?, ?, NULL)
            """,
            (ts, payload_json, now),
        )
        row_id = cursor.lastrowid
        await cursor.close()
        await conn.commit()

    if row_id is None:
        raise RuntimeError("Failed to enqueue reading: no row id returned")
    return int(row_id)


async def list_pending(
    db_path: str | Path = DEFAULT_DB_PATH,
) -> list[StoredReading]:
    """Return unsent readings ordered by ts ascending."""
    await token_store.ensure_db(db_path)
    db_path_str, is_uri = _normalize_db_path(db_path)

    async with aiosqlite.connect(db_path_str, uri=is_uri) as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            """
            SELECT id, ts, payload_json, created_at, sent_at
            FROM readings
            WHERE sent_at IS NULL
            ORDER BY ts ASC, id ASC
            """
        )
        rows = await cursor.fetchall()
        await cursor.close()

    readings: list[StoredReading] = []
    for row in rows:
        readings.append(
            {
                "id": int(row["id"]),
                "ts": int(row["ts"]),
                "payload": json.loads(row["payload_json"]),
                "created_at": int(row["created_at"]),
                "sent_at": int(row["sent_at"]) if row["sent_at"] is not None else None,
            }
        )
    return readings


async def mark_sent(
    reading_ids: list[int],
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    sent_at: int | None = None,
) -> None:
    """Set sent_at on the given reading ids."""
    if not reading_ids:
        return

    await token_store.ensure_db(db_path)
    db_path_str, is_uri = _normalize_db_path(db_path)
    now = sent_at if sent_at is not None else int(time.time())
    placeholders = ",".join("?" for _ in reading_ids)

    async with aiosqlite.connect(db_path_str, uri=is_uri) as conn:
        await conn.execute(
            f"""
            UPDATE readings
            SET sent_at = ?
            WHERE id IN ({placeholders}) AND sent_at IS NULL
            """,
            (now, *reading_ids),
        )
        await conn.commit()


async def prune_expired(
    retention_seconds: int,
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    now_seconds: int | None = None,
) -> int:
    """Delete readings whose ts is older than now - retention_seconds. Returns deleted count."""
    if retention_seconds < 0:
        raise ValueError("retention_seconds must be >= 0")

    await token_store.ensure_db(db_path)
    db_path_str, is_uri = _normalize_db_path(db_path)
    now = now_seconds if now_seconds is not None else int(time.time())
    cutoff = now - retention_seconds

    async with aiosqlite.connect(db_path_str, uri=is_uri) as conn:
        cursor = await conn.execute(
            "DELETE FROM readings WHERE ts < ?",
            (cutoff,),
        )
        deleted = cursor.rowcount
        await cursor.close()
        await conn.commit()

    return int(deleted) if deleted is not None else 0


async def get_sync_state(db_path: str | Path = DEFAULT_DB_PATH) -> SyncState:
    """Return the single sync_state row (ensuring it exists)."""
    await token_store.ensure_db(db_path)
    db_path_str, is_uri = _normalize_db_path(db_path)

    async with aiosqlite.connect(db_path_str, uri=is_uri) as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            """
            SELECT last_successful_upload_at, hello_upload_interval_seconds, updated_at
            FROM sync_state
            WHERE id = 1
            """
        )
        row = await cursor.fetchone()
        await cursor.close()

        if row is None:
            now = int(time.time())
            await conn.execute(
                """
                INSERT INTO sync_state
                    (id, last_successful_upload_at, hello_upload_interval_seconds, updated_at)
                VALUES (1, NULL, NULL, ?)
                """,
                (now,),
            )
            await conn.commit()
            return {
                "last_successful_upload_at": None,
                "hello_upload_interval_seconds": None,
                "updated_at": now,
            }

    return {
        "last_successful_upload_at": (
            int(row["last_successful_upload_at"])
            if row["last_successful_upload_at"] is not None
            else None
        ),
        "hello_upload_interval_seconds": (
            int(row["hello_upload_interval_seconds"])
            if row["hello_upload_interval_seconds"] is not None
            else None
        ),
        "updated_at": int(row["updated_at"]),
    }


async def set_last_successful_upload(
    uploaded_at: int,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> None:
    """Record wall-clock time of the last successful webhook POST."""
    await token_store.ensure_db(db_path)
    db_path_str, is_uri = _normalize_db_path(db_path)
    now = int(time.time())

    async with aiosqlite.connect(db_path_str, uri=is_uri) as conn:
        await conn.execute(
            """
            INSERT INTO sync_state
                (id, last_successful_upload_at, hello_upload_interval_seconds, updated_at)
            VALUES (1, ?, NULL, ?)
            ON CONFLICT(id) DO UPDATE SET
                last_successful_upload_at = excluded.last_successful_upload_at,
                updated_at = excluded.updated_at
            """,
            (uploaded_at, now),
        )
        await conn.commit()


async def set_hello_upload_interval(
    upload_interval_seconds: int,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> None:
    """Cache webhookPolicy.uploadInterval from /hello."""
    if upload_interval_seconds <= 0:
        raise ValueError("upload_interval_seconds must be > 0")

    await token_store.ensure_db(db_path)
    db_path_str, is_uri = _normalize_db_path(db_path)
    now = int(time.time())

    async with aiosqlite.connect(db_path_str, uri=is_uri) as conn:
        await conn.execute(
            """
            INSERT INTO sync_state
                (id, last_successful_upload_at, hello_upload_interval_seconds, updated_at)
            VALUES (1, NULL, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                hello_upload_interval_seconds = excluded.hello_upload_interval_seconds,
                updated_at = excluded.updated_at
            """,
            (upload_interval_seconds, now),
        )
        await conn.commit()
