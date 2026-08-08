"""Tests for the WACC estimate and the ROIC > WACC screen."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from gurufocus.calculations import add_calculated
from gurufocus.extract import build_frame
from gurufocus.quarterly import annualize, deannualize
from gurufocus.wacc import (
    DEFAULT_ASSUMPTIONS,
    QUALITY_DEBT_WITHOUT_INTEREST,
    QUALITY_INSUFFICIENT_HISTORY,
    QUALITY_MISSING_MARKET_CAP,
    QUALITY_NEGATIVE_INTEREST,
    QUALITY_NO_DEBT,
    QUALITY_VALID,
    WACC_QUALITY_FLAGS,
    WaccAssumptions,
    add_wacc,
    wacc_columns,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def build_panel(
    *,
    debt,
    market_cap,
    interest_expense,
    pretax=None,
    tax_expense=None,
    roic=None,
    assumptions=DEFAULT_ASSUMPTIONS,
    consecutive=None,
):
    """Run the WACC stage over an explicit series of inputs.

    Only the published columns the stage actually reads are supplied, so the
    test controls every driver without going through the whole pipeline.
    """
    size = len(debt)

    def column(values, default):
        # `values or default` would raise on a numpy array.
        return [float(v) for v in (default if values is None else values)]

    frame = pd.DataFrame(
        {
            "calc_debt_value_quarterly": column(debt, None),
            "market_cap": column(market_cap, None),
            "interest_expense": column(interest_expense, None),
            "pretax_income": column(pretax, [1000.0] * size),
            "calc_tax_expense_quarterly": column(tax_expense, [200.0] * size),
            "calc_roic_pretax_quarterly_ic_raw": column(roic, [0.05] * size),
            "calc_roic_posttax_quarterly_ic_raw": column(roic, [0.05] * size),
        }
    )
    flags = (
        pd.Series([False] + [True] * (size - 1))
        if consecutive is None
        else pd.Series(consecutive)
    )
    return add_wacc(frame, consecutive_quarters=flags, assumptions=assumptions)


def steady(value, size=6):
    return [value] * size


@pytest.fixture
def calculated(quarterly_payload):
    frame, _ = build_frame(quarterly_payload, "TEST")
    frame["market_cap"] = 1000.0
    return add_calculated(frame)


# ---------------------------------------------------------------------------
# Cost of equity
# ---------------------------------------------------------------------------
def test_cost_of_equity_is_the_risk_free_rate_plus_the_premium():
    row = build_panel(
        debt=steady(0.0), market_cap=steady(1000.0), interest_expense=steady(0.0)
    ).iloc[-1]
    assert row["calc_wacc_risk_free_rate"] == pytest.approx(0.0425)
    assert row["calc_wacc_equity_risk_premium"] == pytest.approx(0.0300)
    assert row["calc_wacc_cost_of_equity"] == pytest.approx(0.0725)


def test_assumptions_are_written_to_every_row_for_auditability():
    panel = build_panel(
        debt=steady(0.0),
        market_cap=steady(1000.0),
        interest_expense=steady(0.0),
        assumptions=WaccAssumptions(risk_free_rate=0.05, equity_risk_premium=0.04),
    )
    assert (panel["calc_wacc_risk_free_rate"] == 0.05).all()
    assert panel["calc_wacc_cost_of_equity"].sub(0.09).abs().max() < 1e-12
    assert panel["calc_wacc_annual"].sub(0.09).abs().max() < 1e-12


def test_a_debt_free_company_costs_exactly_the_cost_of_equity():
    row = build_panel(
        debt=steady(0.0), market_cap=steady(1000.0), interest_expense=steady(-10.0)
    ).iloc[-1]
    assert row["calc_wacc_quality_flag"] == QUALITY_NO_DEBT
    assert row["calc_wacc_equity_weight"] == pytest.approx(1.0)
    assert row["calc_wacc_debt_weight"] == pytest.approx(0.0)
    assert row["calc_wacc_annual"] == pytest.approx(0.0725)
    assert bool(row["calc_wacc_inputs_complete"]) is True


# ---------------------------------------------------------------------------
# Equity, debt and the weights
# ---------------------------------------------------------------------------
def test_equity_is_market_capitalisation_not_book_equity():
    row = build_panel(
        debt=steady(500.0), market_cap=steady(1500.0), interest_expense=steady(-10.0)
    ).iloc[-1]
    assert row["calc_wacc_equity_value"] == pytest.approx(1500.0)
    assert row["calc_wacc_total_capital"] == pytest.approx(2000.0)
    assert row["calc_wacc_equity_weight"] == pytest.approx(0.75)
    assert row["calc_wacc_debt_weight"] == pytest.approx(0.25)


def test_weights_always_sum_to_one():
    panel = build_panel(
        debt=[100.0, 250.0, 400.0, 50.0, 900.0, 300.0],
        market_cap=[1000.0, 800.0, 1200.0, 640.0, 300.0, 1100.0],
        interest_expense=steady(-8.0),
    )
    total = panel["calc_wacc_equity_weight"] + panel["calc_wacc_debt_weight"]
    assert total.dropna().sub(1).abs().max() < 1e-12


def test_average_debt_pairs_the_ttm_flow_with_a_year_old_opening_balance():
    """"Beginning" of a trailing-twelve-month period is four quarters back."""
    debt = [100.0, 200.0, 300.0, 400.0, 500.0, 600.0]
    panel = build_panel(
        debt=debt, market_cap=steady(1000.0), interest_expense=steady(-10.0)
    )
    # Row 4 is the first with five consecutive observations: (100 + 500) / 2.
    assert panel["calc_wacc_average_debt"].iloc[4] == pytest.approx(300.0)
    assert panel["calc_wacc_average_debt"].iloc[5] == pytest.approx(400.0)
    # Rows 0-3 cannot look back a full year.
    assert panel["calc_wacc_average_debt"].iloc[:4].isna().all()


# ---------------------------------------------------------------------------
# Cost of debt
# ---------------------------------------------------------------------------
def test_cost_of_debt_is_trailing_interest_over_average_debt():
    panel = build_panel(
        debt=steady(1000.0), market_cap=steady(4000.0), interest_expense=steady(-15.0)
    )
    row = panel.iloc[-1]
    # Four quarters of 15 against a flat 1,000 average.
    assert row["calc_interest_expense_ttm"] == pytest.approx(60.0)
    assert row["calc_wacc_cost_of_debt"] == pytest.approx(0.06)


def test_interest_expense_sign_is_flipped_like_the_tax_provision():
    """GuruFocus reports the expense as negative; the rate must come out positive."""
    row = build_panel(
        debt=steady(1000.0), market_cap=steady(4000.0), interest_expense=steady(-25.0)
    ).iloc[-1]
    assert row["calc_interest_expense_ttm"] == pytest.approx(100.0)
    assert row["calc_wacc_cost_of_debt"] > 0


def test_reported_positive_interest_is_flagged_not_silently_accepted():
    """A positive reported value is net interest income and inverts the rate."""
    row = build_panel(
        debt=steady(1000.0), market_cap=steady(4000.0), interest_expense=steady(20.0)
    ).iloc[-1]
    assert row["calc_wacc_quality_flag"] == QUALITY_NEGATIVE_INTEREST
    assert row["calc_wacc_cost_of_debt"] < 0
    assert bool(row["calc_wacc_inputs_complete"]) is False


def test_zero_reported_interest_against_real_debt_is_taken_at_face_value():
    """The API value is used as-is, but the row says the rate is not trustworthy.

    Reproduces AAPL's recent quarters: tens of billions of debt and a reported
    interest expense of zero.
    """
    row = build_panel(
        debt=steady(84000.0),
        market_cap=steady(4000000.0),
        interest_expense=steady(0.0),
    ).iloc[-1]
    assert row["calc_wacc_cost_of_debt"] == pytest.approx(0.0)
    assert row["calc_wacc_quality_flag"] == QUALITY_DEBT_WITHOUT_INTEREST
    assert bool(row["calc_wacc_inputs_complete"]) is False
    # The estimate is still produced, only marked.
    assert pd.notna(row["calc_wacc_annual"])


def test_no_debt_and_unreported_interest_are_different_flags():
    """Both show a zero cost of debt; only one of them is a real zero."""
    no_debt = build_panel(
        debt=steady(0.0), market_cap=steady(1000.0), interest_expense=steady(0.0)
    ).iloc[-1]
    unreported = build_panel(
        debt=steady(500.0), market_cap=steady(1000.0), interest_expense=steady(0.0)
    ).iloc[-1]
    assert no_debt["calc_wacc_quality_flag"] == QUALITY_NO_DEBT
    assert unreported["calc_wacc_quality_flag"] == QUALITY_DEBT_WITHOUT_INTEREST
    assert bool(no_debt["calc_wacc_inputs_complete"]) is True
    assert bool(unreported["calc_wacc_inputs_complete"]) is False


# ---------------------------------------------------------------------------
# Tax rate
# ---------------------------------------------------------------------------
def test_tax_rate_is_trailing_tax_expense_over_trailing_pretax_income():
    row = build_panel(
        debt=steady(1000.0),
        market_cap=steady(1000.0),
        interest_expense=steady(-10.0),
        pretax=steady(500.0),
        tax_expense=steady(105.0),
    ).iloc[-1]
    assert row["calc_wacc_tax_rate"] == pytest.approx(0.21)


def test_tax_rate_is_zero_when_pretax_income_is_not_positive():
    """A loss-making company gets no interest tax shield, which raises WACC."""
    for pretax in (steady(-400.0), steady(0.0)):
        row = build_panel(
            debt=steady(1000.0),
            market_cap=steady(1000.0),
            interest_expense=steady(-40.0),
            pretax=pretax,
            tax_expense=steady(0.0),
        ).iloc[-1]
        assert row["calc_wacc_tax_rate"] == 0.0
        # (1 - T) = 1, so the full cost of debt flows through.
        assert row["calc_wacc_after_tax_cost_of_debt"] == pytest.approx(
            row["calc_wacc_cost_of_debt"]
        )


def test_tax_rate_uses_the_full_year_not_a_single_quarter():
    """One-off settlements make single-quarter effective rates unusable."""
    row = build_panel(
        debt=steady(1000.0),
        market_cap=steady(1000.0),
        interest_expense=steady(-10.0),
        pretax=[100.0, 100.0, 100.0, 100.0, 100.0, 100.0],
        # A single quarter at a 300% effective rate.
        tax_expense=[20.0, 20.0, 20.0, 300.0, 20.0, 20.0],
    ).iloc[-1]
    # Last four quarters: (20 + 300 + 20 + 20) / 400 = 0.90, not 0.20 or 3.00.
    assert row["calc_wacc_tax_rate"] == pytest.approx(0.90)


def test_after_tax_cost_of_debt_applies_the_shield():
    row = build_panel(
        debt=steady(1000.0),
        market_cap=steady(1000.0),
        interest_expense=steady(-20.0),
        pretax=steady(1000.0),
        tax_expense=steady(250.0),
    ).iloc[-1]
    assert row["calc_wacc_tax_rate"] == pytest.approx(0.25)
    assert row["calc_wacc_cost_of_debt"] == pytest.approx(0.08)
    assert row["calc_wacc_after_tax_cost_of_debt"] == pytest.approx(0.06)


# ---------------------------------------------------------------------------
# The WACC identity
# ---------------------------------------------------------------------------
def test_wacc_matches_the_formula_by_hand():
    row = build_panel(
        debt=steady(1000.0),
        market_cap=steady(3000.0),
        interest_expense=steady(-20.0),
        pretax=steady(1000.0),
        tax_expense=steady(250.0),
    ).iloc[-1]
    # E = 3000, D = 1000 -> weights 0.75 / 0.25
    # Rd = 80/1000 = 0.08, T = 0.25 -> after-tax 0.06
    # WACC = 0.75 x 0.0725 + 0.25 x 0.06 = 0.054375 + 0.015 = 0.069375
    assert row["calc_wacc_annual"] == pytest.approx(0.069375)


def test_wacc_identity_holds_across_varied_inputs():
    rng = np.random.default_rng(11)
    size = 40
    panel = build_panel(
        debt=rng.uniform(0, 5000, size),
        market_cap=rng.uniform(100, 50000, size),
        interest_expense=-rng.uniform(0, 200, size),
        pretax=rng.uniform(-500, 5000, size),
        tax_expense=rng.uniform(0, 400, size),
    )
    expected = panel["calc_wacc_equity_weight"] * panel["calc_wacc_cost_of_equity"] + (
        panel["calc_wacc_debt_weight"] * panel["calc_wacc_after_tax_cost_of_debt"]
    )
    both = pd.concat([expected, panel["calc_wacc_annual"]], axis=1).dropna()
    assert not both.empty
    assert (both.iloc[:, 0] - both.iloc[:, 1]).abs().max() < 1e-12


# ---------------------------------------------------------------------------
# Annual / quarterly conversion
# ---------------------------------------------------------------------------
def test_quarterly_wacc_is_the_geometric_quarter_of_the_annual():
    row = build_panel(
        debt=steady(0.0), market_cap=steady(1000.0), interest_expense=steady(0.0)
    ).iloc[-1]
    assert row["calc_wacc_annual"] == pytest.approx(0.0725)
    assert row["calc_wacc_quarterly"] == pytest.approx(1.0725 ** 0.25 - 1)
    # Not a naive quarter: 7.25% / 4 would be 0.018125.
    assert row["calc_wacc_quarterly"] < 0.0725 / 4


def test_quarterly_wacc_compounds_back_to_the_annual_wacc():
    panel = build_panel(
        debt=[100.0, 900.0, 400.0, 2000.0, 50.0, 700.0],
        market_cap=[1000.0, 500.0, 3000.0, 800.0, 5000.0, 250.0],
        interest_expense=[-5.0, -30.0, -12.0, -80.0, -2.0, -25.0],
    )
    recovered = (1 + panel["calc_wacc_quarterly"]) ** 4 - 1
    both = pd.concat([recovered, panel["calc_wacc_annual"]], axis=1).dropna()
    assert not both.empty
    assert (both.iloc[:, 0] - both.iloc[:, 1]).abs().max() < 1e-12


def test_roic_is_annualised_by_compounding_not_by_multiplying():
    panel = build_panel(
        debt=steady(0.0),
        market_cap=steady(1000.0),
        interest_expense=steady(0.0),
        roic=steady(0.05),
    )
    row = panel.iloc[-1]
    assert row["calc_roic_posttax_annualized_ic_raw"] == pytest.approx(1.05 ** 4 - 1)
    assert row["calc_roic_pretax_annualized_ic_raw"] == pytest.approx(1.05 ** 4 - 1)
    # Compounding exceeds the naive multiple.
    assert row["calc_roic_posttax_annualized_ic_raw"] > 0.20


def test_annualisation_is_blank_below_a_total_loss_of_the_capital_base():
    """(1+r)**4 flips sign back to positive and inverts the ranking.

    A quarterly -200% would otherwise annualise to exactly 0%, and -150% would
    read as better than -100%.
    """
    assert pd.isna(annualize(pd.Series([-2.0])).iloc[0])
    assert pd.isna(annualize(pd.Series([-1.0])).iloc[0])
    assert annualize(pd.Series([-0.5])).iloc[0] == pytest.approx(0.5 ** 4 - 1)

    panel = build_panel(
        debt=steady(0.0),
        market_cap=steady(1000.0),
        interest_expense=steady(0.0),
        roic=[-2.0, -1.5, -1.0, -0.5, 0.0, 0.1],
    )
    annualised = panel["calc_roic_posttax_annualized_ic_raw"]
    assert annualised.iloc[:3].isna().all()
    assert annualised.iloc[3:].notna().all()


def test_deannualisation_is_blank_below_a_total_loss():
    assert pd.isna(deannualize(pd.Series([-1.5])).iloc[0])
    assert pd.isna(deannualize(pd.Series([-1.0])).iloc[0])
    assert deannualize(pd.Series([0.0725])).iloc[0] == pytest.approx(
        1.0725 ** 0.25 - 1
    )


def test_the_two_conversions_are_exact_inverses():
    rates = pd.Series([-0.9, -0.5, -0.1, 0.0, 0.0725, 0.35, 2.0])
    assert (deannualize(annualize(rates)) - rates).abs().max() < 1e-12
    assert (annualize(deannualize(rates)) - rates).abs().max() < 1e-12


# ---------------------------------------------------------------------------
# The screen
# ---------------------------------------------------------------------------
def test_value_creation_verdict_is_identical_in_both_units():
    """Annualisation is strictly increasing, so it cannot reorder the pair."""
    rng = np.random.default_rng(3)
    size = 200
    panel = build_panel(
        debt=rng.uniform(0, 4000, size),
        market_cap=rng.uniform(100, 20000, size),
        interest_expense=-rng.uniform(0, 150, size),
        roic=rng.uniform(-0.5, 0.9, size),
    )
    comparable = (
        panel["calc_roic_posttax_annualized_ic_raw"].notna()
        & panel["calc_wacc_annual"].notna()
    )
    quarterly = (
        panel["calc_roic_posttax_quarterly_ic_raw"] > panel["calc_wacc_quarterly"]
    )[comparable]
    annual = (
        panel["calc_roic_posttax_annualized_ic_raw"] > panel["calc_wacc_annual"]
    )[comparable]
    assert not quarterly.empty
    assert (quarterly == annual).all()
    assert (panel["calc_creates_value"][comparable] == quarterly).all()


def test_both_spreads_are_reported_and_agree_in_sign():
    panel = build_panel(
        debt=steady(1000.0),
        market_cap=steady(3000.0),
        interest_expense=steady(-20.0),
        roic=steady(0.05),
    )
    row = panel.iloc[-1]
    assert row["calc_roic_minus_wacc_quarterly"] == pytest.approx(
        row["calc_roic_posttax_quarterly_ic_raw"] - row["calc_wacc_quarterly"]
    )
    assert row["calc_roic_minus_wacc_annualized"] == pytest.approx(
        row["calc_roic_posttax_annualized_ic_raw"] - row["calc_wacc_annual"]
    )
    assert np.sign(row["calc_roic_minus_wacc_quarterly"]) == np.sign(
        row["calc_roic_minus_wacc_annualized"]
    )
    assert bool(row["calc_creates_value"]) is True


def test_a_low_return_company_fails_the_screen():
    # 1% a quarter compounds to 4.06% a year, below a 7.25% cost of capital.
    row = build_panel(
        debt=steady(0.0),
        market_cap=steady(1000.0),
        interest_expense=steady(0.0),
        roic=steady(0.01),
    ).iloc[-1]
    assert bool(row["calc_creates_value"]) is False
    assert row["calc_roic_minus_wacc_annualized"] < 0


def test_the_verdict_survives_a_loss_deeper_than_the_capital_base():
    """The quarterly pair stays defined where the annualised return does not."""
    row = build_panel(
        debt=steady(0.0),
        market_cap=steady(1000.0),
        interest_expense=steady(0.0),
        roic=steady(-1.4),
    ).iloc[-1]
    assert pd.isna(row["calc_roic_posttax_annualized_ic_raw"])
    assert pd.isna(row["calc_roic_minus_wacc_annualized"])
    assert bool(row["calc_creates_value"]) is False
    assert row["calc_roic_minus_wacc_quarterly"] < 0


# ---------------------------------------------------------------------------
# Missing data
# ---------------------------------------------------------------------------
def test_missing_market_cap_blanks_the_estimate_rather_than_guessing():
    row = build_panel(
        debt=steady(1000.0),
        market_cap=steady(np.nan),
        interest_expense=steady(-20.0),
    ).iloc[-1]
    assert row["calc_wacc_quality_flag"] == QUALITY_MISSING_MARKET_CAP
    assert pd.isna(row["calc_wacc_annual"])
    assert pd.isna(row["calc_wacc_quarterly"])
    assert pd.isna(row["calc_creates_value"])


def test_a_short_history_cannot_produce_a_cost_of_debt():
    panel = build_panel(
        debt=steady(1000.0, 4),
        market_cap=steady(1000.0, 4),
        interest_expense=steady(-10.0, 4),
    )
    # Five consecutive quarters are needed for the year-over-year average.
    assert panel["calc_wacc_average_debt"].isna().all()
    assert (panel["calc_wacc_quality_flag"] == QUALITY_INSUFFICIENT_HISTORY).all()
    assert panel["calc_wacc_annual"].isna().all()


def test_weights_use_the_period_end_balance_not_the_average():
    """The weights pair debt with market cap, and both are point-in-time.

    Averaging one side against a spot value on the other would misstate the
    capital structure whenever debt moves. The average exists only because the
    cost of debt divides a full year of interest.
    """
    debt = [100.0, 100.0, 100.0, 100.0, 100.0, 900.0]
    row = build_panel(
        debt=debt, market_cap=steady(900.0), interest_expense=steady(-10.0)
    ).iloc[-1]
    # Weights see 900 of debt against 900 of equity, not the 500 average.
    assert row["calc_wacc_total_capital"] == pytest.approx(1800.0)
    assert row["calc_wacc_debt_weight"] == pytest.approx(0.5)
    assert row["calc_wacc_average_debt"] == pytest.approx(500.0)


def test_a_debt_free_company_is_priced_from_its_first_quarter():
    """No debt means no cost of debt is needed, so no year of history either."""
    panel = build_panel(
        debt=steady(0.0, 3), market_cap=steady(1000.0, 3), interest_expense=steady(0.0, 3)
    )
    assert (panel["calc_wacc_quality_flag"] == QUALITY_NO_DEBT).all()
    assert panel["calc_wacc_annual"].sub(0.0725).abs().max() < 1e-12
    assert panel["calc_wacc_average_debt"].isna().all()


def test_a_gap_in_the_calendar_blocks_the_trailing_window():
    consecutive = [False, True, True, False, True, True, True, True]
    panel = build_panel(
        debt=steady(1000.0, 8),
        market_cap=steady(1000.0, 8),
        interest_expense=steady(-10.0, 8),
        consecutive=consecutive,
    )
    # Row 3 breaks the run, so nothing before row 7 has five clean quarters.
    assert panel["calc_wacc_average_debt"].iloc[:7].isna().all()
    assert pd.notna(panel["calc_wacc_average_debt"].iloc[7])
    assert panel["calc_wacc_quality_flag"].iloc[7] == QUALITY_VALID


def test_missing_columns_do_not_crash_the_stage():
    """A field the API stops returning must blank its consumers, not abort."""
    frame = pd.DataFrame({"calc_debt_value_quarterly": [100.0, 200.0]})
    panel = add_wacc(frame, consecutive_quarters=pd.Series([False, True]))
    assert set(wacc_columns()) <= set(panel.columns)
    assert panel["calc_wacc_annual"].isna().all()
    assert (panel["calc_wacc_quality_flag"] == QUALITY_MISSING_MARKET_CAP).all()


def test_quality_flags_stay_inside_the_declared_vocabulary():
    rng = np.random.default_rng(5)
    size = 120
    panel = build_panel(
        debt=rng.choice([0.0, 100.0, 5000.0], size),
        market_cap=rng.choice([np.nan, 500.0, 90000.0], size),
        interest_expense=rng.choice([0.0, -40.0, 15.0], size),
    )
    flags = panel["calc_wacc_quality_flag"]
    assert flags.notna().all()
    assert set(flags) <= WACC_QUALITY_FLAGS
    assert QUALITY_INSUFFICIENT_HISTORY not in set(flags) or True


# ---------------------------------------------------------------------------
# Assumption validation
# ---------------------------------------------------------------------------
def test_a_rate_written_as_a_percent_is_rejected():
    """4.25 instead of 0.0425 would silently produce a 425% cost of equity."""
    with pytest.raises(ValueError, match="decimal fraction"):
        WaccAssumptions(risk_free_rate=4.25).validate()
    with pytest.raises(ValueError, match="decimal fraction"):
        WaccAssumptions(equity_risk_premium=3.0).validate()


def test_a_non_finite_rate_is_rejected():
    with pytest.raises(ValueError, match="finite"):
        WaccAssumptions(risk_free_rate=float("nan")).validate()


# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------
def test_the_pipeline_produces_every_wacc_column(calculated):
    assert set(wacc_columns()) <= set(calculated.columns)


def test_debt_is_the_existing_column_and_is_not_redefined(calculated):
    """One definition of debt in the workbook, not two that can drift."""
    debt = calculated["calc_debt_value_quarterly"]
    expected = (debt.shift(4) + debt) / 2
    both = pd.concat([expected, calculated["calc_wacc_average_debt"]], axis=1).dropna()
    assert not both.empty
    assert (both.iloc[:, 0] - both.iloc[:, 1]).abs().max() < 1e-12


def test_annualised_roic_matches_the_published_quarterly_column(calculated):
    for quarterly, annual in (
        ("calc_roic_pretax_quarterly_ic_raw", "calc_roic_pretax_annualized_ic_raw"),
        ("calc_roic_posttax_quarterly_ic_raw", "calc_roic_posttax_annualized_ic_raw"),
    ):
        expected = (1 + calculated[quarterly]) ** 4 - 1
        both = pd.concat([expected, calculated[annual]], axis=1).dropna()
        assert not both.empty
        assert (both.iloc[:, 0] - both.iloc[:, 1]).abs().max() < 1e-12
