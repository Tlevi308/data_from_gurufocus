"""
בדיקות אינטגרציה מול ה-JSON האמיתי שנשמר במטמון.

אלה הבדיקות שתופסות שינוי בצד של GuruFocus: שם מפתח שהשתנה, מוסכמת סימן
שהתהפכה, או מבנה שזז. הן רצות על קובץ שמור ולא פונות לרשת.
מדלגות אוטומטית אם עוד לא הורדתם נתונים.
"""

from __future__ import annotations

import pandas as pd
import pytest

from gurufocus.calculations import add_calculated
from gurufocus.extract import build_frame
from gurufocus.parsing import block_to_records, find_period_block, inventory_keys
from gurufocus.resolver import resolve_fields


class TestRealStructure:
    def test_top_level_keys(self, real_payload):
        assert {"quarterly", "annually", "ttm", "basic_information"} <= set(real_payload)

    def test_period_block_is_list_of_rows(self, real_payload):
        block = find_period_block(real_payload, "quarterly")
        assert isinstance(block, list) and len(block) > 50
        assert {"date", "filing_date", "balance_sheet"} <= set(block[0])

    def test_date_is_year_month(self, real_payload):
        block = find_period_block(real_payload, "quarterly")
        assert pd.Series([r["date"] for r in block]).str.match(r"^\d{4}-\d{2}$").all()

    def test_filing_date_fully_populated(self, real_payload):
        """filing_date קיים ב-fundamentals — אין צורך ב-endpoint נפרד עבורו."""
        block = find_period_block(real_payload, "quarterly")
        assert all(row.get("filing_date") for row in block)


class TestRealFieldCoverage:
    def test_all_core_fields_resolve(self, real_payload):
        records = block_to_records(find_period_block(real_payload, "quarterly"))
        resolution = resolve_fields(
            inventory_keys(records), real_payload["basic_information"].keys()
        )
        from gurufocus.fields import FIELDS_CORE

        unresolved = [f.out for f in FIELDS_CORE if f.out not in resolution.mapping]
        assert not unresolved, f"שדות ליבה שלא נפתרו: {unresolved}"

    def test_requested_schema_has_no_missing_fields(self, real_payload):
        records = block_to_records(find_period_block(real_payload, "quarterly"))
        resolution = resolve_fields(
            inventory_keys(records), real_payload["basic_information"].keys()
        )
        assert resolution.missing == []


class TestRealTaxConvention:
    def test_tax_reported_negative(self, real_payload):
        """הזהות pretax + tax = net מחזיקה על כל ההיסטוריה."""
        block = find_period_block(real_payload, "quarterly")
        residual = sum(
            abs(r["income_statement"]["pretax_income"]
                + r["income_statement"]["tax_provision"]
                - r["income_statement"]["net_income"])
            for r in block
        )
        total = sum(abs(r["income_statement"]["net_income"]) for r in block)
        assert residual < 0.01 * total

    def test_pipeline_produces_raw_tax_rates(self, real_payload):
        frame, _ = build_frame(real_payload, "AAPL")
        result = add_calculated(frame)
        rates = result["calc_raw_tax_rate_quarterly"].dropna()
        assert len(rates) > 50
        # שיעור המס האפקטיבי של AAPL בשנים האחרונות סביב 15%-25%
        assert 0.10 < rates.tail(8).median() < 0.30

    def test_no_gaps_in_recent_history(self, real_payload):
        frame, _ = build_frame(real_payload, "AAPL", start_date="2015-01-01")
        dates = pd.to_datetime(frame["fiscal_period_end_date"])
        gaps = dates.diff().dt.days.dropna()
        assert gaps.between(80, 100).all()

    def test_period_keys_unique(self, real_payload):
        frame, report = build_frame(real_payload, "AAPL")
        assert not report.duplicate_period_keys
        assert frame["period_key"].is_unique


