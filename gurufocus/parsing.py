"""
שלב 3 — פירוק ה-JSON לרשומות שטוחות.
================================================================================
פונקציות טהורות בלבד: JSON נכנס, רשומות יוצאות. אין רשת ואין קונפיגורציה.
זה מה שמאפשר לבדוק את כל השלב הזה בלי לגעת ב-API.

מבנה ה-JSON כפי שאומת מול ה-API החי (AAPL, 2026-07):

    {
      "basic_information": {"company", "currency", "exchange", "symbol", ...},
      "quarterly":  [ {row}, {row}, ... ],   # 119 שורות, בסדר עולה
      "annually":   [ {row}, ... ],          # 30 שורות
      "ttm":        {row},                   # שורה בודדת, לא רשימה
      "stockid":    "US01WD"
    }

    row = {
      "date": "2026-03",             # YYYY-MM — רזולוציית חודש, לא יום
      "filing_date": "2026-05-01",
      "balance_sheet":      {65 מפתחות},
      "income_statement":   {32 מפתחות},
      "cashflow_statement": {40 מפתחות}
    }

הערה על רזולוציית התאריך: מכיוון ש-``date`` הוא YYYY-MM בלבד, אי אפשר לזהות
מכאן דיווח של 53 שבועות. זו מגבלה של המקור, לא של הקוד.
"""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

# שמות הסקשנים המוכרים בתוך שורת תקופה
KNOWN_SECTIONS = ("balance_sheet", "income_statement", "cashflow_statement")

# ערכים שמייצגים "אין נתון"
_NULLISH = {"", "-", "--", "n/a", "na", "none", "nan", "null"}


# ---------------------------------------------------------------------------
# נרמול ערכים
# ---------------------------------------------------------------------------
def norm_key(value: Any) -> str:
    """נרמול שם שדה להשוואה עמידה בפני הבדלי כתיב.

    >>> norm_key("Short-Term Debt & Capital Lease Obligation")
    'short_term_debt_and_capital_lease_obligation'
    >>> norm_key("Property, Plant and Equipment")
    'property_plant_and_equipment'
    """
    text = str(value).strip().lower()
    text = text.replace("&", " and ").replace("/", " ")
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return re.sub(r"_+", "_", text).strip("_")


def to_number(value: Any) -> float | None:
    """המרה למספר. GuruFocus מחזירים לעיתים מחרוזות, פסיקים או סוגריים."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return None if pd.isna(value) else float(value)

    text = str(value).strip().replace(",", "")
    if text.lower() in _NULLISH:
        return None

    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    text = text.rstrip("%")

    try:
        number = float(text)
    except ValueError:
        return None
    return -number if negative else number


def to_period_end(value: Any) -> pd.Timestamp:
    """המרת ערך תקופה לתאריך סוף-תקופה.

    ⚠️ הסדר קריטי: YYYY-MM נבדק *לפני* ניסיון פענוח כתאריך מלא.
    ``pd.to_datetime('2026-03')`` מחזיר 2026-03-01, ולכן בלי הבדיקה המוקדמת
    כל התקופות היו נרשמות כ-1 בחודש במקום כסוף החודש.

    >>> to_period_end("2026-03").strftime("%Y-%m-%d")
    '2026-03-31'
    >>> to_period_end("2025").strftime("%Y-%m-%d")
    '2025-12-31'
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return pd.NaT

    text = str(value).strip()
    if not text:
        return pd.NaT

    if re.fullmatch(r"\d{4}-\d{1,2}", text):                       # YYYY-MM -> סוף חודש
        return (pd.to_datetime(text, format="%Y-%m") + pd.offsets.MonthEnd(0)).normalize()
    if re.fullmatch(r"\d{4}", text):                               # YYYY -> 31/12
        return pd.Timestamp(int(text), 12, 31)

    parsed = pd.to_datetime(text, errors="coerce")
    return parsed.normalize() if pd.notna(parsed) else pd.NaT


