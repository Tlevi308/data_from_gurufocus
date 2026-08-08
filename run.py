#!/usr/bin/env python
"""
נקודת הכניסה — הרצת הצנרת מהטרמינל.

דוגמאות:
    python run.py                                  # לפי config.yaml
    python run.py --tickers AAPL MSFT JNJ          # דריסת רשימת הטיקרים
    python run.py --period annually                # נתונים שנתיים
    python run.py --refresh                        # התעלמות מהמטמון
    python run.py --formats csv                    # פלט CSV בלבד
    python run.py --check-only                     # בדיקה בלי לכתוב קבצים
"""

from __future__ import annotations

import argparse
import logging
import sys

import pandas as pd

from gurufocus.config import load_settings
from gurufocus.pipeline import run
from gurufocus.validation import failed_checks, quality_checks


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="משיכת נתונים פונדמנטליים מ-GuruFocus Data API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--config", default="config.yaml",
                        help="נתיב לקובץ ההגדרות (ברירת מחדל: config.yaml)")
    parser.add_argument("--tickers", nargs="+", metavar="SYM",
                        help="רשימת טיקרים; דורסת את config.yaml")
    parser.add_argument("--period", choices=["quarterly", "annually"],
                        help="תדירות הדיווח")
    parser.add_argument("--start-date", metavar="YYYY-MM-DD",
                        help="חיתוך תחתון לתאריך סוף התקופה")
    parser.add_argument("--end-date", metavar="YYYY-MM-DD",
                        help="חיתוך עליון לתאריך סוף התקופה")
    parser.add_argument("--formats", nargs="+", choices=["excel", "csv", "parquet"],
                        help="פורמטי הפלט")
    parser.add_argument("--out-dir", metavar="DIR", help="תיקיית הפלט")
    parser.add_argument("--refresh", action="store_true",
                        help="התעלמות מהמטמון ומשיכה מחדש מה-API")
    parser.add_argument("--no-cache", action="store_true",
                        help="ביטול מוחלט של המטמון (לא קורא ולא כותב)")
    parser.add_argument("--check-only", action="store_true",
                        help="הרצה מלאה ודוחות, בלי כתיבת קבצי פלט")
    parser.add_argument("-v", "--verbose", action="store_true", help="לוג מפורט")
    parser.add_argument("-q", "--quiet", action="store_true", help="שגיאות בלבד")
    return parser.parse_args(argv)


def configure_logging(*, verbose: bool, quiet: bool) -> None:
    # ב-Windows קידוד ברירת המחדל עשוי להיות cp1252, שאינו מסוגל להדפיס עברית.
    # reconfigure לא קיים ב-StringIO של בדיקות, ולכן נשמרת תאימות גם שם.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")
    level = logging.DEBUG if verbose else logging.ERROR if quiet else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(levelname)-7s %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def build_overrides(args: argparse.Namespace) -> dict:
    overrides: dict = {
        "tickers": args.tickers,
        "period": args.period,
        "start_date": args.start_date,
        "end_date": args.end_date,
    }
    output: dict = {}
    if args.formats:
        output["formats"] = tuple(args.formats)
    if args.out_dir:
        from pathlib import Path
        output["directory"] = Path(args.out_dir).resolve()
    if output:
        overrides["output"] = output
    if args.no_cache:
        overrides["cache"] = {"enabled": False}
    return overrides


def print_summary(result, settings) -> None:
    """סיכום קריא לטרמינל — מה נאסף, מה נכשל, ומה ראוי לבדוק."""
    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 40)

    print("\n" + "=" * 78)
    print(f"  סיכום ריצה · {settings.period}")
    print("=" * 78)
    print(f"בקשות רשת: {result.network_calls} | פגיעות מטמון: {result.cache_hits}")
    print(f"טיקרים: {len(result.succeeded)} הצליחו, {len(result.failed)} נכשלו")

    if not result.manifest.empty:
        columns = [c for c in ("symbol", "status", "rows", "date_min", "date_max",
                               "fields_found", "fields_total", "fields_missing",
                               "valuation_rows_matched", "valuation_fields_found",
                               "valuation_fields_missing", "tax_valid",
                               "tax_warning", "tax_invalid")
                   if c in result.manifest.columns]
        print("\n--- Manifest ---")
        print(result.manifest[columns].to_string(index=False))

    for failure in result.failed:
        print(f"\n✗ {failure.symbol}: {failure.error}")

    first = result.succeeded[0] if result.succeeded else None
    if first and first.report and first.report.resolution.missing:
        print("\n--- שדות שלא נמצאו ב-API ---")
        coverage = first.report.coverage
        missing = coverage[coverage["status"] == "MISSING"]
        print(missing[["group", "requested_label", "output_column"]].to_string(index=False))

    if not result.panel.empty:
        checks = quality_checks(result.panel, settings.period)
        failures = failed_checks(checks)
        print(f"\n--- בדיקות תקינות: {len(checks) - len(failures)}/{len(checks)} נקיות ---")
        if not failures.empty:
            print(failures.to_string(index=False))

        if "calc_nopat_quarterly" in result.panel.columns:
            valid_nopat = int(result.panel["calc_nopat_quarterly"].notna().sum())
            print(f"\nתקופות עם NOPAT רבעוני: {valid_nopat}/{len(result.panel)}")
        if "calc_ev_to_fcf_quarterly" in result.panel.columns:
            valid_ev_fcf = int(
                result.panel["calc_ev_to_fcf_quarterly"].notna().sum()
            )
            print(
                "תקופות עם EV/FCF לסוף הרבעון מול FCF TTM: "
                f"{valid_ev_fcf}/{len(result.panel)}"
            )
        if "calc_roic_decomposition_status" in result.panel.columns:
            status = result.panel["calc_roic_decomposition_status"]
            classified = int((status == "VALID").sum())
            print(
                "תקופות עם פירוק ROIC מלא: "
                f"{classified}/{len(result.panel)}"
            )
            unclassified = status[status != "VALID"].value_counts()
            for reason, count in unclassified.items():
                print(f"    {reason}: {count}")

    if result.written:
        print("\n--- קבצים שנכתבו ---")
        for path in result.written:
            print(f"  {path}")
    print()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging(verbose=args.verbose, quiet=args.quiet)

    try:
        settings = load_settings(args.config, overrides=build_overrides(args))
    except (ValueError, RuntimeError) as exc:
        print(f"שגיאת הגדרות: {exc}", file=sys.stderr)
        return 2

    try:
        settings.validate()
    except ValueError as exc:
        print(f"הגדרות לא תקינות: {exc}", file=sys.stderr)
        print("\nרמז: ודאו שקיים קובץ .env עם GURUFOCUS_API_KEY, "
              "או הגדירו את משתנה הסביבה.", file=sys.stderr)
        return 2

    result = run(
        settings,
        use_cache=not args.refresh,
        write=not args.check_only,
    )
    print_summary(result, settings)

    if not result.succeeded:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
