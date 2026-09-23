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
    already (that's what creates those tables in the first place)."""
    pool = await get_pool()
    with open(schema_path, "r") as f:
        sql = f.read()
    async with pool.acquire() as conn:
        await conn.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto";')
        await conn.execute(sql)
    print("Admin schema applied successfully.")
