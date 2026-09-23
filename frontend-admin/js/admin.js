// UPITS 2026 Admin Portal.
// A single shared staff login gates the whole portal — see
// admin-backend/utils/admin_auth.py. The session token lives in
// localStorage and is sent as `Authorization: Bearer <token>` on every
// /api/admin/* call. The "Acting as" free-text name in the sidebar is
// separate from login — it's only used to label who did what in the
// audit log.

const API_BASE = window.UPITS_ADMIN_API_BASE || "";
const TOKEN_KEY = "upits_admin_token";
const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[c]));
const n = (v) => Number(v || 0).toLocaleString("en-IN");
const fmt = (d) => (d ? new Date(d).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "—");

const state = { section: "dashboard", reasonCallback: null };

// ---------------------------------------------------------------
// Auth — token storage + login screen
// ---------------------------------------------------------------
function getToken() { return localStorage.getItem(TOKEN_KEY) || ""; }
function setToken(t) { localStorage.setItem(TOKEN_KEY, t); }
function clearToken() { localStorage.removeItem(TOKEN_KEY); }

function showLogin() {
  $("appRoot").classList.add("hidden");
  $("loginScreen").classList.remove("hidden");
}
function showApp() {
  $("loginScreen").classList.add("hidden");
  $("appRoot").classList.remove("hidden");
}

async function handleLogin(e) {
  e.preventDefault();
  const username = $("loginUsername").value.trim();
  const password = $("loginPassword").value;
  $("loginError").classList.add("hidden");
  try {
    const r = await fetch(API_BASE + "/api/admin/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || "Login failed.");
    setToken(d.token);
    $("loginPassword").value = "";
    showApp();
    startApp();
  } catch (err) {
    $("loginError").textContent = err.message;
    $("loginError").classList.remove("hidden");
  }
}

function handleLogout() {
  clearToken();
  location.reload();
}

// ---------------------------------------------------------------
// Core fetch helper — attaches the admin session token, redirects to
// the login screen on a 401 (missing/expired/invalid token).
// ---------------------------------------------------------------
async function api(path, opt = {}) {
  const headers = { "Content-Type": "application/json", Authorization: "Bearer " + getToken(), ...(opt.headers || {}) };
  const r = await fetch(API_BASE + path, { ...opt, headers });
  if (r.status === 401) {
    clearToken();
    showLogin();
    throw new Error("Session expired. Please log in again.");
  }
  let d = {};
  try { d = await r.json(); } catch {}
  if (!r.ok) throw new Error(d.detail || "Request failed");
  return d;
}

function staffName() {
  return ($("staffName").value || "").trim() || undefined;
}

function toast(msg, isErr) {
  const t = document.createElement("div");
  t.className = "toast" + (isErr ? " err" : "");
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3800);
}

async function downloadCsv(dataset) {
  // A plain window.open() can't send the Authorization header, and this
  // endpoint is login-protected now — so fetch it as a blob instead and
  // trigger the download from that.
  try {
    const r = await fetch(API_BASE + "/api/admin/export/" + dataset, {
      headers: { Authorization: "Bearer " + getToken() },
    });
    if (r.status === 401) { clearToken(); showLogin(); return; }
    if (!r.ok) throw new Error("Export failed.");
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = dataset + ".csv";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  } catch (err) {
    toast(err.message, true);
  }
}

// ---------------------------------------------------------------
// Navigation
// ---------------------------------------------------------------
const TITLE_KEY = {
  dashboard: "title_dashboard", students: "title_students", checkpoints: "title_checkpoints",
  reviews: "title_reviews", scans: "title_scans", fraud: "title_fraud", certificates: "title_certificates",
  redemption: "title_redemption", feedback: "title_feedback", institutions: "title_institutions",
  footfall: "title_footfall", exports: "title_exports", audit: "title_audit", consent: "title_consent",
  settings: "title_settings",
};

const RENDERERS = {
  dashboard: renderDashboard, students: renderStudents, checkpoints: renderCheckpoints,
  reviews: renderReviews, scans: renderScans, fraud: renderFraud, certificates: renderCertificates,
  redemption: renderRedemption, feedback: renderFeedback, institutions: renderInstitutions,
  footfall: renderFootfall, exports: renderExports, audit: renderAudit, consent: renderConsent,
  settings: renderSettings,
};

function loadSection(section) {
  state.section = section;
  document.querySelectorAll(".nav").forEach((b) => b.classList.toggle("active", b.dataset.section === section));
  $("pageTitle").textContent = window.upitsAdminI18n.t(TITLE_KEY[section] || section);
  $("content").innerHTML = '<div class="card muted">Loading…</div>';
  Promise.resolve(RENDERERS[section]()).catch((x) => {
    $("content").innerHTML = `<div class="card"><div class="error">${esc(x.message)}</div></div>`;
  });
}

window.onLangChanged = () => loadSection(state.section);

function startApp() {
  refreshBadges();
  loadSection("dashboard");
  setInterval(refreshBadges, 30000);
}

document.addEventListener("DOMContentLoaded", () => {
  window.upitsAdminI18n.init();
  document.querySelectorAll(".nav").forEach((b) => b.addEventListener("click", () => loadSection(b.dataset.section)));
  $("checkpointForm").addEventListener("submit", saveCheckpoint);
  $("loginForm").addEventListener("submit", handleLogin);
  $("logoutBtn").addEventListener("click", handleLogout);

  if (getToken()) {
    showApp();
    startApp();
  } else {
    showLogin();
  }
});

async function refreshBadges() {
  try {
    const d = await api("/api/admin/dashboard");
    setBadge("badgeReviews", d.metrics.pending_reviews);
    setBadge("badgeFraud", d.metrics.fraud_flagged_stamps);
  } catch {}
}
function setBadge(id, val) {
  const el = $(id); if (!el) return;
  el.textContent = n(val); el.classList.toggle("zero", !val);
}

