"""
שלב 9 — ייצוא.
================================================================================
אחריות יחידה: לכתוב את מה שכבר חושב. אין כאן שינוי נתונים מלבד סדר עמודות.

קובץ האקסל מכיל את כל מה שצריך כדי לשפוט את הריצה בלי להסתכל בקוד:

    Data       הפאנל עצמו
    Coverage   מיפוי השדות — מה נמצא, איפה, ואיך
    Nulls      אחוז ריקים לכל עמודה + הערך האחרון
    Checks     בדיקות התקינות
    Manifest   סיכום ריצה לכל טיקר
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)


EXCLUDED_OUTPUT_COLUMNS = {
    "currency",
    "exchange",
    "reporting_unit",
    "form_type",
    "accession_number",
    "cik",
    "filing_url",
    "filing_match",
    "calendar_year",
    "calendar_quarter",
}

STRUCTURAL_OUTPUT_COLUMNS = [
    "symbol",
    "company",
    "fiscal_period_end_date",
    "filing_date",
    "period_key",
    "period_year",
    "period_quarter",
    "run_date",
]

# הסדר שנקבע למחקר: מתחיל מיד אחרי run_date וללא כפילויות.
PRIMARY_OUTPUT_COLUMNS = [
    "tax_provision",
    "calc_tax_expense_quarterly",
    "pretax_income",
    "calc_raw_tax_rate_quarterly",
    "ebit",
    "calc_nopat_quarterly",
    "total_current_assets",
    "cash_and_cash_equivalents",
    "short_term_investments",
    "total_current_liabilities",
    "short_term_debt",
    "intangible_assets",
    "goodwill",
    "net_ppe",
    "calc_ic_raw",
    "calc_average_ic_raw_quarterly",
    "calc_roic_pretax_quarterly_ic_raw",
    "calc_roic_posttax_quarterly_ic_raw",
    # Debt-to-equity: every direct input precedes the calculation.
    "short_term_debt_and_capital_lease",
    "long_term_debt_and_capital_lease",
    "calc_debt_value_quarterly",
    "total_assets",
    "total_liabilities",
    "equity",
    "total_stockholders_equity",
    "calc_debt_to_equity_quarterly",
    "valuations__ratios__debt_to_equity",
    # EV/FCF: repeated inputs are intentional for auditability.
    "market_cap",
    "calc_debt_value_quarterly",
    "cash_and_cash_equivalents",
    "short_term_investments",
    "calc_enterprise_value_quarterly",
    "free_cash_flow",
    "calc_free_cash_flow_ttm",
    "calc_ev_to_fcf_quarterly",
    "valuations__valuation_ratios__enterprise_value_to_fcf",
    "valuations__per_share_data__shares_outstanding",
    "valuations__per_share_data__month_end_stock_price",
]


def _research_order(
    frame: pd.DataFrame,
    *,
    repeat_sources: bool = False,
) -> pd.DataFrame:
    preferred = STRUCTURAL_OUTPUT_COLUMNS + PRIMARY_OUTPUT_COLUMNS
    candidates = preferred if repeat_sources else dict.fromkeys(preferred)
    ordered = [
        column
        for column in candidates
        if column in frame.columns and column not in EXCLUDED_OUTPUT_COLUMNS
    ]
    ordered_set = set(ordered)
    remaining = [
        column
        for column in frame.columns
        if column not in ordered_set and column not in EXCLUDED_OUTPUT_COLUMNS
    ]
    result = frame.loc[:, ordered + remaining].copy()
    # כל נתוני המחקר המספריים נשמרים כ-float. שדות המבנה הם מזהים,
    # תאריכים ותוויות תקופה ולכן נשמרים בסוג הטבעי שלהם.
    for position, column in enumerate(result.columns):
        if column in STRUCTURAL_OUTPUT_COLUMNS:
            continue
        numeric = pd.to_numeric(result.iloc[:, position], errors="coerce")
        result.isetitem(position, numeric.astype(float))
    return result


def order_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """מסדר את העמודות: מפתחות -> יישור -> שדות מדווחים -> מחושבים.

    עמודה שקיימת בטבלה אך לא ברשימה מצורפת בסוף, כדי שלעולם לא נאבד נתון.
    """
    return _research_order(frame)


def calculation_audit_view(frame: pd.DataFrame) -> pd.DataFrame:
    """מחזיר תצוגת מחקר ובה כל מקורות החישוב צמודים לתוצאה.

    שדות מקור משותפים חוזרים בכוונה לפני חישובים שונים כדי שהפלט יהיה
    ניתן לביקורת גם ללא דילוג בין עמודות.
    """
    if frame.empty:
        return frame.copy()
    if not frame.columns.is_unique:
        raise ValueError("תצוגת המחקר מצפה לטבלה עם שמות עמודות ייחודיים")
    return _research_order(frame, repeat_sources=True)


def _timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def build_output_path(
    directory: Path, period: str, extension: str, *, suffix: str = ""
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"GuruFocus_{period}_{_timestamp()}{suffix}"
    path = directory / f"{stem}.{extension}"
    if not path.exists():
        return path

    # לא דורסים קובץ מאותה ריצה/יום — במיוחד כשהוא פתוח ב-Excel.
    version = 2
    while True:
        candidate = directory / f"{stem}_v{version}.{extension}"
        if not candidate.exists():
            return candidate
        version += 1


def write_excel(
    path: Path,
    data: pd.DataFrame,
    *,
    coverage: pd.DataFrame | None = None,
    nulls: pd.DataFrame | None = None,
    checks: pd.DataFrame | None = None,
    manifest: pd.DataFrame | None = None,
) -> Path:
    """כותב את חוברת האקסל המלאה."""
    path.parent.mkdir(parents=True, exist_ok=True)
    sheets: list[tuple[str, pd.DataFrame]] = [("Data", data)]

    for name, table in (
        ("Coverage", coverage),
        ("Nulls", nulls),
        ("Checks", checks),
        ("Manifest", manifest),
    ):
        if table is not None and not table.empty:
            sheets.append((name, table))

    with pd.ExcelWriter(path, engine="openpyxl", mode="w") as writer:
        for name, table in sheets:
            table.to_excel(writer, sheet_name=name, index=False)
            _autosize(writer.sheets[name], table)
            if name == "Data":
                _format_float_values(writer.sheets[name], table)

    log.info("נשמר: %s (%d שורות, %d עמודות)", path, len(data), data.shape[1])
    return path


def _autosize(worksheet, frame: pd.DataFrame, *, max_width: int = 42) -> None:
    """רוחב עמודות סביר — כדי שהקובץ יהיה קריא בלי התאמה ידנית."""
    from openpyxl.utils import get_column_letter

    for position, column in enumerate(frame.columns, start=1):
        header = len(str(column))
        # בחירת עמודה לפי מיקום חיונית: בתצוגת הביקורת יש בכוונה
        # כותרות כפולות כאשר אותו מקור משתתף בכמה חישובים.
        sample = frame.iloc[:200, position - 1].astype(str).str.len().max()
        width = max(header, int(sample) if pd.notna(sample) else 0) + 2
        worksheet.column_dimensions[get_column_letter(position)].width = min(width, max_width)


def _format_float_values(worksheet, frame: pd.DataFrame) -> None:
    """מציג את כל נתוני המחקר המספריים עם שתי ספרות אחרי הנקודה."""
    for position, column in enumerate(frame.columns, start=1):
        if column in STRUCTURAL_OUTPUT_COLUMNS:
            continue
        for row in range(2, len(frame) + 2):
            worksheet.cell(row=row, column=position).number_format = "0.00"


def write_csv(path: Path, data: pd.DataFrame) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig כדי שאקסל בעברית יפתח את הקובץ בקידוד הנכון
    data.to_csv(
        path,
        index=False,
        encoding="utf-8-sig",
        float_format="%.2f",
    )
    log.info("נשמר: %s", path)
    return path


def write_parquet(path: Path, data: pd.DataFrame) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        data.to_parquet(path, index=False)
    except ImportError as exc:
        raise RuntimeError(
            "כתיבת parquet דורשת pyarrow — התקינו: pip install pyarrow"
        ) from exc
    log.info("נשמר: %s", path)
    return path
