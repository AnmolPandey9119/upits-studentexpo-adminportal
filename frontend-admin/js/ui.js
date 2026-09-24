// UPITS 2026 Admin Portal — presentation helpers only.
// Loaded AFTER admin.js. It never calls the API, never reads or writes
// login/session data, and never touches admin.js functions or state.
// It only (1) opens/closes the mobile menu drawer and (2) shows which
// event day it is (25–29 Sept 2026, IST) in the top bar.
// If this file fails to load, the portal still works exactly as before.

(function () {
    "use strict";
    const $ = (id) => document.getElementById(id);
  
    // ------------------------------------------------------------
    // 1. Mobile / tablet menu drawer
    // ------------------------------------------------------------
    function initDrawer() {
      const btn = $("menuBtn");
      const scrim = $("scrim");
      if (!btn || !scrim) return;
  
      const setOpen = (open) => {
        document.body.classList.toggle("nav-open", open);
        btn.setAttribute("aria-expanded", String(open));
        btn.setAttribute("aria-label", open ? "Close menu" : "Open menu");
      };
  
      btn.addEventListener("click", () => setOpen(!document.body.classList.contains("nav-open")));
      scrim.addEventListener("click", () => setOpen(false));
      document.addEventListener("keydown", (e) => { if (e.key === "Escape") setOpen(false); });
      window.addEventListener("resize", () => { if (window.innerWidth > 1024) setOpen(false); });
  
      // choosing a section closes the drawer and returns to the top of the page
      document.querySelectorAll(".nav").forEach((b) =>
        b.addEventListener("click", () => { setOpen(false); window.scrollTo(0, 0); })
      );
      const logout = $("logoutBtn");
      if (logout) logout.addEventListener("click", () => setOpen(false));
    }
  
    // ------------------------------------------------------------
    // 2. Event-day indicator (pure date maths, no network)
    // ------------------------------------------------------------
    const START = Date.UTC(2026, 8, 25);   // 25 Sept 2026
    const DAYS = 5;
    const TEXT = {
      en: {
        tomorrow: "Starts tomorrow",
        soon: (n) => "Starts in " + n + " days",
        live: (k) => "Live now, day " + k + " of " + DAYS,
        done: "Event concluded",
      },
      hi: {
        tomorrow: "कल शुरू होगा",
        soon: (n) => n + " दिन में शुरू",
        live: (k) => "अभी चालू, दिन " + k + " / " + DAYS,
        done: "कार्यक्रम संपन्न",
      },
    };
  
    function todayIST() {
      const ymd = new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" }); // YYYY-MM-DD
      const [y, m, d] = ymd.split("-").map(Number);
      return Date.UTC(y, m - 1, d);
    }
  
    function updateEventStatus() {
      const el = $("eventStatus");
      if (!el) return;
      const lang = localStorage.getItem("upits_lang") === "hi" ? "hi" : "en";
      const t = TEXT[lang];
      const diff = Math.round((todayIST() - START) / 86400000);   // <0 = before day 1
  
      el.classList.remove("live", "done");
      document.querySelectorAll("#appRoot .day").forEach((chip, i) => {
        chip.classList.toggle("past", diff > i);
        chip.classList.toggle("today", diff === i);
      });
  
      if (diff < 0) {
        el.textContent = diff === -1 ? t.tomorrow : t.soon(-diff);
      } else if (diff < DAYS) {
        el.textContent = t.live(diff + 1);
        el.classList.add("live");
      } else {
        el.textContent = t.done;
        el.classList.add("done");
      }
      el.classList.remove("hidden");
    }
  
    function init() {
      try { initDrawer(); } catch (e) { /* cosmetic only */ }
      try {
        updateEventStatus();
        setInterval(updateEventStatus, 10 * 60 * 1000);
        const lang = $("langToggle");
        if (lang) lang.addEventListener("click", () => setTimeout(updateEventStatus, 0));
      } catch (e) { /* cosmetic only */ }
    }
  
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
    else init();
  })();