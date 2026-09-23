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

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from database import get_pool, close_pool, run_schema
from routers import admin


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
    return {"status": "ok", "service": "upits-admin-backend"}