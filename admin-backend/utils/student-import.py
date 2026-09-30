"""
Student bulk import — pure logic (no FastAPI, no database).

Everything that decides "is this spreadsheet row acceptable, and what
exactly gets stored?" lives here so it can be unit-tested on its own.
The HTTP + database side is routers/student_import.py.

The browser reads the Excel file (SheetJS) and sends rows already keyed
by the field names below. This module is the single source of truth for
those fields, their header synonyms (used by the browser to auto-detect
columns) and every validation rule — the server re-validates everything
on commit, so the browser is never trusted.
"""

import os
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Fields
# ---------------------------------------------------------------------------
# `synonyms` are compared against normalised spreadsheet headers
# (lower-case, letters/digits only) — see normalize_header().
FIELDS: list[dict] = [
    {"key": "full_name", "label": "Student name", "required": True, "kind": "text",
     "synonyms": ["name", "fullname", "studentname", "nameofstudent", "nameofthestudent",
                  "studentfullname", "candidatename", "participantname"]},
    {"key": "mobile_number", "label": "Mobile number", "required": True, "kind": "mobile",
     "synonyms": ["mobile", "mobileno", "mobilenumber", "phone", "phoneno", "phonenumber",
                  "contact", "contactno", "contactnumber", "whatsapp", "whatsappnumber",
                  "studentmobile", "studentphone", "studentcontact"]},
    {"key": "institution_name", "label": "Institution", "required": True, "kind": "text",
     "synonyms": ["institution", "institutionname", "school", "schoolname", "college",
                  "collegename", "university", "organisation", "organization",
                  "nameofinstitution", "nameofschool", "nameofcollege", "schoolcollege",
                  "schoolcollegename", "institute", "institutename"]},
    {"key": "email", "label": "Email", "required": False, "kind": "email",
     "synonyms": ["email", "emailid", "emailaddress", "mail", "mailid"]},
    {"key": "student_category", "label": "Student category", "required": False, "kind": "text",
     "synonyms": ["category", "studentcategory", "studenttype", "type", "level"]},
    {"key": "class_or_course", "label": "Class / course", "required": False, "kind": "text",
     "synonyms": ["class", "classcourse", "classorcourse", "course", "standard", "grade",
                  "program", "programme", "stream", "branch", "classstandard", "yearcourse"]},
    {"key": "age_group", "label": "Age group", "required": False, "kind": "text",
     "synonyms": ["agegroup", "age", "agerange"]},
    {"key": "district_city", "label": "District / city", "required": False, "kind": "text",
     "synonyms": ["district", "city", "districtcity", "citydistrict", "town", "location",
                  "place", "cityvillage"]},
    {"key": "teacher_name", "label": "Teacher name", "required": False, "kind": "text",
     "synonyms": ["teacher", "teachername", "nameofteacher", "faculty", "facultyname",
                  "coordinator", "coordinatorname", "teacherincharge", "incharge", "mentor"]},
    {"key": "teacher_mobile", "label": "Teacher mobile", "required": False, "kind": "mobile",
     "synonyms": ["teachermobile", "teachermobileno", "teachermobilenumber", "teacherphone",
                  "teachercontact", "facultymobile", "coordinatormobile", "inchargemobile"]},
    {"key": "route_colour", "label": "Route colour", "required": False, "kind": "text",
     "synonyms": ["route", "routecolour", "routecolor", "colour", "color", "group"]},
]
FIELD_KEYS = [f["key"] for f in FIELDS]
_FIELD_BY_KEY = {f["key"]: f for f in FIELDS}

# Columns the importer writes to `students` (only those that actually
# exist in the live table are used — see routers/student_import.py).
INSERT_COLUMNS = [
    "passport_id", "full_name", "mobile_number", "email", "student_category",
    "class_or_course", "age_group", "institution_name", "district_city",
    "teacher_name", "teacher_mobile", "route_colour", "registration_status",
]
# Without these the import cannot work at all.
MUST_EXIST_COLUMNS = ["passport_id", "full_name", "mobile_number", "institution_name",
                      "registration_status"]

MAX_ROWS_PER_REQUEST = 300

# Passport ID format. Adjust via env if your student backend differs.
PASSPORT_ID_PREFIX = os.getenv("PASSPORT_ID_PREFIX", "UPITS-2026-")
PASSPORT_ID_DIGITS = int(os.getenv("PASSPORT_ID_DIGITS", "6"))


def format_passport_id(number: int) -> str:
    return f"{PASSPORT_ID_PREFIX}{number:0{PASSPORT_ID_DIGITS}d}"


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------
def normalize_header(header: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(header or "").lower())


# Leading characters that make Excel/Sheets treat a cell as a formula when
# the data is later exported to CSV and opened in a spreadsheet.
_FORMULA_LEAD = "=+-@\t\r"


