"""
Stage C — QR scan + geofence validation + answer submit (BRD section 4,
Stage C, and section 8 "Location and Anti-Fraud Rules").

Two student-facing calls, kept deliberately separate so the task question
is revealed only after validation passes (BRD: "The linked question opens
only after validation."):

    POST /api/checkpoints/validate  -> checks QR token, geofence, accuracy,
                                        rate-limit, duplicate-claim, travel
                                        pattern. Returns the question + a
                                        short-lived scan_token if OK.
    POST /api/checkpoints/submit    -> re-verifies the scan_token, checks
                                        the answer, writes the stamp.

Plus one staff/kiosk call:

    GET /api/checkpoints/{id}/display-token -> current rotating QR payload
                                        for the screen standing at that
                                        hall/zone. Protected by a shared
                                        staff key.

v2 (admin-portal build): every /validate attempt — success or failure —
is now written to scan_logs via utils/scan_log.py so the no-auth admin
portal has real data for "QR & scan monitoring" and "Fraud / anti-cheating
review" instead of only ever seeing attempts that became a stamp.
"""

import os
import time
from datetime import timezone

from fastapi import APIRouter, HTTPException, Header
import asyncpg
from pydantic import BaseModel, Field

from database import get_pool
from models import (
    CheckpointValidateRequest,
    CheckpointValidateResponse,
    CheckpointSubmitRequest,
    CheckpointSubmitResponse,
)
from utils import qr_token, scan_token as scan_token_util
from utils.geofence import is_within_geofence, accuracy_acceptable, is_impossible_travel, distance_metres
from utils.scan_log import log_scan

router = APIRouter(prefix="/api/checkpoints", tags=["checkpoints"])

REQUIRED_STAMPS = 10
PRIORITY_DRAW_MIN_STAMPS = 12
RATE_LIMIT_SECONDS = 60  # BRD: "no more than one successful stamp in 60 seconds"

STAFF_DISPLAY_KEY = os.getenv("STAFF_DISPLAY_KEY", "")


class CheckpointCodeValidateRequest(BaseModel):
    """Camera-can't-scan fallback (BRD section 12): the student types the
    6-character code printed under the checkpoint's QR instead of
    scanning it. Same fields as the QR path, minus qr_token, plus
    manual_code."""
    passport_id: str
    manual_code: str = Field(min_length=6, max_length=6)
    latitude: float
    longitude: float
    accuracy_m: float
    is_mock_location: bool = False


# ---------------------------------------------------------------
# Kiosk: rotating QR token for the checkpoint stand's display screen
# ---------------------------------------------------------------
@router.get("/{checkpoint_id}/display-token")
async def get_display_token(checkpoint_id: str, x_staff_key: str = Header(default="")):
    if not STAFF_DISPLAY_KEY or x_staff_key != STAFF_DISPLAY_KEY:
        raise HTTPException(status_code=401, detail="Missing or invalid staff key.")

    pool = await get_pool()
    async with pool.acquire() as conn:
        checkpoint = await conn.fetchrow(
            "SELECT id, qr_token_secret, is_active, manual_code FROM checkpoints WHERE id = $1",
            checkpoint_id,
        )
    if not checkpoint or not checkpoint["is_active"]:
        raise HTTPException(status_code=404, detail="Checkpoint not found or inactive.")
    if not checkpoint["qr_token_secret"]:
        raise HTTPException(status_code=500, detail="Checkpoint has no QR signing secret configured.")

    # Always 0 = never expires, hard-coded rather than read from the
    # checkpoint's qr_expires_seconds column so this can't regress even
    # if that column still holds an old value from before this decision.
    data = qr_token.generate_token(str(checkpoint["id"]), checkpoint["qr_token_secret"], 0)
    data["manual_code"] = checkpoint["manual_code"]
    return data


