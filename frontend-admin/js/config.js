// UPITS 2026 Admin Portal — frontend-only deployment.
// This points the static admin UI at the standalone admin-backend
// (deployed separately, e.g. on Vercel or Render). It does NOT need to
// match the student backend's URL — they're two different services.
//window.UPITS_ADMIN_API_BASE = "https://your-admin-backend.vercel.app";
// Local dev against `uvicorn main:app --reload --port 8001`:
window.UPITS_ADMIN_API_BASE = "http://localhost:8001";
