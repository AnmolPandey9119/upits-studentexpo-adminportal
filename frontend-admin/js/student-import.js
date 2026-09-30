// UPITS 2026 Admin Portal — Student import (Excel / CSV -> students).  SIMPLE VERSION.
//
// Loaded AFTER admin.js (uses its globals: api, esc, n, toast, staffName,
// loadSection) and after SheetJS (window.XLSX, from cdnjs — see index.html).
//
// Flow:  choose file  ->  "N students found" + Import button  ->  done.
//   * No column-matching screen: columns are detected automatically
//     (by heading, and if the heading is odd, by looking at the data).
//   * No separate check step, no certificates.
//   * The server still skips mobiles that are already registered and
//     creates Passport IDs, so uploading the same file twice is safe.

(function () {
    "use strict";

    const BASE = "/api/admin/student-import";
    const MAX_FILE_BYTES = 10 * 1024 * 1024;
    const MAX_ROWS = 20000;
    const COMMIT_CHUNK = 200;          // server accepts up to 300 per request
    const PROBLEM_ROWS_SHOWN = 100;

    const S = {
      busy: false,
      fields: [], serverChunk: 300,
      workbook: null, filename: "", sheetName: "",
      headers: [], rows: [],          // rows: [{ rowNumber, cells: [] }]
      mapping: {},                    // field key -> column index (-1 = none)
      results: [],                    // parallel to rows: server verdict per row
      commitPos: 0, commitError: "", batchId: "",
    };

    const el = (id) => document.getElementById(id);
    const normHeader = (h) => String(h == null ? "" : h).toLowerCase().replace(/[^a-z0-9]/g, "");
    const cell = (row, idx) => (idx >= 0 && idx < row.cells.length ? String(row.cells[idx] ?? "").trim() : "");
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
          <div id="impBody"></div>
        </div>`;
      document.body.appendChild(m);
      m.addEventListener("click", onClick);
      m.addEventListener("change", onChange);
    }

    function setBody(html) {
      el("impBody").innerHTML = html;
      const close = el("impClose");
      close.disabled = S.busy;
      close.style.visibility = S.busy ? "hidden" : "visible";
    }

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
        busy: false, workbook: null, filename: "", sheetName: "", headers: [], rows: [],
        mapping: {}, results: [], commitPos: 0, commitError: "", batchId: "",
      });
    }

    function showError(msg) {
      const box = el("impError");
      if (box) { box.textContent = msg; box.classList.remove("hidden"); }
      else toast(msg, true);
    }
    const clearError = () => { const b = el("impError"); if (b) b.classList.add("hidden"); };

    // ------------------------------------------------------------------
    // Upload
    // ------------------------------------------------------------------
    function renderUpload() {
      const libOk = typeof XLSX !== "undefined";
      setBody(`
        <p class="muted imp-lead">Upload your student list (.xlsx, .xls or .csv) — it is imported as it is.
          Columns are detected automatically.</p>
        <label class="imp-drop" id="impDrop" tabindex="0">
          <input type="file" id="impFile" accept=".xlsx,.xls,.csv" ${libOk ? "" : "disabled"} hidden>
          <strong>Choose an Excel file</strong>
          <span>or drag and drop it here</span>
          <small>.xlsx, .xls or .csv · up to 10 MB · ${n(MAX_ROWS)} students max</small>
        </label>
        <div id="impError" class="error hidden" role="alert"></div>
        ${libOk ? "" : `<div class="error">The spreadsheet reader could not be loaded (check your internet connection and reload the page).</div>`}
        <ul class="imp-rules">
          <li>Every student needs at least a <b>name</b> and a <b>mobile number</b>. Rows without them can't be imported.</li>
          <li>A mobile number that is <b>already registered is skipped</b>, so uploading the same file twice is safe.</li>
        </ul>
        <div class="modal-actions">
          <button class="btn" data-act="close">Cancel</button>
        </div>`);
      const drop = el("impDrop");
      ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
      ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
      drop.addEventListener("drop", (e) => { const f = e.dataTransfer.files && e.dataTransfer.files[0]; if (f) loadFile(f); });
      drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); el("impFile").click(); } });
    }

    async function loadFile(file) {
      clearError();
      if (!/\.(xlsx|xls|csv)$/i.test(file.name)) return showError("Please choose an .xlsx, .xls or .csv file.");
      if (file.size > MAX_FILE_BYTES) return showError("That file is larger than 10 MB. Split it into smaller files.");
      S.busy = true; setBody(`<p class="muted">Reading file…</p>`);
      try {
        const buf = await file.arrayBuffer();
        S.workbook = XLSX.read(buf, { type: "array" });
        S.filename = file.name;
        const usable = S.workbook.SheetNames.find((nm) => parseSheet(nm).rows.length > 0);
        if (!usable) throw new Error("No rows found in this file.");
        selectSheet(usable);
        if (S.rows.length > MAX_ROWS) throw new Error(`This file has ${n(S.rows.length)} rows; the limit is ${n(MAX_ROWS)} per upload. Please split it.`);
        S.busy = false;
        renderConfirm();
      } catch (x) {
        S.busy = false;
        renderUpload();
        showError(x.message || "Could not read that file.");
      }
    }

    // ------------------------------------------------------------------
    // Reading the sheet
    // ------------------------------------------------------------------
    // A cell that looks like an Indian mobile number (used to spot the
    // mobile column, and to tell a data row from a heading row).
    function looksMobile(v) {
      let s = String(v == null ? "" : v).trim();
      if (!s) return false;
      if (/^\d+(\.\d+)?[eE]\+?\d+$/.test(s)) { const x = Number(s); if (isFinite(x)) s = String(Math.round(x)); }
      s = s.replace(/\.0+$/, "");
      const d = s.replace(/[\s\-+().]/g, "");
      if (!/^\d+$/.test(d)) return false;
      const t = d.length === 12 && d.startsWith("91") ? d.slice(2)
              : d.length === 11 && d.startsWith("0") ? d.slice(1) : d;
      return t.length === 10 && /^[6-9]/.test(t);
    }

    const cellText = (c) => String(c ?? "").trim();

    // One sheet -> { headers, rows }.
    function parseSheet(name) {
      const ws = S.workbook.Sheets[name];
      if (!ws || !ws["!ref"]) return { headers: [], rows: [] };
      const range = XLSX.utils.decode_range(ws["!ref"]);
      // raw:true keeps long numbers (mobiles) exact instead of 9.88E+09.
      const aoa = XLSX.utils.sheet_to_json(ws, { header: 1, raw: true, defval: "", blankrows: true });
      const filled = (r) => r.filter((c) => String(c).trim() !== "").length;
      const first = aoa.findIndex((r) => filled(r) >= 1);
      if (first < 0) return { headers: [], rows: [] };

      // If the first filled row already holds a mobile number, there is no heading row.
      const noHeader = aoa[first].some(looksMobile);
      const width = aoa.reduce((m, r) => Math.max(m, r.length), 0);
      let headers, start;
      if (noHeader) {
        headers = Array.from({ length: width }, (_, i) => "Column " + XLSX.utils.encode_col(i));
        start = first;
      } else {
        const seen = {};
        headers = Array.from({ length: width }, (_, i) => {
          const label = String(aoa[first][i] ?? "").trim() || "Column " + XLSX.utils.encode_col(i);
          seen[label] = (seen[label] || 0) + 1;
          return seen[label] > 1 ? `${label} (${seen[label]})` : label;
        });
        start = first + 1;
      }
      const rows = [];
      for (let i = start; i < aoa.length; i++) {
        if (filled(aoa[i]) === 0) continue;
        rows.push({ rowNumber: range.s.r + i + 1, cells: aoa[i].map(cellText) });
      }
      return { headers, rows };
    }

    function selectSheet(name) {
      const p = parseSheet(name);
      S.sheetName = name; S.headers = p.headers; S.rows = p.rows;
      autoMap();
    }

    // ------------------------------------------------------------------
    // Automatic column detection (no screen for this)
    // ------------------------------------------------------------------
    function columnValues(idx) { return S.rows.slice(0, 300).map((r) => cell(r, idx)).filter(Boolean); }
    const share = (vals, test) => (vals.length ? vals.filter(test).length / vals.length : 0);

    function autoMap() {
      S.mapping = {};
      const used = new Set();
      const normalised = S.headers.map(normHeader);
      // 1) by heading
      const ordered = [...S.fields].sort((a, b) => Number(b.required) - Number(a.required));
      ordered.forEach((f) => {
        const wanted = new Set([f.key.replace(/_/g, ""), ...f.synonyms]);
        const idx = normalised.findIndex((h, i) => !used.has(i) && wanted.has(h));
        S.mapping[f.key] = idx;
        if (idx >= 0) used.add(idx);
      });
      // 2) heading not recognised -> look at the data itself
      if (!(S.mapping.mobile_number >= 0)) {
        let best = -1, bestShare = 0.5;
        S.headers.forEach((_, i) => {
          if (used.has(i)) return;
          const s = share(columnValues(i), looksMobile);
          if (s > bestShare) { best = i; bestShare = s; }
        });
        if (best >= 0) { S.mapping.mobile_number = best; used.add(best); }
      }
      if (!(S.mapping.full_name >= 0)) {
        const nameLike = (v) => /[^\W\d_]/.test(v) && !/\d/.test(v) && !/@/.test(v) && v.length >= 2 && v.length <= 60;
        let best = -1, bestShare = 0.6;
        S.headers.forEach((_, i) => {
          if (used.has(i)) return;
          const s = share(columnValues(i), nameLike);
          if (s > bestShare) { best = i; bestShare = s; }   // leftmost column wins ties
        });
        if (best >= 0) { S.mapping.full_name = best; used.add(best); }
      }
    }

    // ------------------------------------------------------------------
    // Confirm
    // ------------------------------------------------------------------
    function rowData(row) {
      const d = {};
      S.fields.forEach((f) => { const i = S.mapping[f.key]; if (i >= 0) d[f.key] = cell(row, i); });
      return d;
    }

    function renderConfirm() {
      const multi = S.workbook.SheetNames.length > 1;
      const sheetPicker = multi
        ? `<label class="imp-sheet">Sheet
             <select id="impSheet">${S.workbook.SheetNames.map((nm) => `<option value="${esc(nm)}" ${nm === S.sheetName ? "selected" : ""}>${esc(nm)}</option>`).join("")}</select>
           </label>` : "";
      const missing = [];
      if (!(S.mapping.full_name >= 0)) missing.push("student name");
      if (!(S.mapping.mobile_number >= 0)) missing.push("mobile number");
      const found = S.fields.filter((f) => S.mapping[f.key] >= 0).map((f) => f.label);

      setBody(`
        <div class="imp-file"><b>${esc(S.filename)}</b><span>${n(S.rows.length)} row${S.rows.length === 1 ? "" : "s"} found</span>${sheetPicker}</div>
        ${found.length ? `<p class="muted imp-lead">Columns picked up: ${found.map(esc).join(", ")}.</p>` : ""}
        ${missing.length ? `<div class="error" role="alert">Couldn't find a <b>${missing.join("</b> or <b>")}</b> column in this ${multi ? "sheet" : "file"}. ${multi ? "Try another sheet, or add" : "Add"} a column with that heading and upload again.</div>` : ""}
        <div id="impError" class="error hidden" role="alert"></div>
        <div class="modal-actions">
          <button class="btn" data-act="restart">Choose a different file</button>
          <button class="btn primary" data-act="import" ${missing.length ? "disabled" : ""}>Import ${n(S.rows.length)} student${S.rows.length === 1 ? "" : "s"}</button>
        </div>`);
    }

    // ------------------------------------------------------------------
    // Import (in batches, resumable)
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
      S.commitPos = 0; S.commitError = ""; S.results = new Array(S.rows.length);
      S.batchId = "imp-" + (window.crypto && crypto.randomUUID ? crypto.randomUUID().slice(0, 8) : Date.now().toString(36));
      await continueImport();
    }

    async function continueImport() {
      S.busy = true; S.commitError = "";
      window.addEventListener("beforeunload", guardUnload);
      setBody(progressHtml("Importing students…", S.commitPos, S.rows.length));
      const size = Math.min(COMMIT_CHUNK, S.serverChunk);
      try {
        while (S.commitPos < S.rows.length) {
          const from = S.commitPos;
          const slice = S.rows.slice(from, from + size);
          const d = await post("/commit", {
            rows: slice.map((r) => ({ row_number: r.rowNumber, data: rowData(r) })),
            staff_name: staffName(), filename: S.filename, batch_id: S.batchId,
            issue_certificates: false,
          });
          d.items.forEach((item, k) => { S.results[from + k] = item; });
          S.commitPos += slice.length;
          updateProgress(S.commitPos, S.rows.length);
        }
      } catch (x) {
        S.commitError = x.message || "Import was interrupted.";
      }
      window.removeEventListener("beforeunload", guardUnload);
      S.busy = false;
      renderDone();
    }

    const tally = () => S.results.reduce((a, r) => { if (r) a[r.status] = (a[r.status] || 0) + 1; return a; }, {});

    function renderDone() {
      const t = tally();
      const imported = t.imported || 0, skipped = t.duplicate || 0, bad = (t.invalid || 0) + (t.failed || 0);
      const remaining = S.rows.length - S.commitPos;
      const rejected = S.rows.map((row, i) => ({ row, res: S.results[i] }))
        .filter((x) => x.res && (x.res.status === "failed" || x.res.status === "invalid"));
      setBody(`
        ${S.commitError ? `<div class="error" role="alert"><b>The import was interrupted.</b> ${esc(S.commitError)}<br>
          ${n(S.commitPos)} of ${n(S.rows.length)} rows were processed before it stopped. You can continue safely — students already saved are never added twice.</div>` : ""}
        ${!S.commitError && imported === 0 ? `<div class="error" role="alert"><b>Nothing new was imported.</b> ${skipped && !bad ? "All of these students are already registered." : "See the reasons below."}</div>` : ""}
        ${!S.commitError && imported > 0 ? `<div class="notice imp-done"><b>Import finished.</b> ${n(imported)} student${imported === 1 ? "" : "s"} added.</div>` : ""}
        <div class="imp-chips">
          <div class="imp-chip ok"><strong>${n(imported)}</strong><span>imported</span></div>
          <div class="imp-chip warn"><strong>${n(skipped)}</strong><span>already registered (skipped)</span></div>
          <div class="imp-chip bad"><strong>${n(bad)}</strong><span>not imported</span></div>
          ${remaining > 0 ? `<div class="imp-chip"><strong>${n(remaining)}</strong><span>not processed yet</span></div>` : ""}
        </div>
        ${rejected.length ? `<h3 class="imp-h">Rows that were not imported</h3>
          <div class="table-wrap imp-problems"><table class="data-table">
            <thead><tr><th>Row</th><th>Name</th><th>Mobile</th><th>Why</th></tr></thead>
            <tbody>${rejected.slice(0, PROBLEM_ROWS_SHOWN).map(({ row, res }) => `
              <tr><td>${row.rowNumber}</td><td>${esc(cell(row, S.mapping.full_name) || "—")}</td><td>${esc(cell(row, S.mapping.mobile_number) || "—")}</td>
              <td class="imp-why">${esc(res.message || "No reason returned by the server.")}</td></tr>`).join("")}
            </tbody></table></div>
          ${rejected.length > PROBLEM_ROWS_SHOWN ? `<p class="muted">Showing the first ${PROBLEM_ROWS_SHOWN} of ${n(rejected.length)}. The CSV report has all of them.</p>` : ""}` : ""}
        <div class="modal-actions">
          <button class="btn" data-act="dl-report">Download report (CSV)</button>
          ${S.commitError ? `<button class="btn primary" data-act="resume">Continue import</button>` : `
          <button class="btn" data-act="restart">Import another file</button>
          <button class="btn primary" data-act="close">Done</button>`}
        </div>`);
    }

    // ------------------------------------------------------------------
    // CSV report (every row + its new Passport ID)
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
    const LABEL = { imported: "Imported", duplicate: "Skipped – already registered", invalid: "Not imported", failed: "Not imported – error", ready: "Not processed" };

    function downloadReport() {
      const base = S.filename.replace(/\.[^.]+$/, "") || "students";
      const lines = S.rows.map((row, i) => {
        const r = S.results[i] || {};
        return [row.rowNumber, cell(row, S.mapping.full_name), cell(row, S.mapping.mobile_number), cell(row, S.mapping.institution_name),
          LABEL[r.status] || "Not processed", r.passport_id || "", r.message || ""];
      });
      downloadCsv(`${base}-import-report.csv`, ["Row", "Student name", "Mobile", "Institution", "Result", "Passport ID", "Note"], lines);
    }

    // ------------------------------------------------------------------
    // Events
    // ------------------------------------------------------------------
    function onClick(e) {
      const t = e.target.closest("[data-act]");
      if (!t || t.disabled) return;
      switch (t.dataset.act) {
        case "close": closeModal(); break;
        case "restart": reset(); renderUpload(); break;
        case "import": runImport(); break;
        case "resume": continueImport(); break;
        case "dl-report": downloadReport(); break;
      }
    }

    function onChange(e) {
      const t = e.target;
      if (t.id === "impFile" && t.files && t.files[0]) { loadFile(t.files[0]); t.value = ""; }
      else if (t.id === "impSheet") { selectSheet(t.value); renderConfirm(); }
    }

    document.addEventListener("keydown", (e) => {
      const m = el("importModal");
      if (e.key === "Escape" && m && !m.classList.contains("hidden")) closeModal();
    });
  })();