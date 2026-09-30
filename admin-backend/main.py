"""
UPITS 2026 — Admin Backend (standalone, login-protected).

This is a SEPARATE service from the student-facing backend. It shares
the same Postgres database (same DATABASE_URL) but ships its own
process/deployment, on purpose — so the admin API and the student API
can be deployed, scaled and redeployed independently.

Every /api/admin/* route requires a session token from
POST /api/admin/auth/login (single shared login, see
utils/admin_auth.py) except that login route itself and /api/health.

Run locally:
    uvicorn main:app --reload --port 8001

Deployed on Vercel via api/index.py (see that file + vercel.json).
"""

import traceback
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from database import get_pool, close_pool, run_schema
from routers import admin

# The Excel student-import feature is loaded defensively: if it ever fails
# to import, the rest of the admin API (dashboard, students, ...) must
# still start. The reason is printed in the Vercel function logs.
student_import_error = None
try:
    from routers import student_import
except Exception as exc:  # pragma: no cover
    student_import = None
    student_import_error = f"{type(exc).__name__}: {exc}"[:200]
    print(f"WARNING: student import router not loaded ({exc!r}).")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Applies admin_schema.sql (scan_logs, admin_settings, extra columns).
    # Assumes the student backend has already created the base tables
    # (students, checkpoints, stamps, certificates, feedback_responses).
    await run_schema("admin_schema.sql")
    yield
    await close_pool()


app = FastAPI(
    title="UPITS 2026 Admin API",
    version="2.0.0",
    lifespan=lifespan,
)

# An unhandled error normally produces a bare 500 that is sent WITHOUT CORS
# headers, so the browser reports it as a confusing "blocked by CORS
# policy" instead of the real problem. This catches such errors, logs the
# traceback, and answers with a JSON 500 that goes through CORS below.
# (It must be registered BEFORE CORSMiddleware so CORS is the outer layer.)
@app.middleware("http")
async def catch_unhandled_errors(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception as exc:
        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={"detail": f"Server error ({type(exc).__name__}). Check the admin-backend function logs."},
        )


# Open CORS since the admin frontend is a separate static deployment on
# its own domain. Tighten to the exact frontend domain once you know it.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(admin.auth_router)
app.include_router(admin.router)
if student_import is not None:
    app.include_router(student_import.router)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    messages = []
    for err in exc.errors():
        field = err["loc"][-1] if err["loc"] else "field"
        messages.append(f"{field}: {err['msg']}")
    return JSONResponse(
        status_code=422,
        content={"detail": " | ".join(messages) or "Invalid input."},
    )


@app.get("/api/health")
async def health_check():
    # `student_import` tells you at a glance whether the Excel-import
    # feature is deployed and loaded (false + a reason if it is not).
    return {
        "status": "ok",
        "service": "upits-admin-backend",
        "student_import": student_import is not None,
        **({"student_import_error": student_import_error} if student_import_error else {}),
    }