// =================================================================
// DASHBOARD
// =================================================================
async function renderDashboard() {
  const d = await api("/api/admin/dashboard");
  const m = d.metrics;
  $("content").innerHTML = `
    <div class="metrics">
      <div class="metric"><span>Registrations</span><strong>${n(m.registrations)}</strong></div>
      <div class="metric green"><span>Active Passports</span><strong>${n(m.active_passports)}</strong></div>
      <div class="metric saffron"><span>Registered Today</span><strong>${n(m.registrations_today)}</strong></div>
      <div class="metric gold"><span>Scans Today</span><strong>${n(m.scans_today)}</strong></div>
      <div class="metric green"><span>Approved Stamps</span><strong>${n(m.approved_stamps)}</strong></div>
      <div class="metric saffron"><span>Pending Review</span><strong>${n(m.pending_reviews)}</strong></div>
      <div class="metric danger"><span>Rejected Stamps</span><strong>${n(m.rejected_stamps)}</strong></div>
      <div class="metric danger"><span>Fraud Flagged</span><strong>${n(m.fraud_flagged_stamps)}</strong></div>
      <div class="metric"><span>Certificates Issued</span><strong>${n(m.certificates)}</strong></div>
      <div class="metric green"><span>Redeemed</span><strong>${n(m.redeemed)}</strong></div>
      <div class="metric gold"><span>Prize Eligible</span><strong>${n(m.prize_eligible)}</strong></div>
      <div class="metric gold"><span>Priority Draw Eligible</span><strong>${n(m.priority_draw_eligible)}</strong></div>
    </div>
    <div class="grid-2">
      <div class="card">
        <div class="card-head"><h3>Checkpoint Activity</h3><button class="btn small" onclick="loadSection('dashboard')">Refresh</button></div>
        <div class="table-wrap"><table class="data-table"><thead><tr><th>Checkpoint</th><th>Hall</th><th>Approved</th><th>Pending</th><th>Rejected</th><th>Active</th></tr></thead><tbody>
          ${d.checkpoints.map((c) => `<tr>
            <td>${c.is_bonus ? "Bonus " + c.stamp_number : "Stamp " + c.stamp_number}</td>
            <td>${esc(c.hall_zone)}</td>
            <td><span class="status ok">${n(c.approved)}</span></td>
            <td><span class="status warn">${n(c.pending)}</span></td>
            <td><span class="status bad">${n(c.rejected)}</span></td>
            <td><span class="status ${c.is_active ? "ok" : "bad"}">${c.is_active ? "ACTIVE" : "OFF"}</span></td>
          </tr>`).join("")}
        </tbody></table></div>
      </div>
      <div class="card">
        <h3>Route Load</h3>
        ${d.route_load.map((r) => `<div style="display:flex;justify-content:space-between;padding:9px 0;border-bottom:1px solid #eee9df">
          <span class="muted">${esc(r.route_colour)}</span><b>${n(r.students)} students</b></div>`).join("")}
        <h3 style="margin-top:16px">System Snapshot</h3>
        <div style="display:flex;justify-content:space-between;padding:9px 0;border-bottom:1px solid #eee9df"><span class="muted">Failed scans today</span><b>${n(m.failed_scans_today)}</b></div>
        <div style="display:flex;justify-content:space-between;padding:9px 0"><span class="muted">Feedback responses</span><b>${n(m.feedback_responses)}</b></div>
      </div>
    </div>`;
}

// =================================================================
// STUDENTS
// =================================================================
async function renderStudents() {
  $("content").innerHTML = `
    <div class="section-head"><div><h3>Student Directory</h3><p>Search by name, Passport ID, mobile or institution.</p></div></div>
    <div class="search" style="margin-bottom:14px"><input id="studentSearch" placeholder="Search students…" onkeydown="if(event.key==='Enter')studentSearch()"><button class="btn primary" onclick="studentSearch()">Search</button></div>
    <div id="studentTable" class="card table-wrap">Loading…</div>`;
  await studentSearch();
}

async function studentSearch() {
  const q = $("studentSearch") ? $("studentSearch").value : "";
  const d = await api("/api/admin/students?search=" + encodeURIComponent(q || ""));
  $("studentTable").innerHTML = `<table class="data-table"><thead><tr><th>Passport</th><th>Name</th><th>Institution</th><th>Route</th><th>Stamps</th><th>Pending</th><th>Status</th><th></th></tr></thead><tbody>
    ${d.items.map((s) => `<tr>
      <td><b>${esc(s.passport_id)}</b></td>
      <td>${esc(s.full_name)}<br><small class="muted">${esc(s.mobile_number)}</small></td>
      <td>${esc(s.institution_name)}</td>
      <td>${esc(s.route_colour || "—")}</td>
      <td>${n(s.verified_stamps)} + ${n(s.bonus_stamps)} bonus</td>
      <td>${n(s.pending_stamps)}</td>
      <td><span class="status ${s.registration_status === "active" ? "ok" : "bad"}">${esc(s.registration_status)}</span></td>
      <td><button class="btn small" onclick="openStudent('${s.passport_id}')">View</button></td>
    </tr>`).join("") || `<tr><td colspan="8" class="muted">No students found.</td></tr>`}
  </tbody></table>`;
}