class TestRealTaxExpenseSign:
    """מוסכמת הסימן של TaxExpense מול נתונים אמיתיים."""

    def test_sign_semantics_hold_across_cached_tickers(self):
        """המבחן המכריע: tax_provision שלילי = הוצאה, חיובי = החזר.

        נבדק על כל טיקר שנמצא במטמון — ככל שיש יותר, הבדיקה חזקה יותר.
        """
        import json
        from tests.conftest import CACHED_AAPL

        raw_dir = CACHED_AAPL.parent
        files = sorted(raw_dir.glob("*__fundamentals.json"))
        if not files:
            pytest.skip("אין נתונים במטמון")

        expense_ok = expense_total = benefit_ok = benefit_total = 0
        for path in files:
            payload = json.loads(path.read_text(encoding="utf-8"))
            for row in payload.get("quarterly", []):
                inc = row.get("income_statement", {})
                pretax, tax = inc.get("pretax_income"), inc.get("tax_provision")
                # ⚠️ מול פעילות נמשכת ולא מול הרווח הכולל: פעילות מופסקת
                # מדווחת אחרי מס ומנפחת את הרווח בלי קשר לסעיף המס.
                net = inc.get("net_income_continuing_operations")
                if net is None:
                    net = inc.get("net_income")
                if None in (pretax, tax, net) or tax == 0:
                    continue
                if tax < 0:
                    expense_total += 1
                    expense_ok += net < pretax      # המס מקטין את הרווח
                else:
                    benefit_total += 1
                    benefit_ok += net > pretax      # המס מגדיל את הרווח

        assert expense_total > 100, "מדגם קטן מדי להסקה"
        assert expense_ok / expense_total > 0.98, (
            f"tax_provision שלילי אמור להיות הוצאה: {expense_ok}/{expense_total}"
        )
        if benefit_total:
            assert benefit_ok / benefit_total > 0.95, (
                f"tax_provision חיובי אמור להיות החזר: {benefit_ok}/{benefit_total}"
            )

    def test_continuing_operations_identity_beats_total_net(self):
        """הזהות מתקיימת מול פעילות נמשכת, לא מול הרווח הכולל."""
        import json
        from tests.conftest import CACHED_AAPL

        files = sorted(CACHED_AAPL.parent.glob("*__fundamentals.json"))
        if len(files) < 2:
            pytest.skip("נדרשים כמה טיקרים במטמון")

        resid_net = resid_continuing = 0.0
        for path in files:
            payload = json.loads(path.read_text(encoding="utf-8"))
            for row in payload.get("quarterly", []):
                inc = row.get("income_statement", {})
                pretax, tax = inc.get("pretax_income"), inc.get("tax_provision")
                net = inc.get("net_income")
                continuing = inc.get("net_income_continuing_operations")
                if None in (pretax, tax, net, continuing):
                    continue
                resid_net += abs(pretax + tax - net)
                resid_continuing += abs(pretax + tax - continuing)

        assert resid_continuing < resid_net / 10, (
            f"פעילות נמשכת: {resid_continuing:,.0f} | רווח כולל: {resid_net:,.0f}"
        )

class TestRealAlignment:
    @pytest.mark.parametrize("period_end,expected", [
        ("2026-03-31", "2026Q1"),
        ("2025-12-31", "2025Q4"),
        ("2025-09-30", "2025Q3"),
    ])
    def test_known_quarters(self, real_payload, period_end, expected):
        frame, _ = build_frame(real_payload, "AAPL")
        row = frame[frame["fiscal_period_end_date"] == period_end]
        assert not row.empty
        assert row["period_key"].iloc[0] == expected


class TestRealValues:
    def test_known_balance_sheet_values(self, real_payload):
        """ערכים שאומתו ידנית מול הדוח של AAPL ל-2026-03."""
        frame, _ = build_frame(real_payload, "AAPL")
        row = frame[frame["fiscal_period_end_date"] == "2026-03-31"].iloc[0]
        assert row["total_current_assets"] == pytest.approx(144114)
        assert row["short_term_investments"] == pytest.approx(22935)
        assert row["net_ppe"] == pytest.approx(50116)
        assert row["pretax_income"] == pytest.approx(35833)
        assert row["tax_provision"] == pytest.approx(-6255)

    def test_removed_metadata_not_in_panel(self, real_payload):
        frame, _ = build_frame(real_payload, "AAPL")
        assert "currency" not in frame.columns
        assert "exchange" not in frame.columns
        assert "reporting_unit" not in frame.columns
