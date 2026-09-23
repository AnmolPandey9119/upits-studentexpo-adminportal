-- UPITS 2026 Admin additions. Safe to run repeatedly.
-- v2: no-auth admin portal — adds scan_logs (every QR+location attempt,
-- success or failure, for "QR & scan monitoring" and "Fraud review") and
-- admin_settings (key/value config used by the Settings screen).

CREATE TABLE IF NOT EXISTS admin_audit_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    action VARCHAR(80) NOT NULL,
    entity_type VARCHAR(80),
    entity_id VARCHAR(120),
    details JSONB,
    staff_name VARCHAR(120),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_admin_audit_created ON admin_audit_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_admin_audit_entity ON admin_audit_logs(entity_type, entity_id);
ALTER TABLE admin_audit_logs ADD COLUMN IF NOT EXISTS staff_name VARCHAR(120);

ALTER TABLE checkpoints ADD COLUMN IF NOT EXISTS max_accuracy_m INT DEFAULT 50;
ALTER TABLE checkpoints ADD COLUMN IF NOT EXISTS qr_rotated_at TIMESTAMPTZ;
ALTER TABLE checkpoints ADD COLUMN IF NOT EXISTS qr_expires_seconds INT DEFAULT 600;

-- Fallback manual entry code (BRD section 12: "QR scanner using mobile
-- camera, plus fallback code entry for technical failure"). One unique
-- 6-character alphanumeric code per checkpoint, printed below its QR.
ALTER TABLE checkpoints ADD COLUMN IF NOT EXISTS manual_code VARCHAR(6);
CREATE UNIQUE INDEX IF NOT EXISTS idx_checkpoints_manual_code ON checkpoints(manual_code) WHERE manual_code IS NOT NULL;

ALTER TABLE stamps ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ;
ALTER TABLE stamps ADD COLUMN IF NOT EXISTS reviewed_reason TEXT;
ALTER TABLE stamps ADD COLUMN IF NOT EXISTS reviewed_by VARCHAR(80);

-- Every /api/checkpoints/validate attempt — success AND failure — so the
-- admin portal can show "QR & scan monitoring" and "Fraud / anti-cheating
-- review" (failed attempts, unusual activity, impossible travel, mock
-- location) instead of only seeing attempts that became a stamp.
CREATE TABLE IF NOT EXISTS scan_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id UUID REFERENCES students(id) ON DELETE SET NULL,
    passport_id VARCHAR(20),
    checkpoint_id UUID REFERENCES checkpoints(id) ON DELETE SET NULL,
    hall_zone VARCHAR(100),
    result VARCHAR(20) NOT NULL CHECK (result IN ('success','failed')),
    reason VARCHAR(200),
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    accuracy_m DOUBLE PRECISION,
    is_mock_location BOOLEAN DEFAULT FALSE,
    fraud_flag VARCHAR(50),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_scan_logs_created ON scan_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_scan_logs_student ON scan_logs(student_id);
CREATE INDEX IF NOT EXISTS idx_scan_logs_checkpoint ON scan_logs(checkpoint_id);
CREATE INDEX IF NOT EXISTS idx_scan_logs_result ON scan_logs(result);

-- Simple key/value config store for the Settings screen (required stamp
-- counts, rate limits, default geofence, etc.) so those numbers can be
-- tuned on-ground without a redeploy.
CREATE TABLE IF NOT EXISTS admin_settings (
    key VARCHAR(80) PRIMARY KEY,
    value JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- "Consent & data controls": lets the portal mark a record as erased
-- (right-to-erasure) without breaking the stamp/certificate history the
-- event still needs for reporting.
ALTER TABLE students ADD COLUMN IF NOT EXISTS data_erased_at TIMESTAMPTZ;