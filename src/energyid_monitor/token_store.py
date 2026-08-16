from __future__ import annotations

import time
from pathlib import Path
from typing import TypedDict

import aiosqlite

DEFAULT_DB_PATH = Path("data/token.db")
MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "dbscripts"
EXPIRY_BUFFER_SECONDS = 3600


class StoredToken(TypedDict):
    bearer_token: str
    twin_id: str
    exp: int


def _normalize_db_path(db_path: str | Path) -> tuple[str, bool, Path | None]:
    db_path_str = str(db_path)
    is_uri = db_path_str.startswith("file:")
    file_path = None if is_uri else Path(db_path_str)
    return db_path_str, is_uri, file_path


async def ensure_db(db_path: str | Path = DEFAULT_DB_PATH) -> None:
    """Ensure the SQLite database exists and run migrations."""
    db_path_str, is_uri, file_path = _normalize_db_path(db_path)
    if file_path:
        file_path.parent.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(db_path_str, uri=is_uri) as conn:
        for script_path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            script_sql = script_path.read_text(encoding="utf-8")
            await conn.executescript(script_sql)
        await conn.commit()


async def get_latest_token(db_path: str | Path = DEFAULT_DB_PATH) -> StoredToken | None:
    """Return the most recent token by expiration, or None if missing."""
    db_path_str, is_uri, _ = _normalize_db_path(db_path)
    async with aiosqlite.connect(db_path_str, uri=is_uri) as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute("""
            SELECT bearer_token, twin_id, exp
            FROM tokens
            ORDER BY exp DESC
            LIMIT 1
            """)
        row = await cursor.fetchone()
        await cursor.close()
        if not row:
            return None
        return {
            "bearer_token": row["bearer_token"],
            "twin_id": row["twin_id"],
            "exp": int(row["exp"]),
        }


async def store_token(
    token: StoredToken, db_path: str | Path = DEFAULT_DB_PATH
) -> None:
    """Persist a new token record and prune expired rows."""
    db_path_str, is_uri, _ = _normalize_db_path(db_path)
    now = int(time.time())
    async with aiosqlite.connect(db_path_str, uri=is_uri) as conn:
        await conn.execute(
            """
            INSERT INTO tokens (bearer_token, twin_id, exp, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (token["bearer_token"], token["twin_id"], int(token["exp"]), now, now),
        )
        # Drop rows already past expiry so the cache stays small for cron runs.
        await conn.execute("DELETE FROM tokens WHERE exp <= ?", (now,))
        await conn.commit()


def is_token_valid(token: StoredToken, now_seconds: int | None = None) -> bool:
    """Check whether the token is valid with a one-hour buffer."""
    now = now_seconds or int(time.time())
    return token["exp"] > now + EXPIRY_BUFFER_SECONDS
