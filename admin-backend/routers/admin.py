"""
UPITS 2026 — Main Admin API.

v3 (login added back): every /api/admin/* route below (except the
login route itself) now requires a valid session token, checked via a
single `Depends(require_admin_token)` on the router — see
utils/admin_auth.py for the login/token logic. Nothing else in this
file changed to get that protection back.

Endpoints are grouped to match the admin portal's sidebar sections:
dashboard, students, checkpoints, answer review, scan monitoring, fraud
review, certificates, redemption desk, feedback analytics, institutions,
footfall, exports, audit log, settings, consent/data controls.
"""

import csv
import io
import json
import secrets
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from database import get_pool
from utils import cert_token, qr_token
from utils.admin_audit import audit
from utils.admin_auth import check_credentials, issue_token, require_admin_token

# Login itself must NOT require the token (you don't have one yet), so
# it lives on its own unprotected router.
auth_router = APIRouter(prefix='/api/admin/auth', tags=['admin-auth'])

router = APIRouter(prefix='/api/admin', tags=['admin'], dependencies=[Depends(require_admin_token)])


class LoginRequest(BaseModel):
    username: str
    password: str


@auth_router.post('/login')
async def login(payload: LoginRequest):
    if not check_credentials(payload.username, payload.password):
        raise HTTPException(401, 'Invalid username or password.')
    return issue_token()

REQUIRED_STAMPS = 10
PRIORITY_DRAW_MIN_STAMPS = 12


# =========================================================================
# Request models
# =========================================================================
class CheckpointPayload(BaseModel):
    stamp_number: int = Field(ge=1, le=999)
    is_bonus: bool = False
    hall_zone: str = Field(min_length=1, max_length=100)
    theme: str = Field(min_length=1, max_length=100)
    question_text: str = Field(min_length=1, max_length=3000)
    answer_type: str = Field(pattern='^(short_text|dropdown|multiple_choice|photo_upload)$')
    answer_options: Optional[list[str]] = None
    min_answer_length: int = Field(default=0, ge=0, le=500)
    latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)
    geofence_radius_m: int = Field(default=40, ge=10, le=500)
    max_accuracy_m: int = Field(default=50, ge=1, le=500)
    qr_expires_seconds: int = Field(default=0, ge=0, le=31536000)  # 0 = never expires (default)
    is_active: bool = True

class ReviewPayload(BaseModel):
    action: str = Field(pattern='^(approve|reject|manual)$')
    reason: str = Field(min_length=2, max_length=500)
    staff_name: Optional[str] = Field(default=None, max_length=120)

class RedemptionLookup(BaseModel):
    completion_token: str = Field(min_length=10)
    staff_name: Optional[str] = Field(default=None, max_length=120)

class StudentStatusPayload(BaseModel):
    status: str = Field(pattern='^(active|blocked)$')
    reason: Optional[str] = Field(default=None, max_length=300)
    staff_name: Optional[str] = Field(default=None, max_length=120)

class SettingsPayload(BaseModel):
    values: dict

DEFAULT_SETTINGS = {
    "required_stamps": REQUIRED_STAMPS,
    "priority_draw_min_stamps": PRIORITY_DRAW_MIN_STAMPS,
    "points_per_compulsory_stamp": 10,
    "points_per_bonus_stamp": 20,
    "rate_limit_seconds": 60,
    "default_geofence_radius_m": 40,
    "default_max_accuracy_m": 50,
    "default_qr_expires_seconds": 0,
    "event_start_date": "2026-09-25",
    "event_end_date": "2026-09-29",
    "default_language": "en",
}


# =========================================================================
# Settings helper + certificate auto/manual generation
# =========================================================================
async def _get_setting(conn, key: str, default):
    """Reads one admin_settings value, falling back to DEFAULT_SETTINGS /
    a caller-supplied default when Settings hasn't been saved yet. This is
    what makes "Configure compulsory stamps" and "Configure reward points"
    on the Settings screen actually take effect everywhere, instead of the
    old hard-coded REQUIRED_STAMPS / PRIORITY_DRAW_MIN_STAMPS constants."""
    row = await conn.fetchrow('SELECT value FROM admin_settings WHERE key=$1', key)
    if not row:
        return default
    val = row['value']
    return json.loads(val) if isinstance(val, str) else val


async def _maybe_issue_certificate(conn, student_id: str, staff_name: Optional[str] = None, force: bool = False):
    """Certificate generation facility — automatic AND manual.

    Automatic: called from review_stamp() every time a stamp is approved,
    so the moment a student's approved-compulsory-stamp count crosses the
    configured `required_stamps` threshold, a certificate row is created
    right there — no separate job or button needed.

    Manual: called with force=True from the "Generate Certificate" button
    (single student) or the "Sync Eligible Certificates" button (catches
    up anyone who reached eligibility a different way, e.g. a manual
    stamp override, or before this feature existed).

    No-ops (returns None) if a certificate already exists for the student,
    or if not yet eligible and force=False.
    """
    existing = await conn.fetchval('SELECT id FROM certificates WHERE student_id=$1', student_id)
    if existing:
        return None

    required = int(await _get_setting(conn, 'required_stamps', REQUIRED_STAMPS))
    priority_min = int(await _get_setting(conn, 'priority_draw_min_stamps', PRIORITY_DRAW_MIN_STAMPS))
    pts_compulsory = float(await _get_setting(conn, 'points_per_compulsory_stamp', 10))
    pts_bonus = float(await _get_setting(conn, 'points_per_bonus_stamp', 20))

    counts = await conn.fetchrow('''
        SELECT s.passport_id,
               COUNT(st.id) FILTER (WHERE st.status='approved' AND NOT c.is_bonus) AS verified,
               COUNT(st.id) FILTER (WHERE st.status='approved' AND c.is_bonus) AS bonus
        FROM students s
        LEFT JOIN stamps st ON st.student_id = s.id
        LEFT JOIN checkpoints c ON c.id = st.checkpoint_id
        WHERE s.id = $1 GROUP BY s.id, s.passport_id
    ''', student_id)
    if not counts:
        return None

    verified, bonus = counts['verified'] or 0, counts['bonus'] or 0
    if not force and verified < required:
        return None

    total = verified + bonus
    serial_number = f"{counts['passport_id']}-CERT"
    reward_points = round(verified * pts_compulsory + bonus * pts_bonus, 2)
    priority_draw_eligible = total >= priority_min

    row = await conn.fetchrow('''
        INSERT INTO certificates(student_id, serial_number, total_stamps, prize_eligible,
                                  priority_draw_eligible, reward_points, issued_at)
        VALUES($1,$2,$3,true,$4,$5,now()) RETURNING *
    ''', student_id, serial_number, total, priority_draw_eligible, reward_points)
    await audit(
        'certificate_manual_issue' if force else 'certificate_auto_issue',
        'certificate', serial_number,
        {'passport_id': counts['passport_id'], 'total_stamps': total, 'reward_points': reward_points},
        staff_name,
    )
    return dict(row)


