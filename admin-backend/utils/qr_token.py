"""
Signed, rotating QR token for checkpoints — BRD section 8:
"Use dynamic, signed QR tokens, ideally rotating every 5-10 minutes.
QR payload should contain checkpoint ID, expiry time and signature —
not merely a public URL."

Token shape (base64url, '.' separated so it's easy to eyeball in logs):
    <checkpoint_id>.<expiry_unix>.<hmac_signature>

Signed with the per-checkpoint secret stored in checkpoints.qr_token_secret
(see schema.sql) — so rotating/regenerating ONE checkpoint's secret from
the admin panel later invalidates only that checkpoint's tokens, not every
QR stand at once.

This module never touches the DB itself — callers pass in the checkpoint's
secret (already fetched) and get back a token string, or pass in a token
string + the secret and get back a verified/expired/invalid result.
"""

import base64
import hashlib
import hmac
import time
from dataclasses import dataclass


ROTATE_SECONDS = 300  # 5 minutes — BRD says "5-10 minutes", pick the safer end


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _sign(checkpoint_id: str, expiry: int, secret: str) -> str:
    msg = f"{checkpoint_id}.{expiry}".encode()
    sig = hmac.new(secret.encode(), msg, hashlib.sha256).digest()
    return _b64url_encode(sig)


def generate_token(checkpoint_id: str, secret: str, rotate_seconds: int = ROTATE_SECONDS) -> dict:
    """Returns {token, expires_at} for display at the checkpoint QR stand.
    expires_at is a unix timestamp so the kiosk screen can show/refresh
    the QR shortly before it goes stale."""
    expiry = int(time.time()) + rotate_seconds
    sig = _sign(checkpoint_id, expiry, secret)
    token = f"{checkpoint_id}.{expiry}.{sig}"
    return {"token": token, "expires_at": expiry}


@dataclass
class TokenVerifyResult:
    valid: bool
    reason: str = ""
    checkpoint_id: str = ""


def verify_token(token: str, secret: str, expected_checkpoint_id: str) -> TokenVerifyResult:
    """Verifies signature + expiry + that the token belongs to the
    checkpoint the student says they scanned (defence against pasting a
    token copied from a different checkpoint's QR)."""
    try:
        checkpoint_id, expiry_str, sig = token.split(".")
        expiry = int(expiry_str)
    except (ValueError, AttributeError):
        return TokenVerifyResult(valid=False, reason="Malformed QR token.")

    if checkpoint_id != expected_checkpoint_id:
        return TokenVerifyResult(valid=False, reason="This QR does not belong to this checkpoint.")

    expected_sig = _sign(checkpoint_id, expiry, secret)
    if not hmac.compare_digest(sig, expected_sig):
        return TokenVerifyResult(valid=False, reason="Invalid QR signature.")

    if int(time.time()) > expiry:
        return TokenVerifyResult(valid=False, reason="This QR code has expired. Please scan the current code.")

    return TokenVerifyResult(valid=True, checkpoint_id=checkpoint_id)