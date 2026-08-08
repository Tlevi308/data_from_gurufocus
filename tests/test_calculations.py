"""Tests for the selected quarterly calculations."""

from __future__ import annotations

import pandas as pd
import pytest

from gurufocus.calculations import (
    add_calculated,
    calc_columns,
    calculation_dependencies,
)
from gurufocus.decomposition import decomposition_columns
from gurufocus.extract import build_frame
from gurufocus.wacc import wacc_columns


@pytest.fixture
def calculated(quarterly_payload):
    frame, _ = build_frame(quarterly_payload, "TEST")
    frame["market_cap"] = 1000.0
    return add_calculated(frame)


def test_only_declared_flows_are_summed_over_twelve_months(calculated):
    """TTM windows are the exception, not the default.

    Every other flow column stays on a single quarter. These two are annual by
    necessity: the EV/FCF multiple is a market convention, and the cost of debt
    has to be an annual rate to sit alongside the cost of equity.
    """
    assert [
        column for column in calculated.columns if column.endswith("_ttm")
    ] == ["calc_free_cash_flow_ttm", "calc_interest_expense_ttm"]


def test_tax_expense_and_nopat_use_only_current_quarter(calculated):
    assert (calculated["calc_tax_expense_quarterly"] == 20).all()
    assert calculated["calc_raw_tax_rate_quarterly"].tolist() == pytest.approx(
        [20 / 90] * len(calculated)
    )
    expected = calculated["ebit"] * (
        1 - calculated["calc_raw_tax_rate_quarterly"]
    )
    assert calculated["calc_nopat_quarterly"].tolist() == pytest.approx(
        expected.tolist()
    )


def test_zero_pretax_income_returns_blank_rate_and_nopat(quarterly_payload):
    from tests.conftest import make_row

    payload = dict(quarterly_payload)
    payload["quarterly"] = [make_row("2020-03", pretax=0, tax=0)]
    frame, _ = build_frame(payload, "TEST")
    result = add_calculated(frame)
    assert pd.isna(result["calc_raw_tax_rate_quarterly"].iloc[0])
    assert pd.isna(result["calc_nopat_quarterly"].iloc[0])


def test_ic_raw_formula(calculated):
    # 400 - 300 + 200 + 30 = 330
    assert calculated["calc_ic_raw"].iloc[0] == pytest.approx(330)


def test_ic_raw_average_uses_opening_and_closing_balances(calculated):
    expected = (
        calculated["calc_ic_raw"].shift(1) + calculated["calc_ic_raw"]
    ) / 2
    both = pd.concat(
        [expected, calculated["calc_average_ic_raw_quarterly"]], axis=1
    ).dropna()
    assert not both.empty
    assert (both.iloc[:, 0] - both.iloc[:, 1]).abs().max() < 1e-9


def test_gap_blocks_only_the_following_ic_average(quarterly_payload):
    payload = dict(quarterly_payload)
    payload["quarterly"] = [
        row
        for row in quarterly_payload["quarterly"]
        if row["date"] != "2021-06"
    ]
    frame, _ = build_frame(payload, "TEST")
    result = add_calculated(frame)
    after_gap = result[result["fiscal_period_end_date"] == "2021-09-30"].iloc[0]
    assert pd.isna(after_gap["calc_average_ic_raw_quarterly"])
    assert pd.isna(after_gap["calc_roic_posttax_quarterly_ic_raw"])
    assert pd.isna(after_gap["calc_free_cash_flow_ttm"])
    assert pd.isna(after_gap["calc_ev_to_fcf_quarterly"])
    assert pd.notna(after_gap["calc_nopat_quarterly"])


def test_raw_roic_uses_quarterly_numerators_without_annualization(calculated):
    average = calculated["calc_average_ic_raw_quarterly"]
    expected_pretax = calculated["ebit"] / average
    expected_posttax = calculated["calc_nopat_quarterly"] / average
    for expected, actual in (
        (expected_pretax, calculated["calc_roic_pretax_quarterly_ic_raw"]),
        (expected_posttax, calculated["calc_roic_posttax_quarterly_ic_raw"]),
    ):
        both = pd.concat([expected, actual], axis=1).dropna()
        assert not both.empty
        assert (both.iloc[:, 0] - both.iloc[:, 1]).abs().max() < 1e-9


def test_zero_ic_denominator_returns_blank(quarterly_payload):
    from tests.conftest import make_row

    payload = dict(quarterly_payload)
    payload["quarterly"] = [
        make_row(
            f"{year}-{month}",
            total_current_assets=300,
            total_current_liabilities=300,
            net_ppe=0,
            goodwill=0,
        )
        for year in (2020, 2021)
        for month in ("03", "06", "09", "12")
    ]
    frame, _ = build_frame(payload, "TEST")
    result = add_calculated(frame)
    assert result["calc_roic_pretax_quarterly_ic_raw"].isna().all()
    assert result["calc_roic_posttax_quarterly_ic_raw"].isna().all()


def test_debt_value_and_debt_to_equity(calculated):
    assert (calculated["calc_debt_value_quarterly"] == 100).all()
    assert calculated["calc_debt_to_equity_quarterly"].tolist() == pytest.approx(
        [0.25] * len(calculated)
    )


def test_ev_to_fcf_uses_trailing_four_quarter_fcf(calculated):
    # EV = 1,000 + 100 - 100 - 50 = 950; TTM FCF = 4 * 70 = 280.
    assert calculated["calc_free_cash_flow_ttm"].iloc[:3].isna().all()
    assert calculated["calc_free_cash_flow_ttm"].iloc[3:].tolist() == pytest.approx(
        [280] * (len(calculated) - 3)
    )
    assert calculated["calc_ev_to_fcf_quarterly"].iloc[3:].tolist() == pytest.approx(
        [950 / 280] * (len(calculated) - 3)
    )


def test_zero_fcf_and_equity_return_blank_ratios(quarterly_payload):
    frame, _ = build_frame(quarterly_payload, "TEST")
    frame["market_cap"] = 1000.0
    frame["free_cash_flow"] = 0.0
    frame["total_stockholders_equity"] = 0.0
    result = add_calculated(frame)
    assert result["calc_ev_to_fcf_quarterly"].isna().all()
    assert result["calc_debt_to_equity_quarterly"].isna().all()


def test_only_requested_result_columns_are_exposed():
    base = [
        "calc_tax_expense_quarterly",
        "calc_raw_tax_rate_quarterly",
        "calc_nopat_quarterly",
        "calc_ic_raw",
        "calc_average_ic_raw_quarterly",
        "calc_roic_pretax_quarterly_ic_raw",
        "calc_roic_posttax_quarterly_ic_raw",
        "calc_debt_value_quarterly",
        "calc_debt_to_equity_quarterly",
        "calc_enterprise_value_quarterly",
        "calc_free_cash_flow_ttm",
        "calc_ev_to_fcf_quarterly",
    ]
    columns = calc_columns()
    # The original twelve keep their names and their position; later stages are
    # appended after them in dependency order, never interleaved.
    assert columns[: len(base)] == base
    assert columns[len(base):] == decomposition_columns() + wacc_columns()


def test_every_calculated_column_declares_its_sources(calculated):
    dependencies = calculation_dependencies()
    assert set(dependencies) == set(calc_columns())
    # A column that is declared but never actually produced would make the
    # contract silently wrong, so check the frame too.
    assert set(calc_columns()) <= set(calculated.columns)
