"""Tests for selected fields from GuruFocus historical valuations."""

from __future__ import annotations

import pandas as pd
import pytest

from gurufocus.extract import build_frame
from gurufocus.valuations import VALUATION_COLUMNS, attach_valuations


def test_quarterly_valuations_are_joined_by_period_end(quarterly_payload):
    frame, _ = build_frame(quarterly_payload, "TEST")
    payload = {
        "quarterly": [
            {
                "date": "2020-03",
                "valuationand_quality": {"mktcap": "1,234.5"},
                "valuation_ratios": {"enterprise_value_to_fcf": "15.75"},
                "per_share_data": {
                    "shares_outstanding": "50.25",
                    "month_end_stock_price": "24.57",
                },
                "ratios": {"debt_to_equity": "0.42"},
            },
            {
                "date": "2020-06",
                "valuationand_quality": {"mktcap": 1300},
                "valuation_ratios": {"enterprise_value_to_fcf": 16},
                "per_share_data": {
                    "shares_outstanding": 51,
                    "month_end_stock_price": 25,
                },
                "ratios": {"debt_to_equity": 0.40},
            },
        ]
    }

    result, report = attach_valuations(frame, payload)
    first = result.iloc[0]
    assert first["market_cap"] == pytest.approx(1234.5)
    assert first[
        "valuations__valuation_ratios__enterprise_value_to_fcf"
    ] == pytest.approx(15.75)
    assert first[
        "valuations__per_share_data__shares_outstanding"
    ] == pytest.approx(50.25)
    assert first[
        "valuations__per_share_data__month_end_stock_price"
    ] == pytest.approx(24.57)
    assert first["valuations__ratios__debt_to_equity"] == pytest.approx(0.42)
    assert pd.isna(result.loc[2, "market_cap"])
    assert report["rows_available"] == 2
    assert report["rows_matched"] == 2
    assert set(report["coverage"]["status"]) == {"OK"}


def test_empty_valuations_keep_all_requested_blank_columns(quarterly_payload):
    frame, _ = build_frame(quarterly_payload, "TEST")
    result, report = attach_valuations(frame, {})
    for column in VALUATION_COLUMNS:
        assert result[column].isna().all()
    assert len(report["coverage"]) == len(VALUATION_COLUMNS)
    assert set(report["coverage"]["status"]) == {"MISSING"}
