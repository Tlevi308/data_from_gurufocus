"""
שלב 5 — בניית הטבלה המסודרת.
================================================================================
פונקציה טהורה: payload גולמי נכנס, DataFrame + דוח יוצאים. אין רשת.
זה מאפשר לבדוק את כל לוגיקת היישור על JSON שמור, בלי לצרוך מהמכסה.

מה קורה כאן:
  1. איתור בלוק התקופות ופירוקו לרשומות (שלב 3)
  2. פתרון השדות מול המפתחות שקיימים (שלב 4)
  3. בניית העמודות לפי סדר המילון — עמודה חסרה נשארת ריקה ולא נעלמת
  4. יישור תקופות: fiscal -> קלנדרי, ובניית period_key
  5. סינון, הסרת כפילויות ומיון

──────────────────────────────────────────────────────────────────────────────
כלל היישור (alignment.quarter_shift_months, ברירת מחדל 2)
──────────────────────────────────────────────────────────────────────────────
מסיטים את סוף התקופה אחורה בחודשיים ולוקחים את הרבעון הקלנדרי של התוצאה.
בפועל: כל רבעון פיסקלי משויך לרבעון הקלנדרי של החודש הראשון שהוא מכסה.

    2026-03-31  ->  2026-01-31  ->  Q1   (רבעון שמכסה ינו'-מרץ)
    2025-09-30  ->  2025-07-31  ->  Q3   (חברה מיושרת ללוח השנה: אין שינוי)
    2025-12-31  ->  2025-10-31  ->  Q4   (רבעון שמכסה אוק'-דצמ')
    2026-01-31  ->  2025-11-30  ->  Q4   (ולא Q1 של השנה הבאה)

היתרון: נגזר מהתאריך עצמו, בלי טבלת חודשי סוף שנת כספים לכל חברה.
הפלט שומר רק את מפתח התקופה המיושר והנגזרות שלו.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field as dc_field
from typing import Any

import pandas as pd

from .fields import ALL_FIELDS, TEXT_FIELDS
from .parsing import (
    available_periods,
    block_to_records,
    extract_metadata,
    find_period_block,
    inventory_keys,
    to_number,
    to_period_end,
)
from .resolver import Resolution, mark_derived, resolve_fields, unmapped_api_keys

log = logging.getLogger(__name__)

# עמודות היישור שנוספות מעבר למילון השדות
ALIGNMENT_COLUMNS = [
    "period_key", "period_year", "period_quarter",
]


class ExtractionError(RuntimeError):
    """לא ניתן לבנות טבלה מה-payload."""


@dataclass
class ExtractReport:
    """כל מה שצריך כדי לשפוט אם השליפה הצליחה."""
    symbol: str
    period: str
    resolution: Resolution
    rows_raw: int = 0
    rows_kept: int = 0
    duplicates_dropped: int = 0
    duplicate_period_keys: list[str] = dc_field(default_factory=list)
    available_keys: list[str] = dc_field(default_factory=list)
    unmapped_keys: list[str] = dc_field(default_factory=list)
    metadata: dict[str, Any] = dc_field(default_factory=dict)
    date_min: str = ""
    date_max: str = ""

    @property
    def coverage(self) -> pd.DataFrame:
        return self.resolution.coverage

    def summary(self) -> str:
        res = self.resolution
        parts = [
            f"[{self.symbol}] {self.period}: {self.rows_kept} תקופות",
            f"{self.date_min} → {self.date_max}",
            f"שדות: {res.found_count}/{len(res.coverage)}",
        ]
        if res.missing:
            parts.append(f"חסרים: {len(res.missing)}")
        if self.duplicate_period_keys:
            parts.append(f"⚠️ period_key כפול: {len(self.duplicate_period_keys)}")
        return " | ".join(parts)


def _apply_alignment(frame: pd.DataFrame, shift_months: int) -> pd.DataFrame:
    """מוסיף את עמודות היישור על בסיס fiscal_period_end_date."""
    end = frame["fiscal_period_end_date"]

    anchor = end - pd.DateOffset(months=shift_months)
    frame["period_year"] = anchor.dt.year
    frame["period_quarter"] = "Q" + anchor.dt.quarter.astype(str)
    frame["period_key"] = (
        frame["period_year"].astype(str) + frame["period_quarter"]
    )  # "2026Q1" — מפתח החיבור לפאנל
    return frame


def build_frame(
    payload: dict,
    symbol: str,
    *,
    period: str = "quarterly",
    start_date: str = "1950-01-01",
    end_date: str = "",
    quarter_shift_months: int = 2,
) -> tuple[pd.DataFrame, ExtractReport]:
    """בונה טבלה מסודרת לטיקר אחד מתוך payload של fundamentals.

    Args:
        start_date / end_date: חיתוך על תאריך *סוף התקופה*.
            end_date ריק = בלי חיתוך עליון.
    """
    # --- 1. איתור ופירוק -----------------------------------------------------
    block = find_period_block(payload, period)
    if block is None:
        raise ExtractionError(
            f"לא נמצא בלוק '{period}' עבור {symbol}. "
            f"תקופות זמינות ב-payload: {available_periods(payload)}"
        )

    records = block_to_records(block)
    if not records:
        raise ExtractionError(f"בלוק '{period}' של {symbol} ריק")

    metadata = extract_metadata(payload)
    available = inventory_keys(records)

    # --- 2. פתרון שדות -------------------------------------------------------
    resolution = resolve_fields(available, metadata.keys())
    raw = pd.DataFrame(records)

    # --- 3. בניית העמודות ----------------------------------------------------
    out = pd.DataFrame(index=raw.index)
    for fld in ALL_FIELDS:
        source = resolution.mapping.get(fld.out)
        if source is not None and source in raw.columns:
            column = raw[source]
            out[fld.out] = (
                column.astype("string") if fld.out in TEXT_FIELDS
                else column.map(to_number)
            )
        elif fld.out in resolution.meta_mapping:
            # שדה ברמת החברה — אותו ערך לכל השורות
            out[fld.out] = metadata.get(resolution.meta_mapping[fld.out])
        else:
            out[fld.out] = pd.NA  # עמודה חסרה נשארת — ולא נעלמת בשקט

    # ⚠️ עמודת symbol היא *תמיד* הטיקר שביקשנו, ולא זה ש-basic_information
    # מחזיר. GuruFocus עשויים להחזיר סמל קנוני שונה (רישום כפול, ADR), ואם
    # ניקח אותו — שני טיקרים שונים בפאנל יקבלו את אותו symbol, יתמזגו זה
    # לתוך זה, ובדיקות תלויות-סדר יתבלבלו בין החברות בלי שום סימן אזהרה.
    api_symbol = str(out["symbol"].dropna().iloc[0]) if out["symbol"].notna().any() else ""
    if api_symbol and api_symbol.upper() != symbol.upper():
        log.warning(
            "[%s] ה-API מדווח symbol='%s'. עמודת symbol תישאר '%s' (מה שביקשנו); "
            "הסמל של ה-API נשמר ב-report.metadata.",
            symbol, api_symbol, symbol,
        )
    out["symbol"] = pd.Series(symbol, index=out.index, dtype="string")

    # --- 4. תאריכים, יישור וסינון -------------------------------------------
    out["fiscal_period_end_date"] = out["fiscal_period_end_date"].map(to_period_end)
    rows_raw = len(out)

    out = out[out["fiscal_period_end_date"].notna()]
    out = out[out["fiscal_period_end_date"] >= pd.Timestamp(start_date)]
    if end_date:
        out = out[out["fiscal_period_end_date"] <= pd.Timestamp(end_date)]
    if out.empty:
        raise ExtractionError(
            f"לא נותרו תקופות עבור {symbol} אחרי סינון התאריכים "
            f"(start_date={start_date}, end_date={end_date or 'ללא'})"
        )

    out = _apply_alignment(out, quarter_shift_months)

    # --- 5. כפילויות ומיון ---------------------------------------------------
    # אותה תקופה יכולה לחזור אחרי הצגה מחדש (restatement) — שומרים את האחרונה
    duplicates = int(out.duplicated(subset=["symbol", "fiscal_period_end_date"]).sum())
    out = out.drop_duplicates(subset=["symbol", "fiscal_period_end_date"], keep="last")
    out = out.sort_values("fiscal_period_end_date").reset_index(drop=True)

    # ⚠️ שני רבעונים על אותו period_key = יישור לא חד-ערכי. חייב טיפול
    # לפני חיבור לפאנל. קורה בלוחות שנה של 52/53 שבועות.
    dup_keys = out.loc[out["period_key"].duplicated(keep=False), "period_key"]
    duplicate_period_keys = sorted(dup_keys.unique().tolist())
    if duplicate_period_keys:
        log.warning("[%s] period_key לא חד-ערכי: %s", symbol, duplicate_period_keys)

    date_min = out["fiscal_period_end_date"].min()
    date_max = out["fiscal_period_end_date"].max()
    out["fiscal_period_end_date"] = out["fiscal_period_end_date"].dt.strftime("%Y-%m-%d")

    # symbol נקבע על ידינו ולא נשלף — הדוח משקף זאת
    resolution = Resolution(
        mapping=resolution.mapping,
        meta_mapping=resolution.meta_mapping,
        coverage=mark_derived(resolution.coverage, ["symbol"]),
    )

    report = ExtractReport(
        symbol=symbol,
        period=period,
        resolution=resolution,
        rows_raw=rows_raw,
        rows_kept=len(out),
        duplicates_dropped=duplicates,
        duplicate_period_keys=duplicate_period_keys,
        available_keys=available,
        unmapped_keys=unmapped_api_keys(available, resolution.mapping),
        metadata=metadata,
        date_min=date_min.strftime("%Y-%m-%d") if pd.notna(date_min) else "",
        date_max=date_max.strftime("%Y-%m-%d") if pd.notna(date_max) else "",
    )
    return out, report
