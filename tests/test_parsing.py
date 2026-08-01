"""בדיקות שלב 3 — פירוק ה-JSON."""

from __future__ import annotations

import pandas as pd
import pytest

from gurufocus.parsing import (
    block_to_records,
    extract_metadata,
    find_period_block,
    flatten_row,
    norm_key,
    to_number,
    to_period_end,
)


class TestNormKey:
    @pytest.mark.parametrize("raw,expected", [
        ("Total Assets", "total_assets"),
        ("Property, Plant and Equipment", "property_plant_and_equipment"),
        ("Short-Term Debt & Capital Lease Obligation",
         "short_term_debt_and_capital_lease_obligation"),
        ("EV/EBITDA", "ev_ebitda"),
        ("  Mixed   Spaces  ", "mixed_spaces"),
        ("already_snake", "already_snake"),
    ])
    def test_normalizes(self, raw, expected):
        assert norm_key(raw) == expected


class TestToNumber:
    @pytest.mark.parametrize("raw,expected", [
        (1234, 1234.0),
        (12.5, 12.5),
        ("1,234", 1234.0),
        ("(500)", -500.0),        # סוגריים = שלילי
        ("17.46%", 17.46),
        ("-6255", -6255.0),
        ("", None), ("-", None), ("N/A", None), (None, None),
        (True, None),            # bool אינו מספר לצורך העניין
    ])
    def test_converts(self, raw, expected):
        assert to_number(raw) == expected


class TestToPeriodEnd:
    def test_year_month_becomes_month_end(self):
        """⚠️ הרגרסיה המרכזית: YYYY-MM חייב להפוך לסוף החודש ולא ל-1 בו."""
        assert to_period_end("2026-03") == pd.Timestamp("2026-03-31")
        assert to_period_end("2024-02") == pd.Timestamp("2024-02-29")  # שנה מעוברת
        assert to_period_end("1996-09") == pd.Timestamp("1996-09-30")

    def test_bare_year(self):
        assert to_period_end("2025") == pd.Timestamp("2025-12-31")

    def test_full_date_preserved(self):
        assert to_period_end("2026-03-28") == pd.Timestamp("2026-03-28")

    @pytest.mark.parametrize("raw", ["", None, "not a date"])
    def test_invalid(self, raw):
        assert pd.isna(to_period_end(raw))


class TestFlattenRow:
    def test_sections_are_qualified(self):
        row = {"date": "2026-03", "balance_sheet": {"total_assets": 100}}
        flat = flatten_row(row)
        assert flat["balance_sheet::total_assets"] == 100
        assert flat["date"] == "2026-03"

    def test_same_name_in_two_sections_does_not_collide(self):
        """net_income קיים בכמה סקשנים — כל אחד נשמר בנפרד."""
        row = {
            "income_statement": {"net_income": 100},
            "cashflow_statement": {"net_income": 999},
        }
        flat = flatten_row(row)
        assert flat["income_statement::net_income"] == 100
        assert flat["cashflow_statement::net_income"] == 999


class TestFindPeriodBlock:
    def test_finds_top_level(self, quarterly_payload):
        block = find_period_block(quarterly_payload, "quarterly")
        assert isinstance(block, list) and len(block) == 12

    def test_annually_not_annuals(self, quarterly_payload):
        """שם התקופה השנתית ב-API הוא annually."""
        assert find_period_block(quarterly_payload, "annually") is not None
        assert find_period_block(quarterly_payload, "annuals") is None

    def test_finds_nested(self):
        """עמידות בפני עטיפה עתידית ב-data."""
        payload = {"data": {"quarterly": [{"date": "2026-03"}]}}
        assert find_period_block(payload, "quarterly") is not None

    def test_missing_returns_none(self, quarterly_payload):
        assert find_period_block(quarterly_payload, "monthly") is None


class TestBlockToRecords:
    def test_row_oriented(self, quarterly_payload):
        records = block_to_records(quarterly_payload["quarterly"])
        assert len(records) == 12
        assert records[0]["balance_sheet::total_assets"] == 1000.0

    def test_single_row_dict(self, quarterly_payload):
        """מבנה ttm — dict בודד ולא רשימה."""
        records = block_to_records(quarterly_payload["quarterly"][0])
        assert len(records) == 1

    def test_column_oriented_fallback(self):
        block = {"balance_sheet": {"total_assets": [1, 2, 3]}, "date": ["a", "b", "c"]}
        records = block_to_records(block)
        assert len(records) == 3
        assert records[1]["balance_sheet::total_assets"] == 2

    def test_empty(self):
        assert block_to_records([]) == []
        assert block_to_records({}) == []


class TestExtractMetadata:
    def test_pulls_only_requested_company_metadata(self, quarterly_payload):
        meta = extract_metadata(quarterly_payload)
        assert meta["company"] == "Test Corp"
        assert meta["symbol"] == "TEST"
        assert "currency" not in meta
        assert "exchange" not in meta

    def test_missing_section(self):
        assert extract_metadata({"quarterly": []}) == {}
