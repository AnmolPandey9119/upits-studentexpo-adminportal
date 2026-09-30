"""
UPITS 2026 — Student bulk import (Excel/CSV -> `students` table).

Flow (the browser parses the spreadsheet with SheetJS and sends rows in
small batches, so this works within serverless request-size/time limits):

    GET  /api/admin/student-import/fields    field list + header synonyms
    POST /api/admin/student-import/validate  dry run: nothing is written
    POST /api/admin/student-import/commit    validates again, then inserts

Rules enforced here (not trusted from the browser):
  * name, mobile and institution are required; mobile must be a valid
    10-digit Indian number (Excel artefacts like 9.87E+09 / +91 are fixed)
  * a mobile that already exists in `students` (any storage format) is
    SKIPPED, never overwritten — so re-running the same file is safe
  * Passport IDs are issued sequentially after the current highest one,
    under a Postgres advisory lock so two admins can't collide
  * mobiles are stored in the same format your student backend already
    uses (detected from an existing row), so imported students can log in
  * one audit-log entry per batch
The importer only writes to columns that exist in the live table, and
fills any NOT NULL column it has no data for (see _students_schema).
"""

import time
from typing import Any, Optional

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from database import get_pool
from utils import student_import as si
from utils.admin_audit import audit
from utils.admin_auth import require_admin_token

router = APIRouter(
    prefix="/api/admin/student-import",
    tags=["admin-student-import"],
    dependencies=[Depends(require_admin_token)],
)

# Arbitrary constant: serialises concurrent imports (pg_advisory_xact_lock).
IMPORT_LOCK_KEY = 2026093001

_TEXT_TYPES = {"character varying", "text", "character"}
_SCHEMA_TTL_SECONDS = 300
_schema_cache: dict[str, Any] = {"at": 0.0, "value": None}


# =========================================================================
# Request models
# =========================================================================
class ImportRow(BaseModel):
    row_number: int = Field(ge=1)
    data: dict[str, Any]


class ValidatePayload(BaseModel):
    rows: list[ImportRow] = Field(min_length=1, max_length=si.MAX_ROWS_PER_REQUEST)


class CommitPayload(ValidatePayload):
    staff_name: Optional[str] = Field(default=None, max_length=120)
    filename: Optional[str] = Field(default=None, max_length=200)
    batch_id: Optional[str] = Field(default=None, max_length=60)


# =========================================================================
# Live-table introspection
# =========================================================================
async def _students_schema(conn) -> dict:
    """Reads the real `students` columns so the importer adapts to your
    schema instead of assuming it: which target columns exist, their max
    lengths / nullability, and how to fill any NOT NULL column we have no
    spreadsheet data for (booleans -> FALSE, text -> ''; e.g. consent
    flags stay FALSE because no consent was captured in the app)."""
    now = time.time()
    if _schema_cache["value"] and now - _schema_cache["at"] < _SCHEMA_TTL_SECONDS:
        return _schema_cache["value"]

    rows = await conn.fetch(
        """
        SELECT column_name, data_type, udt_name, is_nullable, column_default,
               character_maximum_length, is_identity, is_generated
        FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = 'students'
        """
    )
    if not rows:
        raise HTTPException(500, "The `students` table was not found in this database.")

    cols = {r["column_name"]: dict(r) for r in rows}
    missing = [c for c in si.MUST_EXIST_COLUMNS if c not in cols]
    if missing:
        raise HTTPException(500, f"`students` is missing expected column(s): {', '.join(missing)}.")

    insert_cols = [c for c in si.INSERT_COLUMNS if c in cols]
    fills: dict[str, str] = {}
    unsupported: list[str] = []
    for name, c in cols.items():
        if name in insert_cols:
            continue
        needs_value = (
            c["is_nullable"] == "NO"
            and c["column_default"] is None
            and c["is_identity"] != "YES"
            and c["is_generated"] != "ALWAYS"
        )
        if not needs_value:
            continue
        if c["data_type"] == "boolean":
            fills[name] = "FALSE"
        elif c["data_type"] in _TEXT_TYPES:
            fills[name] = "''"
        else:
            unsupported.append(name)
    if unsupported:
        raise HTTPException(
            500,
            "`students` has required column(s) the importer can't fill: "
            + ", ".join(unsupported) + ". Add a default to them or extend the importer.",
        )

    value = {
        "cols": cols,
        "insert_cols": insert_cols,
        "fills": fills,
        "max_lengths": {
            k: cols[k]["character_maximum_length"]
            for k in si.FIELD_KEYS
            if k in cols and cols[k]["character_maximum_length"]
        },
    }
    _schema_cache.update(at=now, value=value)
    return value


