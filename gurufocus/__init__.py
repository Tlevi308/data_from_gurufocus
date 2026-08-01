"""
משיכת נתונים פונדמנטליים מ-GuruFocus Data API.

השלבים, לפי הסדר שבו הם רצים:

    config        שלב 0 — הגדרות וסודות
    client        שלב 1 — תעבורה (HTTP, retry, מטמון)
    fields        שלב 2 — מילון השדות (חוזה הסכימה)
    parsing       שלב 3 — JSON -> רשומות שטוחות
    resolver      שלב 4 — מיפוי שדות מבוקשים למפתחות אמיתיים
    extract       שלב 5 — בניית טבלה + יישור תקופות
    valuations    שלב 6 — שווי שוק, מניות, מחיר ויחסים מדווחים
    calculations  שלב 7 — מס, NOPAT, IC_RAW, ROIC, EV/FCF ויחס חוב
    validation    שלב 8 — בדיקות תקינות
    export        שלב 9 — כתיבה לאקסל / CSV / parquet
    pipeline      תזמור

שלבים 2-5 ו-7-8 הם פונקציות טהורות: אפשר לבדוק אותם על JSON שמור בלי לגעת
ב-API ובלי לצרוך מהמכסה.

שימוש מהיר:

    from gurufocus import load_settings, run
    result = run(load_settings())
    print(result.manifest)
"""

from .config import Settings, load_settings
from .client import (
    AuthenticationError,
    GuruFocusClient,
    GuruFocusError,
    NotFoundError,
)
from .extract import ExtractionError, build_frame
from .calculations import add_calculated
from .validation import null_report, quality_checks
from .pipeline import RunResult, TickerResult, process_ticker, run

__version__ = "1.0.0"

__all__ = [
    "Settings", "load_settings",
    "GuruFocusClient", "GuruFocusError", "AuthenticationError", "NotFoundError",
    "build_frame", "ExtractionError",
    "add_calculated",
    "quality_checks", "null_report",
    "run", "process_ticker", "RunResult", "TickerResult",
    "__version__",
]
