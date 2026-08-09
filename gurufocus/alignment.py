"""
כלל היישור — תאריך קלנדרי אחד ← רבעון אחד.
================================================================================
מקור האמת היחיד לשיוך תאריך לרבעון. משמש את :mod:`gurufocus.extract` על
``fiscal_period_end_date``, ויושב במודול משלו כדי שכל צרכן עתידי יקבל בדיוק
את אותו ``period_key`` במקום לשכפל את הכלל.

הכלל: מסיטים את התאריך אחורה ב-``shift_months`` ולוקחים את הרבעון הקלנדרי של
התוצאה. ברירת המחדל היא חודשיים (``alignment.quarter_shift_months``), וכך כל
רבעון פיסקלי משויך לרבעון של החודש הראשון שהוא מכסה.

    2026-03-31  ->  2026-01-31  ->  2026Q1
    2025-09-30  ->  2025-07-31  ->  2025Q3
    2026-01-31  ->  2025-11-30  ->  2025Q4   (ולא Q1 של השנה הבאה)
"""

from __future__ import annotations

import pandas as pd

# העמודות שהיישור מייצר, בסדר שבו הן נכתבות
ALIGNMENT_COLUMNS = ("period_year", "period_quarter", "period_key")


def validate_shift(shift_months: int) -> int:
    """מוודא שההזזה בטווח החוקי ומחזיר אותה כמספר שלם."""
    shift = int(shift_months)
    if not 0 <= shift <= 11:
        raise ValueError(
            f"quarter_shift_months חייב להיות בטווח 0-11 — התקבל {shift_months!r}"
        )
    return shift


def align_to_quarter(dates: pd.Series, shift_months: int) -> pd.DataFrame:
    """ממפה סדרת תאריכים ל-period_year / period_quarter / period_key.

    Args:
        dates: סדרת ``datetime64`` **ללא ערכים חסרים**.
        shift_months: מספר החודשים שמסיטים אחורה לפני לקיחת הרבעון.

    Raises:
        ValueError: כשיש תאריך חסר. שורה בלי תאריך לא יכולה לקבל רבעון, ומפתח
            כמו ``"nanQ1"`` או ``"<NA>"`` היה מתחבר בשקט לשורה הלא נכונה —
            או, גרוע מכך, לא מתחבר לאף שורה ונראה כמו נתון חסר לגיטימי.
    """
    shift = validate_shift(shift_months)
    values = pd.to_datetime(dates, errors="coerce")
    if values.isna().any():
        raise ValueError(
            "align_to_quarter קיבלה תאריך חסר או לא תקין — "
            "יש לסנן תאריכים ריקים לפני היישור"
        )

    anchor = values - pd.DateOffset(months=shift)
    year = anchor.dt.year
    quarter = "Q" + anchor.dt.quarter.astype(str)
    return pd.DataFrame(
        {
            "period_year": year,
            "period_quarter": quarter,
            "period_key": year.astype(str) + quarter,
        },
        index=values.index,
    )