async def _context(conn) -> dict:
    schema = await _students_schema(conn)
    sample = await conn.fetchval(
        """SELECT mobile_number FROM students
           WHERE mobile_number ~ '^[+0-9]{10,13}$'
           ORDER BY created_at DESC LIMIT 1"""
    )
    return {**schema, "mobile_style": si.detect_mobile_style(sample)}


# =========================================================================
# Classification (shared by validate + commit)
# =========================================================================
async def _existing_students(conn, keys: list[str]) -> dict[str, str]:
    """mobile last-10-digits -> passport_id, for mobiles already registered."""
    if not keys:
        return {}
    variants = [v for k in keys for v in si.mobile_variants(k)]
    recs = await conn.fetch(
        "SELECT mobile_number, passport_id FROM students WHERE mobile_number = ANY($1::text[])",
        variants,
    )
    return {si.mobile_key(r["mobile_number"]): r["passport_id"] for r in recs}


async def _classify(conn, rows: list[ImportRow], ctx: dict) -> list[dict]:
    items: list[dict] = []
    first_seen: dict[str, int] = {}

    for r in rows:
        res = si.validate_row(r.data, ctx["max_lengths"])
        item = {
            "row_number": r.row_number,
            "clean": res["clean"],
            "mobile_key": res["mobile_key"],
            "errors": res["errors"],
            "warnings": res["warnings"],
            "status": "ready",
            "message": "",
            "passport_id": None,
        }
        if res["errors"]:
            item["status"] = "invalid"
            item["message"] = " ".join(res["errors"])
        elif res["mobile_key"] in first_seen:
            item["status"] = "duplicate"
            item["message"] = f"Same mobile as row {first_seen[res['mobile_key']]} of this file."
        else:
            first_seen[res["mobile_key"]] = r.row_number
        items.append(item)

    existing = await _existing_students(conn, [i["mobile_key"] for i in items if i["status"] == "ready"])
    for item in items:
        if item["status"] == "ready" and item["mobile_key"] in existing:
            item["status"] = "duplicate"
            item["message"] = f"Already registered ({existing[item['mobile_key']]}). Skipped."
    return items


def _public(item: dict) -> dict:
    message = item["message"]
    if not message and item["warnings"]:
        message = " ".join(item["warnings"])
    return {
        "row_number": item["row_number"],
        "status": item["status"],
        "mobile_key": item["mobile_key"],
        "passport_id": item["passport_id"],
        "message": message,
        "warnings": item["warnings"],
    }


def _counts(items: list[dict]) -> dict:
    out: dict[str, int] = {}
    for i in items:
        out[i["status"]] = out.get(i["status"], 0) + 1
    return out


# =========================================================================
# Inserting
# =========================================================================
async def _next_passport_number(conn) -> int:
    prefix = si.PASSPORT_ID_PREFIX
    return int(
        await conn.fetchval(
            """
            SELECT COALESCE(MAX(substring(passport_id FROM $2::int)::bigint), 0)
            FROM students
            WHERE left(passport_id, $3::int) = $1
              AND substring(passport_id FROM $2::int) ~ '^[0-9]{1,15}$'
            """,
            prefix, len(prefix) + 1, len(prefix),
        )
    )


def _record_values(item: dict, ctx: dict) -> dict:
    c = item["clean"]
    cols = ctx["cols"]
    values = {
        "passport_id": item["passport_id"],
        "full_name": c["full_name"],
        "mobile_number": si.to_storage_mobile(c["mobile_number"], ctx["mobile_style"]),
        "email": c["email"],
        "student_category": c["student_category"],
        "class_or_course": c["class_or_course"],
        "age_group": c["age_group"],
        "institution_name": c["institution_name"],
        "district_city": c["district_city"],
        "teacher_name": c["teacher_name"],
        "teacher_mobile": (
            si.to_storage_mobile(c["teacher_mobile"], ctx["mobile_style"]) if c["teacher_mobile"] else None
        ),
        "route_colour": c["route_colour"],
        "registration_status": "active",
    }
    # A NOT NULL text column can't take NULL — use '' for blank optionals.
    for name, v in values.items():
        if v is None and name in cols and cols[name]["is_nullable"] == "NO":
            values[name] = ""
    return values


