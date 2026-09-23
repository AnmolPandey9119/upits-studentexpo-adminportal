"""
Database connection layer for the admin backend — Neon Postgres.

This is a SEPARATE deployment from the student-facing backend, but it
must point at the SAME database (same DATABASE_URL) since it reads and
writes the same students / checkpoints / stamps / certificates tables.

Sized differently from the student backend's pool because this runs as
a Vercel serverless function: each cold-started instance opens its own
small pool, so min_size stays at 0 and max_size stays small. If you hit
Neon's connection-limit errors under load, switch DATABASE_URL to Neon's
*pooled* connection string (the one with "-pooler" in the hostname,
using PgBouncer) — or move this service to Render/Railway where one
process keeps one long-lived pool instead of many serverless instances
each opening their own.
"""

import os
import asyncpg
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        if not DATABASE_URL:
            raise RuntimeError(
                "DATABASE_URL not set. Use the SAME Neon connection string as "
                "your student backend (ideally the pooled '-pooler' one for "
                "serverless), with ?sslmode=require."
            )
        _pool = await asyncpg.create_pool(
            dsn=DATABASE_URL,
            min_size=0,
            max_size=5,
            command_timeout=30,
        )
    return _pool


async def close_pool():
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def run_schema(schema_path: str = "admin_schema.sql"):
    """One-time helper: applies admin_schema.sql (scan_logs, admin_settings,
    the extra checkpoint/stamp columns). Safe to re-run.

    NOTE: this ALTERs the students/checkpoints/stamps tables, so the
    student backend must have run its own schema.sql at least once
    already (that's what creates those tables in the first place).

    Deliberately non-fatal: if this raises (e.g. a base table like
    `certificates` doesn't exist yet in a fresh DB, or a future schema
    edit has a typo), we log it and let the app keep starting rather than
    crashing the whole admin-backend process. A schema problem should
    only break the specific screens that depend on the missing
    table/column — not turn every screen (Dashboard, Students,
    Checkpoints...) into "Failed to fetch" because the server never
    came up at all.
    """
    pool = await get_pool()
    with open(schema_path, "r") as f:
        sql = f.read()
    try:
        async with pool.acquire() as conn:
            await conn.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto";')
            await conn.execute(sql)
        print("Admin schema applied successfully.")
    except Exception as exc:
        print(f"WARNING: admin_schema.sql did not fully apply ({exc!r}). "
              f"Server is still starting — but screens relying on the "
              f"missing table/column will error until this is fixed and "
              f"the server is restarted.")