# ---------------------------------------------------------------
# Student: validate a scan (QR + location), reveal the question
# ---------------------------------------------------------------
@router.post("/validate", response_model=CheckpointValidateResponse)
async def validate_scan(payload: CheckpointValidateRequest):
    pool = await get_pool()
    async with pool.acquire() as conn:
        student = await conn.fetchrow(
            "SELECT id, registration_status FROM students WHERE passport_id = $1",
            payload.passport_id,
        )
        if not student:
            await log_scan(result="failed", reason="passport_not_found", passport_id=payload.passport_id,
                            latitude=payload.latitude, longitude=payload.longitude, accuracy_m=payload.accuracy_m,
                            is_mock_location=payload.is_mock_location)
            raise HTTPException(status_code=404, detail="Passport ID not found.")
        if student["registration_status"] == "blocked":
            await log_scan(result="failed", reason="passport_blocked", student_id=str(student["id"]),
                            passport_id=payload.passport_id, latitude=payload.latitude, longitude=payload.longitude,
                            accuracy_m=payload.accuracy_m, is_mock_location=payload.is_mock_location)
            raise HTTPException(status_code=403, detail="This passport is currently blocked. Please visit the help desk.")

        # The QR token itself carries the checkpoint_id (see qr_token.py),
        # so we don't trust a client-supplied checkpoint_id at all here.
        try:
            raw_checkpoint_id = payload.qr_token.split(".")[0]
        except (IndexError, AttributeError):
            await log_scan(result="failed", reason="malformed_qr_token", student_id=str(student["id"]),
                            passport_id=payload.passport_id, latitude=payload.latitude, longitude=payload.longitude,
                            accuracy_m=payload.accuracy_m, is_mock_location=payload.is_mock_location)
            raise HTTPException(status_code=400, detail="Malformed QR token.")

        checkpoint = await conn.fetchrow(
            """
            SELECT id, stamp_number, is_bonus, hall_zone, theme, question_text,
                   answer_type, answer_options, min_answer_length,
                   latitude, longitude, geofence_radius_m, max_accuracy_m, qr_token_secret, is_active
            FROM checkpoints WHERE id = $1
            """,
            raw_checkpoint_id,
        )
        if not checkpoint or not checkpoint["is_active"]:
            await log_scan(result="failed", reason="checkpoint_inactive_or_missing", student_id=str(student["id"]),
                            passport_id=payload.passport_id, checkpoint_id=raw_checkpoint_id,
                            latitude=payload.latitude, longitude=payload.longitude, accuracy_m=payload.accuracy_m,
                            is_mock_location=payload.is_mock_location)
            raise HTTPException(status_code=404, detail="This checkpoint is not active.")

        common = dict(
            student_id=str(student["id"]), passport_id=payload.passport_id,
            checkpoint_id=str(checkpoint["id"]), hall_zone=checkpoint["hall_zone"],
            latitude=payload.latitude, longitude=payload.longitude, accuracy_m=payload.accuracy_m,
            is_mock_location=payload.is_mock_location,
        )

        # 1. QR signature + expiry
        result = qr_token.verify_token(payload.qr_token, checkpoint["qr_token_secret"], str(checkpoint["id"]))
        if not result.valid:
            await log_scan(result="failed", reason=f"qr_token:{result.reason}", **common)
            raise HTTPException(status_code=400, detail=result.reason)

        # 2. Already claimed?
        already = await conn.fetchval(
            "SELECT 1 FROM stamps WHERE student_id = $1 AND checkpoint_id = $2",
            student["id"], checkpoint["id"],
        )
        if already:
            await log_scan(result="failed", reason="already_claimed", **common)
            raise HTTPException(status_code=409, detail="You've already collected the stamp for this checkpoint.")

        # 3. Location accuracy
        max_accuracy = checkpoint["max_accuracy_m"] or 50
        if payload.accuracy_m > max_accuracy:
            await log_scan(result="failed", reason="accuracy_too_low", **common)
            raise HTTPException(
                status_code=400,
                detail="Your location isn't precise enough here. Please move closer to an entrance/open area with a clear GPS signal and try again.",
            )

        # 4. Geofence
        if checkpoint["latitude"] is None or checkpoint["longitude"] is None:
            await log_scan(result="failed", reason="checkpoint_location_not_configured", **common)
            raise HTTPException(status_code=500, detail="This checkpoint's location isn't configured yet. Please tell a staff member.")
        inside, distance_m = is_within_geofence(
            payload.latitude, payload.longitude,
            checkpoint["latitude"], checkpoint["longitude"],
            checkpoint["geofence_radius_m"],
        )
        if not inside:
            await log_scan(result="failed", reason=f"outside_geofence:{round(distance_m)}m", **common)
            raise HTTPException(
                status_code=400,
                detail="We could not verify that you are at this checkpoint. Please move closer to the displayed QR stand, enable precise location, and try again.",
            )

        # 5. Rate limit — most recent APPROVED stamp by this student
        last_stamp = await conn.fetchrow(
            """
            SELECT s.created_at, c.latitude, c.longitude
            FROM stamps s JOIN checkpoints c ON c.id = s.checkpoint_id
            WHERE s.student_id = $1 AND s.status = 'approved'
            ORDER BY s.created_at DESC LIMIT 1
            """,
            student["id"],
        )

        fraud_flag = None
        if last_stamp:
            seconds_since = (
                time.time() - last_stamp["created_at"].replace(tzinfo=timezone.utc).timestamp()
            )
            if seconds_since < RATE_LIMIT_SECONDS:
                await log_scan(result="failed", reason="rate_limited", **common)
                raise HTTPException(
                    status_code=429,
                    detail=f"Please wait a moment before scanning your next checkpoint ({RATE_LIMIT_SECONDS}s between stamps).",
                )
            # 6. Impossible-travel check — flagged for staff review, not blocked
            travel_distance_m = distance_metres(
                last_stamp["latitude"], last_stamp["longitude"],
                checkpoint["latitude"], checkpoint["longitude"],
            )
            if is_impossible_travel(travel_distance_m, seconds_since):
                fraud_flag = "impossible_travel"

        if payload.is_mock_location:
            fraud_flag = fraud_flag or "mock_location"

        token = scan_token_util.issue(
            str(student["id"]), str(checkpoint["id"]), fraud_flag,
            payload.latitude, payload.longitude, payload.accuracy_m,
        )

        await log_scan(result="success", reason=fraud_flag, fraud_flag=fraud_flag, **common)

        return CheckpointValidateResponse(
            scan_token=token,
            checkpoint_id=checkpoint["id"],
            stamp_number=checkpoint["stamp_number"],
            hall_zone=checkpoint["hall_zone"],
            theme=checkpoint["theme"],
            question_text=checkpoint["question_text"],
            answer_type=checkpoint["answer_type"],
            answer_options=checkpoint["answer_options"],
            min_answer_length=checkpoint["min_answer_length"] or 0,
        )