# =========================================================================
# Dashboard
# =========================================================================
@router.get('/dashboard')
async def dashboard():
    pool = await get_pool()
    async with pool.acquire() as conn:
        required_stamps = int(await _get_setting(conn, 'required_stamps', REQUIRED_STAMPS))
        priority_draw_min = int(await _get_setting(conn, 'priority_draw_min_stamps', PRIORITY_DRAW_MIN_STAMPS))

        row = await conn.fetchrow('''
            WITH progress AS (
                SELECT s.id, s.registration_status,
                       COUNT(st.id) FILTER (WHERE st.status='approved' AND NOT c.is_bonus) AS verified,
                       COUNT(st.id) FILTER (WHERE st.status='approved' AND c.is_bonus) AS bonus
                FROM students s
                LEFT JOIN stamps st ON st.student_id = s.id
                LEFT JOIN checkpoints c ON c.id = st.checkpoint_id
                GROUP BY s.id, s.registration_status
            )
            SELECT
              (SELECT COUNT(*) FROM students) AS registrations,
              (SELECT COUNT(*) FROM students WHERE registration_status='active') AS active_passports,
              -- Active passports TODAY: distinct active students with at least
              -- one successful scan today (i.e. actually out on the floor today).
              (SELECT COUNT(DISTINCT sl.student_id) FROM scan_logs sl
                 JOIN students s2 ON s2.id = sl.student_id AND s2.registration_status='active'
                 WHERE sl.result='success' AND sl.created_at::date = CURRENT_DATE) AS active_passports_today,
              (SELECT COUNT(*) FROM students WHERE created_at::date = CURRENT_DATE) AS registrations_today,
              (SELECT COUNT(*) FROM students s WHERE NOT EXISTS (
                  SELECT 1 FROM certificates ce WHERE ce.student_id = s.id
              )) AS passports_in_queue,
              -- Completed passports: scanned + got all `required_stamps`
              -- (default 10 of 10) compulsory checkpoints approved.
              (SELECT COUNT(*) FROM progress WHERE verified >= $1) AS completed_passports,
              -- Certificate eligible: same condition, computed live from
              -- stamps (not from the certificates table), so this number is
              -- correct even a moment before the certificate row is issued.
              (SELECT COUNT(*) FROM progress WHERE verified >= $1) AS certificate_eligible,
              -- Prize draw eligible ("bonus wale"): compulsory done AND
              -- total (compulsory+bonus) stamps clears the configured
              -- priority-draw threshold (default 12).
              (SELECT COUNT(*) FROM progress WHERE verified >= $1 AND (verified + bonus) >= $2) AS prize_draw_eligible,
              (SELECT COUNT(*) FROM stamps) AS checkpoint_responses_total,
              (SELECT COUNT(*) FROM stamps WHERE status='approved') AS approved_stamps,
              (SELECT COUNT(*) FROM stamps WHERE status IN ('pending','manual')) AS pending_reviews,
              (SELECT COUNT(*) FROM stamps WHERE status='rejected') AS rejected_stamps,
              (SELECT COUNT(*) FROM stamps WHERE fraud_flag IS NOT NULL) AS fraud_flagged_stamps,
              (SELECT COUNT(*) FROM certificates) AS certificates,
              (SELECT COUNT(*) FROM certificates WHERE redeemed) AS redeemed,
              (SELECT COUNT(*) FROM certificates WHERE prize_eligible) AS prize_eligible,
              (SELECT COUNT(*) FROM certificates WHERE priority_draw_eligible) AS priority_draw_eligible,
              (SELECT COUNT(*) FROM scan_logs WHERE created_at::date = CURRENT_DATE) AS scans_today,
              (SELECT COUNT(*) FROM scan_logs WHERE result='failed' AND created_at::date = CURRENT_DATE) AS failed_scans_today,
              (SELECT COUNT(*) FROM feedback_responses) AS feedback_responses
        ''', required_stamps, priority_draw_min)

        checkpoints = await conn.fetch('''
            SELECT c.id, c.stamp_number, c.is_bonus, c.hall_zone, c.theme, c.is_active,
                   COUNT(s.id) FILTER (WHERE s.status='approved') AS approved,
                   COUNT(s.id) FILTER (WHERE s.status IN ('pending','manual')) AS pending,
                   COUNT(s.id) FILTER (WHERE s.status='rejected') AS rejected
            FROM checkpoints c LEFT JOIN stamps s ON s.checkpoint_id=c.id
            GROUP BY c.id ORDER BY c.is_bonus, c.stamp_number
        ''')
        route_load = await conn.fetch('''
            SELECT COALESCE(route_colour,'unassigned') AS route_colour, COUNT(*) AS students
            FROM students GROUP BY route_colour ORDER BY 1
        ''')
        # Hall-wise footfall, right on the dashboard (full breakdown still
        # lives on the Footfall & Routes screen) — top 10 busiest halls.
        hall_wise = await conn.fetch('''
            SELECT c.hall_zone, c.theme, BOOL_OR(c.is_bonus) AS is_bonus,
                   COUNT(sl.id) FILTER (WHERE sl.result='success') AS successful_scans,
                   COUNT(sl.id) FILTER (WHERE sl.result='failed') AS failed_scans
            FROM checkpoints c LEFT JOIN scan_logs sl ON sl.checkpoint_id = c.id
            GROUP BY c.hall_zone, c.theme
            ORDER BY successful_scans DESC, c.hall_zone LIMIT 10
        ''')
        # School/college-wise participation, top 8 by completions (full
        # list still lives on the Institutions screen).
        top_institutions = await conn.fetch('''
            WITH per_student AS (
                SELECT s.id, s.institution_name,
                       COUNT(st.id) FILTER (WHERE st.status='approved' AND NOT c.is_bonus) AS verified
                FROM students s
                LEFT JOIN stamps st ON st.student_id = s.id
                LEFT JOIN checkpoints c ON c.id = st.checkpoint_id
                GROUP BY s.id, s.institution_name
            )
            SELECT institution_name, COUNT(*) AS registrations,
                   COUNT(*) FILTER (WHERE verified >= $1) AS completions
            FROM per_student GROUP BY institution_name
            ORDER BY completions DESC, registrations DESC LIMIT 8
        ''', required_stamps)
    return {
        'metrics': dict(row),
        'checkpoints': [dict(x) for x in checkpoints],
        'route_load': [dict(x) for x in route_load],
        'hall_wise': [dict(x) for x in hall_wise],
        'top_institutions': [dict(x) for x in top_institutions],
    }


