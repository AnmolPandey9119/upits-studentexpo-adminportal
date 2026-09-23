"""
Writes one row to scan_logs for every /api/checkpoints/validate attempt,
success or failure. This is what powers the admin portal's "QR & scan
monitoring" and "Fraud / anti-cheating review" screens — without it the
admin side only ever sees attempts that turned into a stamp, never the
failed/blocked ones.

Never raises: a logging failure should never break the student-facing
scan flow, so every call is wrapped in try/except.
"""

from database import get_pool


async def log_scan(
    *,
    result: str,                      # 'success' | 'failed'
    reason: str | None = None,
    student_id: str | None = None,
    passport_id: str | None = None,
    checkpoint_id: str | None = None,
    hall_zone: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    accuracy_m: float | None = None,
    is_mock_location: bool = False,
    fraud_flag: str | None = None,
):
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO scan_logs (
                    student_id, passport_id, checkpoint_id, hall_zone,
                    result, reason, latitude, longitude, accuracy_m,
                    is_mock_location, fraud_flag
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                """,
                student_id, passport_id, checkpoint_id, hall_zone,
                result, reason, latitude, longitude, accuracy_m,
                is_mock_location, fraud_flag,
            )
    except Exception:
        pass
