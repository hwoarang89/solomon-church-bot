"""Database layer — asyncpg pool, DDL schema, all SQL queries."""

from __future__ import annotations

import json
import time
import logging
from typing import Optional

import asyncpg

from config import DATABASE_URL
from models import (
    User, Event, EventRegistration, AdminRequest,
    UserRole, EventStatus, RequestStatus,
)

logger = logging.getLogger(__name__)

pool: Optional[asyncpg.Pool] = None

# ---------------------------------------------------------------------------
# DDL — idempotent schema creation
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
-- Custom enum types (IF NOT EXISTS via DO blocks)
DO $$ BEGIN
    CREATE TYPE user_role AS ENUM ('user', 'admin', 'super_admin');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE event_status AS ENUM ('draft', 'pending', 'active', 'archived');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE request_status AS ENUM ('pending', 'approved', 'rejected');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS users (
    telegram_id  BIGINT PRIMARY KEY,
    username     TEXT UNIQUE,
    full_name    TEXT NOT NULL,
    phone        TEXT,
    role         user_role NOT NULL DEFAULT 'user',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS events (
    id               SERIAL PRIMARY KEY,
    title            TEXT NOT NULL,
    type             TEXT,
    date_start       DATE NOT NULL,
    date_end         DATE,
    time             TEXT,
    place            TEXT,
    description      TEXT,
    max_participants INTEGER DEFAULT 0,
    status           event_status NOT NULL DEFAULT 'pending',
    created_by       TEXT REFERENCES users(username) ON UPDATE CASCADE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS event_registrations (
    id            SERIAL PRIMARY KEY,
    event_id      INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    username      TEXT,
    telegram_id   BIGINT REFERENCES users(telegram_id),
    full_name     TEXT NOT NULL,
    phone         TEXT,
    level         TEXT,
    comment       TEXT,
    registered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (event_id, telegram_id)
);

CREATE TABLE IF NOT EXISTS info (
    id          SERIAL PRIMARY KEY,
    category    TEXT NOT NULL,
    title       TEXT NOT NULL,
    content     TEXT NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS admin_requests (
    id              SERIAL PRIMARY KEY,
    username        TEXT NOT NULL,
    telegram_id     BIGINT REFERENCES users(telegram_id),
    full_name       TEXT,
    phone           TEXT,
    requested_table TEXT,
    request_type    TEXT NOT NULL DEFAULT 'admin_access',
    payload_json    JSONB,
    comment         TEXT,
    status          request_status NOT NULL DEFAULT 'pending',
    reviewed_by     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    reviewed_at     TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS admin_table_access (
    id         SERIAL PRIMARY KEY,
    username   TEXT NOT NULL REFERENCES users(username) ON UPDATE CASCADE,
    table_name TEXT NOT NULL,
    granted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (username, table_name)
);

CREATE TABLE IF NOT EXISTS home_groups (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    leader_username TEXT REFERENCES users(username) ON UPDATE CASCADE,
    day_of_week     TEXT,
    time            TEXT,
    address         TEXT,
    district        TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS home_group_members (
    id          SERIAL PRIMARY KEY,
    group_id    INTEGER NOT NULL REFERENCES home_groups(id) ON DELETE CASCADE,
    username    TEXT NOT NULL,
    telegram_id BIGINT,
    joined_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (group_id, username)
);

CREATE TABLE IF NOT EXISTS attendance (
    id          SERIAL PRIMARY KEY,
    group_id    INTEGER NOT NULL REFERENCES home_groups(id) ON DELETE CASCADE,
    username    TEXT NOT NULL,
    telegram_id BIGINT,
    event_date  DATE NOT NULL,
    status      TEXT NOT NULL DEFAULT 'no_response',
    marked_at   TIMESTAMPTZ,
    UNIQUE (group_id, username, event_date)
);

CREATE TABLE IF NOT EXISTS prayer_requests (
    id          SERIAL PRIMARY KEY,
    username    TEXT,
    telegram_id BIGINT,
    content     TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    notified    BOOLEAN DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS broadcasts (
    id                 SERIAL PRIMARY KEY,
    sender_username    TEXT NOT NULL,
    sender_telegram_id BIGINT,
    scope              TEXT NOT NULL DEFAULT 'local',
    target_table       TEXT,
    message            TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'pending',
    approved_by        TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at            TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS notification_settings (
    id            SERIAL PRIMARY KEY,
    username      TEXT NOT NULL REFERENCES users(username) ON UPDATE CASCADE,
    table_name    TEXT NOT NULL,
    enabled       BOOLEAN DEFAULT TRUE,
    schedule_time TEXT DEFAULT '09:00',
    last_sent     TIMESTAMPTZ,
    UNIQUE (username, table_name)
);
"""


# ---------------------------------------------------------------------------
# Pool lifecycle
# ---------------------------------------------------------------------------

async def init_db() -> None:
    """Create the connection pool and run DDL."""
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA_SQL)
    logger.info("Database initialised")


async def close_db() -> None:
    """Gracefully close the pool."""
    global pool
    if pool:
        await pool.close()
        pool = None
    logger.info("Database pool closed")


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

async def upsert_user(
    telegram_id: int,
    username: Optional[str],
    full_name: str,
    phone: Optional[str] = None,
) -> User:
    row = await pool.fetchrow(
        """
        INSERT INTO users (telegram_id, username, full_name, phone)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (telegram_id) DO UPDATE
            SET username  = COALESCE(EXCLUDED.username, users.username),
                full_name = EXCLUDED.full_name
        RETURNING *
        """,
        telegram_id, username, full_name, phone,
    )
    return _row_to_user(row)


async def get_user(telegram_id: int) -> Optional[User]:
    row = await pool.fetchrow(
        "SELECT * FROM users WHERE telegram_id = $1", telegram_id,
    )
    return _row_to_user(row) if row else None


async def get_user_by_username(username: str) -> Optional[User]:
    row = await pool.fetchrow(
        "SELECT * FROM users WHERE username = $1", username,
    )
    return _row_to_user(row) if row else None


async def set_user_role(username: str, role: UserRole) -> bool:
    tag = await pool.execute(
        "UPDATE users SET role = $1 WHERE username = $2",
        role.value, username,
    )
    return tag == "UPDATE 1"


async def get_all_users() -> list[User]:
    rows = await pool.fetch("SELECT * FROM users ORDER BY created_at")
    return [_row_to_user(r) for r in rows]


def _row_to_user(row: asyncpg.Record) -> User:
    return User(
        telegram_id=row["telegram_id"],
        username=row["username"],
        full_name=row["full_name"],
        phone=row["phone"],
        role=UserRole(row["role"]),
        created_at=row["created_at"],
    )


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

async def create_event(
    title: str,
    date_start,
    *,
    type: Optional[str] = None,
    date_end=None,
    time: Optional[str] = None,
    place: Optional[str] = None,
    description: Optional[str] = None,
    max_participants: int = 0,
    status: EventStatus = EventStatus.PENDING,
    created_by: Optional[str] = None,
) -> Event:
    row = await pool.fetchrow(
        """
        INSERT INTO events
            (title, type, date_start, date_end, time, place,
             description, max_participants, status, created_by)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
        RETURNING *
        """,
        title, type, date_start, date_end, time, place,
        description, max_participants, status.value, created_by,
    )
    return _row_to_event(row)


async def get_active_events() -> list[Event]:
    rows = await pool.fetch(
        "SELECT * FROM events WHERE status = 'active' ORDER BY date_start",
    )
    return [_row_to_event(r) for r in rows]


async def get_events_by_status(status: EventStatus) -> list[Event]:
    rows = await pool.fetch(
        "SELECT * FROM events WHERE status = $1 ORDER BY date_start",
        status.value,
    )
    return [_row_to_event(r) for r in rows]


async def get_event(event_id: int) -> Optional[Event]:
    row = await pool.fetchrow("SELECT * FROM events WHERE id = $1", event_id)
    return _row_to_event(row) if row else None


async def update_event(event_id: int, **fields) -> Optional[Event]:
    if not fields:
        return await get_event(event_id)
    sets = []
    vals = []
    for i, (k, v) in enumerate(fields.items(), 1):
        sets.append(f"{k} = ${i}")
        vals.append(v)
    vals.append(event_id)
    row = await pool.fetchrow(
        f"UPDATE events SET {', '.join(sets)} WHERE id = ${len(vals)} RETURNING *",
        *vals,
    )
    return _row_to_event(row) if row else None


async def activate_event(event_id: int) -> Optional[Event]:
    return await update_event(event_id, status=EventStatus.ACTIVE.value)


async def archive_event(event_id: int) -> Optional[Event]:
    return await update_event(event_id, status=EventStatus.ARCHIVED.value)


async def delete_event(event_id: int) -> bool:
    tag = await pool.execute("DELETE FROM events WHERE id = $1", event_id)
    return tag == "DELETE 1"


def _row_to_event(row: asyncpg.Record) -> Event:
    return Event(
        id=row["id"],
        title=row["title"],
        type=row["type"],
        date_start=row["date_start"],
        date_end=row["date_end"],
        time=row["time"],
        place=row["place"],
        description=row["description"],
        max_participants=row["max_participants"],
        status=EventStatus(row["status"]),
        created_by=row["created_by"],
        created_at=row["created_at"],
    )


# ---------------------------------------------------------------------------
# Event registrations
# ---------------------------------------------------------------------------

async def register_for_event(
    event_id: int,
    full_name: str,
    *,
    username: Optional[str] = None,
    telegram_id: Optional[int] = None,
    phone: Optional[str] = None,
    level: Optional[str] = None,
    comment: Optional[str] = None,
) -> EventRegistration:
    row = await pool.fetchrow(
        """
        INSERT INTO event_registrations
            (event_id, username, telegram_id, full_name, phone, level, comment)
        VALUES ($1,$2,$3,$4,$5,$6,$7)
        ON CONFLICT (event_id, telegram_id) DO UPDATE
            SET full_name = EXCLUDED.full_name,
                phone     = EXCLUDED.phone,
                level     = EXCLUDED.level,
                comment   = EXCLUDED.comment
        RETURNING *
        """,
        event_id, username, telegram_id, full_name, phone, level, comment,
    )
    return _row_to_registration(row)


async def get_event_registrations(event_id: int) -> list[EventRegistration]:
    rows = await pool.fetch(
        "SELECT * FROM event_registrations WHERE event_id = $1 ORDER BY registered_at",
        event_id,
    )
    return [_row_to_registration(r) for r in rows]


async def count_event_registrations(event_id: int) -> int:
    return await pool.fetchval(
        "SELECT count(*) FROM event_registrations WHERE event_id = $1", event_id,
    )


def _row_to_registration(row: asyncpg.Record) -> EventRegistration:
    return EventRegistration(
        id=row["id"],
        event_id=row["event_id"],
        username=row["username"],
        telegram_id=row["telegram_id"],
        full_name=row["full_name"],
        phone=row["phone"],
        level=row["level"],
        comment=row["comment"],
        registered_at=row["registered_at"],
    )


# ---------------------------------------------------------------------------
# Info / knowledge base
# ---------------------------------------------------------------------------

async def create_info(category: str, title: str, content: str) -> int:
    return await pool.fetchval(
        "INSERT INTO info (category, title, content) VALUES ($1,$2,$3) RETURNING id",
        category, title, content,
    )


async def update_info(info_id: int, **fields) -> bool:
    if not fields:
        return False
    sets = []
    vals = []
    for i, (k, v) in enumerate(fields.items(), 1):
        sets.append(f"{k} = ${i}")
        vals.append(v)
    vals.append(info_id)
    tag = await pool.execute(
        f"UPDATE info SET {', '.join(sets)}, updated_at = now() WHERE id = ${len(vals)}",
        *vals,
    )
    return tag == "UPDATE 1"


async def delete_info(info_id: int) -> bool:
    tag = await pool.execute("DELETE FROM info WHERE id = $1", info_id)
    return tag == "DELETE 1"


async def get_all_info() -> list[dict]:
    rows = await pool.fetch("SELECT * FROM info ORDER BY category, id")
    return [dict(r) for r in rows]


async def get_info_by_category(category: str) -> list[dict]:
    rows = await pool.fetch(
        "SELECT * FROM info WHERE category = $1 ORDER BY id", category,
    )
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Admin requests
# ---------------------------------------------------------------------------

async def create_admin_request(
    username: str,
    request_type: str,
    *,
    telegram_id: Optional[int] = None,
    full_name: Optional[str] = None,
    phone: Optional[str] = None,
    requested_table: Optional[str] = None,
    payload_json: Optional[dict] = None,
    comment: Optional[str] = None,
) -> AdminRequest:
    row = await pool.fetchrow(
        """
        INSERT INTO admin_requests
            (username, telegram_id, full_name, phone,
             requested_table, request_type, payload_json, comment)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
        RETURNING *
        """,
        username, telegram_id, full_name, phone,
        requested_table, request_type,
        json.dumps(payload_json) if payload_json else None,
        comment,
    )
    return _row_to_request(row)


async def get_pending_requests() -> list[AdminRequest]:
    rows = await pool.fetch(
        "SELECT * FROM admin_requests WHERE status = 'pending' ORDER BY created_at",
    )
    return [_row_to_request(r) for r in rows]


async def get_request(request_id: int) -> Optional[AdminRequest]:
    row = await pool.fetchrow(
        "SELECT * FROM admin_requests WHERE id = $1", request_id,
    )
    return _row_to_request(row) if row else None


async def approve_request(request_id: int, reviewed_by: str) -> Optional[AdminRequest]:
    row = await pool.fetchrow(
        """
        UPDATE admin_requests
        SET status = 'approved', reviewed_by = $1, reviewed_at = now()
        WHERE id = $2 AND status = 'pending'
        RETURNING *
        """,
        reviewed_by, request_id,
    )
    return _row_to_request(row) if row else None


async def reject_request(request_id: int, reviewed_by: str) -> Optional[AdminRequest]:
    row = await pool.fetchrow(
        """
        UPDATE admin_requests
        SET status = 'rejected', reviewed_by = $1, reviewed_at = now()
        WHERE id = $2 AND status = 'pending'
        RETURNING *
        """,
        reviewed_by, request_id,
    )
    return _row_to_request(row) if row else None


def _row_to_request(row: asyncpg.Record) -> AdminRequest:
    pj = row["payload_json"]
    if isinstance(pj, str):
        pj = json.loads(pj)
    return AdminRequest(
        id=row["id"],
        username=row["username"],
        request_type=row["request_type"],
        telegram_id=row["telegram_id"],
        full_name=row["full_name"],
        phone=row["phone"],
        requested_table=row["requested_table"],
        payload_json=pj,
        comment=row["comment"],
        status=RequestStatus(row["status"]),
        reviewed_by=row["reviewed_by"],
        created_at=row["created_at"],
        reviewed_at=row["reviewed_at"],
    )


# ---------------------------------------------------------------------------
# Admin table access
# ---------------------------------------------------------------------------

async def grant_table_access(username: str, table_name: str) -> None:
    await pool.execute(
        """
        INSERT INTO admin_table_access (username, table_name)
        VALUES ($1, $2) ON CONFLICT DO NOTHING
        """,
        username, table_name,
    )


async def get_admin_tables(username: str) -> list[str]:
    rows = await pool.fetch(
        "SELECT table_name FROM admin_table_access WHERE username = $1",
        username,
    )
    return [r["table_name"] for r in rows]


# ---------------------------------------------------------------------------
# Claude context (cached)
# ---------------------------------------------------------------------------

_context_cache: dict = {"data": None, "ts": 0.0}
_CACHE_TTL = 60  # seconds


async def get_claude_context() -> str:
    """Build a context string from DB data for Claude. Cached for 60s."""
    now = time.time()
    if _context_cache["data"] and (now - _context_cache["ts"]) < _CACHE_TTL:
        return _context_cache["data"]

    events = await get_active_events()
    infos = await get_all_info()

    parts = ["=== Active events ==="]
    for e in events:
        parts.append(
            f"- {e.title} | {e.date_start} | {e.time or ''} | {e.place or ''} "
            f"| {e.description or ''}"
        )
    parts.append("\n=== Information ===")
    for i in infos:
        parts.append(f"[{i['category']}] {i['title']}: {i['content']}")

    text = "\n".join(parts)
    _context_cache["data"] = text
    _context_cache["ts"] = now
    return text


# ---------------------------------------------------------------------------
# Super-admin helpers
# ---------------------------------------------------------------------------

async def get_super_admin_ids() -> list[int]:
    rows = await pool.fetch(
        "SELECT telegram_id FROM users WHERE role = 'super_admin'",
    )
    return [r["telegram_id"] for r in rows]


async def get_all_telegram_ids() -> list[int]:
    rows = await pool.fetch("SELECT telegram_id FROM users")
    return [r["telegram_id"] for r in rows]
