"""
Admin login — one shared admin/staff login, added on top of the old
"no-auth build" (see the removed note at the top of routers/admin.py).

There is no admin_users table — just a single username/password pair
read from environment variables (ADMIN_USERNAME, ADMIN_PASSWORD). If
you ever need multiple named logins (e.g. one per staff member), swap
check_credentials() for a real lookup against a users table; nothing
else in this file or in admin.py needs to change for that.

On successful login, issues a signed, time-limited session token:

    <expiry_unix>.<hmac_signature>

The token is opaque on purpose (no username/role encoded in it, since
there's only one login) — the frontend just stores it and sends it back
as `Authorization: Bearer <token>` on every /api/admin/* call. The
whole admin router (except /api/admin/auth/login itself) is protected
with a single `Depends(require_admin_token)` in routers/admin.py.
"""

import hashlib
import hmac
import os
import time

from fastapi import Header, HTTPException

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
ADMIN_SESSION_SECRET = os.getenv("ADMIN_SESSION_SECRET", "dev-only-change-me")
SESSION_SECONDS = 12 * 60 * 60  # 12 hours — re-login once per shift


def _sign(expiry: int) -> str:
    return hmac.new(ADMIN_SESSION_SECRET.encode(), str(expiry).encode(), hashlib.sha256).hexdigest()


def check_credentials(username: str, password: str) -> bool:
    if not ADMIN_USERNAME or not ADMIN_PASSWORD:
        # Misconfigured deployment (env vars not set) — fail closed,
        # never fail open into "no password required".
        raise HTTPException(500, "Admin login is not configured on the server (ADMIN_USERNAME/ADMIN_PASSWORD missing).")
    # compare_digest on both sides so neither comparison leaks timing info.
    user_ok = hmac.compare_digest((username or "").strip(), ADMIN_USERNAME)
    pass_ok = hmac.compare_digest(password or "", ADMIN_PASSWORD)
    return user_ok and pass_ok


def issue_token() -> dict:
    expiry = int(time.time()) + SESSION_SECONDS
    return {"token": f"{expiry}.{_sign(expiry)}", "expires_at": expiry}


def require_admin_token(authorization: str = Header(default="")):
    """FastAPI dependency — attach to any route/router that needs login."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing admin session. Please log in.")
    token = authorization[len("Bearer "):].strip()
    try:
        expiry_str, sig = token.split(".")
        expiry = int(expiry_str)
    except (ValueError, AttributeError):
        raise HTTPException(401, "Malformed session token. Please log in again.")

    if not hmac.compare_digest(sig, _sign(expiry)):
        raise HTTPException(401, "Invalid session token. Please log in again.")
    if int(time.time()) > expiry:
        raise HTTPException(401, "Session expired. Please log in again.")