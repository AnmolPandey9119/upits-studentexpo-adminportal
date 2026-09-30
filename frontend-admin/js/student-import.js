// UPITS 2026 Admin Portal — Student import wizard (Excel / CSV -> students).
//
// Loaded AFTER admin.js (uses its globals: api, esc, n, toast, staffName,
// loadSection) and after SheetJS (window.XLSX, from cdnjs — see index.html).
//
// The spreadsheet is parsed here in the browser and sent to the admin API
// in small batches:
//   1. Upload   – pick a .xlsx / .xls / .csv (or download the template)
//   2. Map      – match spreadsheet columns to student fields (auto-detected)
//   3. Check    – dry run against the server: ready / already registered / needs fixing
//   4. Import   – commit in batches (optionally issuing a certificate for each new student),
//                 then download a result report and print the certificates
// The server re-validates every row on commit; nothing here is trusted.
// Re-running the same file is safe: mobiles that already exist are skipped.

(function () {
    "use strict";
  
    const BASE = "/api/admin/student-import";
    const MAX_FILE_BYTES = 10 * 1024 * 1024;
    const MAX_ROWS = 20000;
    const COMMIT_CHUNK = 200;          // server accepts up to 300 per request
    const PROBLEM_ROWS_SHOWN = 100;
  
    const S = {
      step: 1, busy: false, issueCerts: true,
      fields: [], serverChunk: 300,
      workbook: null, filename: "", sheetName: "",
      headers: [], rows: [],          // rows: [{ rowNumber, cells: [] }]
      mapping: {},                    // field key -> column index (-1 = none)
      autoMapped: {},                 // field key -> true when auto-detected
      results: [],                    // parallel to rows: server verdict per row
      commitPos: 0, commitError: "", batchId: "", queue: [],
    };
  
    const el = (id) => document.getElementById(id);
    const normHeader = (h) => String(h == null ? "" : h).toLowerCase().replace(/[^a-z0-9]/g, "");
    const cell = (row, idx) => (idx >= 0 && idx < row.cells.length ? String(row.cells[idx] ?? "").trim() : "");
    const fieldByKey = (k) => S.fields.find((f) => f.key === k);
    const post = (path, body) => api(BASE + path, { method: "POST", body: JSON.stringify(body) });
  
    // ------------------------------------------------------------------
    // Modal shell
    // ------------------------------------------------------------------
    function ensureModal() {
      if (el("importModal")) return;
      const m = document.createElement("div");
      m.id = "importModal";
      m.className = "modal hidden";
      m.innerHTML = `
        <div class="modal-card imp-card" role="dialog" aria-modal="true" aria-labelledby="impTitle">
          <div class="modal-head">
            <h3 id="impTitle">Import students from Excel</h3>
            <button class="icon-btn" id="impClose" aria-label="Close" data-act="close">×</button>
          </div>
          <ol class="imp-steps" id="impSteps"></ol>
          <div id="impBody"></div>
        </div>`;
      document.body.appendChild(m);
      m.addEventListener("click", onClick);
      m.addEventListener("change", onChange);
    }
  
    function renderSteps() {
      const names = ["Upload", "Match columns", "Check", "Import"];
      el("impSteps").innerHTML = names
        .map((t, i) => `<li class="${i + 1 === S.step ? "on" : i + 1 < S.step ? "done" : ""}"><span>${i + 1}</span>${t}</li>`)
        .join("");
      const close = el("impClose");
      close.disabled = S.busy;
      close.style.visibility = S.busy ? "hidden" : "visible";
    }
  
    function setBody(html) { el("impBody").innerHTML = html; renderSteps(); }
  
    window.openStudentImport = async function openStudentImport() {
      ensureModal();
      reset();
      el("importModal").classList.remove("hidden");
      renderUpload();
      if (!S.fields.length) {
        try {
          const d = await api(BASE + "/fields");
          S.fields = d.fields;
          S.serverChunk = d.max_rows_per_request || 300;
        } catch (x) { showError(x.message); }
      }
    };
  
    function closeModal() {
      if (S.busy) return;
      el("importModal").classList.add("hidden");
      if (S.results.some((r) => r && r.status === "imported") && state.section === "students") loadSection("students");
    }
  
    function reset() {
      Object.assign(S, {
        step: 1, busy: false, workbook: null, filename: "", sheetName: "", headers: [], rows: [],
        mapping: {}, autoMapped: {}, results: [], commitPos: 0, commitError: "", batchId: "", queue: [],
      });
    }
  
    function showError(msg) {
      const box = el("impError");
      if (box) { box.textContent = msg; box.classList.remove("hidden"); }
      else toast(msg, true);
    }
    const clearError = () => { const b = el("impError"); if (b) b.classList.add("hidden"); };
  
    // ------------------------------------------------------------------
    // Step 1 — upload
    // ------------------------------------------------------------------
    function renderUpload() {
      S.step = 1;
      const libOk = typeof XLSX !== "undefined";
      setBody(`
        <p class="muted imp-lead">Upload your student list (.xlsx, .xls or .csv). The first row should contain column headings.
          You'll match the columns and review everything before anything is saved.</p>
        <label class="imp-drop" id="impDrop" tabindex="0">
          <input type="file" id="impFile" accept=".xlsx,.xls,.csv" ${libOk ? "" : "disabled"} hidden>
          <strong>Choose an Excel file</strong>
          <span>or drag and drop it here</span>
          <small>.xlsx, .xls or .csv · up to 10 MB · ${n(MAX_ROWS)} students max</small>
        </label>
        <div id="impError" class="error hidden" role="alert"></div>
        ${libOk ? "" : `<div class="error">The spreadsheet reader could not be loaded (check your internet connection and reload the page).</div>`}
        <ul class="imp-rules">
          <li><b>Required:</b> student name, mobile number, institution.</li>
          <li>Students whose <b>mobile number is already registered are skipped</b> — nothing existing is changed, so it's safe to upload the same file twice.</li>
          <li><b>Passport IDs are created automatically</b> and listed in the report you can download afterwards.</li>
          <li>You can also <b>generate a participation certificate</b> for every imported student and print them all as one PDF.</li>
        </ul>
        <div class="modal-actions">
          <button class="btn" data-act="template" ${libOk ? "" : "disabled"}>Download template</button>
          <button class="btn" data-act="close">Cancel</button>
        </div>`);
      const drop = el("impDrop");
      ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
      ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
      drop.addEventListener("drop", (e) => { const f = e.dataTransfer.files && e.dataTransfer.files[0]; if (f) loadFile(f); });
      drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); el("impFile").click(); } });
    }
  
    function downloadTemplate() {
      const head = ["Student name", "Mobile number", "Institution", "Email", "Student category", "Class / course", "Age group", "District / city", "Teacher name", "Teacher mobile"];
      const wb = XLSX.utils.book_new();
      const ws = XLSX.utils.aoa_to_sheet([head]);
      ws["!cols"] = head.map((h) => ({ wch: Math.max(14, h.length + 4) }));
      XLSX.utils.book_append_sheet(wb, ws, "Students");
      const help = XLSX.utils.aoa_to_sheet([
        ["Fill the 'Students' sheet, one student per row, and upload it in Import students."],
        ["Required: Student name, Mobile number (10 digits), Institution."],
        ["Everything else is optional. Leave a cell empty if unknown."],
        ["Students whose mobile number is already registered are skipped."],
      ]);
      XLSX.utils.book_append_sheet(wb, help, "Instructions");
      XLSX.writeFile(wb, "UPITS-student-import-template.xlsx");
    }
  
    async function loadFile(file) {
      clearError();
      if (!S.fields.length) return showError("Couldn't load the import settings from the server. Close this window and try again.");
      if (!/\.(xlsx|xls|csv)$/i.test(file.name)) return showError("Please choose an .xlsx, .xls or .csv file.");
      if (file.size > MAX_FILE_BYTES) return showError("That file is larger than 10 MB. Split it into smaller files.");
      S.busy = true; renderSteps();
      try {
        const buf = await file.arrayBuffer();
        S.workbook = XLSX.read(buf, { type: "array" });
        S.filename = file.name;
        const usable = S.workbook.SheetNames.find((nm) => parseSheet(nm).rows.length > 0);
        if (!usable) throw new Error("No student rows found. Make sure the first row has column headings and the rows below have data.");
        selectSheet(usable);
        if (S.rows.length > MAX_ROWS) throw new Error(`This file has ${n(S.rows.length)} students; the limit is ${n(MAX_ROWS)} per upload. Please split it.`);
        S.busy = false;
        renderMapping();
      } catch (x) {
        S.busy = false; renderSteps();
        showError(x.message || "Could not read that file.");
      }
    }
  
    // Reads one sheet -> { headers, rows }. Nothing is stored in S here.
    function parseSheet(name) {
      const ws = S.workbook.Sheets[name];
      if (!ws || !ws["!ref"]) return { headers: [], rows: [] };
      const range = XLSX.utils.decode_range(ws["!ref"]);
      // raw:false -> values as shown in Excel (keeps long numbers/mobiles intact).
      const aoa = XLSX.utils.sheet_to_json(ws, { header: 1, raw: false, defval: "", blankrows: true });
      const filled = (r) => r.filter((c) => String(c).trim() !== "").length;
      const hi = aoa.findIndex((r) => filled(r) >= 2);
      if (hi < 0) return { headers: [], rows: [] };
      const seen = {};
      const headers = aoa[hi].map((h, i) => {
        let label = String(h).trim() || "Column " + XLSX.utils.encode_col(i);
        seen[label] = (seen[label] || 0) + 1;
        return seen[label] > 1 ? `${label} (${seen[label]})` : label;
      });
      const rows = [];
      for (let i = hi + 1; i < aoa.length; i++) {
        if (filled(aoa[i]) === 0) continue;
        rows.push({ rowNumber: range.s.r + i + 1, cells: aoa[i].map((c) => String(c).trim()) });
      }
      return { headers, rows };
    }
  
    function selectSheet(name) {
      const p = parseSheet(name);
      S.sheetName = name; S.headers = p.headers; S.rows = p.rows;
      autoMap();
    }
  
    // ------------------------------------------------------------------
    // Step 2 — match columns
    // ------------------------------------------------------------------
    function autoMap() {
      S.mapping = {}; S.autoMapped = {};
      const used = new Set();
      const normalised = S.headers.map(normHeader);
      // Required fields first so they win contested headers.
      const ordered = [...S.fields].sort((a, b) => Number(b.required) - Number(a.required));
      ordered.forEach((f) => {
        const wanted = new Set([f.key.replace(/_/g, ""), ...f.synonyms]);
        const idx = normalised.findIndex((h, i) => !used.has(i) && wanted.has(h));
        S.mapping[f.key] = idx;
        if (idx >= 0) { used.add(idx); S.autoMapped[f.key] = true; }
      });
    }
  
    function renderMapping() {
      S.step = 2;
      const sheetPicker = S.workbook.SheetNames.length > 1
        ? `<label class="imp-sheet">Sheet
             <select id="impSheet">${S.workbook.SheetNames.map((nm) => `<option value="${esc(nm)}" ${nm === S.sheetName ? "selected" : ""}>${esc(nm)}</option>`).join("")}</select>
           </label>` : "";
      const sample = (idx) => {
        if (idx < 0) return "";
        const r = S.rows.find((x) => cell(x, idx) !== "");
        return r ? cell(r, idx) : "";
      };
      const optionsFor = (key) =>
        `<option value="-1">— not in my file —</option>` +
        S.headers.map((h, i) => `<option value="${i}" ${S.mapping[key] === i ? "selected" : ""}>${esc(h)}</option>`).join("");
  
      setBody(`
        <div class="imp-file"><b>${esc(S.filename)}</b><span>${n(S.rows.length)} student row${S.rows.length === 1 ? "" : "s"} found</span>${sheetPicker}</div>
        <p class="muted imp-lead">Match each student field to a column from your file. We've filled in what we could detect.</p>
        <div class="table-wrap"><table class="data-table imp-map">
          <thead><tr><th>Student field</th><th>Column in your file</th><th>Example</th></tr></thead>
          <tbody>${S.fields.map((f) => `
            <tr>
              <td><b>${esc(f.label)}</b>${f.required ? ' <span class="imp-req" title="Required">*</span>' : ""}</td>
              <td><select data-map="${f.key}" aria-label="Column for ${esc(f.label)}">${optionsFor(f.key)}</select></td>
              <td class="imp-sample" data-sample="${f.key}">${esc(sample(S.mapping[f.key]))}</td>
            </tr>`).join("")}
          </tbody></table></div>
        <div id="impError" class="error hidden" role="alert"></div>
        <div class="modal-actions">
          <button class="btn" data-act="restart">Choose a different file</button>
          <button class="btn primary" id="impCheck" data-act="check">Check data</button>
        </div>`);
      updateMappingState();
    }
  
    function updateMappingState() {
      const missing = S.fields.filter((f) => f.required && !(S.mapping[f.key] >= 0));
      const btn = el("impCheck");
      if (btn) btn.disabled = missing.length > 0;
      const box = el("impError");
      if (box) {
        if (missing.length) { box.textContent = "Still needed: " + missing.map((f) => f.label).join(", ") + "."; box.classList.remove("hidden"); }
        else box.classList.add("hidden");
      }
    }
  
    // ------------------------------------------------------------------
    // Step 3 — check (dry run)
    // ------------------------------------------------------------------
    function rowData(row) {
      const d = {};
      S.fields.forEach((f) => { const i = S.mapping[f.key]; if (i >= 0) d[f.key] = cell(row, i); });
      return d;
    }
    const chunks = (arr, size) => { const out = []; for (let i = 0; i < arr.length; i += size) out.push(arr.slice(i, i + size)); return out; };
  
    async function runCheck() {
      S.step = 3; S.busy = true; S.results = new Array(S.rows.length);
      setBody(progressHtml("Checking your data…", 0, S.rows.length));
      try {
        const size = Math.min(S.serverChunk, 250);
        let done = 0;
        for (const part of chunks(S.rows.map((r, i) => ({ r, i })), size)) {
          const d = await post("/validate", { rows: part.map(({ r }) => ({ row_number: r.rowNumber, data: rowData(r) })) });
          d.items.forEach((item, k) => { S.results[part[k].i] = item; });
          done += part.length;
          updateProgress(done, S.rows.length);
        }
        markFileDuplicates();
        S.busy = false;
        renderReview();
      } catch (x) {
        S.busy = false;
        renderMapping();
        showError(x.message);
      }
    }
  
    // The same mobile twice in one file: the first wins, later ones are skipped.
    function markFileDuplicates() {
      const seen = new Map();
      S.results.forEach((res, i) => {
        if (!res || res.status !== "ready" || !res.mobile_key) return;
        if (seen.has(res.mobile_key)) {
          res.status = "duplicate";
          res.message = `Same mobile as row ${S.rows[seen.get(res.mobile_key)].rowNumber} of this file.`;
        } else seen.set(res.mobile_key, i);
      });
    }
  
    const tally = () => S.results.reduce((a, r) => { if (r) a[r.status] = (a[r.status] || 0) + 1; return a; }, {});
  
    function renderReview() {
      S.step = 3;
      const t = tally();
      const ready = t.ready || 0, dup = t.duplicate || 0, bad = t.invalid || 0;
      const nm = (row) => cell(row, S.mapping.full_name) || "—";
      const mob = (row) => cell(row, S.mapping.mobile_number) || "—";
      const problems = S.rows.map((row, i) => ({ row, res: S.results[i] })).filter((x) => x.res.status !== "ready");
      const warned = S.results.filter((r) => r.status === "ready" && r.warnings && r.warnings.length).length;
  
      setBody(`
        <div class="imp-chips">
          <div class="imp-chip ok"><strong>${n(ready)}</strong><span>ready to import</span></div>
          <div class="imp-chip warn"><strong>${n(dup)}</strong><span>already registered / repeated (skipped)</span></div>
          <div class="imp-chip bad"><strong>${n(bad)}</strong><span>need fixing (skipped)</span></div>
        </div>
        ${warned ? `<p class="muted">${n(warned)} ready row${warned === 1 ? " has" : "s have"} an optional value (email or teacher mobile) that will be left blank because it wasn't valid.</p>` : ""}
        ${problems.length ? `
          <h3 class="imp-h">Rows that won't be imported</h3>
          <div class="table-wrap imp-problems"><table class="data-table">
            <thead><tr><th>Row</th><th>Name</th><th>Mobile</th><th>Why</th></tr></thead>
            <tbody>${problems.slice(0, PROBLEM_ROWS_SHOWN).map(({ row, res }) => `
              <tr><td>${row.rowNumber}</td><td>${esc(nm(row))}</td><td>${esc(mob(row))}</td>
              <td class="imp-why"><span class="status ${res.status === "invalid" ? "bad" : "warn"}">${res.status === "invalid" ? "Fix" : "Skip"}</span> ${esc(res.message)}</td></tr>`).join("")}
            </tbody></table></div>
          ${problems.length > PROBLEM_ROWS_SHOWN ? `<p class="muted">Showing the first ${PROBLEM_ROWS_SHOWN} of ${n(problems.length)}. Download the full list below.</p>` : ""}
          <button class="btn small" data-act="dl-problems">Download these rows (CSV)</button>` : `<div class="notice">Every row looks good.</div>`}
        ${ready ? `<label class="imp-opt"><input type="checkbox" data-opt="certs" ${S.issueCerts ? "checked" : ""}>
          <span><b>Also generate a participation certificate</b> for each of the ${n(ready)} student${ready === 1 ? "" : "s"} being imported.
          You can print them all as one PDF when the import finishes.</span></label>` : ""}
        <div id="impError" class="error hidden" role="alert"></div>
        <div class="modal-actions">
          <button class="btn" data-act="back-map">Back</button>
          <button class="btn primary" data-act="import" ${ready ? "" : "disabled"}>Import ${n(ready)} student${ready === 1 ? "" : "s"}</button>
        </div>`);
    }
  
    // ------------------------------------------------------------------
    // Step 4 — import (commit in batches, resumable)
    // ------------------------------------------------------------------
    function progressHtml(label, done, total) {
      const pct = total ? Math.round((done / total) * 100) : 0;
      return `<div class="imp-progress" aria-live="polite">
          <p id="impProgLabel"><b>${esc(label)}</b> <span id="impProgCount">${n(done)} / ${n(total)}</span></p>
          <div class="imp-bar"><i id="impBarFill" style="width:${pct}%"></i></div>
          <p class="muted">Please keep this window open.</p>
        </div>`;
    }
    function updateProgress(done, total) {
      const c = el("impProgCount"), f = el("impBarFill");
      if (c) c.textContent = `${n(done)} / ${n(total)}`;
      if (f) f.style.width = (total ? Math.round((done / total) * 100) : 0) + "%";
    }
  
    const guardUnload = (e) => { e.preventDefault(); e.returnValue = ""; };
  
    async function runImport() {
      S.step = 4; S.busy = true; S.commitError = ""; S.commitPos = 0;
      S.batchId = "imp-" + (window.crypto && crypto.randomUUID ? crypto.randomUUID().slice(0, 8) : Date.now().toString(36));
      // Only rows that passed the check; the server re-validates each one anyway.
      S.queue = S.rows.map((r, i) => i).filter((i) => S.results[i].status === "ready");
      await continueImport();
    }
  
    async function continueImport() {
      S.busy = true; S.commitError = "";
      window.addEventListener("beforeunload", guardUnload);
      setBody(progressHtml("Importing students…", S.commitPos, S.queue.length));
      try {
        while (S.commitPos < S.queue.length) {
          const slice = S.queue.slice(S.commitPos, S.commitPos + COMMIT_CHUNK);
          const d = await post("/commit", {
            rows: slice.map((i) => ({ row_number: S.rows[i].rowNumber, data: rowData(S.rows[i]) })),
            staff_name: staffName(), filename: S.filename, batch_id: S.batchId,
            issue_certificates: S.issueCerts,
          });
          d.items.forEach((item, k) => { S.results[slice[k]] = item; });
          S.commitPos += slice.length;
          updateProgress(S.commitPos, S.queue.length);
        }
      } catch (x) {
        S.commitError = x.message || "Import was interrupted.";
      }
      window.removeEventListener("beforeunload", guardUnload);
      S.busy = false;
      renderDone();
    }
  
    function renderDone() {
      S.step = 4;
      const t = tally();
      const imported = t.imported || 0, skipped = (t.duplicate || 0), invalid = t.invalid || 0, failed = t.failed || 0;
      const remaining = S.queue.length - S.commitPos;
      const certs = certificateList();
      const certMissing = S.issueCerts ? Math.max(0, imported - certs.length) : 0;
      setBody(`
        ${S.commitError ? `<div class="error" role="alert"><b>The import was interrupted.</b> ${esc(S.commitError)}<br>
          ${n(S.commitPos)} of ${n(S.queue.length)} were processed before it stopped. You can continue safely — students already saved are never added twice.</div>` : ""}
        ${!S.commitError ? `<div class="notice imp-done"><b>Import finished.</b> ${n(imported)} student${imported === 1 ? "" : "s"} added.</div>` : ""}
        <div class="imp-chips">
          <div class="imp-chip ok"><strong>${n(imported)}</strong><span>imported</span></div>
          <div class="imp-chip warn"><strong>${n(skipped)}</strong><span>already registered (skipped)</span></div>
          <div class="imp-chip bad"><strong>${n(invalid + failed)}</strong><span>not imported</span></div>
          ${S.issueCerts ? `<div class="imp-chip ok"><strong>${n(certs.length)}</strong><span>certificates generated</span></div>` : ""}
          ${remaining > 0 ? `<div class="imp-chip"><strong>${n(remaining)}</strong><span>not processed yet</span></div>` : ""}
        </div>
        ${certMissing ? `<div class="error" role="alert">${n(certMissing)} student${certMissing === 1 ? " was" : "s were"} imported but the certificate could not be created. See the report; you can generate it later from Certificates → Generate Certificate.</div>` : ""}
        <p class="muted">The report lists every row with its result, the new Passport ID and certificate number.</p>
        <div class="modal-actions">
          <button class="btn" data-act="dl-report">Download report (CSV)</button>
          ${certs.length ? `<button class="btn ${S.commitError ? "" : "primary"}" data-act="print-certs">Print ${n(certs.length)} certificate${certs.length === 1 ? "" : "s"} / Save as PDF</button>` : ""}
          ${S.commitError ? `<button class="btn primary" data-act="resume">Continue import</button>` : `
          <button class="btn" data-act="restart">Import another file</button>
          <button class="btn ${certs.length ? "" : "primary"}" data-act="close">Done</button>`}
        </div>`);
    }
  
    // ------------------------------------------------------------------
    // CSV downloads
    // ------------------------------------------------------------------
    function csvCell(v) {
      let s = String(v ?? "");
      if (/^[=+\-@\t\r]/.test(s)) s = "'" + s;           // never let a cell run as a formula
      return /[",\n\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
    }
    function downloadCsv(name, header, lines) {
      const text = "\ufeff" + [header, ...lines].map((r) => r.map(csvCell).join(",")).join("\r\n");
      const url = URL.createObjectURL(new Blob([text], { type: "text/csv;charset=utf-8" }));
      const a = document.createElement("a");
      a.href = url; a.download = name;
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
    }
    const baseName = () => S.filename.replace(/\.[^.]+$/, "") || "students";
    const LABEL = { imported: "Imported", duplicate: "Skipped – already registered", invalid: "Not imported – needs fixing", failed: "Not imported – error", ready: "Not processed" };
  
    function downloadReport() {
      const lines = S.rows.map((row, i) => {
        const r = S.results[i] || {};
        return [row.rowNumber, cell(row, S.mapping.full_name), cell(row, S.mapping.mobile_number), cell(row, S.mapping.institution_name),
          LABEL[r.status] || "", r.passport_id || "", r.certificate_serial || "", r.message || ""];
      });
      downloadCsv(`${baseName()}-import-report.csv`, ["Row", "Student name", "Mobile", "Institution", "Result", "Passport ID", "Certificate No.", "Note"], lines);
    }

    // Certificates created in this import (name/institution come back from the
    // server exactly as stored, so the printed certificate matches the record).
    function certificateList() {
      return S.results
        .filter((r) => r && r.status === "imported" && r.certificate_serial)
        .map((r) => ({ name: r.full_name, institution: r.institution_name, serial: r.certificate_serial,
                       passportId: r.passport_id, issuedAt: r.certificate_issued_at }));
    }
    function printBatchCertificates() {
      if (typeof window.printCertificates !== "function") return toast("Certificate printing is not loaded. Reload the page.", true);
      window.printCertificates(certificateList());
    }
  
    function downloadProblems() {
      const lines = S.rows.map((row, i) => ({ row, r: S.results[i] })).filter((x) => x.r.status !== "ready")
        .map(({ row, r }) => [row.rowNumber, cell(row, S.mapping.full_name), cell(row, S.mapping.mobile_number), cell(row, S.mapping.institution_name), r.message]);
      downloadCsv(`${baseName()}-rows-to-fix.csv`, ["Row", "Student name", "Mobile", "Institution", "Why"], lines);
    }
  
    // ------------------------------------------------------------------
    // Events
    // ------------------------------------------------------------------
    function onClick(e) {
      const t = e.target.closest("[data-act]");
      if (!t || t.disabled) return;
      switch (t.dataset.act) {
        case "close": closeModal(); break;
        case "template": downloadTemplate(); break;
        case "restart": reset(); renderUpload(); break;
        case "back-map": renderMapping(); break;
        case "check": runCheck(); break;
        case "import": runImport(); break;
        case "resume": continueImport(); break;
        case "dl-report": downloadReport(); break;
        case "print-certs": printBatchCertificates(); break;
        case "dl-problems": downloadProblems(); break;
      }
    }
  
    function onChange(e) {
      const t = e.target;
      if (t.dataset && t.dataset.opt === "certs") { S.issueCerts = t.checked; }
      else if (t.id === "impFile" && t.files && t.files[0]) { loadFile(t.files[0]); t.value = ""; }
      else if (t.id === "impSheet") {
        selectSheet(t.value);
        renderMapping();
        if (!S.rows.length) showError("That sheet has no student rows.");
      } else if (t.dataset && t.dataset.map) {
        const key = t.dataset.map, idx = Number(t.value);
        S.mapping[key] = idx;
        // one column can't feed two fields: release it from any other field
        if (idx >= 0) Object.keys(S.mapping).forEach((k) => { if (k !== key && S.mapping[k] === idx) { S.mapping[k] = -1; const sel = document.querySelector(`[data-map="${k}"]`); if (sel) sel.value = "-1"; refreshSample(k); } });
        refreshSample(key);
        updateMappingState();
      }
    }
  
    function refreshSample(key) {
      const td = document.querySelector(`[data-sample="${key}"]`);
      if (!td) return;
      const idx = S.mapping[key];
      const r = idx >= 0 ? S.rows.find((x) => cell(x, idx) !== "") : null;
      td.textContent = r ? cell(r, idx) : "";
    }
  
    document.addEventListener("keydown", (e) => {
      const m = el("importModal");
      if (e.key === "Escape" && m && !m.classList.contains("hidden")) closeModal();
    });
  })();