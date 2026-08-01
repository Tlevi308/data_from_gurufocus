"""בדיקות שלבים 4-5 — פתרון שדות ובניית הטבלה."""

from __future__ import annotations

import pandas as pd
import pytest

from gurufocus.extract import ExtractionError, build_frame
from gurufocus.parsing import block_to_records, inventory_keys
from gurufocus.resolver import resolve_fields


class TestResolver:
    def test_resolves_real_key_names(self, quarterly_payload):
        records = block_to_records(quarterly_payload["quarterly"])
        resolution = resolve_fields(inventory_keys(records))
        mapping = resolution.mapping

        # השמות שתוקנו מול הגרסה הקודמת של הקוד
        assert mapping["goodwill"] == "balance_sheet::good_will"
        assert mapping["intangible_assets"] == "balance_sheet::intangibles"
        assert mapping["net_ppe"] == "balance_sheet::net_ppe"
        assert mapping["tax_provision"] == "income_statement::tax_provision"
        assert mapping["short_term_investments"] == "balance_sheet::marke_table_securities"
        assert mapping["free_cash_flow"] == "cashflow_statement::total_free_cash_flow"

    def test_section_qualification_prevents_collision(self):
        """net_income חייב להילקח מדוח רו\"ה גם כשהוא קיים גם בתזרים."""
        keys = ["income_statement::ebit", "cashflow_statement::ebit"]
        resolution = resolve_fields(keys)
        assert resolution.mapping["ebit"] == "income_statement::ebit"

    def test_missing_field_reported(self):
        resolution = resolve_fields(["balance_sheet::total_current_assets"])
        assert "total_current_assets" in resolution.mapping
        assert "goodwill" in resolution.missing

    def test_removed_metadata_fields_are_not_resolved(self, quarterly_payload):
        records = block_to_records(quarterly_payload["quarterly"])
        resolution = resolve_fields(
            inventory_keys(records), quarterly_payload["basic_information"].keys()
        )
        assert "currency" not in resolution.meta_mapping
        assert "exchange" not in resolution.meta_mapping


class TestBuildFrame:
    def test_shape_and_columns(self, quarterly_payload):
        frame, report = build_frame(quarterly_payload, "TEST")
        assert len(frame) == 12
        assert report.rows_kept == 12
        assert frame["symbol"].unique().tolist() == ["TEST"]

    def test_removed_metadata_columns_are_not_created(self, quarterly_payload):
        frame, _ = build_frame(quarterly_payload, "TEST")
        assert "currency" not in frame.columns
        assert "exchange" not in frame.columns
        assert "reporting_unit" not in frame.columns

    def test_symbol_is_requested_ticker_not_api_symbol(self, quarterly_payload):
        """⚠️ רגרסיה: אם לוקחים symbol מ-basic_information, שני טיקרים שונים
        עלולים לקבל את אותו סמל ולהתמזג בפאנל בלי שום אזהרה."""
        payload = dict(quarterly_payload)
        payload["basic_information"] = dict(quarterly_payload["basic_information"])
        payload["basic_information"]["symbol"] = "CANONICAL"

        a, _ = build_frame(payload, "AAA")
        b, _ = build_frame(payload, "BBB")
        assert a["symbol"].unique().tolist() == ["AAA"]
        assert b["symbol"].unique().tolist() == ["BBB"]

    def test_api_symbol_preserved_in_metadata(self, quarterly_payload):
        payload = dict(quarterly_payload)
        payload["basic_information"] = dict(quarterly_payload["basic_information"])
        payload["basic_information"]["symbol"] = "CANONICAL"
        _, report = build_frame(payload, "AAA")
        assert report.metadata["symbol"] == "CANONICAL"

    def test_period_end_is_month_end(self, quarterly_payload):
        frame, _ = build_frame(quarterly_payload, "TEST")
        assert frame["fiscal_period_end_date"].iloc[0] == "2020-03-31"
        assert frame["fiscal_period_end_date"].iloc[-1] == "2022-12-31"

    def test_sorted_ascending(self, quarterly_payload):
        frame, _ = build_frame(quarterly_payload, "TEST")
        dates = pd.to_datetime(frame["fiscal_period_end_date"])
        assert dates.is_monotonic_increasing

    def test_start_date_filter(self, quarterly_payload):
        frame, _ = build_frame(quarterly_payload, "TEST", start_date="2022-01-01")
        assert len(frame) == 4

    def test_end_date_filter(self, quarterly_payload):
        frame, _ = build_frame(quarterly_payload, "TEST", end_date="2020-12-31")
        assert len(frame) == 4
        assert frame["fiscal_period_end_date"].iloc[-1] == "2020-12-31"

    def test_both_date_bounds(self, quarterly_payload):
        frame, _ = build_frame(
            quarterly_payload, "TEST",
            start_date="2021-01-01", end_date="2021-12-31",
        )
        assert len(frame) == 4
        assert frame["fiscal_period_end_date"].iloc[0] == "2021-03-31"
        assert frame["fiscal_period_end_date"].iloc[-1] == "2021-12-31"

    def test_empty_range_raises_clear_error(self, quarterly_payload):
        with pytest.raises(ExtractionError, match="end_date"):
            build_frame(quarterly_payload, "TEST", start_date="2030-01-01")

    def test_unknown_period_raises_with_helpful_message(self, quarterly_payload):
        with pytest.raises(ExtractionError, match="annuals|תקופות זמינות"):
            build_frame(quarterly_payload, "TEST", period="annuals")

    def test_duplicate_periods_dropped(self, quarterly_payload):
        payload = dict(quarterly_payload)
        payload["quarterly"] = quarterly_payload["quarterly"] + [
            quarterly_payload["quarterly"][-1]
        ]
        frame, report = build_frame(payload, "TEST")
        assert report.duplicates_dropped == 1
        assert len(frame) == 12


class TestAlignment:
    """כלל היישור: סוף תקופה פחות חודשיים -> הרבעון הקלנדרי של התוצאה."""

    @pytest.mark.parametrize("period_end,expected_key", [
        ("2026-03", "2026Q1"),   # מרץ  -> ינואר   -> Q1
        ("2025-09", "2025Q3"),   # ספט' -> יולי    -> Q3
        ("2025-12", "2025Q4"),   # דצמ' -> אוקטובר -> Q4
        ("2026-01", "2025Q4"),   # ינו' -> נובמבר  -> Q4 של השנה הקודמת
        ("2025-10", "2025Q3"),   # אוק' -> אוגוסט  -> Q3
    ])
    def test_period_key(self, quarterly_payload, period_end, expected_key):
        from tests.conftest import make_row

        payload = dict(quarterly_payload)
        payload["quarterly"] = [make_row(period_end)]
        frame, _ = build_frame(payload, "TEST")
        assert frame["period_key"].iloc[0] == expected_key

    def test_removed_calendar_columns_are_not_created(self, quarterly_payload):
        from tests.conftest import make_row

        payload = dict(quarterly_payload)
        payload["quarterly"] = [make_row("2026-01")]
        frame, _ = build_frame(payload, "TEST")
        assert "calendar_year" not in frame.columns
        assert "calendar_quarter" not in frame.columns
        assert frame["period_quarter"].iloc[0] == "Q4"

    def test_shift_zero_disables_alignment(self, quarterly_payload):
        from tests.conftest import make_row

        payload = dict(quarterly_payload)
        payload["quarterly"] = [make_row("2026-01")]
        frame, _ = build_frame(payload, "TEST", quarter_shift_months=0)
        assert frame["period_key"].iloc[0] == "2026Q1"
