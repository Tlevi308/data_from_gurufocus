"""
תזמור — מחבר את השלבים לריצה אחת.
================================================================================
הקובץ הזה לא מכיל לוגיקה עסקית משלו. כל מה שהוא עושה הוא להריץ את השלבים
בסדר הנכון ולאסוף דוחות. אם משהו כאן נראה מסובך — הוא שייך לשלב, לא לכאן.

    שלב 1  client      →  JSON גולמי
    שלב 3  parsing     →  רשומות שטוחות
    שלב 4  resolver    →  מיפוי שדות + דוח כיסוי
    שלב 5  extract     →  טבלה מסודרת + יישור תקופות
    שלב 6ב valuations  →  שווי שוק, מכפיל מזומנים, מניות, מחיר ויחס חוב להון
    שלב 7  calculations→  RawTaxRate, NOPAT, IC_RAW, ROIC, EV/FCF ויחס חוב
    שלב 8  validation  →  בדיקות תקינות
    שלב 9  export      →  אקסל / CSV / parquet

טיקר שנכשל לא מפיל את הריצה: הוא נרשם ב-manifest עם סיבת הכישלון,
ושאר הטיקרים ממשיכים.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field as dc_field
from pathlib import Path

import pandas as pd

from .calculations import add_calculated
from .client import GuruFocusClient, GuruFocusError
from .config import Settings
from .export import (
    build_output_path,
    calculation_audit_view,
    order_columns,
    write_csv,
    write_excel,
    write_parquet,
)
from .extract import ExtractReport, ExtractionError, build_frame
from .validation import null_report, quality_checks
from .valuations import attach_valuations

log = logging.getLogger(__name__)


@dataclass
class TickerResult:
    """תוצאת עיבוד של טיקר אחד."""
    symbol: str
    frame: pd.DataFrame | None = None
    report: ExtractReport | None = None
    valuations_report: dict = dc_field(default_factory=dict)
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.error == "" and self.frame is not None and not self.frame.empty


@dataclass
class RunResult:
    """תוצאת הריצה כולה."""
    panel: pd.DataFrame
    results: list[TickerResult]
    manifest: pd.DataFrame
    written: list[Path] = dc_field(default_factory=list)
    network_calls: int = 0
    cache_hits: int = 0

    @property
    def succeeded(self) -> list[TickerResult]:
        return [r for r in self.results if r.ok]

    @property
    def failed(self) -> list[TickerResult]:
        return [r for r in self.results if not r.ok]


def process_ticker(
    client: GuruFocusClient,
    symbol: str,
    settings: Settings,
    *,
    use_cache: bool = True,
) -> TickerResult:
    """מריץ את כל השלבים על טיקר אחד. לא זורק — מחזיר את השגיאה בתוצאה."""
    try:
        # ── שלבים 1-5: שליפה ובנייה ──────────────────────────────────────
        payload = client.fundamentals(symbol, use_cache=use_cache)
        frame, report = build_frame(
            payload,
            symbol,
            period=settings.period,
            start_date=settings.start_date,
            end_date=settings.end_date,
            quarter_shift_months=settings.quarter_shift_months,
        )

        # ── שלב 6: נתוני valuations הרלוונטיים ───────────────────────────
        valuations_report: dict = {}
        if settings.is_quarterly:
            try:
                valuations_payload = client.valuations(
                    symbol,
                    use_cache=use_cache,
                )
                frame, valuations_report = attach_valuations(
                    frame,
                    valuations_payload,
                    period=settings.period,
                )
            except GuruFocusError as exc:
                # ללא Market Cap יחסי EV וחוב נשארים ריקים; יתר הפאנל תקין.
                log.warning("[%s] דילוג על valuations: %s", symbol, exc)
                frame, valuations_report = attach_valuations(
                    frame,
                    {},
                    period=settings.period,
                )
                valuations_report["error"] = str(exc)

        # ── שלב 7: חישובים רבעוניים ──────────────────────────────────────
        if settings.is_quarterly:
            frame = add_calculated(frame)
        else:
            log.info("[%s] period=%s — מדלג על חישובי ROIC/EV רבעוניים",
                     symbol, settings.period)

        log.info(report.summary())
        return TickerResult(
            symbol=symbol,
            frame=frame,
            report=report,
            valuations_report=valuations_report,
        )

    except (GuruFocusError, ExtractionError) as exc:
        log.error("[%s] נכשל: %s", symbol, exc)
        return TickerResult(symbol=symbol, error=str(exc))
    except Exception as exc:  # noqa: BLE001 — טיקר בודד לא מפיל את הריצה
        log.exception("[%s] שגיאה לא צפויה", symbol)
        return TickerResult(symbol=symbol, error=f"{type(exc).__name__}: {exc}")


def build_manifest(results: list[TickerResult]) -> pd.DataFrame:
    """סיכום ריצה — שורה לכל טיקר. זו הטבלה שאומרת אם הריצה הצליחה."""
    rows = []
    for result in results:
        row: dict = {"symbol": result.symbol, "status": "OK" if result.ok else "FAILED"}
        if result.report:
            resolution = result.report.resolution
            row.update({
                "rows": result.report.rows_kept,
                "date_min": result.report.date_min,
                "date_max": result.report.date_max,
                "fields_found": resolution.found_count,
                "fields_total": len(resolution.coverage),
                "fields_missing": len(resolution.missing),
                "missing_columns": ", ".join(resolution.missing),
                "duplicates_dropped": result.report.duplicates_dropped,
                "duplicate_period_keys": ", ".join(result.report.duplicate_period_keys),
                "api_keys_in_json": len(result.report.available_keys),
                "company": result.report.metadata.get("company", ""),
            })
        if result.valuations_report:
            coverage = result.valuations_report.get("coverage")
            missing = []
            found = 0
            if isinstance(coverage, pd.DataFrame) and not coverage.empty:
                missing = coverage.loc[
                    coverage["status"] == "MISSING",
                    "output_column",
                ].tolist()
                found = int((coverage["status"] != "MISSING").sum())
            row.update({
                "valuation_rows_available": result.valuations_report.get(
                    "rows_available", 0
                ),
                "valuation_rows_matched": result.valuations_report.get(
                    "rows_matched", 0
                ),
                "valuation_fields_found": found,
                "valuation_fields_total": len(coverage) if isinstance(
                    coverage, pd.DataFrame
                ) else 0,
                "valuation_fields_missing": ", ".join(missing),
            })
        row["error"] = result.error
        rows.append(row)
    return pd.DataFrame(rows)


def run(
    settings: Settings,
    *,
    use_cache: bool = True,
    write: bool = True,
) -> RunResult:
    """מריץ את כל הצנרת על כל הטיקרים ומייצא."""
    settings.validate()
    log.info("מפתח API: %s | טיקרים: %s | תקופה: %s",
             settings.masked_key(), ", ".join(settings.tickers), settings.period)

    results: list[TickerResult] = []
    cache_dir = settings.cache.directory if settings.cache.enabled else None

    with GuruFocusClient(
        settings.api_key,
        timeout=settings.network.timeout_seconds,
        max_retries=settings.network.max_retries,
        cache_dir=cache_dir,
        cache_ttl_hours=settings.cache.ttl_hours,
    ) as client:
        for position, symbol in enumerate(settings.tickers):
            results.append(process_ticker(client, symbol, settings, use_cache=use_cache))
            # ריסון קצב מול ה-API — לא אחרי הטיקר האחרון
            if position < len(settings.tickers) - 1:
                time.sleep(settings.network.sleep_between_tickers)
        network_calls, cache_hits = client.network_calls, client.cache_hits

    frames = [r.frame for r in results if r.ok]
    panel = (
        order_columns(pd.concat(frames, ignore_index=True, sort=False))
        if frames else pd.DataFrame()
    )
    if not panel.empty:
        panel["run_date"] = pd.Timestamp.now().strftime("%Y-%m-%d")

    manifest = build_manifest(results)
    run_result = RunResult(
        panel=panel, results=results, manifest=manifest,
        network_calls=network_calls, cache_hits=cache_hits,
    )

    if write and not panel.empty:
        run_result.written = _write_outputs(panel, results, manifest, settings)
    elif panel.empty:
        log.error("לא נאספו נתונים משום טיקר — אין מה לכתוב")

    return run_result


def _write_outputs(
    panel: pd.DataFrame,
    results: list[TickerResult],
    manifest: pd.DataFrame,
    settings: Settings,
) -> list[Path]:
    """כותב את כל פורמטי הפלט המבוקשים."""
    written: list[Path] = []
    succeeded = [r for r in results if r.ok]
    # ה-panel הפנימי נשאר עם שמות עמודות ייחודיים עבור בדיקות וניתוח.
    # רק Excel/CSV מקבלים תצוגת ביקורת עם כפילויות מכוונות.
    audit_panel = calculation_audit_view(panel)

    for fmt in settings.output.formats:
        if fmt == "excel":
            # דוחות הנלווים נלקחים מהטיקר הראשון שהצליח: הכיסוי זהה בין
            # טיקרים באותו endpoint, והבדיקות מוצגות על הפאנל המלא.
            first = succeeded[0] if succeeded else None
            coverage = first.report.coverage if first and first.report else None
            if first and first.valuations_report:
                valuation_coverage = first.valuations_report.get("coverage")
                if isinstance(valuation_coverage, pd.DataFrame):
                    coverage = pd.concat(
                        [coverage, valuation_coverage],
                        ignore_index=True,
                    ) if coverage is not None else valuation_coverage
            path = write_excel(
                build_output_path(settings.output.directory, settings.period, "xlsx"),
                audit_panel,
                coverage=coverage,
                nulls=null_report(panel),
                checks=quality_checks(panel, settings.period),
                manifest=manifest,
            )
        elif fmt == "csv":
            path = write_csv(
                build_output_path(settings.output.directory, settings.period, "csv"),
                audit_panel,
            )
        elif fmt == "parquet":
            path = write_parquet(
                build_output_path(settings.output.directory, settings.period, "parquet"),
                panel,
            )
        else:
            log.warning("פורמט פלט לא מוכר, מדלג: %s", fmt)
            continue
        written.append(path)

    return written