async def _bulk_insert(conn, items: list[dict], ctx: dict) -> set[str]:
    """One round-trip INSERT ... SELECT FROM unnest(...). Returns the set of
    passport_ids actually inserted (ON CONFLICT DO NOTHING skips races)."""
    cols = ctx["insert_cols"]
    fills = ctx["fills"]
    arrays: list[list] = [[] for _ in cols]
    for item in items:
        vals = _record_values(item, ctx)
        for i, name in enumerate(cols):
            arrays[i].append(vals[name])

    select_exprs = []
    for i, name in enumerate(cols):
        meta = ctx["cols"][name]
        cast = f'::"{meta["udt_name"]}"' if meta["data_type"] == "USER-DEFINED" else ""
        select_exprs.append(f"u.c{i}{cast}")
    select_exprs += list(fills.values())

    col_list = ", ".join(f'"{c}"' for c in cols + list(fills))
    aliases = ", ".join(f"c{i}" for i in range(len(cols)))
    unnest_args = ", ".join(f"${i + 1}::text[]" for i in range(len(cols)))
    sql = (
        f"INSERT INTO students ({col_list}) "
        f"SELECT {', '.join(select_exprs)} FROM unnest({unnest_args}) AS u({aliases}) "
        f"ON CONFLICT DO NOTHING RETURNING passport_id"
    )
    recs = await conn.fetch(sql, *arrays)
    return {r["passport_id"] for r in recs}


def _friendly_db_error(exc: Exception) -> str:
    if isinstance(exc, asyncpg.UniqueViolationError):
        return "Already exists (duplicate value)."
    if isinstance(exc, asyncpg.CheckViolationError):
        return f"A value isn't allowed by the database rules ({getattr(exc, 'constraint_name', None) or 'check'})."
    if isinstance(exc, asyncpg.StringDataRightTruncationError):
        return "A value is too long for its column."
    if isinstance(exc, asyncpg.NotNullViolationError):
        return f"A required value is missing ({getattr(exc, 'column_name', None) or 'column'})."
    return "The database rejected this row."


async def _insert_items(conn, items: list[dict], ctx: dict) -> tuple[set[str], dict[str, str]]:
    """Fast path: whole batch in one statement (in a savepoint). If the
    database rejects anything, retry row-by-row so one bad row can't take
    the rest down, and report exactly which rows failed and why."""
    try:
        async with conn.transaction():
            return await _bulk_insert(conn, items, ctx), {}
    except asyncpg.PostgresError as exc:
        print(f"student-import: bulk insert failed ({exc!r}); retrying row by row")

    inserted: set[str] = set()
    failed: dict[str, str] = {}
    for item in items:
        try:
            async with conn.transaction():
                inserted |= await _bulk_insert(conn, [item], ctx)
        except asyncpg.PostgresError as exc:
            print(f"student-import: row {item['row_number']} rejected ({exc!r})")
            failed[item["passport_id"]] = _friendly_db_error(exc)
    return inserted, failed


# =========================================================================
# Endpoints
# =========================================================================
@router.get("/fields")
async def import_fields():
    return {"fields": si.FIELDS, "max_rows_per_request": si.MAX_ROWS_PER_REQUEST}


@router.post("/validate")
async def validate_import(payload: ValidatePayload):
    """Dry run — reports what WOULD happen to each row; writes nothing."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        ctx = await _context(conn)
        items = await _classify(conn, payload.rows, ctx)
    return {"items": [_public(i) for i in items], "counts": _counts(items)}


@router.post("/commit")
async def commit_import(payload: CommitPayload):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # One import at a time: makes the Passport ID sequence safe.
            await conn.execute("SELECT pg_advisory_xact_lock($1)", IMPORT_LOCK_KEY)
            ctx = await _context(conn)
            items = await _classify(conn, payload.rows, ctx)

            ready = [i for i in items if i["status"] == "ready"]
            if ready:
                last = await _next_passport_number(conn)
                for offset, item in enumerate(ready, start=1):
                    item["passport_id"] = si.format_passport_id(last + offset)
                inserted, failed = await _insert_items(conn, ready, ctx)
                for item in ready:
                    pid = item["passport_id"]
                    if pid in inserted:
                        item["status"] = "imported"
                    elif pid in failed:
                        item["status"] = "failed"
                        item["message"] = failed[pid]
                        item["passport_id"] = None
                    else:
                        item["status"] = "failed"
                        item["message"] = "Not saved — this mobile or Passport ID was added at the same moment. Re-run the import to retry."
                        item["passport_id"] = None

    counts = _counts(items)
    try:
        await audit(
            "student_import", "students", payload.batch_id,
            {"filename": payload.filename, "rows": len(items), **counts,
             "first_passport": next((i["passport_id"] for i in items if i["status"] == "imported"), None)},
            payload.staff_name,
        )
    except Exception as exc:  # never undo a finished import because logging failed
        print(f"student-import: audit log write failed ({exc!r})")

    return {"batch_id": payload.batch_id, "counts": counts, "items": [_public(i) for i in items]}