// UPITS 2026 Admin Portal — printable participation certificates.
//
// window.printCertificates(list)
//   list: [{ name, institution, serial, passportId, issuedAt }]
//
// Opens a print-ready window (A4 landscape, one certificate per page).
// In the print dialog choose "Save as PDF" to get a PDF — for a whole
// batch this gives ONE PDF with one page per student.
//
// Change the wording / event details in CERT below; nothing else needs
// to be touched. Works entirely in the browser (no backend call).

(function () {
    "use strict";
  
    const CERT = {
      title: "Certificate of Participation",
      event: "UP International Trade Show 2026 (4th Edition)",
      dates: "25 – 29 September 2026",
      organisers: "Jointly organised by Government of Uttar Pradesh and India Exposition Mart Limited (IEML)",
      signatories: ["Authorised Signatory", "Authorised Signatory"],
    };
  
    const escHtml = (v) =>
      String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[c]));
  
    const assetUrl = (file) => new URL("assets/" + file, window.location.href).href;
  
    const issueDate = (iso) => {
      const d = iso ? new Date(iso) : new Date();
      return (isNaN(d) ? new Date() : d).toLocaleDateString("en-IN", { day: "2-digit", month: "long", year: "numeric" });
    };
  
    function certificateHtml(c) {
      const inst = c.institution ? `<p class="of">of <b>${escHtml(c.institution)}</b></p>` : "";
      return `
      <section class="cert">
        <div class="tri"><i></i><i></i><i></i></div>
        <div class="frame">
          <header>
            <img src="${assetUrl("upits-logo.png")}" alt="UPITS">
            <img src="${assetUrl("ieml-logo.png")}" alt="IEML">
          </header>
          <h1>${escHtml(CERT.title)}</h1>
          <p class="lead">This is to certify that</p>
          <h2 class="name">${escHtml(c.name)}</h2>
          ${inst}
          <p class="body">has participated in the <b>${escHtml(CERT.event)}</b><br>held on ${escHtml(CERT.dates)}.</p>
          <p class="org">${escHtml(CERT.organisers)}</p>
          <footer>
            <div class="sig"><span></span>${escHtml(CERT.signatories[0])}</div>
            <div class="meta">
              <div><small>Certificate No.</small><b>${escHtml(c.serial)}</b></div>
              ${c.passportId ? `<div><small>Passport ID</small><b>${escHtml(c.passportId)}</b></div>` : ""}
              <div><small>Date of issue</small><b>${escHtml(issueDate(c.issuedAt))}</b></div>
            </div>
            <div class="sig"><span></span>${escHtml(CERT.signatories[1])}</div>
          </footer>
        </div>
      </section>`;
    }
  
    const CSS = `
      @page { size: A4 landscape; margin: 0; }
      * { box-sizing: border-box; }
      html, body { margin: 0; padding: 0; background: #e9e6ef; }
      body { font-family: "Plus Jakarta Sans", "Segoe UI", Arial, sans-serif; color: #1f1a3d;
             -webkit-print-color-adjust: exact; print-color-adjust: exact; }
      .cert { position: relative; width: 297mm; height: 209mm; margin: 0 auto 8mm; background: #fffdf9;
              overflow: hidden; page-break-after: always; break-after: page; }
      .cert:last-child { page-break-after: auto; break-after: auto; margin-bottom: 0; }
      .tri { position: absolute; top: 0; left: 0; right: 0; height: 4mm; display: flex; }
      .tri i { flex: 1; } .tri i:nth-child(1){ background:#FF9933; } .tri i:nth-child(2){ background:#fff; } .tri i:nth-child(3){ background:#138808; }
      .frame { position: absolute; inset: 10mm; border: 1.6mm double #b8862b; padding: 9mm 16mm 8mm;
               display: flex; flex-direction: column; align-items: center; text-align: center; }
      header { width: 100%; display: flex; justify-content: space-between; align-items: center; height: 22mm; }
      header img { height: 20mm; width: auto; max-width: 70mm; object-fit: contain; }
      h1 { margin: 9mm 0 0; font-family: Georgia, "Times New Roman", serif; font-size: 30pt; letter-spacing: .06em;
           text-transform: uppercase; color: #8a1c5c; font-weight: 700; }
      .lead { margin: 10mm 0 0; font-size: 12pt; color: #5b5675; }
      .name { margin: 5mm 0 0; font-family: Georgia, "Times New Roman", serif; font-style: italic; font-weight: 700;
              font-size: 34pt; line-height: 1.15; color: #1f1a3d; padding: 0 8mm 2mm; border-bottom: .4mm solid #b8862b;
              max-width: 100%; overflow-wrap: anywhere; }
      .of { margin: 6mm 0 0; font-size: 13pt; color: #3a3560; max-width: 100%; overflow-wrap: anywhere; }
      .body { margin: 10mm 0 0; font-size: 13pt; line-height: 1.6; color: #3a3560; }
      .org { margin: 5mm 0 0; font-size: 9.5pt; color: #7a7592; }
      footer { margin-top: auto; width: 100%; display: flex; justify-content: space-between; align-items: flex-end; gap: 8mm; }
      .sig { width: 46mm; flex: none; font-size: 9.5pt; color: #3a3560; }
      .sig span { display: block; border-top: .3mm solid #3a3560; margin-bottom: 1.5mm; }
      .meta { display: flex; gap: 9mm; justify-content: center; text-align: center; white-space: nowrap; }
      .meta small { display: block; font-size: 7.5pt; letter-spacing: .08em; text-transform: uppercase; color: #7a7592; }
      .meta b { font-size: 9.5pt; font-family: "Courier New", monospace; }
      .toolbar { position: sticky; top: 0; z-index: 5; display: flex; gap: 10px; align-items: center; justify-content: center;
                 padding: 10px; background: #1f1a3d; color: #fff; font-size: 14px; }
      .toolbar button { font: inherit; font-weight: 700; padding: 8px 16px; border: 0; border-radius: 8px; cursor: pointer; background: #ff9933; color: #1f1a3d; }
      @media print { html, body { background: #fff; } .cert { margin: 0; } .toolbar { display: none; } }
    `;
  
    window.printCertificates = function printCertificates(list) {
      const items = (list || []).filter((c) => c && c.name && c.serial);
      if (!items.length) {
        if (typeof toast === "function") toast("No certificates to print.", true);
        return;
      }
      const w = window.open("", "_blank");
      if (!w) {
        if (typeof toast === "function") toast("Your browser blocked the pop-up. Allow pop-ups for this site and try again.", true);
        return;
      }
      const count = items.length;
      w.document.open();
      w.document.write(`<!doctype html><html lang="en"><head><meta charset="utf-8">
        <title>UPITS 2026 — ${count} certificate${count === 1 ? "" : "s"}</title>
        <link rel="preconnect" href="https://fonts.googleapis.com">
        <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;600;700&display=swap" rel="stylesheet">
        <style>${CSS}</style></head><body>
        <div class="toolbar"><span>${count} certificate${count === 1 ? "" : "s"} ready.
          In the print window choose <b>Save as PDF</b> (or a printer).</span>
          <button onclick="window.print()">Print / Save as PDF</button></div>
        ${items.map(certificateHtml).join("")}
        </body></html>`);
      w.document.close();
      // Give logos + font a moment, then open the print dialog automatically.
      let printed = false;
      const go = () => { if (printed) return; printed = true; try { w.focus(); w.print(); } catch (_) { /* toolbar button still works */ } };
      w.addEventListener("load", () => setTimeout(go, 600));
      setTimeout(go, 3000); // fallback in case the load event was missed
  
    };
  })();