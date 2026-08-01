"""
שלב 6 — העשרה מ-SEC Filings.
================================================================================
מוסיף form_type / accession_number / cik / filing_url.

──────────────────────────────────────────────────────────────────────────────
למה החיבור כאן מדויק ולא הערכה
──────────────────────────────────────────────────────────────────────────────
endpoint ה-fundamentals כבר מחזיר ``filing_date`` לכל תקופה (כיסוי 100% —
אומת על 119 רבעונים של AAPL). לכן החיבור הוא **התאמה מדויקת על filing_date**,
ולא merge_asof שמנחש איזה דיווח שייך לאיזו תקופה.

merge_asof נשאר כנפילה-לאחור בלבד, לתקופות שבהן ההתאמה המדויקת נכשלה
(למשל אם GuruFocus עיגלו תאריך). כל שורה מקבלת ``filing_match`` שאומרת
איך היא הותאמה: exact / asof / none — כדי שלא תסיקו מסקנה מהתאמה מנוחשת.

──────────────────────────────────────────────────────────────────────────────
מגבלת כיסוי — חשוב
──────────────────────────────────────────────────────────────────────────────
ה-endpoint מחזיר היסטוריה קצרה בהרבה מ-fundamentals: 38 דיווחים מול 119
רבעונים ב-AAPL. ולכן טבעי שרוב התקופות ההיסטוריות יישארו בלי form_type.
זה לא באג — הדוח מציין במפורש כמה שורות הותאמו.
"""

from __future__ import annotations

import logging

import pandas as pd

from .fields import FILING_FIELDS
from .parsing import norm_key

log = logging.getLogger(__name__)

# רק דיווחים תקופתיים. 8-K/S-1 וכו' אינם דוחות כספיים תקופתיים ואין
# לשייך אותם לרבעון.
PERIODIC_FORMS = ("10-Q", "10-K", "20-F", "40-F", "6-K")

# חלון הנפילה-לאחור: דיווח שמוגש יותר מ-120 יום אחרי סוף התקופה
# כמעט תמיד שייך לתקופה אחרת.
ASOF_TOLERANCE_DAYS = 120


def _normalize_filings(records: list[dict]) -> pd.DataFrame:
    """מנרמל את תשובת ה-endpoint לטבלה עם העמודות שאנחנו צריכים."""
    if not records:
        return pd.DataFrame()

    frame = pd.DataFrame(records)
    frame.columns = [norm_key(c) for c in frame.columns]

    if "filing_date" not in frame.columns or "form_type" not in frame.columns:
        log.warning("תשובת filings חסרה filing_date/form_type — עמודות: %s",
                    list(frame.columns))
        return pd.DataFrame()

    keep = ["filing_date", *[c for c in FILING_FIELDS if c in frame.columns]]
    frame = frame[[c for c in dict.fromkeys(keep) if c in frame.columns]].copy()

    forms = frame["form_type"].astype(str).str.upper().str.strip()
    frame = frame[forms.str.startswith(PERIODIC_FORMS)]

    frame["filing_date"] = pd.to_datetime(frame["filing_date"], errors="coerce")
    frame = frame.dropna(subset=["filing_date"])

    # אותו תאריך יכול להופיע פעמיים (10-Q ותיקון). שומרים את הראשון.
    frame = frame.sort_values("filing_date").drop_duplicates(
        subset=["filing_date"], keep="first"
    )
    return frame.reset_index(drop=True)


def attach_filings(
    frame: pd.DataFrame,
    filing_records: list[dict],
) -> tuple[pd.DataFrame, dict]:
    """מצרף מטא-נתוני דיווח לטבלת התקופות.

    Returns:
        (הטבלה המועשרת, דוח התאמה)
    """
    out = frame.copy()
    added = [c for c in FILING_FIELDS if c != "filing_date"]
    for column in added:
        if column not in out.columns:
            out[column] = pd.NA
    out["filing_match"] = "none"

    filings = _normalize_filings(filing_records)
    report = {
        "filings_available": len(filings),
        "matched_exact": 0,
        "matched_asof": 0,
        "unmatched": len(out),
    }
    if filings.empty:
        log.warning("endpoint ה-filings לא החזיר רשומות תקופתיות שמישות")
        return out, report

    value_columns = [c for c in added if c in filings.columns]
    if not value_columns:
        return out, report

    out["_filing_dt"] = pd.to_datetime(out.get("filing_date"), errors="coerce")

    # --- א. התאמה מדויקת על filing_date ------------------------------------
    lookup = filings.set_index("filing_date")[value_columns]
    exact = out["_filing_dt"].map(lambda d: d if pd.notna(d) else pd.NaT)
    joined = lookup.reindex(exact.values)
    joined.index = out.index

    matched = joined[value_columns].notna().any(axis=1)
    for column in value_columns:
        out.loc[matched, column] = joined.loc[matched, column]
    out.loc[matched, "filing_match"] = "exact"
    report["matched_exact"] = int(matched.sum())

    # --- ב. נפילה-לאחור: הדיווח הראשון שאחרי סוף התקופה --------------------
    pending = ~matched
    if pending.any():
        left = (
            out.loc[pending, ["fiscal_period_end_date"]]
            .assign(_pe=lambda d: pd.to_datetime(d["fiscal_period_end_date"],
                                                 errors="coerce"))
            .dropna(subset=["_pe"])
            .sort_values("_pe")
        )
        if not left.empty:
            asof = pd.merge_asof(
                left,
                filings.rename(columns={"filing_date": "_fd"}).sort_values("_fd"),
                left_on="_pe",
                right_on="_fd",
                direction="forward",
                tolerance=pd.Timedelta(days=ASOF_TOLERANCE_DAYS),
            )
            asof.index = left.index
            hit = asof[value_columns].notna().any(axis=1)
            for column in value_columns:
                out.loc[hit[hit].index, column] = asof.loc[hit, column]
            out.loc[hit[hit].index, "filing_match"] = "asof"
            report["matched_asof"] = int(hit.sum())

    report["unmatched"] = int((out["filing_match"] == "none").sum())
    report["filings_date_min"] = filings["filing_date"].min().strftime("%Y-%m-%d")
    report["filings_date_max"] = filings["filing_date"].max().strftime("%Y-%m-%d")
    out = out.drop(columns=["_filing_dt"], errors="ignore")

    # מספר השורות שהותאמו יכול לעלות על מספר הדיווחים: דוח 10-K אחד מקבל
    # אותו filing_date בכמה שורות תקופה, וכולן מתאימות אליו. זה תקין.
    log.info(
        "filings: %d שורות הותאמו במדויק, %d בהערכה, %d ללא התאמה "
        "(%d דיווחים תקופתיים זמינים, %s → %s)",
        report["matched_exact"], report["matched_asof"], report["unmatched"],
        report["filings_available"], report["filings_date_min"], report["filings_date_max"],
    )
    if report["unmatched"]:
        log.info(
            "  %d תקופות בלי form_type — ה-endpoint מחזיר היסטוריה קצרה "
            "מ-fundamentals. זו מגבלת המקור, לא כשל.", report["unmatched"],
        )
    return out, report