# =========================================================================
# Students
# =========================================================================
@router.get('/students')
async def students(search: str = Query(default=''), limit: int = Query(default=50, ge=1, le=500)):
    pool = await get_pool()
    q = f'%{search.strip()}%'
    async with pool.acquire() as conn:
        rows = await conn.fetch('''
            SELECT s.id, s.passport_id, s.full_name, s.mobile_number, s.email,
                   s.student_category, s.class_or_course, s.age_group,
                   s.institution_name, s.district_city, s.route_colour,
                   s.registration_status, s.data_erased_at, s.created_at,
                   COUNT(st.id) FILTER (WHERE st.status='approved' AND NOT c.is_bonus) AS verified_stamps,
                   COUNT(st.id) FILTER (WHERE st.status='approved' AND c.is_bonus) AS bonus_stamps,
                   COUNT(st.id) FILTER (WHERE st.status IN ('pending','manual')) AS pending_stamps
            FROM students s
            LEFT JOIN stamps st ON st.student_id=s.id
            LEFT JOIN checkpoints c ON c.id=st.checkpoint_id
            WHERE ($1='' OR s.passport_id ILIKE $2 OR s.full_name ILIKE $2
                   OR s.mobile_number ILIKE $2 OR s.institution_name ILIKE $2)
            GROUP BY s.id ORDER BY s.created_at DESC LIMIT $3
        ''', search.strip(), q, limit)
    return {'items': [dict(x) for x in rows]}

