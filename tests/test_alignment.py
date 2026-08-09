"""בדיקות כלל היישור.

הכלל יושב במודול משלו כדי שיהיה לו מקור אמת אחד. הבדיקות כאן נועלות אותו
מול המימוש שהיה מוטמע ב-extract.py לפני החילוץ.
"""

from __future__ import annotations

import pandas as pd
import pytest

from gurufocus.alignment import align_to_quarter
from gurufocus.extract import build_frame


class TestQuarterMapping:
    """סוף התקופה פחות חודשיים -> הרבעון הקלנדרי של התוצאה."""

    @pytest.mark.parametrize("date,expected_key", [
        ("2026-03-31", "2026Q1"),   # מרץ   -> ינואר           -> Q1
        ("2025-09-30", "2025Q3"),   # ספט'  -> יולי            -> Q3
        ("2025-12-31", "2025Q4"),   # דצמ'  -> אוקטובר         -> Q4
        ("2026-01-31", "2025Q4"),   # ינו'  -> נובמבר 2025     -> Q4 שנה קודמת
    ])
    def test_date_maps_to_expected_quarter(self, date, expected_key):
        result = align_to_quarter(pd.Series([pd.Timestamp(date)]), 2)
        assert result["period_key"].iloc[0] == expected_key


class TestSharedWithExtract:
    def test_extract_produces_exactly_the_shared_alignment(self, quarterly_payload):
        """רגרסיה על הרפקטור: build_frame לא סטה מהכלל שבמודול."""
        frame, _ = build_frame(quarterly_payload, "TEST")
        expected = align_to_quarter(
            pd.to_datetime(frame["fiscal_period_end_date"]), 2
        )
        for column in ("period_year", "period_quarter", "period_key"):
            assert frame[column].tolist() == expected[column].tolist()

    def test_shift_flows_through_to_extract(self, quarterly_payload):
        frame, _ = build_frame(quarterly_payload, "TEST", quarter_shift_months=0)
        expected = align_to_quarter(
            pd.to_datetime(frame["fiscal_period_end_date"]), 0
        )
        assert frame["period_key"].tolist() == expected["period_key"].tolist()


class TestGuards:
    def test_missing_date_is_rejected(self):
        """מפתח כמו 'nanQ1' היה מתחבר לשום שורה ונראה כמו נתון חסר לגיטימי."""
        with pytest.raises(ValueError, match="תאריך חסר"):
            align_to_quarter(pd.Series([pd.Timestamp("2025-05-30"), pd.NaT]), 2)

    @pytest.mark.parametrize("shift", [-1, 12, 24])
    def test_shift_outside_range_is_rejected(self, shift):
        with pytest.raises(ValueError, match="0-11"):
            align_to_quarter(pd.Series([pd.Timestamp("2025-05-30")]), shift)