async function openStudent(passportId) {
  const d = await api("/api/admin/students/" + encodeURIComponent(passportId));
  const s = d.student;
  $("studentModalBody").innerHTML = `
    <div class="detail-grid">
      <div><span>Passport ID</span><b>${esc(s.passport_id)}</b></div>
      <div><span>Status</span><b>${esc(s.registration_status)}</b></div>
      <div><span>Name</span><b>${esc(s.full_name)}</b></div>
      <div><span>Mobile</span><b>${esc(s.mobile_number)}</b></div>
      <div><span>Institution</span><b>${esc(s.institution_name)}</b></div>
      <div><span>District / City</span><b>${esc(s.district_city)}</b></div>
      <div><span>Category</span><b>${esc(s.student_category)} — ${esc(s.class_or_course)}</b></div>
      <div><span>Route</span><b>${esc(s.route_colour || "—")}</b></div>
    </div>
    <div style="display:flex;gap:8px;margin:10px 0 16px">
      <button class="btn small" onclick="setStudentStatus('${s.passport_id}','${s.registration_status === "active" ? "blocked" : "active"}')">
        ${s.registration_status === "active" ? "Block Passport" : "Unblock Passport"}
      </button>
      <button class="btn small" onclick="closeStudentModal();loadSection('consent')">Open Consent & Data</button>
    </div>
    <h3>Stamps (${d.stamps.length})</h3>
    <div class="table-wrap"><table class="data-table"><thead><tr><th>#</th><th>Hall</th><th>Status</th><th>Fraud</th><th>Time</th></tr></thead><tbody>
      ${d.stamps.map((st) => `<tr><td>${st.is_bonus ? "B" + st.stamp_number : st.stamp_number}</td><td>${esc(st.hall_zone)}</td>
        <td><span class="status ${st.status === "approved" ? "ok" : st.status === "rejected" ? "bad" : "warn"}">${esc(st.status)}</span></td>
        <td>${st.fraud_flag ? `<span class="status bad">${esc(st.fraud_flag)}</span>` : "—"}</td><td>${fmt(st.created_at)}</td></tr>`).join("") || `<tr><td colspan="5" class="muted">No stamps yet.</td></tr>`}
    </tbody></table></div>
    ${d.certificate ? `<h3 style="margin-top:16px">Certificate</h3><div class="detail-grid">
      <div><span>Serial</span><b>${esc(d.certificate.serial_number)}</b></div>
      <div><span>Redeemed</span><b>${d.certificate.redeemed ? "Yes — " + fmt(d.certificate.redeemed_at) : "Not yet"}</b></div>
    </div>` : ""}`;
  $("studentModal").classList.remove("hidden");
}
function closeStudentModal() { $("studentModal").classList.add("hidden"); }

async function setStudentStatus(passportId, status) {
  openReason(status === "blocked" ? "Block passport" : "Unblock passport", "Reason", async (reason) => {
    await api(`/api/admin/students/${encodeURIComponent(passportId)}/status`, {
      method: "PATCH", body: JSON.stringify({ status, reason, staff_name: staffName() }),
    });
    toast("Passport status updated.");
    closeStudentModal();
    loadSection("students");
  });
}

// =================================================================
// CHECKPOINTS & QR
// =================================================================
let checkpointCache = [];
async function renderCheckpoints() {
  const d = await api("/api/admin/checkpoints");
  checkpointCache = d.items;
  $("content").innerHTML = `
    <div class="section-head">
      <div><h3>Checkpoint Management</h3><p>Create and configure every hall checkpoint, its geofence and its QR without any code changes.</p></div>
      <div style="display:flex;gap:8px">
        ${d.items.length === 0 ? `<button class="btn" onclick="seedCheckpoints()">Seed Official 15 Checkpoints</button>` : ""}
        <button class="btn primary" onclick="openCheckpointModal()">+ New Checkpoint</button>
      </div>
    </div>
    ${d.items.length === 0 ? `<div class="card"><p class="muted" style="margin:0">No checkpoints yet. Click <b>Seed Official 15 Checkpoints</b> to create the 10 compulsory + 5 bonus hall checkpoints from the BRD in one go (each gets its own QR), then edit each one to set its GPS latitude/longitude from the on-site survey before going live.</p></div>` : ""}
    <div class="card table-wrap"><table class="data-table"><thead><tr><th>#</th><th>Hall</th><th>Theme</th><th>Type</th><th>Geo</th><th>Active</th><th>Responses</th><th></th></tr></thead><tbody>
      ${d.items.map((c) => `<tr>
        <td>${c.is_bonus ? "B" + c.stamp_number : c.stamp_number}</td>
        <td>${esc(c.hall_zone)}</td><td>${esc(c.theme)}</td><td>${esc(c.answer_type)}</td>
        <td>${c.latitude != null ? c.geofence_radius_m + "m" : `<span class="status warn">Set GPS</span>`}</td>
        <td><span class="status ${c.is_active ? "ok" : "bad"}">${c.is_active ? "ACTIVE" : "OFF"}</span></td>
        <td><button class="btn small" onclick="viewCheckpointResponses('${c.id}')">View</button></td>
        <td style="white-space:nowrap">
          <button class="btn small" onclick="openCheckpointModal('${c.id}')">Edit</button>
          <button class="btn small" onclick="showQr('${c.id}')">QR</button>
          <button class="btn small" onclick="rotateQr('${c.id}')">Rotate</button>
          <button class="btn small" onclick="toggleCheckpoint('${c.id}',${!c.is_active})">${c.is_active ? "Disable" : "Enable"}</button>
        </td>
      </tr>`).join("")}
    </tbody></table></div>`;
}

function viewCheckpointResponses(checkpointId) {
  state.section = "reviews";
  document.querySelectorAll(".nav").forEach((b) => b.classList.toggle("active", b.dataset.section === "reviews"));
  $("pageTitle").textContent = window.upitsAdminI18n.t(TITLE_KEY.reviews || "reviews");
  $("content").innerHTML = '<div class="card muted">Loading…</div>';
  renderReviews("all", checkpointId).catch((x) => { $("content").innerHTML = `<div class="card"><div class="error">${esc(x.message)}</div></div>`; });
}

async function seedCheckpoints() {
  try {
    const d = await api("/api/admin/checkpoints/seed-default" + (staffName() ? "?staff_name=" + encodeURIComponent(staffName()) : ""), { method: "POST" });
    toast(`${d.created.length} checkpoints created${d.skipped_existing.length ? `, ${d.skipped_existing.length} already existed` : ""}.`);
    loadSection("checkpoints");
  } catch (x) { toast(x.message, true); }
}

function openCheckpointModal(id) {
  $("formError").classList.add("hidden");
  $("checkpointForm").reset();
  $("cpId").value = id || "";
  $("modalTitle").textContent = id ? "Edit Checkpoint" : "New Checkpoint";
  if (id) {
    const c = checkpointCache.find((x) => x.id === id);
    if (c) {
      $("stampNumber").value = c.stamp_number; $("isBonus").value = String(c.is_bonus);
      $("hallZone").value = c.hall_zone; $("theme").value = c.theme; $("questionText").value = c.question_text;
      $("answerType").value = c.answer_type; $("minLength").value = c.min_answer_length || 0;
      $("answerOptions").value = (c.answer_options || []).join(", ");
      $("latitude").value = c.latitude ?? ""; $("longitude").value = c.longitude ?? "";
      $("radius").value = c.geofence_radius_m; $("maxAccuracy").value = c.max_accuracy_m;
      $("qrExpiry").value = c.qr_expires_seconds; $("isActive").value = String(c.is_active);
    }
  } else {
    $("radius").value = 40; $("maxAccuracy").value = 50; $("qrExpiry").value = 600; $("minLength").value = 0;
  }
  $("checkpointModal").classList.remove("hidden");
}
function closeModal() { $("checkpointModal").classList.add("hidden"); }