@router.get('/students/{passport_id}')
async def student_detail(passport_id: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        student = await conn.fetchrow('SELECT * FROM students WHERE passport_id=$1', passport_id)
        if not student: raise HTTPException(404, 'Student not found.')
        stamps = await conn.fetch('''
          SELECT st.*, c.stamp_number, c.is_bonus, c.hall_zone, c.theme, c.question_text
          FROM stamps st JOIN checkpoints c ON c.id=st.checkpoint_id
          WHERE st.student_id=$1 ORDER BY st.created_at
        ''', student['id'])
        cert = await conn.fetchrow('SELECT * FROM certificates WHERE student_id=$1', student['id'])
        feedback = await conn.fetchrow('SELECT * FROM feedback_responses WHERE student_id=$1', student['id'])
        scans = await conn.fetch('''
          SELECT * FROM scan_logs WHERE student_id=$1 ORDER BY created_at DESC LIMIT 100
        ''', student['id'])
        status_history = await conn.fetch('''
          SELECT * FROM student_status_history WHERE student_id=$1 ORDER BY created_at DESC LIMIT 50
        ''', student['id'])
        pts_compulsory = float(await _get_setting(conn, 'points_per_compulsory_stamp', 10))
        pts_bonus = float(await _get_setting(conn, 'points_per_bonus_stamp', 20))
        required_stamps = int(await _get_setting(conn, 'required_stamps', REQUIRED_STAMPS))

    verified_ct = sum(1 for x in stamps if x['status'] == 'approved' and not x['is_bonus'])
    bonus_ct = sum(1 for x in stamps if x['status'] == 'approved' and x['is_bonus'])
    return {
        'student': dict(student),
        'stamps': [dict(x) for x in stamps],
        'certificate': dict(cert) if cert else None,
        'feedback': dict(feedback) if feedback else None,
        'scan_history': [dict(x) for x in scans],
        'status_history': [dict(x) for x in status_history],
        'reward_points': round(verified_ct * pts_compulsory + bonus_ct * pts_bonus, 2),
        'certificate_eligible': verified_ct >= required_stamps,
    }

@router.patch('/students/{passport_id}/status')
async def set_student_status(passport_id: str, payload: StudentStatusPayload):
    pool = await get_pool()
    async with pool.acquire() as conn:
        before = await conn.fetchrow('SELECT id, registration_status FROM students WHERE passport_id=$1', passport_id)
        if not before: raise HTTPException(404, 'Student not found.')
        row = await conn.fetchrow(
            'UPDATE students SET registration_status=$2, updated_at=now() WHERE passport_id=$1 RETURNING *',
            passport_id, payload.status,
        )
        # Explicit, queryable record of every status change — kept in its
        # own table (student_status_history) in addition to the general
        # admin_audit_logs entry below, so the Student Passport screen can
        # show a clean "status changed" timeline for just this student.
        await conn.execute('''
            INSERT INTO student_status_history(student_id, passport_id, old_status, new_status, reason, changed_by)
            VALUES($1,$2,$3,$4,$5,$6)
        ''', before['id'], passport_id, before['registration_status'], payload.status,
        payload.reason, payload.staff_name or 'Admin Portal')
    if not row: raise HTTPException(404, 'Student not found.')
    await audit('student_status', 'student', passport_id,
                {'status': payload.status, 'reason': payload.reason}, payload.staff_name)
    return dict(row)

@router.get('/students/{passport_id}/status-history')
async def student_status_history(passport_id: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            'SELECT * FROM student_status_history WHERE passport_id=$1 ORDER BY created_at DESC', passport_id,
        )
    return {'items': [dict(x) for x in rows]}


# =========================================================================
# Checkpoints & QR
# =========================================================================
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # excludes O/0/I/1 — hard to misread on a printed sheet

async def _unique_manual_code(conn) -> str:
    """6-character alphanumeric fallback code for a checkpoint (BRD
    section 12: 'QR scanner using mobile camera, plus fallback code
    entry for technical failure'). Retries on the rare collision."""
    for _ in range(20):
        code = ''.join(secrets.choice(CODE_ALPHABET) for _ in range(6))
        exists = await conn.fetchval('SELECT 1 FROM checkpoints WHERE manual_code=$1', code)
        if not exists:
            return code
    raise HTTPException(500, 'Could not generate a unique fallback code, please retry.')


@router.get('/checkpoints')
async def list_checkpoints():
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Backfill: checkpoints created before the fallback-code feature
        # existed have manual_code = NULL. Assign them one now so nothing
        # needs a manual migration step.
        missing = await conn.fetch('SELECT id FROM checkpoints WHERE manual_code IS NULL')
        for row in missing:
            code = await _unique_manual_code(conn)
            await conn.execute('UPDATE checkpoints SET manual_code=$2 WHERE id=$1', row['id'], code)
        rows = await conn.fetch('SELECT * FROM checkpoints ORDER BY is_bonus, stamp_number, created_at')
    return {'items': [dict(x) for x in rows]}

@router.post('/checkpoints')
async def create_checkpoint(payload: CheckpointPayload):
    pool = await get_pool()
    secret = secrets.token_hex(24)
    async with pool.acquire() as conn:
        manual_code = await _unique_manual_code(conn)
        row = await conn.fetchrow('''
          INSERT INTO checkpoints(stamp_number,is_bonus,hall_zone,theme,question_text,answer_type,answer_options,
          min_answer_length,latitude,longitude,geofence_radius_m,max_accuracy_m,qr_token_secret,qr_expires_seconds,is_active,qr_rotated_at,manual_code)
          VALUES($1,$2,$3,$4,$5,$6,$7::jsonb,$8,$9,$10,$11,$12,$13,$14,$15,now(),$16) RETURNING *
        ''', payload.stamp_number,payload.is_bonus,payload.hall_zone,payload.theme,payload.question_text,
        payload.answer_type,json.dumps(payload.answer_options) if payload.answer_options is not None else None,
        payload.min_answer_length,payload.latitude,payload.longitude,payload.geofence_radius_m,payload.max_accuracy_m,
        secret,payload.qr_expires_seconds,payload.is_active,manual_code)
    await audit('checkpoint_create','checkpoint',str(row['id']),{'stamp_number':payload.stamp_number})
    return dict(row)

@router.put('/checkpoints/{checkpoint_id}')
async def update_checkpoint(checkpoint_id: str, payload: CheckpointPayload):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow('''
          UPDATE checkpoints SET stamp_number=$2,is_bonus=$3,hall_zone=$4,theme=$5,question_text=$6,answer_type=$7,
          answer_options=$8::jsonb,min_answer_length=$9,latitude=$10,longitude=$11,geofence_radius_m=$12,max_accuracy_m=$13,
          qr_expires_seconds=$14,is_active=$15,updated_at=now() WHERE id=$1 RETURNING *
        ''', checkpoint_id,payload.stamp_number,payload.is_bonus,payload.hall_zone,payload.theme,payload.question_text,
        payload.answer_type,json.dumps(payload.answer_options) if payload.answer_options is not None else None,
        payload.min_answer_length,payload.latitude,payload.longitude,payload.geofence_radius_m,payload.max_accuracy_m,
        payload.qr_expires_seconds,payload.is_active)
    if not row: raise HTTPException(404,'Checkpoint not found.')
    await audit('checkpoint_update','checkpoint',checkpoint_id)
    return dict(row)

DEFAULT_CHECKPOINTS = [
    # Compulsory missions — BRD section 5. stamp_number 1-10.
    dict(stamp_number=1, is_bonus=False, hall_zone='Hall 1 — UP / Made in India Entry Checkpoint',
         theme='Uttar Pradesh Manufacturing',
         question_text='Find a product manufactured in Uttar Pradesh. What is the product, and which district is it associated with?',
         answer_type='short_text', answer_options=None, min_answer_length=3),
    dict(stamp_number=2, is_bonus=False, hall_zone='Hall 3 — ODOP / GI Product Checkpoint',
         theme='ODOP and GI',
         question_text='Identify one ODOP or GI product you saw, and name it. Then select its category below.',
         answer_type='dropdown',
         answer_options=['Handicraft', 'Textile', 'Food', 'Leather', 'Home décor', 'Other'],
         min_answer_length=0),
    dict(stamp_number=3, is_bonus=False, hall_zone='Hall 5 — Agriculture / Food Processing Checkpoint',
         theme='Agriculture and Food',
         question_text='Name one value-added agriculture or food product displayed here.',
         answer_type='short_text', answer_options=None, min_answer_length=2),
    dict(stamp_number=4, is_bonus=False, hall_zone='Hall 7 — Innovation / Technology Checkpoint',
         theme='Innovation and Technology',
         question_text='What new technology or innovation did you discover here?',
         answer_type='dropdown',
         answer_options=['AI', 'Robotics', 'Electronics', 'Manufacturing', 'Clean technology', 'Other'],
         min_answer_length=0),
    dict(stamp_number=5, is_bonus=False, hall_zone='Hall 9 — Green Product Checkpoint',
         theme='Sustainability',
         question_text='Find an eco-friendly product or sustainable business practice. What makes it eco-friendly?',
         answer_type='short_text', answer_options=None, min_answer_length=15),
    dict(stamp_number=6, is_bonus=False, hall_zone='Hall 10/11 — International Participation Checkpoint',
         theme='World Cultures / International Trade',
         question_text='Visit an international/partner-country exhibitor. Which country or region did you visit, and what product/sector did you see?',
         answer_type='dropdown',
         answer_options=['Japan', 'Vietnam', 'Russia', 'Singapore', 'Austria', 'Belarus', 'Other'],
         min_answer_length=0),
    dict(stamp_number=7, is_bonus=False, hall_zone='Hall 12 — Entrepreneurship Checkpoint',
         theme='Entrepreneurship',
         question_text='Speak to an exhibitor/entrepreneur. What problem does their product or service solve?',
         answer_type='short_text', answer_options=None, min_answer_length=20),
    dict(stamp_number=8, is_bonus=False, hall_zone='Hall 14 — Main Entry / Themed Activity Zone',
         theme='Global Trade',
         question_text='Complete this statement: "This product can reach international markets because…"',
         answer_type='short_text', answer_options=None, min_answer_length=20),
    dict(stamp_number=9, is_bonus=False, hall_zone='Hall 15/16 — Career Checkpoint',
         theme='Careers in Trade',
         question_text='Which career area interested you most?',
         answer_type='multiple_choice',
         answer_options=['Export sales', 'Manufacturing', 'Design', 'Logistics', 'E-commerce', 'Technology', 'Entrepreneurship', 'Marketing', 'Other'],
         min_answer_length=0),
    dict(stamp_number=10, is_bonus=False, hall_zone='Hall 17 — Reflection Checkpoint',
         theme='Learning Reflection',
         question_text='Name one product you would recommend to a friend and explain why.',
         answer_type='short_text', answer_options=None, min_answer_length=25),
    # Bonus missions — BRD section 6. stamp_number 1-5 (is_bonus=True).
    dict(stamp_number=1, is_bonus=True, hall_zone='Hall 2 — Official Programme / Knowledge-Session Area',
         theme='Knowledge Session',
         question_text='Attend a knowledge session or activity. Select the topic you attended.',
         answer_type='dropdown',
         answer_options=['Science Quiz', 'Career Counselling & Business Expo', 'Yoga & Mental Health', 'Robotics & AI', 'MUN', 'Other official session'],
         min_answer_length=0),
    dict(stamp_number=2, is_bonus=True, hall_zone='Photo Installation / Designated Social Zone',
         theme='Photo Moment',
         question_text='Take a photo at the "Made in India, Made for the World" or "Next Stop: Global" installation.',
         answer_type='photo_upload', answer_options=None, min_answer_length=0),
    dict(stamp_number=3, is_bonus=True, hall_zone='Designated Startup / Founder Zone',
         theme='Young Entrepreneur Hunt',
         question_text='Identify a product that could become a ₹100-crore brand. Why? (50–100 words)',
         answer_type='short_text', answer_options=None, min_answer_length=150),
    dict(stamp_number=4, is_bonus=True, hall_zone='Any Distinct Hall Not Already Used',
         theme='Extra Exploration',
         question_text='Explore one additional hall and name a product category you discovered.',
         answer_type='short_text', answer_options=None, min_answer_length=2),
    dict(stamp_number=5, is_bonus=True, hall_zone='Official Reel Booth / Social Zone',
         theme='Best Expo Reel',
         question_text='Upload your original 30-second Expo reel link, or submit at the designated desk.',
         answer_type='short_text', answer_options=None, min_answer_length=0),
]


@router.post('/checkpoints/seed-default')
async def seed_default_checkpoints(staff_name: Optional[str] = Query(default=None)):
    """One-click setup: creates the 10 compulsory + 5 bonus checkpoints
    from the UPITS 2026 BRD (15 total, one per hall/zone), each with its
    own unique QR-signing secret. Skips any stamp_number+is_bonus pair
    that already exists, so it's safe to re-run — e.g. after adding a
    16th custom checkpoint, running seed again won't duplicate the 15
    official ones. GPS latitude/longitude is left unset; edit each
    checkpoint afterwards with the coordinates from the on-site GPS
    survey (BRD section 8) before going live."""
    pool = await get_pool()
    created, skipped = [], []
    async with pool.acquire() as conn:
        existing = await conn.fetch('SELECT stamp_number, is_bonus FROM checkpoints')
        existing_keys = {(r['stamp_number'], r['is_bonus']) for r in existing}
        for item in DEFAULT_CHECKPOINTS:
            key = (item['stamp_number'], item['is_bonus'])
            if key in existing_keys:
                skipped.append(item['hall_zone'])
                continue
            secret = secrets.token_hex(24)
            manual_code = await _unique_manual_code(conn)
            row = await conn.fetchrow('''
              INSERT INTO checkpoints(stamp_number,is_bonus,hall_zone,theme,question_text,answer_type,answer_options,
              min_answer_length,latitude,longitude,geofence_radius_m,max_accuracy_m,qr_token_secret,qr_expires_seconds,is_active,qr_rotated_at,manual_code)
              VALUES($1,$2,$3,$4,$5,$6,$7::jsonb,$8,NULL,NULL,40,50,$9,0,true,now(),$10) RETURNING id, hall_zone
            ''', item['stamp_number'], item['is_bonus'], item['hall_zone'], item['theme'], item['question_text'],
            item['answer_type'], json.dumps(item['answer_options']) if item['answer_options'] is not None else None,
            item['min_answer_length'], secret, manual_code)
            created.append(dict(row))
    await audit('checkpoints_seed_default', 'checkpoint', None,
                {'created': len(created), 'skipped': len(skipped)}, staff_name)
    return {'created': created, 'skipped_existing': skipped}


@router.get('/checkpoints/{checkpoint_id}/qr-token')
async def get_qr_token(checkpoint_id: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow('SELECT id, qr_token_secret, is_active, manual_code FROM checkpoints WHERE id=$1', checkpoint_id)
    if not row or not row['is_active']:
        raise HTTPException(404, 'Checkpoint not found or inactive.')
    if not row['qr_token_secret']:
        raise HTTPException(500, 'Checkpoint QR secret is not configured.')
    # Always 0 = never expires. UPITS 2026 uses one static, printable QR
    # per checkpoint for the whole event — this is hard-coded rather than
    # read from the checkpoint's qr_expires_seconds column so that
    # checkpoints seeded before this decision (which stored 600) are
    # fixed automatically too, with no manual migration needed.
    data = qr_token.generate_token(str(row['id']), row['qr_token_secret'], 0)
    data['manual_code'] = row['manual_code']
    await audit('checkpoint_qr_generate','checkpoint',checkpoint_id)
    return data

@router.post('/checkpoints/{checkpoint_id}/rotate-qr')
async def rotate_qr(checkpoint_id: str):
    pool = await get_pool(); secret = secrets.token_hex(24)
    async with pool.acquire() as conn:
        manual_code = await _unique_manual_code(conn)
        row = await conn.fetchrow('''UPDATE checkpoints SET qr_token_secret=$2, manual_code=$3, qr_rotated_at=now(), updated_at=now()
                                      WHERE id=$1 RETURNING id, qr_rotated_at, qr_expires_seconds, manual_code''', checkpoint_id, secret, manual_code)
    if not row: raise HTTPException(404,'Checkpoint not found.')
    await audit('checkpoint_qr_rotate','checkpoint',checkpoint_id)
    return dict(row)

@router.patch('/checkpoints/{checkpoint_id}/status')
async def checkpoint_status(checkpoint_id: str, active: bool = Query(...)):
    pool=await get_pool()
    async with pool.acquire() as conn:
        row=await conn.fetchrow('UPDATE checkpoints SET is_active=$2,updated_at=now() WHERE id=$1 RETURNING *',checkpoint_id,active)
    if not row: raise HTTPException(404,'Checkpoint not found.')
    await audit('checkpoint_status','checkpoint',checkpoint_id,{'active':active})
    return dict(row)


# =========================================================================
# Manual answer review
# =========================================================================
@router.get('/reviews')
async def reviews(status: str = Query(default='pending')):
    statuses = ['pending','manual','rejected','approved'] if status=='all' else [status]
    pool=await get_pool()
    async with pool.acquire() as conn:
        rows=await conn.fetch('''
          SELECT st.id, st.created_at, st.status, st.answer_text, st.unique_observation, st.suggestion_feedback,
                 st.scan_latitude, st.scan_longitude, st.scan_accuracy_m, st.fraud_flag, st.reviewed_at,
                 st.reviewed_reason, st.reviewed_by, c.id AS checkpoint_id,
                 s.passport_id, s.full_name, s.institution_name, c.stamp_number, c.is_bonus, c.hall_zone, c.theme, c.question_text
          FROM stamps st JOIN students s ON s.id=st.student_id JOIN checkpoints c ON c.id=st.checkpoint_id
          WHERE st.status = ANY($1::varchar[]) ORDER BY st.created_at DESC LIMIT 500
        ''', statuses)
    return {'items':[dict(x) for x in rows]}

@router.post('/reviews/{stamp_id}')
async def review_stamp(stamp_id: str, payload: ReviewPayload):
    new_status = {'approve':'approved','reject':'rejected','manual':'manual'}[payload.action]
    staff = payload.staff_name or 'Admin Portal'
    pool=await get_pool()
    async with pool.acquire() as conn:
        row=await conn.fetchrow('''UPDATE stamps SET status=$2, reviewed_at=now(), reviewed_reason=$3, reviewed_by=$4
                                   WHERE id=$1 RETURNING *''', stamp_id,new_status,payload.reason,staff)
        if not row: raise HTTPException(404,'Stamp review record not found.')
        cert = None
        if new_status == 'approved':
            # Automatic certificate generation: this is the moment a stamp
            # actually counts toward completion (stamps sit as 'pending'
            # until an admin approves them here), so this is where a
            # student can newly cross the required-stamps threshold.
            cert = await _maybe_issue_certificate(conn, str(row['student_id']), staff)
    await audit('stamp_review','stamp',stamp_id,{'status':new_status,'reason':payload.reason},staff)
    result = dict(row)
    if cert:
        result['certificate_issued'] = cert
    return result


# =========================================================================
# QR & scan monitoring
# =========================================================================
@router.get('/scans')
async def scan_monitoring(
    result: Optional[str] = Query(default=None, pattern='^(success|failed)$'),
    checkpoint_id: Optional[str] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch('''
            SELECT sl.*, c.stamp_number, c.is_bonus, c.theme
            FROM scan_logs sl LEFT JOIN checkpoints c ON c.id = sl.checkpoint_id
            WHERE ($1::varchar IS NULL OR sl.result=$1)
              AND ($2::uuid IS NULL OR sl.checkpoint_id=$2)
            ORDER BY sl.created_at DESC LIMIT $3
        ''', result, checkpoint_id, limit)
        summary = await conn.fetchrow('''
            SELECT COUNT(*) FILTER (WHERE result='success') AS success,
                   COUNT(*) FILTER (WHERE result='failed') AS failed,
                   COUNT(*) FILTER (WHERE created_at > now() - interval '15 minutes') AS last_15_min
            FROM scan_logs
        ''')
    return {'items': [dict(x) for x in rows], 'summary': dict(summary)}


# =========================================================================
# Fraud / anti-cheating review
# =========================================================================
@router.get('/fraud')
async def fraud_review():
    pool = await get_pool()
    async with pool.acquire() as conn:
        flagged_stamps = await conn.fetch('''
            SELECT st.id, st.fraud_flag, st.status, st.created_at,
                   s.passport_id, s.full_name, s.institution_name, c.hall_zone, c.stamp_number
            FROM stamps st JOIN students s ON s.id=st.student_id JOIN checkpoints c ON c.id=st.checkpoint_id
            WHERE st.fraud_flag IS NOT NULL ORDER BY st.created_at DESC LIMIT 200
        ''')
        failure_reasons = await conn.fetch('''
            SELECT reason, COUNT(*) AS attempts
            FROM scan_logs WHERE result='failed' AND created_at > now() - interval '24 hours'
            GROUP BY reason ORDER BY attempts DESC LIMIT 20
        ''')
        repeat_offenders = await conn.fetch('''
            SELECT passport_id, COUNT(*) AS failed_attempts
            FROM scan_logs WHERE result='failed' AND passport_id IS NOT NULL
              AND created_at > now() - interval '24 hours'
            GROUP BY passport_id HAVING COUNT(*) >= 3 ORDER BY failed_attempts DESC LIMIT 50
        ''')
        mock_location = await conn.fetchval("SELECT COUNT(*) FROM scan_logs WHERE is_mock_location=true")
        impossible_travel = await conn.fetchval("SELECT COUNT(*) FROM scan_logs WHERE fraud_flag='impossible_travel'")
    return {
        'flagged_stamps': [dict(x) for x in flagged_stamps],
        'failure_reasons': [dict(x) for x in failure_reasons],
        'repeat_offenders': [dict(x) for x in repeat_offenders],
        'mock_location_attempts': mock_location or 0,
        'impossible_travel_attempts': impossible_travel or 0,
    }


# =========================================================================
# Certificate management
# =========================================================================
@router.get('/certificates')
async def certificates(status: Optional[str] = Query(default=None, pattern='^(redeemed|not_redeemed|prize_eligible)$')):
    pool = await get_pool()
    where = ''
    if status == 'redeemed': where = 'WHERE c.redeemed = true'
    elif status == 'not_redeemed': where = 'WHERE c.redeemed = false'
    elif status == 'prize_eligible': where = 'WHERE c.prize_eligible = true'
    async with pool.acquire() as conn:
        rows = await conn.fetch(f'''
            SELECT c.*, s.passport_id, s.full_name, s.institution_name, s.mobile_number, s.email
            FROM certificates c JOIN students s ON s.id = c.student_id
            {where} ORDER BY c.issued_at DESC LIMIT 1000
        ''')
    return {'items': [dict(x) for x in rows]}

@router.post('/certificates/sync')
async def sync_certificates(staff_name: Optional[str] = Query(default=None)):
    """Manual/catch-up half of the certificate generation facility: scans
    every student who doesn't have a certificate yet and issues one for
    anyone who already qualifies (e.g. eligibility reached via a manual
    stamp override, or students who completed before this feature
    existed). Safe to click any time — no-ops for anyone not eligible."""
    pool = await get_pool()
    issued = []
    async with pool.acquire() as conn:
        candidates = await conn.fetch('''
            SELECT s.id FROM students s
            WHERE NOT EXISTS (SELECT 1 FROM certificates ce WHERE ce.student_id = s.id)
        ''')
        for c in candidates:
            cert = await _maybe_issue_certificate(conn, str(c['id']), staff_name)
            if cert:
                issued.append(cert)
    return {'issued_count': len(issued), 'issued': issued}

@router.post('/certificates/generate/{passport_id}')
async def generate_certificate(passport_id: str, staff_name: Optional[str] = Query(default=None)):
    """Manual, single-student certificate generation — for a staff member
    who wants to force-issue one right now (e.g. a student is at the
    Redemption Desk and eligibility hasn't been auto-picked-up yet).
    force=True inside _maybe_issue_certificate means this still works even
    for a student who is one review-approval away from the normal
    threshold, e.g. after a manual answer override."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        student = await conn.fetchrow('SELECT id FROM students WHERE passport_id=$1', passport_id)
        if not student: raise HTTPException(404, 'Student not found.')
        existing = await conn.fetchval('SELECT id FROM certificates WHERE student_id=$1', student['id'])
        if existing: raise HTTPException(409, 'Certificate already issued for this student.')
        cert = await _maybe_issue_certificate(conn, str(student['id']), staff_name, force=True)
    if not cert: raise HTTPException(500, 'Could not generate certificate.')
    return cert

@router.post('/certificates/{certificate_id}/resend')
async def resend_certificate(certificate_id: str):
    """Re-issues the verification token so it can be re-sent to the student.
    Actual email/WhatsApp delivery is a separate integration — this just
    confirms the certificate exists and logs the resend for audit."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        cert = await conn.fetchrow('''
            SELECT c.*, s.id AS student_uuid, s.passport_id, s.full_name, s.email, s.mobile_number
            FROM certificates c JOIN students s ON s.id = c.student_id WHERE c.id = $1
        ''', certificate_id)
    if not cert: raise HTTPException(404, 'Certificate not found.')
    token = cert_token.generate(cert['serial_number'], str(cert['student_uuid']))
    await audit('certificate_resend', 'certificate', certificate_id, {'passport_id': cert['passport_id']})
    return {
        'serial_number': cert['serial_number'], 'completion_token': token,
        'passport_id': cert['passport_id'], 'full_name': cert['full_name'],
        'email': cert['email'], 'mobile_number': cert['mobile_number'],
    }


# =========================================================================
# Redemption Desk
# =========================================================================
@router.get('/redemption/lookup')
async def redemption_lookup(token: str = Query(..., min_length=10)):
    result=cert_token.verify(token)
    if not result.valid: raise HTTPException(400,result.reason)
    pool=await get_pool()
    async with pool.acquire() as conn:
        cert=await conn.fetchrow('''SELECT c.*,s.passport_id,s.full_name,s.institution_name
                                    FROM certificates c JOIN students s ON s.id=c.student_id
                                    WHERE c.serial_number=$1''',result.serial_number)
    if not cert or str(cert['student_id']) != result.student_id: raise HTTPException(404,'Certificate not found.')
    return dict(cert)

@router.post('/redemption/redeem')
async def redeem(payload: RedemptionLookup):
    result=cert_token.verify(payload.completion_token)
    if not result.valid: raise HTTPException(400,result.reason)
    staff = payload.staff_name or 'Admin Portal'
    pool=await get_pool()
    async with pool.acquire() as conn:
        cert=await conn.fetchrow('''SELECT c.*,s.passport_id,s.full_name,s.institution_name
                                    FROM certificates c JOIN students s ON s.id=c.student_id
                                    WHERE c.serial_number=$1''',result.serial_number)
        if not cert or str(cert['student_id']) != result.student_id: raise HTTPException(404,'Certificate not found.')
        if cert['redeemed']: raise HTTPException(409,'Already redeemed.')
        updated=await conn.fetchrow('''UPDATE certificates SET redeemed=true, redeemed_at=now(), redeemed_by_staff_id=$2
                                       WHERE id=$1 AND redeemed=false RETURNING redeemed_at''',cert['id'],staff)
        if not updated: raise HTTPException(409,'Already redeemed.')
    await audit('redemption','certificate',str(cert['id']),{'passport_id':cert['passport_id']},staff)
    return {'success':True,'passport_id':cert['passport_id'],'student_name':cert['full_name'],
            'institution_name':cert['institution_name'],'serial_number':cert['serial_number'],
            'total_stamps':cert['total_stamps'],'prize_eligible':cert['prize_eligible'],
            'priority_draw_eligible':cert['priority_draw_eligible'],'redeemed_at':updated['redeemed_at']}


# =========================================================================
# Feedback & questionnaire analytics
# =========================================================================
@router.get('/feedback')
async def feedback():
    pool=await get_pool()
    async with pool.acquire() as conn:
        rows=await conn.fetch('''SELECT f.*,s.passport_id,s.full_name,s.institution_name
                                 FROM feedback_responses f JOIN students s ON s.id=f.student_id
                                 ORDER BY f.created_at DESC LIMIT 1000''')
        agg = await conn.fetch('''SELECT overall_rating, COUNT(*) c FROM feedback_responses GROUP BY overall_rating''')
        themes = await conn.fetch('''SELECT favourite_theme, COUNT(*) c FROM feedback_responses GROUP BY favourite_theme ORDER BY c DESC''')
        recommend = await conn.fetch('''SELECT would_recommend, COUNT(*) c FROM feedback_responses GROUP BY would_recommend''')
        helped = await conn.fetch('''SELECT passport_helped_explore, COUNT(*) c FROM feedback_responses GROUP BY passport_helped_explore''')
    return {
        'items':[dict(x) for x in rows],
        'ratings': {r['overall_rating']: r['c'] for r in agg},
        'favourite_themes': {r['favourite_theme']: r['c'] for r in themes},
        'would_recommend': {r['would_recommend']: r['c'] for r in recommend},
        'passport_helped_explore': {r['passport_helped_explore']: r['c'] for r in helped},
    }


# =========================================================================
# School / college / institution analytics
# =========================================================================
@router.get('/institutions')
async def institutions():
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch('''
            WITH per_student AS (
                SELECT s.id, s.institution_name,
                       COUNT(st.id) FILTER (WHERE st.status='approved' AND NOT c.is_bonus) AS verified
                FROM students s
                LEFT JOIN stamps st ON st.student_id = s.id
                LEFT JOIN checkpoints c ON c.id = st.checkpoint_id
                GROUP BY s.id, s.institution_name
            )
            SELECT institution_name,
                   COUNT(*) AS registrations,
                   COUNT(*) FILTER (WHERE verified >= 10) AS completions,
                   ROUND(100.0 * COUNT(*) FILTER (WHERE verified >= 10) / NULLIF(COUNT(*),0), 1) AS completion_pct
            FROM per_student
            GROUP BY institution_name
            ORDER BY completions DESC, registrations DESC
        ''')
    return {'items': [dict(x) for x in rows]}


# =========================================================================
# Route / hall-wise footfall and crowding
# =========================================================================
@router.get('/footfall')
async def footfall():
    pool = await get_pool()
    async with pool.acquire() as conn:
        hall_wise = await conn.fetch('''
            SELECT c.hall_zone, c.theme, BOOL_OR(c.is_bonus) AS is_bonus,
                   COUNT(sl.id) FILTER (WHERE sl.result='success') AS successful_scans,
                   COUNT(sl.id) FILTER (WHERE sl.result='failed') AS failed_scans
            FROM checkpoints c LEFT JOIN scan_logs sl ON sl.checkpoint_id = c.id
            GROUP BY c.hall_zone, c.theme
            ORDER BY successful_scans DESC, c.hall_zone
        ''')
        route_wise = await conn.fetch('''
            SELECT COALESCE(route_colour,'unassigned') AS route_colour,
                   COUNT(*) AS students
            FROM students GROUP BY route_colour ORDER BY 1
        ''')
        last_hour = await conn.fetch('''
            SELECT date_trunc('minute', created_at) AS minute, COUNT(*) AS scans
            FROM scan_logs WHERE created_at > now() - interval '1 hour' AND result='success'
            GROUP BY 1 ORDER BY 1
        ''')
    return {
        'hall_wise': [dict(x) for x in hall_wise],
        'route_wise': [dict(x) for x in route_wise],
        'last_hour': [dict(x) for x in last_hour],
    }


# =========================================================================
# Reports & CSV/Excel exports
# =========================================================================
def _csv_response(rows: list[dict], filename: str) -> StreamingResponse:
    buf = io.StringIO()
    if rows:
        writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        for r in rows:
            writer.writerow({k: ('' if v is None else v) for k, v in r.items()})
    else:
        buf.write('No data\n')
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]), media_type='text/csv',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )

@router.get('/export/{dataset}')
async def export_dataset(dataset: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        if dataset == 'registrations':
            rows = await conn.fetch('SELECT passport_id, full_name, mobile_number, email, student_category, class_or_course, age_group, institution_name, district_city, teacher_name, teacher_mobile, route_colour, registration_status, created_at FROM students ORDER BY created_at')
        elif dataset == 'stamps':
            rows = await conn.fetch('''SELECT s.passport_id, s.full_name, s.institution_name, c.stamp_number, c.is_bonus, c.hall_zone, c.theme,
                                        st.answer_text, st.status, st.fraud_flag, st.created_at
                                        FROM stamps st JOIN students s ON s.id=st.student_id JOIN checkpoints c ON c.id=st.checkpoint_id
                                        ORDER BY st.created_at''')
        elif dataset == 'responses':
            rows = await conn.fetch('''SELECT s.passport_id, s.full_name, f.overall_rating, f.favourite_theme, f.passport_helped_explore,
                                        f.memorable_experience, f.learning_note, f.wants_future_updates, f.would_recommend, f.created_at
                                        FROM feedback_responses f JOIN students s ON s.id=f.student_id ORDER BY f.created_at''')
        elif dataset == 'redemptions':
            rows = await conn.fetch('''SELECT s.passport_id, s.full_name, s.institution_name, c.serial_number, c.total_stamps,
                                        c.reward_points, c.prize_eligible, c.priority_draw_eligible, c.redeemed, c.redeemed_at, c.redeemed_by_staff_id
                                        FROM certificates c JOIN students s ON s.id=c.student_id ORDER BY c.issued_at''')
        elif dataset == 'checkpoints':
            rows = await conn.fetch('SELECT stamp_number, is_bonus, hall_zone, theme, answer_type, is_active, geofence_radius_m, max_accuracy_m FROM checkpoints ORDER BY is_bonus, stamp_number')
        else:
            raise HTTPException(404, 'Unknown export dataset. Use one of: registrations, stamps, responses, redemptions, checkpoints.')
    await audit('data_export', 'dataset', dataset)
    return _csv_response([dict(x) for x in rows], f'upits_{dataset}.csv')


# =========================================================================
# Audit trail
# =========================================================================
@router.get('/audit')
async def audit_logs(limit: int = Query(default=500, ge=1, le=2000)):
    pool=await get_pool()
    async with pool.acquire() as conn:
        rows=await conn.fetch('SELECT * FROM admin_audit_logs ORDER BY created_at DESC LIMIT $1', limit)
    return {'items':[dict(x) for x in rows]}


# =========================================================================
# Consent & data controls
# =========================================================================
@router.get('/consent/{passport_id}')
async def consent_status(passport_id: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow('''SELECT passport_id, full_name, consent_location, consent_privacy,
                                     consent_guardian, data_erased_at FROM students WHERE passport_id=$1''', passport_id)
    if not row: raise HTTPException(404, 'Student not found.')
    return dict(row)

@router.post('/consent/{passport_id}/erase')
async def erase_student_data(passport_id: str, staff_name: Optional[str] = Query(default=None)):
    """Right-to-erasure: blanks personal-identity fields but keeps the
    anonymised stamp/certificate history intact for event reporting."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow('''
            UPDATE students SET full_name='Erased', mobile_number=CONCAT('erased-',id::text),
                   email=NULL, teacher_name=NULL, teacher_mobile=NULL, data_erased_at=now()
            WHERE passport_id=$1 RETURNING passport_id, data_erased_at
        ''', passport_id)
    if not row: raise HTTPException(404, 'Student not found.')
    await audit('data_erasure', 'student', passport_id, staff_name=staff_name)
    return dict(row)


# =========================================================================
# Settings / configuration
# =========================================================================
@router.get('/settings')
async def get_settings():
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch('SELECT key, value FROM admin_settings')
    values = dict(DEFAULT_SETTINGS)
    for r in rows:
        values[r['key']] = json.loads(r['value']) if isinstance(r['value'], str) else r['value']
    return {'values': values}

@router.put('/settings')
async def update_settings(payload: SettingsPayload):
    pool = await get_pool()
    async with pool.acquire() as conn:
        for key, value in payload.values.items():
            await conn.execute('''
                INSERT INTO admin_settings(key, value) VALUES($1, $2::jsonb)
                ON CONFLICT (key) DO UPDATE SET value=$2::jsonb, updated_at=now()
            ''', key, json.dumps(value))
    await audit('settings_update', 'settings', None, payload.values)
    return await get_settings()