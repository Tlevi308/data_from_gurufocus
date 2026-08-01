"""
שלב 4 — פתרון שדות: מה שביקשנו -> מה שקיים ב-JSON בפועל.
================================================================================
פונקציות טהורות. השלב הזה הוא רשת הביטחון של הפרוייקט: אם GuruFocus ישנו שם
של מפתח, הפלט לא ישתנה בשקט לעמודה ריקה — דוח הכיסוי יסמן אותו כ-MISSING.

סדר החיפוש לכל שדה:
    1. section::api_key   — הצורה המדויקת. מנצחת תמיד ומונעת התנגשות סקשנים.
    2. section::alias     — כינויים בתוך אותו סקשן.
    3. api_key / alias    — בלי סקשן, לשדות ברמת השורה (date, filing_date).
    4. חיפוש מנורמל       — עמיד בפני הבדלי כתיב ("Total Assets" מול total_assets).

השלב מחזיר גם ``coverage``: טבלה של כל שדה מבוקש, המפתח שנתפס בפועל, ואיך.
זו הטבלה שאומרת לכם אם השליפה תקינה — ולא ניחוש.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .fields import FIELD_GROUPS, SECTION_META, SECTION_ROOT, Field
from .parsing import norm_key

STATUS_OK = "OK"
STATUS_MISSING = "MISSING"
STATUS_META = "META"      # נמצא ב-basic_information ולא בבלוק התקופות
STATUS_DERIVED = "DERIVED"  # הושלם על ידינו (למשל symbol מהבקשה)


@dataclass(frozen=True)
class Resolution:
    """תוצאת הפתרון: מיפוי לשימוש, ודוח לבדיקה אנושית."""
    mapping: dict[str, str]          # out -> המפתח האמיתי בתוך הרשומה
    meta_mapping: dict[str, str]     # out -> מפתח ב-basic_information
    coverage: pd.DataFrame

    @property
    def missing(self) -> list[str]:
        rows = self.coverage
        return rows.loc[rows["status"] == STATUS_MISSING, "output_column"].tolist()

    @property
    def found_count(self) -> int:
        return int((self.coverage["status"] != STATUS_MISSING).sum())


def _build_index(keys) -> dict[str, str]:
    """אינדקס חיפוש: צורה מנורמלת -> המפתח המקורי.

    כל מפתח נרשם פעמיים: המלא (``balance_sheet::total_assets``) והחשוף
    (``total_assets``). ``setdefault`` שומר על הראשון שנרשם, כך שהתוצאה
    דטרמיניסטית ולא תלויה בסדר המפתחות ב-JSON.
    """
    index: dict[str, str] = {}
    for key in keys:
        index.setdefault(norm_key(key), key)
        bare = str(key).split("::")[-1]
        index.setdefault(norm_key(bare), key)
    return index


def _match_field(field: Field, index: dict[str, str]) -> tuple[str | None, str]:
    """מאתר את המפתח המתאים לשדה. מחזיר (המפתח שנמצא, איך נמצא)."""
    # 1+2: הצורה המלאה עם הסקשן — עדיפות עליונה
    if field.section not in (SECTION_ROOT, SECTION_META):
        for candidate in field.candidates:
            qualified = f"{field.section}::{candidate}"
            hit = index.get(norm_key(qualified))
            if hit:
                return hit, qualified

    # 3+4: בלי סקשן — לשדות ברמת השורה, ולעמידות בפני שינוי מבנה
    for candidate in [*field.candidates, field.label, field.out]:
        hit = index.get(norm_key(candidate))
        if hit:
            return hit, candidate

    return None, ""


def resolve_fields(
    record_keys,
    metadata_keys=(),
    groups=FIELD_GROUPS,
) -> Resolution:
    """מפה את מילון השדות אל המפתחות שקיימים בפועל.

    Args:
        record_keys: המפתחות שנצפו ברשומות התקופה.
        metadata_keys: המפתחות שנצפו ב-basic_information.
        groups: קבוצות השדות לפתרון. ברירת מחדל — כל המילון.
    """
    record_index = _build_index(record_keys)
    meta_index = _build_index(metadata_keys)

    mapping: dict[str, str] = {}
    meta_mapping: dict[str, str] = {}
    rows: list[dict] = []

    for group_name, fields in groups:
        for field in fields:
            hit, via = _match_field(field, record_index)
            status = STATUS_OK if hit else STATUS_MISSING

            # שדות ברמת החברה: לחפש ב-basic_information אם לא נמצאו בשורה
            if not hit and field.section == SECTION_META:
                meta_hit, meta_via = _match_field(field, meta_index)
                if meta_hit:
                    meta_mapping[field.out] = meta_hit
                    hit, via, status = meta_hit, meta_via, STATUS_META

            if hit and status == STATUS_OK:
                mapping[field.out] = hit

            rows.append({
                "group": group_name,
                "requested_label": field.label,
                "output_column": field.out,
                "expected_section": field.section or "(row)",
                "api_key_found": hit or "",
                "matched_via": via,
                "status": status,
                "note": field.note,
            })

    coverage = pd.DataFrame(rows, columns=[
        "group", "requested_label", "output_column", "expected_section",
        "api_key_found", "matched_via", "status", "note",
    ])
    return Resolution(mapping=mapping, meta_mapping=meta_mapping, coverage=coverage)


def mark_derived(coverage: pd.DataFrame, columns) -> pd.DataFrame:
    """מסמן שדות שהערך שלהם נקבע על ידינו ולא נשלף מה-API.

    מוחל ללא תלות בסטטוס הקודם: גם אם המפתח קיים ב-JSON, אם בחרנו לא
    להשתמש בו הדוח חייב לומר DERIVED ולא OK — אחרת הוא מתאר מציאות שגויה.
    """
    out = coverage.copy()
    out.loc[out["output_column"].isin(list(columns)), "status"] = STATUS_DERIVED
    return out


def unmapped_api_keys(record_keys, mapping: dict[str, str]) -> list[str]:
    """מפתחות שקיימים ב-API אך לא נשלפים לפלט.

    שימושי כדי לגלות שדות שכדאי להוסיף למילון בשלב 2.
    """
    used = set(mapping.values())
    return sorted(k for k in record_keys if k not in used)