async function saveCheckpoint(e) {
  e.preventDefault();
  $("formError").classList.add("hidden");
  const opts = $("answerOptions").value.trim();
  const payload = {
    stamp_number: Number($("stampNumber").value), is_bonus: $("isBonus").value === "true",
    hall_zone: $("hallZone").value, theme: $("theme").value, question_text: $("questionText").value,
    answer_type: $("answerType").value, answer_options: opts ? opts.split(",").map((s) => s.trim()).filter(Boolean) : null,
    min_answer_length: Number($("minLength").value || 0),
    latitude: $("latitude").value ? Number($("latitude").value) : null,
    longitude: $("longitude").value ? Number($("longitude").value) : null,
    geofence_radius_m: Number($("radius").value), max_accuracy_m: Number($("maxAccuracy").value),
    qr_expires_seconds: Number($("qrExpiry").value), is_active: $("isActive").value === "true",
  };
  try {
    const id = $("cpId").value;
    if (id) await api("/api/admin/checkpoints/" + id, { method: "PUT", body: JSON.stringify(payload) });
    else await api("/api/admin/checkpoints", { method: "POST", body: JSON.stringify(payload) });
    closeModal(); toast("Checkpoint saved."); loadSection("checkpoints");
  } catch (x) { $("formError").textContent = x.message; $("formError").classList.remove("hidden"); }
}

async function toggleCheckpoint(id, active) {
  await api(`/api/admin/checkpoints/${id}/status?active=${active}`, { method: "PATCH" });
  toast(active ? "Checkpoint enabled." : "Checkpoint disabled."); loadSection("checkpoints");
}

async function showQr(id) {
  const d = await api(`/api/admin/checkpoints/${id}/qr-token`);
  $("qrBox").innerHTML = "";
  new QRCode($("qrBox"), { text: d.token, width: 220, height: 220 });
  $("qrMeta").innerHTML = `Expires: ${new Date(d.expires_at * 1000).toLocaleTimeString()}<br><span class="mono" style="font-size:10px;word-break:break-all">${esc(d.token)}</span>`;
  $("qrModal").classList.remove("hidden");
}
function closeQR() { $("qrModal").classList.add("hidden"); }
function printQR() { window.print(); }

async function rotateQr(id) {
  const d = await api(`/api/admin/checkpoints/${id}/rotate-qr`, { method: "POST" });
  toast("QR secret rotated. Fetching new QR…");
  showQr(id);
}

// =================================================================
// ANSWER REVIEW
// =================================================================
let reviewCache = [];
let reviewCheckpointFilter = null;
async function renderReviews(status, checkpointId) {
  status = status || "pending";
  reviewCheckpointFilter = checkpointId !== undefined ? checkpointId : reviewCheckpointFilter;
  const d = await api("/api/admin/reviews?status=" + status);
  const items = reviewCheckpointFilter ? d.items.filter((r) => r.checkpoint_id === reviewCheckpointFilter) : d.items;
  reviewCache = items;
  $("content").innerHTML = `
    <div class="section-head"><div><h3>Checkpoint Answer Review</h3><p>Every submitted answer stays pending until an admin marks it correct, incorrect, or as a manual override stamp.</p></div><button class="btn small" onclick="renderReviews('${status}')">Refresh</button></div>
    <div class="pill-tabs">
      ${["pending", "manual", "rejected", "approved", "all"].map((s) => `<button class="${s === status ? "active" : ""}" onclick="renderReviews('${s}')">${s[0].toUpperCase() + s.slice(1)}</button>`).join("")}
      ${reviewCheckpointFilter ? `<button onclick="reviewCheckpointFilter=null;renderReviews('${status}')">✕ Clear checkpoint filter</button>` : ""}
    </div>
    <div class="card table-wrap"><table class="data-table"><thead><tr><th>Passport</th><th>Student</th><th>Hall</th><th>Answer</th><th>Fraud</th><th>Time</th><th></th></tr></thead><tbody>
      ${items.map((r) => `<tr>
        <td>${esc(r.passport_id)}</td><td>${esc(r.full_name)}<br><small class="muted">${esc(r.institution_name)}</small></td>
        <td>${esc(r.hall_zone)}</td>
        <td style="white-space:normal;max-width:260px">${esc(r.answer_text)}</td>
        <td>${r.fraud_flag ? `<span class="status bad">${esc(r.fraud_flag)}</span>` : "—"}</td>
        <td>${fmt(r.created_at)}</td>
        <td style="white-space:nowrap">
          <button class="btn small" onclick="viewResponse('${r.id}')">View</button>
          ${status !== "approved" ? `<button class="btn small" onclick="reviewAction('${r.id}','approve')">✓ Correct</button>` : ""}
          ${status !== "rejected" ? `<button class="btn small" onclick="reviewAction('${r.id}','reject')">✗ Incorrect</button>` : ""}
          ${status !== "manual" ? `<button class="btn small" onclick="reviewAction('${r.id}','manual')">Manual</button>` : ""}
        </td>
      </tr>`).join("") || `<tr><td colspan="7" class="muted">Nothing here.</td></tr>`}
    </tbody></table></div>`;
}