# ---------------------------------------------------------------------------
# איתור בלוק התקופות
# ---------------------------------------------------------------------------
def find_period_block(payload: dict, period: str) -> list | dict | None:
    """מאתר את בלוק התקופות ב-payload.

    מחפש קודם ברמה העליונה (המבנה בפועל), ואם לא נמצא — סורק לעומק,
    כדי לשרוד שינוי עתידי כמו עטיפה ב-{"data": {...}}.
    """
    target = norm_key(period)

    for key, value in payload.items():
        if norm_key(key) == target and value:
            return value

    queue: list[Any] = [payload]
    while queue:
        node = queue.pop(0)
        if isinstance(node, dict):
            for key, value in node.items():
                if norm_key(key) == target and isinstance(value, (dict, list)) and value:
                    return value
            queue.extend(v for v in node.values() if isinstance(v, (dict, list)))
        elif isinstance(node, list):
            queue.extend(v for v in node if isinstance(v, (dict, list)))
    return None


def available_periods(payload: dict) -> list[str]:
    """שמות התקופות שקיימות ב-payload — לשימוש בהודעות שגיאה מועילות."""
    return [k for k, v in payload.items() if isinstance(v, (list, dict)) and v]


# ---------------------------------------------------------------------------
# שיטוח שורות
# ---------------------------------------------------------------------------
def flatten_row(row: dict) -> dict[str, Any]:
    """משטח שורת תקופה אחת למילון ``"section::key" -> value``.

    כל מפתח נשמר *רק* בצורתו המלאה עם שם הסקשן. זו הנקודה שמונעת את בעיית
    ההתנגשות: ``net_income`` קיים גם ב-income_statement וגם (בשם דומה)
    ב-cashflow_statement, והמילון בשלב 2 מציין מאיזה סקשן לקחת.
    שדות ברמת השורה (date, filing_date) נשמרים בלי קידומת.
    """
    flat: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, dict):
            for sub_key, sub_value in value.items():
                if isinstance(sub_value, (dict, list)):
                    continue  # אין קינון עמוק יותר ב-API הזה
                flat[f"{key}::{sub_key}"] = sub_value
        elif not isinstance(value, list):
            flat[str(key)] = value
    return flat


def block_to_records(block: list | dict) -> list[dict[str, Any]]:
    """ממיר בלוק תקופות לרשימת רשומות שטוחות (רשומה = תקופה).

    תומך בשלושת המבנים האפשריים:
      (א) רשימת שורות  — המבנה בפועל של quarterly / annually
      (ב) שורה בודדת   — המבנה של ttm
      (ג) column-oriented — ``{"balance_sheet": {"total_assets": [...]}}``
          לא בשימוש היום, נשמר כרשת ביטחון לשינוי עתידי ב-API.
    """
    # (א) רשימת שורות
    if isinstance(block, list):
        return [flatten_row(row) for row in block if isinstance(row, dict)]

    if not isinstance(block, dict):
        return []

    # (ב) שורה בודדת — מזוהה לפי נוכחות סקשן מוכר שהוא dict של סקלרים
    looks_like_row = any(
        isinstance(block.get(s), dict)
        and not any(isinstance(v, list) for v in block[s].values())
        for s in KNOWN_SECTIONS
    )
    if looks_like_row:
        return [flatten_row(block)]

    # (ג) column-oriented
    series: dict[str, list] = {}
    for key, value in block.items():
        if isinstance(value, dict):
            for sub_key, sub_value in value.items():
                if isinstance(sub_value, list):
                    series[f"{key}::{sub_key}"] = sub_value
        elif isinstance(value, list):
            series[str(key)] = value

    if not series:
        return []

    length = max(len(v) for v in series.values())
    return [
        {k: (v[i] if i < len(v) else None) for k, v in series.items()}
        for i in range(length)
    ]


def extract_metadata(payload: dict) -> dict[str, Any]:
    """שולף רק את מטא-נתוני החברה שנשארו בסכימת הפלט."""
    meta = payload.get("basic_information")
    if not isinstance(meta, dict):
        return {}
    return {key: meta.get(key) for key in ("symbol", "company") if key in meta}


def inventory_keys(records: list[dict]) -> list[str]:
    """כל המפתחות שנצפו בפועל ב-JSON — הבסיס לשלב הפתרון ולדוח RawKeys."""
    return sorted({key for record in records for key in record})
