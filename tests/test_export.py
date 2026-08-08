"""Tests for the reduced Data-sheet schema and display order."""

from __future__ import annotations

import csv

from openpyxl import load_workbook
from pandas.api.types import is_float_dtype

from gurufocus.calculations import add_calculated
from gurufocus.decomposition import DECOMPOSITION_DUMMY_COLUMNS
from gurufocus.export import (
    EXCLUDED_OUTPUT_COLUMNS,
    NON_NUMERIC_OUTPUT_COLUMNS,
    PRIMARY_OUTPUT_COLUMNS,
    STRUCTURAL_OUTPUT_COLUMNS,
    calculation_audit_view,
    number_format_for,
    order_columns,
    split_decomposition_dummies,
    write_csv,
    write_excel,
)
from gurufocus.extract import build_frame


def _exported_panel(quarterly_payload):
    frame, _ = build_frame(quarterly_payload, "TEST")
    frame["sector"] = "Technology"
    frame["industry"] = "Software"
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


def test_sector_and_industry_follow_ticker_and_company(quarterly_payload):
    panel = _exported_panel(quarterly_payload)
    assert panel.columns.tolist()[:4] == [
        "symbol",
        "company",
        "sector",
        "industry",
    ]
    assert panel["sector"].eq("Technology").all()
    assert panel["industry"].eq("Software").all()


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
        # Debt feeds three calculations: the EV bridge, debt-to-equity, and
        # the WACC capital structure. Market cap feeds EV and WACC.
        "calc_debt_value_quarterly": 3,
        "cash_and_cash_equivalents": 2,
        "short_term_investments": 2,
        "market_cap": 2,
    }


def test_all_financial_values_are_float(quarterly_payload):
    panel = _exported_panel(quarterly_payload)
    for position, column in enumerate(panel.columns):
        if column in STRUCTURAL_OUTPUT_COLUMNS:
            continue
        if column in NON_NUMERIC_OUTPUT_COLUMNS:
            continue
        assert is_float_dtype(panel.iloc[:, position]), column


def test_classification_columns_survive_the_export_as_text(quarterly_payload):
    """The float coercion must skip the label columns.

    Without the exemption every classification would be coerced to NaN and the
    workbook would look complete while carrying no decomposition at all.
    """
    panel = _exported_panel(quarterly_payload)
    labels = panel["calc_roic_business_classification"]
    assert labels.notna().all()
    assert labels.map(type).eq(str).all()
    assert panel["calc_roic_explanation"].str.len().gt(0).all()


def test_excel_number_formats_match_the_column_kind(
    quarterly_payload,
    tmp_path,
):
    panel = _exported_panel(quarterly_payload)
    path = tmp_path / "format.xlsx"
    write_excel(path, panel)
    workbook = load_workbook(path, read_only=False, data_only=False)
    sheet = workbook["Data"]
    for position, column in enumerate(panel.columns, start=1):
        expected = number_format_for(column) or "General"
        assert sheet.cell(row=2, column=position).number_format == expected, column


def test_contribution_columns_keep_full_precision_in_csv(
    quarterly_payload,
    tmp_path,
):
    """A ROIC contribution is O(1e-3); "%.2f" would export it as 0.00."""
    panel = _exported_panel(quarterly_payload)
    panel = panel.copy()
    panel["calc_roic_ebit_contribution"] = 0.00312345
    path = tmp_path / "precision.csv"
    write_csv(path, panel)
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    header, first = rows[0], rows[1]
    assert first[header.index("calc_roic_ebit_contribution")] == "0.00312345"
    # Everything else keeps the established two-decimal display.
    assert first[header.index("tax_provision")] == "-20.00"


def test_dummy_columns_move_to_their_own_excel_sheet(quarterly_payload, tmp_path):
    panel = _exported_panel(quarterly_payload)
    data, dummies = split_decomposition_dummies(panel)

    assert not set(DECOMPOSITION_DUMMY_COLUMNS) & set(data.columns)
    assert set(DECOMPOSITION_DUMMY_COLUMNS) <= set(dummies.columns)
    assert dummies.columns.tolist()[:2] == ["symbol", "period_key"]

    path = tmp_path / "split.xlsx"
    write_excel(path, data, decomposition=dummies)
    workbook = load_workbook(path)
    assert "Decomposition" in workbook.sheetnames
    assert workbook["Data"].max_column == data.shape[1]


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