function viewResponse(stampId) {
  const r = reviewCache.find((x) => x.id === stampId) || [];
  if (!r || !r.id) return;
  $("responseModalBody").innerHTML = `
    <div class="detail-grid">
      <div><span>Passport ID</span><b>${esc(r.passport_id)}</b></div>
      <div><span>Student</span><b>${esc(r.full_name)}</b></div>
      <div><span>Institution</span><b>${esc(r.institution_name)}</b></div>
      <div><span>Checkpoint</span><b>${r.is_bonus ? "Bonus " + r.stamp_number : "Stamp " + r.stamp_number} — ${esc(r.hall_zone)}</b></div>
      <div><span>Theme</span><b>${esc(r.theme)}</b></div>
      <div><span>Status</span><b>${esc(r.status)}</b></div>
      <div><span>Scanned At</span><b>${fmt(r.created_at)}</b></div>
      <div><span>GPS Accuracy</span><b>${r.scan_accuracy_m != null ? Math.round(r.scan_accuracy_m) + " m" : "—"}</b></div>
      <div><span>Fraud Flag</span><b>${r.fraud_flag ? esc(r.fraud_flag) : "None"}</b></div>
      <div><span>Location</span><b>${r.scan_latitude != null ? r.scan_latitude.toFixed(5) + ", " + r.scan_longitude.toFixed(5) : "—"}</b></div>
    </div>
    <label style="margin-top:4px">Question Asked</label>
    <p class="muted" style="margin:0 0 10px">${esc(r.question_text)}</p>
    <label>Student's Answer</label>
    <p style="margin:0 0 10px;white-space:pre-wrap">${esc(r.answer_text)}</p>
    ${r.unique_observation ? `<label>Something Unique They Noticed</label><p style="margin:0 0 10px;white-space:pre-wrap">${esc(r.unique_observation)}</p>` : ""}
    ${r.suggestion_feedback ? `<label>Suggestion / Feedback</label><p style="margin:0 0 10px;white-space:pre-wrap">${esc(r.suggestion_feedback)}</p>` : ""}
    ${r.reviewed_by ? `<p class="muted" style="font-size:11px;margin-top:10px">Last reviewed by ${esc(r.reviewed_by)} on ${fmt(r.reviewed_at)} — "${esc(r.reviewed_reason || "")}"</p>` : ""}
    <div class="modal-actions" style="justify-content:flex-start">
      ${r.status !== "approved" ? `<button class="btn primary" onclick="closeResponseModal();reviewAction('${r.id}','approve')">✓ Mark Correct</button>` : ""}
      ${r.status !== "rejected" ? `<button class="btn danger" onclick="closeResponseModal();reviewAction('${r.id}','reject')">✗ Mark Incorrect</button>` : ""}
      ${r.status !== "manual" ? `<button class="btn" onclick="closeResponseModal();reviewAction('${r.id}','manual')">Manual Stamp</button>` : ""}
    </div>`;
  $("responseModal").classList.remove("hidden");
}
function closeResponseModal() { $("responseModal").classList.add("hidden"); }

function reviewAction(stampId, action) {
  const titles = { approve: "Approve stamp", reject: "Reject stamp", manual: "Mark as manual stamp" };
  openReason(titles[action], "Reason (required)", async (reason) => {
    await api(`/api/admin/reviews/${stampId}`, { method: "POST", body: JSON.stringify({ action, reason, staff_name: staffName() }) });
    toast("Review recorded."); loadSection("reviews"); refreshBadges();
  });
}

// =================================================================
// SCAN MONITORING
// =================================================================
async function renderScans(result) {
  const d = await api("/api/admin/scans" + (result ? "?result=" + result : ""));
  $("content").innerHTML = `
    <div class="section-head"><div><h3>QR & Scan Monitoring</h3><p>Every checkpoint scan attempt, successful or failed, in real time.</p></div><button class="btn small" onclick="renderScans('${result || ""}')">Refresh</button></div>
    <div class="metrics" style="grid-template-columns:repeat(3,1fr)">
      <div class="metric green"><span>Successful</span><strong>${n(d.summary.success)}</strong></div>
      <div class="metric danger"><span>Failed</span><strong>${n(d.summary.failed)}</strong></div>
      <div class="metric gold"><span>Last 15 minutes</span><strong>${n(d.summary.last_15_min)}</strong></div>
    </div>
    <div class="pill-tabs">
      ${["", "success", "failed"].map((s) => `<button class="${s === (result || "") ? "active" : ""}" onclick="renderScans('${s}')">${s === "" ? "All" : s[0].toUpperCase() + s.slice(1)}</button>`).join("")}
    </div>
    <div class="card table-wrap"><table class="data-table"><thead><tr><th>Time</th><th>Passport</th><th>Checkpoint</th><th>Result</th><th>Reason</th><th>Accuracy</th></tr></thead><tbody>
      ${d.items.map((s) => `<tr><td>${fmt(s.created_at)}</td><td>${esc(s.passport_id || "—")}</td>
        <td>${s.stamp_number != null ? (s.is_bonus ? "B" + s.stamp_number : s.stamp_number) + " — " + esc(s.theme || "") : "—"}</td>
        <td><span class="status ${s.result === "success" ? "ok" : "bad"}">${esc(s.result)}</span></td>
        <td>${esc(s.reason || "—")}</td><td>${s.accuracy_m != null ? esc(s.accuracy_m) + "m" : "—"}</td></tr>`).join("") || `<tr><td colspan="6" class="muted">No scans logged yet.</td></tr>`}
    </tbody></table></div>`;
}

