"""Tests for the reduced Data-sheet schema and display order."""

from __future__ import annotations

import csv

from openpyxl import load_workbook
from pandas.api.types import is_float_dtype

from gurufocus.calculations import add_calculated
from gurufocus.export import (
    EXCLUDED_OUTPUT_COLUMNS,
    PRIMARY_OUTPUT_COLUMNS,
    STRUCTURAL_OUTPUT_COLUMNS,
    calculation_audit_view,
    order_columns,
    write_csv,
    write_excel,
)
from gurufocus.extract import build_frame


def _exported_panel(quarterly_payload):
    frame, _ = build_frame(quarterly_payload, "TEST")
    frame["market_cap"] = 1000.0
    frame["valuations__valuation_ratios__enterprise_value_to_fcf"] = 12.5
    frame["valuations__per_share_data__shares_outstanding"] = 50.0
    frame["valuations__per_share_data__month_end_stock_price"] = 20.0
    frame["valuations__ratios__debt_to_equity"] = 0.25
    frame["run_date"] = "2026-08-01"
    return calculation_audit_view(order_columns(add_calculated(frame)))


def test_data_columns_have_the_exact_requested_order(quarterly_payload):
    panel = _exported_panel(quarterly_payload)
    assert panel.columns.tolist() == (
        STRUCTURAL_OUTPUT_COLUMNS + PRIMARY_OUTPUT_COLUMNS
    )


def test_sources_are_immediately_before_related_calculations(quarterly_payload):
    columns = _exported_panel(quarterly_payload).columns.tolist()
    assert columns[columns.index("tax_provision"):columns.index("calc_nopat_quarterly") + 1] == [
        "tax_provision",
        "calc_tax_expense_quarterly",
        "pretax_income",
        "calc_raw_tax_rate_quarterly",
        "ebit",
        "calc_nopat_quarterly",
    ]
    assert columns[columns.index("short_term_debt_and_capital_lease"):columns.index("valuations__ratios__debt_to_equity") + 1] == [
        "short_term_debt_and_capital_lease",
        "long_term_debt_and_capital_lease",
        "calc_debt_value_quarterly",
        "total_assets",
        "total_liabilities",
        "equity",
        "total_stockholders_equity",
        "calc_debt_to_equity_quarterly",
        "valuations__ratios__debt_to_equity",
    ]
    assert columns[columns.index("market_cap"):columns.index("valuations__valuation_ratios__enterprise_value_to_fcf") + 1] == [
        "market_cap",
        "calc_debt_value_quarterly",
        "cash_and_cash_equivalents",
        "short_term_investments",
        "calc_enterprise_value_quarterly",
        "free_cash_flow",
        "calc_free_cash_flow_ttm",
        "calc_ev_to_fcf_quarterly",
        "valuations__valuation_ratios__enterprise_value_to_fcf",
    ]


def test_only_shared_calculation_sources_are_duplicated(quarterly_payload):
    panel = _exported_panel(quarterly_payload)
    assert not EXCLUDED_OUTPUT_COLUMNS & set(panel.columns)
    counts = panel.columns.value_counts()
    assert counts[counts > 1].to_dict() == {
        "cash_and_cash_equivalents": 2,
        "short_term_investments": 2,
        "calc_debt_value_quarterly": 2,
    }


def test_all_financial_values_are_float(quarterly_payload):
    panel = _exported_panel(quarterly_payload)
    for position, column in enumerate(panel.columns):
        if column in STRUCTURAL_OUTPUT_COLUMNS:
            continue
        assert is_float_dtype(panel.iloc[:, position]), column


def test_all_financial_values_use_two_decimal_excel_format(
    quarterly_payload,
    tmp_path,
):
    panel = _exported_panel(quarterly_payload)
    path = tmp_path / "format.xlsx"
    write_excel(path, panel)
    workbook = load_workbook(path, read_only=False, data_only=False)
    sheet = workbook["Data"]
    for position, column in enumerate(panel.columns, start=1):
        expected = "General" if column in STRUCTURAL_OUTPUT_COLUMNS else "0.00"
        assert sheet.cell(row=2, column=position).number_format == expected


def test_csv_serializes_float_values_with_two_decimals(
    quarterly_payload,
    tmp_path,
):
    panel = _exported_panel(quarterly_payload)
    path = tmp_path / "format.csv"
    write_csv(path, panel)
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    header, first = rows[0], rows[1]
    assert first[header.index("tax_provision")] == "-20.00"
    assert first[header.index("calc_raw_tax_rate_quarterly")] == "0.22"
    assert first[header.index("market_cap")] == "1000.00"
