"""
Completion QR token — Stage D (BRD section 4 Stage D, section 8 QR controls).

Same signed-token idea as qr_token.py / scan_token.py, but for the
*finished-passport* QR the student shows at the Redemption Desk:

    <serial_number>.<student_id>.<hmac_signature>

Unlike the checkpoint QR (rotates every 5-10 min) this one is NOT
time-limited — a student might finish on Day 1 and redeem on Day 3, so
it stays valid for as long as the certificate row exists. The actual
"can only be used once" rule is enforced in the database
(certificates.redeemed), not by the token expiring.

Signed with one app-wide secret (CERT_TOKEN_SECRET in .env) — same
pattern as SCAN_TOKEN_SECRET in scan_token.py.
"""

import hashlib
import hmac
import os
from dataclasses import dataclass

CERT_TOKEN_SECRET = os.getenv("CERT_TOKEN_SECRET", "dev-only-change-me")


def _sign(serial_number: str, student_id: str) -> str:
    msg = f"{serial_number}.{student_id}".encode()
    return hmac.new(CERT_TOKEN_SECRET.encode(), msg, hashlib.sha256).hexdigest()[:32]


def generate(serial_number: str, student_id: str) -> str:
    """Returns the token to encode in the completion QR / show as a code."""
    sig = _sign(serial_number, student_id)
    return f"{serial_number}.{student_id}.{sig}"


@dataclass
class CertTokenResult:
    valid: bool
    reason: str = ""
    serial_number: str = ""
    student_id: str = ""


def verify(token: str) -> CertTokenResult:
    try:
        serial_number, student_id, sig = token.strip().split(".")
    except (ValueError, AttributeError):
        return CertTokenResult(valid=False, reason="Malformed completion code.")

    expected = _sign(serial_number, student_id)
    if not hmac.compare_digest(sig, expected):
        return CertTokenResult(valid=False, reason="Invalid completion code.")

    return CertTokenResult(valid=True, serial_number=serial_number, student_id=student_id)