// =================================================================
// FRAUD REVIEW
// =================================================================
async function renderFraud() {
  const d = await api("/api/admin/fraud");
  $("content").innerHTML = `
    <div class="section-head"><div><h3>Fraud / Anti-Cheating Review</h3><p>Impossible-travel patterns, mock-location flags and repeat failed attempts.</p></div><button class="btn small" onclick="loadSection('fraud')">Refresh</button></div>
    <div class="metrics" style="grid-template-columns:repeat(2,1fr)">
      <div class="metric danger"><span>Mock Location Attempts</span><strong>${n(d.mock_location_attempts)}</strong></div>
      <div class="metric danger"><span>Impossible Travel Flags</span><strong>${n(d.impossible_travel_attempts)}</strong></div>
    </div>
    <div class="grid-2">
      <div class="card">
        <h3>Flagged Stamps</h3>
        <div class="table-wrap"><table class="data-table"><thead><tr><th>Passport</th><th>Student</th><th>Hall</th><th>Flag</th><th>Time</th></tr></thead><tbody>
          ${d.flagged_stamps.map((f) => `<tr><td>${esc(f.passport_id)}</td><td>${esc(f.full_name)}</td><td>${esc(f.hall_zone)}</td>
            <td><span class="status bad">${esc(f.fraud_flag)}</span></td><td>${fmt(f.created_at)}</td></tr>`).join("") || `<tr><td colspan="5" class="muted">None flagged.</td></tr>`}
        </tbody></table></div>
      </div>
      <div class="card">
        <h3>Repeat Failed Attempts (24h)</h3>
        <div class="table-wrap"><table class="data-table"><thead><tr><th>Passport</th><th>Failed Attempts</th></tr></thead><tbody>
          ${d.repeat_offenders.map((r) => `<tr><td>${esc(r.passport_id)}</td><td><span class="status warn">${n(r.failed_attempts)}</span></td></tr>`).join("") || `<tr><td colspan="2" class="muted">None.</td></tr>`}
        </tbody></table></div>
        <h3 style="margin-top:14px">Top Failure Reasons (24h)</h3>
        ${d.failure_reasons.map((r) => `<div style="display:flex;justify-content:space-between;padding:7px 0;border-bottom:1px solid #eee9df"><span class="muted">${esc(r.reason)}</span><b>${n(r.attempts)}</b></div>`).join("") || `<p class="muted">No failures logged.</p>`}
      </div>
    </div>`;
}

// =================================================================
// CERTIFICATES
// =================================================================
async function renderCertificates(status) {
  const d = await api("/api/admin/certificates" + (status ? "?status=" + status : ""));
  $("content").innerHTML = `
    <div class="section-head"><div><h3>Certificate Management</h3><p>Serial numbers, verification tokens and re-send.</p></div><button class="btn small" onclick="renderCertificates('${status || ""}')">Refresh</button></div>
    <div class="pill-tabs">
      ${[["", "All"], ["not_redeemed", "Not Redeemed"], ["redeemed", "Redeemed"], ["prize_eligible", "Prize Eligible"]].map(([v, l]) => `<button class="${v === (status || "") ? "active" : ""}" onclick="renderCertificates('${v}')">${l}</button>`).join("")}
    </div>
    <div class="card table-wrap"><table class="data-table"><thead><tr><th>Serial</th><th>Passport</th><th>Name</th><th>Stamps</th><th>Prize</th><th>Priority Draw</th><th>Redeemed</th><th></th></tr></thead><tbody>
      ${d.items.map((c) => `<tr><td class="mono">${esc(c.serial_number)}</td><td>${esc(c.passport_id)}</td><td>${esc(c.full_name)}</td>
        <td>${n(c.total_stamps)}</td><td>${c.prize_eligible ? "Yes" : "—"}</td><td>${c.priority_draw_eligible ? "Yes" : "—"}</td>
        <td><span class="status ${c.redeemed ? "ok" : "warn"}">${c.redeemed ? fmt(c.redeemed_at) : "Pending"}</span></td>
        <td><button class="btn small" onclick="resendCertificate('${c.id}')">Re-send</button></td></tr>`).join("") || `<tr><td colspan="8" class="muted">No certificates issued yet.</td></tr>`}
    </tbody></table></div>`;
}

async function resendCertificate(id) {
  const d = await api(`/api/admin/certificates/${id}/resend`, { method: "POST" });
  toast(`Token re-issued for ${d.passport_id}. Share manually via email/WhatsApp — no auto-send configured.`);
}

// =================================================================
// REDEMPTION DESK
// =================================================================
async function renderRedemption() {
  $("content").innerHTML = `
    <div class="grid-2">
      <div class="card">
        <div class="eyebrow">PASSPORT REDEMPTION</div>
        <h3>Verify / Redeem Completion QR</h3>
        <p class="muted">Paste the completion token from the student's certificate screen.</p>
        <label>Completion Token</label>
        <input id="completionToken" class="mono" placeholder="Paste completion token">
        <div style="display:flex;gap:8px;margin-top:12px">
          <button class="btn" onclick="lookupRedemption()">Verify Eligibility</button>
          <button class="btn primary" onclick="doRedeem()">Redeem Now</button>
        </div>
        <div id="redeemResult"></div>
      </div>
      <div class="card">
        <h3>Redemption Rules</h3>
        <ul class="rules">
          <li>10 verified mandatory stamps are required for the certificate.</li>
          <li>Final feedback must be submitted.</li>
          <li>12+ verified stamps unlock priority draw eligibility when theme conditions are met.</li>
          <li>Each certificate can be redeemed only once — enforced server-side.</li>
        </ul>
      </div>
    </div>`;
}

async function lookupRedemption() {
  const token = $("completionToken").value.trim();
  if (!token) return;
  try {
    const c = await api("/api/admin/redemption/lookup?token=" + encodeURIComponent(token));
    $("redeemResult").innerHTML = `<div class="notice">
      <b>${esc(c.passport_id)}</b> — ${esc(c.full_name)}<br>Institution: ${esc(c.institution_name)}<br>
      Stamps: ${n(c.total_stamps)} · Prize eligible: ${c.prize_eligible ? "Yes" : "No"} · Priority draw: ${c.priority_draw_eligible ? "Yes" : "No"}<br>
      Redeemed: ${c.redeemed ? "Yes, " + fmt(c.redeemed_at) : "Not yet"}</div>`;
  } catch (x) { $("redeemResult").innerHTML = `<div class="error">${esc(x.message)}</div>`; }
}

async function doRedeem() {
  const token = $("completionToken").value.trim();
  if (!token) return;
  try {
    const r = await api("/api/admin/redemption/redeem", { method: "POST", body: JSON.stringify({ completion_token: token, staff_name: staffName() }) });
    $("redeemResult").innerHTML = `<div class="notice">Redeemed for <b>${esc(r.passport_id)}</b> (${esc(r.student_name)}) at ${fmt(r.redeemed_at)}.</div>`;
  } catch (x) { $("redeemResult").innerHTML = `<div class="error">${esc(x.message)}</div>`; }
}

