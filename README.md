# UPITS 2026 — Admin Portal (standalone, separate from student backend)

Two independent services sharing **one** Postgres (Neon) database:

```
your student backend (upits-passport/backend)   ──┐
                                                    ├──►  same Neon DB
admin-backend/  (this bundle, new, own repo)    ──┘
```

They do not import each other's code and do not need to be deployed
together. This bundle has three parts:

```
admin-backend/            → deploy as its OWN project (Vercel, or Render
                             later if you outgrow serverless — see note
                             below). Contains only the admin API.
frontend-admin/           → deploy as its OWN static site (Vercel "Other"
                             framework preset, zero config needed).
patch-for-student-backend/→ NOT a separate deploy — a small patch to
                             apply inside your EXISTING student backend
                             repo. See "Why you still need this" below.
```

## 1. `admin-backend/` — deploy this on Vercel

```
admin-backend/
  api/index.py       ← Vercel entrypoint (imports main.py's `app`)
  main.py            ← FastAPI app, mounts routers/admin.py only
  database.py        ← own DB pool, points at the SAME DATABASE_URL
  admin_schema.sql   ← creates scan_logs, admin_settings, etc.
  vercel.json
  requirements.txt
  .env.example
  routers/admin.py
  utils/admin_audit.py, qr_token.py, cert_token.py
```

**Deploy steps:**
1. Push this folder as its own Git repo (or a subfolder — set Vercel's
   "Root Directory" to `admin-backend/` if you keep it in a monorepo).
2. In Vercel → Project Settings → Environment Variables, set:
   - `DATABASE_URL` — the **same** Neon connection string your student
     backend uses. Prefer Neon's pooled (`-pooler`) connection string —
     serverless functions open a fresh pool per cold start, and the
     direct (non-pooled) connection string runs out of slots faster
     under load. If you later move this off Vercel to Render (as you
     mentioned), the plain non-pooled string works fine there too, since
     Render keeps one long-running process.
   - `ADMIN_USERNAME` / `ADMIN_PASSWORD` — pick your own staff login for
     the admin portal. Not related to the student backend at all.
   - `ADMIN_SESSION_SECRET` — a long random string used only to sign the
     login session token. Generate one with
     `python -c "import secrets; print(secrets.token_hex(32))"`.
   - `CERT_TOKEN_SECRET` — **must be the exact same value** as the
     student backend's `.env`. This is the one thing genuinely shared
     between the two codebases: the student backend signs each
     completion QR with it, and this admin backend verifies that
     signature at the Redemption Desk / Certificates screen. Different
     values on each side = every redemption lookup fails.
3. Deploy. First deploy runs `admin_schema.sql` automatically (in
   `main.py`'s startup) — this only ALTERs/adds to tables, so it's safe
   to run repeatedly, but the student backend must have created the base
   tables (`students`, `checkpoints`, `stamps`, …) at least once already.

Local dev: `cd admin-backend && uvicorn main:app --reload --port 8001`.

## 2. `frontend-admin/` — deploy this as its own static site

Plain HTML/CSS/JS, no build step. On Vercel: New Project → point at this
folder → Framework Preset "Other" → Deploy. Before/after deploying, edit
`js/config.js` and set `window.UPITS_ADMIN_API_BASE` to whatever URL
Vercel gave the `admin-backend` deployment.

No authentication anywhere in this UI — same as you asked: it opens
straight to the dashboard, no login screen. The "Acting as" name field in
the sidebar is only a free-text label attached to audit-log entries, not
a real login.

## 3. `patch-for-student-backend/` — apply inside your EXISTING backend

**Why you still need this:** the Scan Monitoring and Fraud Review screens
in the admin portal read from a new `scan_logs` table — one row per QR
scan attempt, success or failure. That table only fills up if the
*student-facing* `/api/checkpoints/validate` endpoint writes to it, and
that endpoint lives in your existing student backend, not in the new
admin-backend. So:

```
patch-for-student-backend/routers/checkpoints.py  → replace your existing
                                                     backend/routers/checkpoints.py
patch-for-student-backend/utils/scan_log.py       → new file, add to
                                                     backend/utils/scan_log.py
patch-for-student-backend/admin_schema.sql        → replace your existing
                                                     backend/admin_schema.sql
                                                     (adds scan_logs, admin_settings,
                                                     students.data_erased_at)
```

If you skip this patch, everything else in the admin portal still works
— you just won't see any data on the Scan Monitoring / Fraud Review
screens, since nothing will be writing to `scan_logs`.

Also delete `backend/utils/admin_auth.py` from the student backend if
it's still there, and remove `ADMIN_PASSWORD` / `ADMIN_TOKEN_SECRET` /
`ADMIN_TOKEN_TTL_SECONDS` from its `.env` — nothing needs them anymore
now that the admin side has no login.

## What's intentionally NOT shared between the two services

- No shared Python package, no shared import. Both sides independently
  define their own tiny copies of `qr_token.py` / `cert_token.py` /
  `database.py` — small files, easier to keep two deploys fully
  independent than to publish a shared internal package for three files.
- Certificate "re-send" (in Certificates screen) re-issues the
  verification token and shows it to you; it does not send an email or
  WhatsApp message itself — wire that to your provider when ready.
- The kiosk `X-Staff-Key` header on the student backend's
  `/api/checkpoints/{id}/display-token` and `/api/certificate/redeem` is
  untouched — that protects the physical scan stands/redemption counter
  and is a separate concern from the admin portal's login (which you
  asked to remove entirely).