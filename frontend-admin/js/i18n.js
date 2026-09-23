// UPITS 2026 Admin Portal — Hindi/English toggle for the fixed UI chrome
// (sidebar nav, section headers, buttons, table column labels). Record
// data (student names, answers, hall names as entered) is never
// translated — only the portal's own interface text.
//
// Uses the SAME localStorage key ("upits_lang") as the student-facing
// app's js/i18n.js, so the whole UPITS product stays in one language
// across screens if it's ever loaded in the same browser/kiosk.

window.upitsAdminI18n = (function () {
  const STORAGE_KEY = "upits_lang";
  const DEFAULT_LANG = "en";

  const STRINGS = {
    en: {
      nav_dashboard: "Dashboard",
      nav_students: "Students",
      nav_checkpoints: "Checkpoints & QR",
      nav_reviews: "Answer Review",
      nav_scans: "Scan Monitoring",
      nav_fraud: "Fraud Review",
      nav_certificates: "Certificates",
      nav_redemption: "Redemption Desk",
      nav_feedback: "Feedback Analytics",
      nav_institutions: "Institutions",
      nav_footfall: "Footfall & Routes",
      nav_exports: "Reports & Export",
      nav_audit: "Audit Log",
      nav_consent: "Consent & Data",
      nav_settings: "Settings",
      staff_name_label: "Acting as (for audit log)",
      title_dashboard: "Dashboard",
      title_students: "Students",
      title_checkpoints: "Checkpoints & QR",
      title_reviews: "Manual Answer Review",
      title_scans: "QR & Scan Monitoring",
      title_fraud: "Fraud / Anti-Cheating Review",
      title_certificates: "Certificate Management",
      title_redemption: "Passport Redemption Desk",
      title_feedback: "Feedback & Questionnaire Analytics",
      title_institutions: "School / College Analytics",
      title_footfall: "Route / Hall-Wise Footfall",
      title_exports: "Reports & CSV Export",
      title_audit: "Audit Trail",
      title_consent: "Consent & Data Controls",
      title_settings: "Settings & Configuration",
      btn_refresh: "Refresh",
      btn_search: "Search",
      btn_export: "Export CSV",
      btn_new_checkpoint: "+ New Checkpoint",
    },
    hi: {
      nav_dashboard: "डैशबोर्ड",
      nav_students: "छात्र",
      nav_checkpoints: "चेकपॉइंट और QR",
      nav_reviews: "उत्तर समीक्षा",
      nav_scans: "स्कैन निगरानी",
      nav_fraud: "धोखाधड़ी समीक्षा",
      nav_certificates: "प्रमाणपत्र",
      nav_redemption: "रिडेम्पशन डेस्क",
      nav_feedback: "फीडबैक विश्लेषण",
      nav_institutions: "संस्थान",
      nav_footfall: "फुटफॉल और रूट",
      nav_exports: "रिपोर्ट और निर्यात",
      nav_audit: "ऑडिट लॉग",
      nav_consent: "सहमति व डेटा",
      nav_settings: "सेटिंग्स",
      staff_name_label: "कार्यरत (ऑडिट लॉग हेतु)",
      title_dashboard: "डैशबोर्ड",
      title_students: "छात्र",
      title_checkpoints: "चेकपॉइंट और QR",
      title_reviews: "मैनुअल उत्तर समीक्षा",
      title_scans: "QR और स्कैन निगरानी",
      title_fraud: "धोखाधड़ी / फर्जीवाड़ा समीक्षा",
      title_certificates: "प्रमाणपत्र प्रबंधन",
      title_redemption: "पासपोर्ट रिडेम्पशन डेस्क",
      title_feedback: "फीडबैक और प्रश्नावली विश्लेषण",
      title_institutions: "स्कूल / कॉलेज विश्लेषण",
      title_footfall: "रूट / हॉल-वार फुटफॉल",
      title_exports: "रिपोर्ट और CSV निर्यात",
      title_audit: "ऑडिट ट्रेल",
      title_consent: "सहमति और डेटा नियंत्रण",
      title_settings: "सेटिंग्स और कॉन्फ़िगरेशन",
      btn_refresh: "रीफ्रेश करें",
      btn_search: "खोजें",
      btn_export: "CSV निर्यात करें",
      btn_new_checkpoint: "+ नया चेकपॉइंट",
    },
  };

  function currentLang() {
    return localStorage.getItem(STORAGE_KEY) || DEFAULT_LANG;
  }

  function t(key) {
    const lang = currentLang();
    return (STRINGS[lang] && STRINGS[lang][key]) || STRINGS.en[key] || key;
  }

  function applyStatic() {
    const lang = currentLang();
    document.querySelectorAll("[data-i18n]").forEach((el) => {
      const key = el.getAttribute("data-i18n");
      const val = (STRINGS[lang] && STRINGS[lang][key]) || STRINGS.en[key];
      if (val) el.textContent = val;
    });
    const toggle = document.getElementById("langToggle");
    if (toggle) toggle.textContent = lang === "en" ? "हिंदी" : "English";
  }

  function toggle() {
    const next = currentLang() === "en" ? "hi" : "en";
    localStorage.setItem(STORAGE_KEY, next);
    applyStatic();
    if (window.onLangChanged) window.onLangChanged();
  }

  function init() {
    applyStatic();
    const btn = document.getElementById("langToggle");
    if (btn) btn.addEventListener("click", toggle);
  }

  return { init, t, applyStatic, currentLang };
})();