// =================================================================
// FEEDBACK ANALYTICS
// =================================================================
function bar(obj) {
  const max = Math.max(1, ...Object.values(obj));
  return Object.entries(obj).map(([k, v]) => `<div style="display:flex;align-items:center;gap:8px;margin:6px 0">
    <span class="muted" style="width:150px;font-size:11px">${esc(k.replaceAll("_", " "))}</span>
    <div style="flex:1;background:#eee9df;border-radius:6px;overflow:hidden;height:12px"><div style="width:${(v / max) * 100}%;background:var(--saffron);height:100%"></div></div>
    <b style="width:30px;text-align:right">${n(v)}</b></div>`).join("");
}

async function renderFeedback() {
  const d = await api("/api/admin/feedback");
  $("content").innerHTML = `
    <div class="section-head"><div><h3>Feedback & Questionnaire Analytics</h3><p>Responses collected after passport completion.</p></div><button class="btn small" onclick="loadSection('feedback')">Refresh</button></div>
    <div class="grid-2">
      <div class="card"><h3>Overall Rating</h3>${bar(d.ratings)}</div>
      <div class="card"><h3>Would Recommend</h3>${bar(d.would_recommend)}</div>
    </div>
    <div class="grid-2">
      <div class="card"><h3>Favourite Theme</h3>${bar(d.favourite_themes)}</div>
      <div class="card"><h3>Passport Helped Explore More</h3>${bar(d.passport_helped_explore)}</div>
    </div>
    <div class="card table-wrap"><h3>Recent Responses (${d.items.length})</h3><table class="data-table"><thead><tr><th>Passport</th><th>Rating</th><th>Theme</th><th>Recommend</th><th>Time</th></tr></thead><tbody>
      ${d.items.slice(0, 200).map((x) => `<tr><td>${esc(x.passport_id)}</td><td>${esc(x.overall_rating)}</td><td>${esc(x.favourite_theme)}</td><td>${esc(x.would_recommend)}</td><td>${fmt(x.created_at)}</td></tr>`).join("") || `<tr><td colspan="5" class="muted">No responses yet.</td></tr>`}
    </tbody></table></div>`;
}

// =================================================================
// INSTITUTIONS
// =================================================================
async function renderInstitutions() {
  const d = await api("/api/admin/institutions");
  $("content").innerHTML = `
    <div class="section-head"><div><h3>School / College Analytics</h3><p>Leaderboard based on verified completions, not just registrations.</p></div><button class="btn small" onclick="loadSection('institutions')">Refresh</button></div>
    <div class="card table-wrap"><table class="data-table"><thead><tr><th>#</th><th>Institution</th><th>Registrations</th><th>Completions</th><th>Completion %</th></tr></thead><tbody>
      ${d.items.map((i, idx) => `<tr><td>${idx + 1}</td><td>${esc(i.institution_name)}</td><td>${n(i.registrations)}</td>
        <td><span class="status ok">${n(i.completions)}</span></td><td>${i.completion_pct ?? 0}%</td></tr>`).join("") || `<tr><td colspan="5" class="muted">No institutions yet.</td></tr>`}
    </tbody></table></div>`;
}

// =================================================================
// FOOTFALL & ROUTES
// =================================================================
async function renderFootfall() {
  const d = await api("/api/admin/footfall");
  $("content").innerHTML = `
    <div class="section-head"><div><h3>Route / Hall-Wise Footfall</h3><p>Where crowding is building up right now.</p></div><button class="btn small" onclick="loadSection('footfall')">Refresh</button></div>
    <div class="grid-2">
      <div class="card">
        <h3>Hall-Wise Scans</h3>
        <div class="table-wrap"><table class="data-table"><thead><tr><th>Hall</th><th>Theme</th><th>Success</th><th>Failed</th></tr></thead><tbody>
          ${d.hall_wise.map((h) => `<tr><td>${esc(h.hall_zone)}</td><td>${esc(h.theme)}</td><td><span class="status ok">${n(h.successful_scans)}</span></td><td><span class="status bad">${n(h.failed_scans)}</span></td></tr>`).join("")}
        </tbody></table></div>
      </div>
      <div class="card">
        <h3>Route Load</h3>
        ${d.route_wise.map((r) => `<div style="display:flex;justify-content:space-between;padding:9px 0;border-bottom:1px solid #eee9df"><span class="muted">${esc(r.route_colour)}</span><b>${n(r.students)} students</b></div>`).join("")}
        <h3 style="margin-top:14px">Scans — Last Hour</h3>
        <p class="muted" style="font-size:12px">${d.last_hour.length} active minute-buckets recorded.</p>
      </div>
    </div>`;
}

// =================================================================
// REPORTS & EXPORT
// =================================================================
function renderExports() {
  const datasets = [
    ["registrations", "Registrations", "All registered students and consent status."],
    ["stamps", "Stamps", "Every checkpoint answer with status and fraud flag."],
    ["responses", "Feedback Responses", "Final questionnaire responses."],
    ["redemptions", "Certificates / Redemptions", "Certificate, prize eligibility and redemption records."],
    ["checkpoints", "Checkpoints", "Checkpoint configuration snapshot."],
  ];
  $("content").innerHTML = `
    <div class="section-head"><div><h3>Reports & CSV Export</h3><p>Downloads open as a CSV file — import into Excel or Google Sheets.</p></div></div>
    <div class="grid-2">
      ${datasets.map(([key, label, desc]) => `<div class="card"><div class="card-head"><h3>${label}</h3></div><p class="muted" style="margin:0 0 12px">${desc}</p><button class="btn primary" onclick="downloadCsv('${key}')">Export CSV</button></div>`).join("")}
    </div>`;
}

// =================================================================
// AUDIT TRAIL
// =================================================================
async function renderAudit() {
  const d = await api("/api/admin/audit");
  $("content").innerHTML = `
    <div class="section-head"><div><h3>Audit Trail</h3><p>Every administrative action, in order.</p></div><button class="btn small" onclick="loadSection('audit')">Refresh</button></div>
    <div class="card table-wrap"><table class="data-table"><thead><tr><th>Time</th><th>Action</th><th>Entity</th><th>Staff</th><th>Details</th></tr></thead><tbody>
      ${d.items.map((a) => `<tr><td>${fmt(a.created_at)}</td><td><span class="status info">${esc(a.action)}</span></td>
        <td>${esc(a.entity_type || "—")}${a.entity_id ? " · " + esc(a.entity_id) : ""}</td><td>${esc(a.staff_name || "—")}</td>
        <td style="white-space:normal;max-width:320px;font-size:11px">${esc(JSON.stringify(a.details || {}))}</td></tr>`).join("") || `<tr><td colspan="5" class="muted">No activity yet.</td></tr>`}
    </tbody></table></div>`;
}