def clean_text(value: Any) -> str:
    """Trim, collapse whitespace, drop control chars, neutralise formula
    injection (`=cmd|...`) so exports of imported data are safe to open."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value)
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text.lstrip(_FORMULA_LEAD).strip()


# ---------------------------------------------------------------------------
# Mobile numbers
# ---------------------------------------------------------------------------
_SCI = re.compile(r"^[+-]?\d+(\.\d+)?[eE][+-]?\d+$")
_INT_DOT0 = re.compile(r"^(\d+)\.0+$")


def normalize_mobile(value: Any) -> tuple[Optional[str], Optional[str]]:
    """Returns (10-digit Indian mobile, None) or (None, error message).

    Copes with what Excel does to phone numbers: 9876543210.0, 9.87654E+09,
    '+91 98765-43210', '098765 43210', '91-9876543210'."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, "Mobile number is missing."
    if isinstance(value, float):
        value = int(value) if value.is_integer() else str(value)
    text = str(value).strip()

    if _SCI.match(text):                       # 9.87654321E+09
        try:
            dec = Decimal(text)
            if dec == dec.to_integral_value():
                text = str(int(dec))
        except InvalidOperation:
            pass
    m = _INT_DOT0.match(text)                  # 9876543210.0
    if m:
        text = m.group(1)

    digits = re.sub(r"\D", "", text)
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    elif len(digits) == 13 and digits.startswith("091"):
        digits = digits[3:]

    if len(digits) != 10:
        return None, f"Mobile must be 10 digits (found {len(digits)})."
    if digits[0] not in "6789":
        return None, "Mobile must start with 6, 7, 8 or 9."
    if len(set(digits)) == 1:
        return None, "Mobile number looks invalid."
    return digits, None


def detect_mobile_style(sample: Optional[str]) -> str:
    """How the student backend stores mobiles — inferred from one existing
    row so imported students can log in with the same number format.
    Returns 'plain' (9876543210), 'plus91' (+919876543210) or '91' (919876543210)."""
    if not sample:
        return "plain"
    s = re.sub(r"[\s-]", "", sample)
    if re.fullmatch(r"\+91\d{10}", s):
        return "plus91"
    if re.fullmatch(r"91\d{10}", s):
        return "91"
    return "plain"


def to_storage_mobile(national: str, style: str) -> str:
    if style == "plus91":
        return "+91" + national
    if style == "91":
        return "91" + national
    return national


def mobile_variants(national: str) -> list[str]:
    """Every form an existing row's mobile_number might take."""
    return [national, "+91" + national, "91" + national, "0" + national]


def mobile_key(stored: str) -> str:
    """Last 10 digits — identity of a mobile regardless of storage style."""
    return re.sub(r"\D", "", stored or "")[-10:]


# ---------------------------------------------------------------------------
# Row validation
# ---------------------------------------------------------------------------
_EMAIL = re.compile(r"^[A-Za-z0-9._%+\-']+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


def validate_row(data: dict, max_lengths: Optional[dict] = None) -> dict:
    """Validate + normalise one spreadsheet row.

    Returns {"clean": {...}, "mobile_key": str|None, "errors": [...], "warnings": [...]}.
    `errors` block the row; `warnings` don't (an optional value was dropped).
    `clean` holds values exactly as they will be stored (except mobile,
    which is kept as the 10-digit national number until storage time)."""
    max_lengths = max_lengths or {}
    errors: list[str] = []
    warnings: list[str] = []
    clean: dict[str, Optional[str]] = {}

    for f in FIELDS:
        key = f["key"]
        raw = data.get(key)

        if f["kind"] == "mobile":
            if key == "mobile_number":
                national, err = normalize_mobile(raw)
                if err:
                    errors.append(err)
                clean[key] = national
            else:  # teacher_mobile — optional
                if raw is None or str(raw).strip() == "":
                    clean[key] = None
                else:
                    national, err = normalize_mobile(raw)
                    if err:
                        warnings.append(f"Teacher mobile ignored: {err}")
                    clean[key] = national
            continue

        text = clean_text(raw)

        if f["kind"] == "email":
            if not text:
                clean[key] = None
            elif _EMAIL.match(text) and len(text) <= 254:
                clean[key] = text.lower()
            else:
                warnings.append("Email ignored: not a valid address.")
                clean[key] = None
            continue

        if not text:
            if f["required"]:
                errors.append(f"{f['label']} is missing.")
            clean[key] = None
            continue

        limit = max_lengths.get(key)
        if limit and len(text) > limit:
            errors.append(f"{f['label']} is too long (max {limit} characters).")
        if key == "full_name":
            if len(text) < 2 or not re.search(r"[^\W\d_]", text):
                errors.append("Student name doesn't look valid.")
        clean[key] = text

    return {
        "clean": clean,
        "mobile_key": clean.get("mobile_number"),
        "errors": errors,
        "warnings": warnings,
    }