# ---------------------------------------------------------------
# Student: validate via the printed fallback code (camera not working)
# ---------------------------------------------------------------
@router.post("/validate-by-code", response_model=CheckpointValidateResponse)
async def validate_scan_by_code(payload: CheckpointCodeValidateRequest):
    pool = await get_pool()
    async with pool.acquire() as conn:
        student = await conn.fetchrow(
            "SELECT id, registration_status FROM students WHERE passport_id = $1",
            payload.passport_id,
        )
        if not student:
            await log_scan(result="failed", reason="passport_not_found", passport_id=payload.passport_id,
                            latitude=payload.latitude, longitude=payload.longitude, accuracy_m=payload.accuracy_m,
                            is_mock_location=payload.is_mock_location)
            raise HTTPException(status_code=404, detail="Passport ID not found.")
        if student["registration_status"] == "blocked":
            await log_scan(result="failed", reason="passport_blocked", student_id=str(student["id"]),
                            passport_id=payload.passport_id, latitude=payload.latitude, longitude=payload.longitude,
                            accuracy_m=payload.accuracy_m, is_mock_location=payload.is_mock_location)
            raise HTTPException(status_code=403, detail="This passport is currently blocked. Please visit the help desk.")

        code = payload.manual_code.strip().upper()
        checkpoint = await conn.fetchrow(
            """
            SELECT id, stamp_number, is_bonus, hall_zone, theme, question_text,
                   answer_type, answer_options, min_answer_length,
                   latitude, longitude, geofence_radius_m, max_accuracy_m, is_active
            FROM checkpoints WHERE manual_code = $1
            """,
            code,
        )
        if not checkpoint or not checkpoint["is_active"]:
            await log_scan(result="failed", reason="checkpoint_code_invalid_or_inactive", student_id=str(student["id"]),
                            passport_id=payload.passport_id, latitude=payload.latitude, longitude=payload.longitude,
                            accuracy_m=payload.accuracy_m, is_mock_location=payload.is_mock_location)
            raise HTTPException(status_code=404, detail="That code doesn't match any active checkpoint. Please check with a staff member.")

        common = dict(
            student_id=str(student["id"]), passport_id=payload.passport_id,
            checkpoint_id=str(checkpoint["id"]), hall_zone=checkpoint["hall_zone"],
            latitude=payload.latitude, longitude=payload.longitude, accuracy_m=payload.accuracy_m,
            is_mock_location=payload.is_mock_location,
        )

        # No QR signature to check here — the printed code itself is the
        # proof of which checkpoint this is. Every other anti-fraud check
        # below (duplicate claim, accuracy, geofence, rate limit,
        # impossible travel, mock location) still applies exactly as it
        # does on the QR path, so typing the code is not a way to skip
        # the live-location requirement.

        # 1. Already claimed?
        already = await conn.fetchval(
            "SELECT 1 FROM stamps WHERE student_id = $1 AND checkpoint_id = $2",
            student["id"], checkpoint["id"],
        )
        if already:
            await log_scan(result="failed", reason="already_claimed", **common)
            raise HTTPException(status_code=409, detail="You've already collected the stamp for this checkpoint.")

        # 2. Location accuracy
        max_accuracy = checkpoint["max_accuracy_m"] or 50
        if payload.accuracy_m > max_accuracy:
            await log_scan(result="failed", reason="accuracy_too_low", **common)
            raise HTTPException(
                status_code=400,
                detail="Your location isn't precise enough here. Please move closer to an entrance/open area with a clear GPS signal and try again.",
            )

        # 3. Geofence
        if checkpoint["latitude"] is None or checkpoint["longitude"] is None:
            await log_scan(result="failed", reason="checkpoint_location_not_configured", **common)
            raise HTTPException(status_code=500, detail="This checkpoint's location isn't configured yet. Please tell a staff member.")
        inside, distance_m = is_within_geofence(
            payload.latitude, payload.longitude,
            checkpoint["latitude"], checkpoint["longitude"],
            checkpoint["geofence_radius_m"],
        )
        if not inside:
            await log_scan(result="failed", reason=f"outside_geofence:{round(distance_m)}m", **common)
            raise HTTPException(
                status_code=400,
                detail="We could not verify that you are at this checkpoint. Please move closer to the displayed QR stand, enable precise location, and try again.",
            )

        # 4. Rate limit — most recent APPROVED stamp by this student
        last_stamp = await conn.fetchrow(
            """
            SELECT s.created_at, c.latitude, c.longitude
            FROM stamps s JOIN checkpoints c ON c.id = s.checkpoint_id
            WHERE s.student_id = $1 AND s.status = 'approved'
            ORDER BY s.created_at DESC LIMIT 1
            """,
            student["id"],
        )

        fraud_flag = None
        if last_stamp:
            seconds_since = (
                time.time() - last_stamp["created_at"].replace(tzinfo=timezone.utc).timestamp()
            )
            if seconds_since < RATE_LIMIT_SECONDS:
                await log_scan(result="failed", reason="rate_limited", **common)
                raise HTTPException(
                    status_code=429,
                    detail=f"Please wait a moment before scanning your next checkpoint ({RATE_LIMIT_SECONDS}s between stamps).",
                )
            # 5. Impossible-travel check — flagged for staff review, not blocked
            travel_distance_m = distance_metres(
                last_stamp["latitude"], last_stamp["longitude"],
                checkpoint["latitude"], checkpoint["longitude"],
            )
            if is_impossible_travel(travel_distance_m, seconds_since):
                fraud_flag = "impossible_travel"

        if payload.is_mock_location:
            fraud_flag = fraud_flag or "mock_location"

        token = scan_token_util.issue(
            str(student["id"]), str(checkpoint["id"]), fraud_flag,
            payload.latitude, payload.longitude, payload.accuracy_m,
        )

        # Tag the log so admin can see how often the fallback was needed
        # versus normal QR scanning.
        await log_scan(result="success", reason=fraud_flag or "manual_code_entry", fraud_flag=fraud_flag, **common)

        return CheckpointValidateResponse(
            scan_token=token,
            checkpoint_id=checkpoint["id"],
            stamp_number=checkpoint["stamp_number"],
            hall_zone=checkpoint["hall_zone"],
            theme=checkpoint["theme"],
            question_text=checkpoint["question_text"],
            answer_type=checkpoint["answer_type"],
            answer_options=checkpoint["answer_options"],
            min_answer_length=checkpoint["min_answer_length"] or 0,
        )