// =================================================================
// CONSENT & DATA CONTROLS
// =================================================================
function renderConsent() {
  $("content").innerHTML = `
    <div class="section-head"><div><h3>Consent & Data Controls</h3><p>Look up a student's consent record, or action a data-erasure request.</p></div></div>
    <div class="card">
      <div class="search"><input id="consentPassport" placeholder="Passport ID (e.g. UPITS-2026-000123)" onkeydown="if(event.key==='Enter')lookupConsent()"><button class="btn primary" onclick="lookupConsent()">Look Up</button></div>
      <div id="consentResult"></div>
    </div>`;
}

async function lookupConsent() {
  const pid = $("consentPassport").value.trim();
  if (!pid) return;
  try {
    const c = await api("/api/admin/consent/" + encodeURIComponent(pid));
    $("consentResult").innerHTML = `
      <div class="detail-grid" style="margin-top:14px">
        <div><span>Name</span><b>${esc(c.full_name)}</b></div>
        <div><span>Passport ID</span><b>${esc(c.passport_id)}</b></div>
        <div><span>Location Consent</span><b>${c.consent_location ? "Given" : "Not given"}</b></div>
        <div><span>Privacy Consent</span><b>${c.consent_privacy ? "Given" : "Not given"}</b></div>
        <div><span>Guardian Consent</span><b>${c.consent_guardian ? "Given" : "Not given / N/A"}</b></div>
        <div><span>Data Erased</span><b>${c.data_erased_at ? fmt(c.data_erased_at) : "No"}</b></div>
      </div>
      ${!c.data_erased_at ? `<button class="btn danger" style="margin-top:12px" onclick="eraseData('${c.passport_id}')">Erase Personal Data</button>` : `<p class="muted" style="margin-top:12px">Personal data already erased; stamp/certificate history is retained anonymised for reporting.</p>`}`;
  } catch (x) { $("consentResult").innerHTML = `<div class="error">${esc(x.message)}</div>`; }
}

function eraseData(passportId) {
  openReason("Erase personal data", "Reason for erasure (required)", async (reason) => {
    await api(`/api/admin/consent/${encodeURIComponent(passportId)}/erase?staff_name=${encodeURIComponent(staffName() || "")}`, { method: "POST" });
    toast("Personal data erased for " + passportId);
    lookupConsent();
  });
}

// =================================================================
// SETTINGS
// =================================================================
async function renderSettings() {
  const d = await api("/api/admin/settings");
  const v = d.values;
  const fields = [
    ["required_stamps", "Required Stamps for Certificate", "number"],
    ["priority_draw_min_stamps", "Priority Draw Minimum Stamps", "number"],
    ["rate_limit_seconds", "Rate Limit Between Stamps (sec)", "number"],
    ["default_geofence_radius_m", "Default Geofence Radius (m)", "number"],
    ["default_max_accuracy_m", "Default Max GPS Accuracy (m)", "number"],
    ["default_qr_expires_seconds", "Default QR Lifetime (sec)", "number"],
    ["event_start_date", "Event Start Date", "date"],
    ["event_end_date", "Event End Date", "date"],
    ["default_language", "Default Interface Language", "select"],
  ];
  $("content").innerHTML = `
    <div class="section-head"><div><h3>Settings & Configuration</h3><p>Tune event-wide defaults without a redeploy. These are guidance values the frontend/backend can read — checkpoint-level overrides in Checkpoints & QR still win.</p></div></div>
    <div class="card">
      <div class="form-grid">
        ${fields.map(([key, label, type]) => `<label>${label}${
          type === "select"
            ? `<select id="set_${key}"><option value="en" ${v[key] === "en" ? "selected" : ""}>English</option><option value="hi" ${v[key] === "hi" ? "selected" : ""}>Hindi</option></select>`
            : `<input id="set_${key}" type="${type}" value="${esc(v[key])}">`
        }</label>`).join("")}
      </div>
      <div class="modal-actions" style="justify-content:flex-start"><button class="btn primary" onclick="saveSettings()">Save Settings</button></div>
      <div id="settingsSaved"></div>
    </div>`;
}

async function saveSettings() {
  const keys = ["required_stamps", "priority_draw_min_stamps", "rate_limit_seconds", "default_geofence_radius_m", "default_max_accuracy_m", "default_qr_expires_seconds", "event_start_date", "event_end_date", "default_language"];
  const numeric = new Set(["required_stamps", "priority_draw_min_stamps", "rate_limit_seconds", "default_geofence_radius_m", "default_max_accuracy_m", "default_qr_expires_seconds"]);
  const values = {};
  keys.forEach((k) => { const el = $("set_" + k); if (el) values[k] = numeric.has(k) ? Number(el.value) : el.value; });
  await api("/api/admin/settings", { method: "PUT", body: JSON.stringify({ values }) });
  $("settingsSaved").innerHTML = `<div class="notice">Settings saved.</div>`;
}

// =================================================================
// Shared "reason" confirmation modal (approve/reject/manual/erase/block)
// =================================================================
function openReason(title, label, callback) {
  $("reasonTitle").textContent = title;
  $("reasonLabel").textContent = label;
  $("reasonText").value = "";
  $("reasonError").classList.add("hidden");
  state.reasonCallback = callback;
  $("reasonModal").classList.remove("hidden");
}
function closeReason() { $("reasonModal").classList.add("hidden"); state.reasonCallback = null; }
async function confirmReason() {
  const val = $("reasonText").value.trim();
  if (val.length < 2) { $("reasonError").textContent = "Please enter a short reason."; $("reasonError").classList.remove("hidden"); return; }
  const cb = state.reasonCallback;
  closeReason();
  try { await cb(val); } catch (x) { toast(x.message, true); }
}