# ---------------------------------------------------------------
# Student: submit the answer, award the stamp
# ---------------------------------------------------------------
@router.post("/submit", response_model=CheckpointSubmitResponse)
async def submit_answer(payload: CheckpointSubmitRequest):
    result = scan_token_util.verify(payload.scan_token)
    if not result.valid:
        raise HTTPException(status_code=400, detail=result.reason)

    pool = await get_pool()
    async with pool.acquire() as conn:
        checkpoint = await conn.fetchrow(
            "SELECT stamp_number, is_bonus, min_answer_length, is_active FROM checkpoints WHERE id = $1",
            result.checkpoint_id,
        )
        if not checkpoint or not checkpoint["is_active"]:
            raise HTTPException(status_code=404, detail="This checkpoint is no longer active.")

        min_len = checkpoint["min_answer_length"] or 0
        if len(payload.answer_text.strip()) < min_len:
            raise HTTPException(
                status_code=422,
                detail=f"Please write at least {min_len} characters for this task.",
            )

        # Per-mission mini questionnaire (manager's request): "what unique
        # thing did you notice in this hall" is mandatory alongside the
        # task answer; the suggestion/review/feedback box is optional.
        unique_observation = payload.unique_observation.strip()
        if not unique_observation:
            raise HTTPException(
                status_code=422,
                detail="Please tell us something unique you noticed in this hall.",
            )

        status = "pending"

        try:
            await conn.execute(
                """
                INSERT INTO stamps (
                    student_id, checkpoint_id, scan_latitude, scan_longitude,
                    scan_accuracy_m, answer_text, unique_observation,
                    suggestion_feedback, status, fraud_flag
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                """,
                result.student_id, result.checkpoint_id,
                result.scan_lat, result.scan_lon, result.scan_accuracy_m,
                payload.answer_text.strip(), unique_observation,
                payload.suggestion_feedback, status, result.fraud_flag,
            )
        except asyncpg.UniqueViolationError:
            raise HTTPException(status_code=409, detail="You've already collected the stamp for this checkpoint.")

        counts = await conn.fetchrow(
            """
            SELECT
                COUNT(*) FILTER (WHERE NOT c.is_bonus AND s.status = 'approved') AS verified,
                COUNT(*) FILTER (WHERE c.is_bonus AND s.status = 'approved') AS bonus
            FROM stamps s JOIN checkpoints c ON c.id = s.checkpoint_id
            WHERE s.student_id = $1
            """,
            result.student_id,
        )

    message = "Your answer has been submitted for review. Your stamp will be added after approval."

    return CheckpointSubmitResponse(
        stamp_number=checkpoint["stamp_number"],
        status=status,
        fraud_flag=result.fraud_flag,
        verified_stamps=counts["verified"] or 0,
        bonus_stamps=counts["bonus"] or 0,
        message=message,
    )