"""Tests for the NOPAT and post-tax ROIC driver decomposition.

Covers the twenty five scenarios required by the specification, plus the
structural guarantees: exhaustive classification, exactly-one-hot encoding,
exact efficiency of the Shapley split, and independence from factor ordering.
"""

from __future__ import annotations

from itertools import permutations

import numpy as np
import pandas as pd
import pytest

from gurufocus.calculations import add_calculated
from gurufocus.decomposition import (
    BUSINESS_CLASSIFICATIONS,
    DECOMPOSITION_DUMMY_COLUMNS,
    DEFAULT_TOLERANCE,
    EFFECT_STRUCTURES,
    NOPAT_COMBO_COLUMNS,
    RAW_COMBO_COLUMNS,
    ROIC_COMBO_COLUMNS,
    SIGN_REGIMES,
    STATUS_MISSING_DATA,
    STATUS_NONCONSECUTIVE,
    STATUS_VALID,
    STATUS_ZERO_IC,
    add_decomposition,
    shapley_contributions,
)
from gurufocus.extract import build_frame


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def build_pair(ebit, tax_rate, capital, *, tolerance=DEFAULT_TOLERANCE):
    """Decompose an explicit series of (EBIT, tax rate, average IC) values.

    The published NOPAT and ROIC columns are derived here exactly as
    :mod:`gurufocus.calculations` derives them, so the decomposition sees the
    same inputs it sees in the pipeline while the test keeps direct control of
    every driver.
    """
    frame = pd.DataFrame(
        {
            "ebit": [float(value) for value in ebit],
            "calc_raw_tax_rate_quarterly": [float(value) for value in tax_rate],
            "calc_average_ic_raw_quarterly": [float(value) for value in capital],
        }
    )
    frame["calc_nopat_quarterly"] = frame["ebit"] * (
        1 - frame["calc_raw_tax_rate_quarterly"]
    )
    invested = frame["calc_average_ic_raw_quarterly"]
    frame["calc_roic_posttax_quarterly_ic_raw"] = frame[
        "calc_nopat_quarterly"
    ] / invested.where(invested != 0)
    consecutive = pd.Series([False] + [True] * (len(frame) - 1))
    return add_decomposition(
        frame, consecutive_quarters=consecutive, tolerance=tolerance
    )


def last(ebit, tax_rate, capital, **kwargs):
    """The classified row for the second of two consecutive observations."""
    return build_pair(ebit, tax_rate, capital, **kwargs).iloc[-1]


@pytest.fixture
def calculated(quarterly_payload):
    frame, _ = build_frame(quarterly_payload, "TEST")
    frame["market_cap"] = 1000.0
    return add_calculated(frame)


# ---------------------------------------------------------------------------
# 1-4: one driver at a time
# ---------------------------------------------------------------------------
def test_ebit_rises_alone():
    row = last([100, 120], [0.25, 0.25], [1000, 1000])
    assert row["calc_roic_posttax_change_direction"] == "INCREASE"
    assert row["calc_roic_ebit_effect"] == "POSITIVE"
    assert row["calc_roic_tax_effect"] == "NEUTRAL"
    assert row["calc_roic_ic_effect"] == "NEUTRAL"
    assert row["calc_roic_effect_structure"] == "SINGLE_POSITIVE_DRIVER"
    assert row["calc_roic_dominant_driver"] == "EBIT"
    assert row["calc_roic_effect_combination"] == "EBIT_POS__TAX_ZERO__IC_ZERO"
    assert row["calc_raw_movement_combination"] == "EBIT_UP__TAX_FLAT__IC_FLAT"


def test_tax_rate_falls_alone():
    row = last([100, 100], [0.30, 0.20], [1000, 1000])
    assert row["calc_roic_posttax_change_direction"] == "INCREASE"
    assert row["calc_roic_tax_effect"] == "POSITIVE"
    assert row["calc_roic_ebit_effect"] == "NEUTRAL"
    assert row["calc_roic_dominant_driver"] == "TAX"
    # The rate went down even though its effect on ROIC was positive.
    assert row["calc_tax_rate_movement"] == "DOWN"


def test_invested_capital_falls_while_nopat_is_positive():
    row = last([100, 100], [0.25, 0.25], [1000, 800])
    assert row["calc_roic_posttax_change_direction"] == "INCREASE"
    assert row["calc_ic_movement"] == "DOWN"
    assert row["calc_roic_ic_effect"] == "POSITIVE"


def test_invested_capital_falls_while_nopat_is_negative():
    """The case the naive rule gets wrong.

    A smaller capital base divides a negative NOPAT into a *more* negative
    ratio, so shrinking capital is a negative contribution here. Nothing may
    infer the effect from the direction of the movement.
    """
    row = last([-100, -100], [0.25, 0.25], [1000, 800])
    assert row["calc_ic_movement"] == "DOWN"
    assert row["calc_roic_ic_effect"] == "NEGATIVE"
    assert row["calc_roic_posttax_change_direction"] == "DECREASE"
    assert row["calc_nopat_sign_regime"] == "LOSS_TO_LOSS"


# ---------------------------------------------------------------------------
# 5-8: drivers pulling against each other
# ---------------------------------------------------------------------------
def test_ebit_rises_but_capital_rises_more():
    row = last([100, 110], [0.25, 0.25], [1000, 1200])
    assert row["calc_roic_posttax_change_direction"] == "DECREASE"
    assert row["calc_roic_ebit_effect"] == "POSITIVE"
    assert row["calc_roic_ic_effect"] == "NEGATIVE"
    assert row["calc_roic_has_opposing_effects"]
    assert row["calc_roic_effect_structure"] == "MIXED_NET_DECREASE"


def test_ebit_falls_but_capital_falls_more():
    row = last([100, 90], [0.25, 0.25], [1000, 800])
    assert row["calc_roic_posttax_change_direction"] == "INCREASE"
    assert row["calc_roic_ebit_effect"] == "NEGATIVE"
    assert row["calc_roic_ic_effect"] == "POSITIVE"
    assert row["calc_roic_effect_structure"] == "MIXED_NET_INCREASE"


def test_ebit_rises_but_the_tax_rate_rises_more():
    row = last([100, 110], [0.20, 0.40], [1000, 1000])
    assert row["calc_nopat_change_direction"] == "DECREASE"
    assert row["calc_nopat_ebit_effect"] == "POSITIVE"
    assert row["calc_nopat_tax_effect"] == "NEGATIVE"
    # C_ebit = (110-100)(0.80+0.60)/2 = 7 ; C_tax = (0.60-0.80)(100+110)/2 = -21
    assert row["calc_nopat_ebit_contribution"] == pytest.approx(7.0)
    assert row["calc_nopat_tax_contribution"] == pytest.approx(-21.0)
    assert row["calc_nopat_change_quarterly"] == pytest.approx(-14.0)


def test_ebit_falls_but_the_tax_rate_falls_enough():
    row = last([100, 90], [0.40, 0.10], [1000, 1000])
    assert row["calc_nopat_change_direction"] == "INCREASE"
    assert row["calc_nopat_ebit_effect"] == "NEGATIVE"
    assert row["calc_nopat_tax_effect"] == "POSITIVE"
    assert row["calc_nopat_ebit_contribution"] == pytest.approx(-7.5)
    assert row["calc_nopat_tax_contribution"] == pytest.approx(28.5)
    assert row["calc_nopat_change_quarterly"] == pytest.approx(21.0)


# ---------------------------------------------------------------------------
# 9-13: driver structures
# ---------------------------------------------------------------------------
def test_all_three_drivers_push_roic_up():
    row = last([100, 120], [0.30, 0.20], [1000, 900])
    assert row["calc_roic_effect_structure"] == "ALL_POSITIVE"
    assert row["calc_roic_positive_driver_count"] == 3
    assert row["calc_roic_negative_driver_count"] == 0
    assert not row["calc_roic_has_opposing_effects"]
    assert row["calc_roic_offset_ratio"] == pytest.approx(0.0)
    assert (
        row["calc_roic_business_classification"]
        == "ROIC_INCREASE_ALL_DRIVERS_POSITIVE"
    )


def test_all_three_drivers_push_roic_down():
    row = last([120, 100], [0.20, 0.30], [900, 1000])
    assert row["calc_roic_effect_structure"] == "ALL_NEGATIVE"
    assert row["calc_roic_negative_driver_count"] == 3
    assert (
        row["calc_roic_business_classification"]
        == "ROIC_DECREASE_ALL_DRIVERS_NEGATIVE"
    )


def test_one_positive_driver_against_two_negative():
    row = last([100, 105], [0.25, 0.35], [1000, 1200])
    assert row["calc_roic_positive_driver_count"] == 1
    assert row["calc_roic_negative_driver_count"] == 2
    assert row["calc_roic_active_driver_count"] == 3
    assert row["calc_roic_has_opposing_effects"]


def test_two_positive_drivers_against_one_negative():
    row = last([100, 120], [0.30, 0.20], [1000, 1100])
    assert row["calc_roic_positive_driver_count"] == 2
    assert row["calc_roic_negative_driver_count"] == 1
    assert row["calc_roic_ic_effect"] == "NEGATIVE"


def test_exact_offset_leaves_roic_unchanged():
    """Doubling both NOPAT and the capital base leaves the ratio identical."""
    row = last([100, 200], [0.25, 0.25], [1000, 2000])
    assert row["calc_roic_posttax_change_quarterly"] == pytest.approx(0.0)
    assert row["calc_roic_posttax_change_direction"] == "STABLE"
    assert row["calc_roic_ebit_effect"] == "POSITIVE"
    assert row["calc_roic_ic_effect"] == "NEGATIVE"
    assert row["calc_roic_effect_structure"] == "MIXED_FULL_OFFSET"
    assert row["calc_roic_offset_ratio"] == pytest.approx(1.0)
    assert (
        row["calc_roic_business_classification"] == "ROIC_STABLE_OFFSETTING_EFFECTS"
    )


# ---------------------------------------------------------------------------
# 14-16: sign transitions
# ---------------------------------------------------------------------------
def test_ebit_crosses_from_loss_to_profit():
    row = last([-50, 60], [0.25, 0.25], [1000, 1000])
    assert row["calc_ebit_sign_regime"] == "LOSS_TO_PROFIT"
    assert row["calc_nopat_sign_regime"] == "LOSS_TO_PROFIT"
    assert row["calc_roic_business_classification"] == "OPERATING_TURNAROUND"
    # No percentage change is involved, so a negative base is harmless.
    assert row["calc_roic_ebit_contribution"] == pytest.approx(110 * 0.75 / 1000)


def test_ebit_crosses_from_profit_to_loss():
    row = last([60, -50], [0.25, 0.25], [1000, 1000])
    assert row["calc_ebit_sign_regime"] == "PROFIT_TO_LOSS"
    assert (
        row["calc_roic_business_classification"] == "PROFIT_TO_LOSS_DETERIORATION"
    )


def test_nopat_crosses_sign_because_of_the_tax_rate_alone():
    """EBIT never moves; a tax rate crossing 100% flips NOPAT on its own."""
    row = last([100, 100], [1.5, 0.5], [1000, 1000])
    assert row["calc_ebit_sign_regime"] == "PROFIT_TO_PROFIT"
    assert row["calc_nopat_sign_regime"] == "LOSS_TO_PROFIT"
    assert row["calc_roic_tax_effect"] == "POSITIVE"
    assert row["calc_roic_business_classification"] == "OPERATING_TURNAROUND"


def test_a_rising_ebit_can_reduce_nopat_when_retention_is_negative():
    """Above a 100% tax rate, more EBIT means less NOPAT.

    This is why the effect columns read the contribution rather than the raw
    movement: EBIT_UP coincides with EBIT_EFFECT_NEGATIVE here.
    """
    row = last([100, 120], [1.4, 1.4], [1000, 1000])
    assert row["calc_ebit_movement"] == "UP"
    assert row["calc_nopat_ebit_effect"] == "NEGATIVE"
    assert row["calc_roic_ebit_effect"] == "NEGATIVE"


# ---------------------------------------------------------------------------
# 17-20: degenerate inputs
# ---------------------------------------------------------------------------
def test_zero_invested_capital_blocks_roic_but_not_nopat():
    row = last([100, 110], [0.25, 0.25], [1000, 0])
    assert row["calc_roic_decomposition_status"] == STATUS_ZERO_IC
    assert pd.isna(row["calc_roic_posttax_change_quarterly"])
    assert pd.isna(row["calc_roic_ebit_contribution"])
    assert row["calc_roic_business_classification"] == STATUS_ZERO_IC
    assert row[ROIC_COMBO_COLUMNS].sum() == 0

    # NOPAT depends only on EBIT and the tax rate, so it survives.
    assert row["calc_nopat_decomposition_status"] == STATUS_VALID
    assert row["calc_nopat_change_quarterly"] == pytest.approx(7.5)
    assert row[NOPAT_COMBO_COLUMNS].sum() == 1


def test_negative_invested_capital_is_mechanical_only():
    row = last([100, 100], [0.25, 0.25], [500, -500])
    assert row["calc_roic_decomposition_status"] == STATUS_VALID
    assert row["calc_roic_quality_flag"] == "NEGATIVE_IC_MECHANICAL_ONLY"
    assert bool(row["calc_roic_economic_interpretation_valid"]) is False
    assert (
        row["calc_roic_business_classification"] == "ROIC_MECHANICAL_NEGATIVE_IC"
    )
    # The arithmetic is still produced and still exact.
    assert pd.notna(row["calc_roic_ic_contribution"])
    assert row["calc_roic_decomposition_residual"] == pytest.approx(0.0, abs=1e-12)


def test_negative_tax_rate_is_flagged_and_never_clipped():
    row = last([100, 120], [0.25, -0.50], [1000, 1000])
    assert row["calc_tax_rate_quality_flag"] == "NEGATIVE_TAX_RATE"
    assert row["calc_roic_quality_flag"] == "TAX_RATE_OUT_OF_RANGE"
    assert bool(row["calc_roic_economic_interpretation_valid"]) is False
    # Retention of 1.50 is used as reported. Clipping to 1.00 would give 8.75.
    assert row["calc_nopat_ebit_contribution"] == pytest.approx(
        20 * (0.75 + 1.50) / 2
    )


def test_tax_rate_above_one_hundred_percent_is_flagged_and_never_clipped():
    row = last([100, 120], [0.25, 1.40], [1000, 1000])
    assert row["calc_tax_rate_quality_flag"] == "ABOVE_100_PERCENT"
    assert row["calc_roic_quality_flag"] == "TAX_RATE_OUT_OF_RANGE"
    # Retention of -0.40 is used as reported. Clipping to 0.00 would give 7.50.
    assert row["calc_nopat_ebit_contribution"] == pytest.approx(
        20 * (0.75 - 0.40) / 2
    )


def test_missing_input_yields_missing_data_rather_than_an_imputed_value():
    row = last([100, np.nan], [0.25, 0.25], [1000, 1000])
    assert row["calc_roic_decomposition_status"] == STATUS_MISSING_DATA
    assert row["calc_nopat_decomposition_status"] == STATUS_MISSING_DATA
    assert pd.isna(row["calc_nopat_change_quarterly"])
    assert row[DECOMPOSITION_DUMMY_COLUMNS].sum() == 0


# ---------------------------------------------------------------------------
# 21: discontinuous history
# ---------------------------------------------------------------------------
def test_a_missing_quarter_blocks_the_comparison_without_imputing(quarterly_payload):
    payload = dict(quarterly_payload)
    payload["quarterly"] = [
        row for row in quarterly_payload["quarterly"] if row["date"] != "2021-06"
    ]
    frame, _ = build_frame(payload, "TEST")
    result = add_calculated(frame).set_index("fiscal_period_end_date")

    # The quarter after the gap has no immediately preceding observation.
    assert (
        result.loc["2021-09-30", "calc_roic_decomposition_status"]
        == STATUS_NONCONSECUTIVE
    )
    # The one after that is consecutive, but its opening IC average is blank
    # because that average itself needs the quarter before the gap.
    assert (
        result.loc["2021-12-31", "calc_roic_decomposition_status"]
        == STATUS_MISSING_DATA
    )
    # NOPAT needs no balance sheet, so it recovers one quarter earlier.
    assert (
        result.loc["2021-12-31", "calc_nopat_decomposition_status"] == STATUS_VALID
    )
    # And by the third quarter everything is comparable again.
    assert result.loc["2022-03-31", "calc_roic_decomposition_status"] == STATUS_VALID

    blocked = result.loc["2021-09-30"]
    assert pd.isna(blocked["calc_roic_posttax_change_quarterly"])
    assert pd.isna(blocked["calc_roic_ebit_contribution"])
    assert blocked["calc_roic_business_classification"] == STATUS_NONCONSECUTIVE
    assert blocked[DECOMPOSITION_DUMMY_COLUMNS].sum() == 0


def test_the_first_observation_is_never_compared_backwards(calculated):
    first = calculated.iloc[0]
    assert first["calc_nopat_decomposition_status"] == STATUS_NONCONSECUTIVE
    assert first["calc_roic_decomposition_status"] == STATUS_NONCONSECUTIVE
    assert first[DECOMPOSITION_DUMMY_COLUMNS].sum() == 0


# ---------------------------------------------------------------------------
# 22-25: structural guarantees
# ---------------------------------------------------------------------------
def _random_panel(seed: int = 20260801, rows: int = 400) -> pd.DataFrame:
    """A panel that deliberately includes the awkward values.

    Zeros, negatives, sign flips, and out-of-range tax rates are drawn on
    purpose: the classification has to stay total on exactly those rows.
    """
    rng = np.random.default_rng(seed)
    choose = lambda size: rng.choice(  # noqa: E731
        [-1000.0, -100.0, -1.0, 0.0, 1.0, 100.0, 1000.0], size=size
    )
    return build_pair(
        choose(rows) * rng.uniform(0.5, 2.0, rows),
        rng.choice([-0.5, 0.0, 0.21, 0.35, 1.0, 1.4], size=rows),
        choose(rows) * rng.uniform(0.5, 2.0, rows),
    )


def test_exactly_one_indicator_is_set_for_every_classified_row():
    panel = _random_panel()
    nopat_valid = panel["calc_nopat_decomposition_status"] == STATUS_VALID
    roic_valid = panel["calc_roic_decomposition_status"] == STATUS_VALID
    for columns, valid in (
        (NOPAT_COMBO_COLUMNS, nopat_valid),
        (RAW_COMBO_COLUMNS, roic_valid),
        (ROIC_COMBO_COLUMNS, roic_valid),
    ):
        assert len(columns) in (9, 27)
        totals = panel[columns].sum(axis=1)
        assert (totals == valid.astype(int)).all()


def test_nopat_contributions_sum_exactly_to_the_nopat_change():
    panel = _random_panel()
    residual = panel["calc_nopat_decomposition_residual"].dropna()
    assert not residual.empty
    scale = panel["calc_nopat_change_quarterly"].abs().max()
    assert residual.abs().max() <= 1e-9 * max(scale, 1.0)


def test_roic_contributions_sum_exactly_to_the_roic_change():
    panel = _random_panel()
    residual = panel["calc_roic_decomposition_residual"].dropna()
    assert not residual.empty
    assert residual.abs().max() < 1e-9


def test_the_split_does_not_depend_on_the_order_of_the_factors():
    """Compare the vectorised weights against an explicit permutation average.

    The implementation evaluates the eight coalition values once instead of
    walking the six orderings per row. This pins that shortcut to the
    definition it claims to compute.
    """
    rng = np.random.default_rng(7)
    for _ in range(200):
        e0, e1 = rng.uniform(-500, 500, 2)
        q0, q1 = rng.uniform(-1.0, 2.0, 2)
        i0, i1 = rng.uniform(50, 5000, 2) * rng.choice([-1.0, 1.0], 2)

        actual = shapley_contributions(
            {
                "ebit": pd.Series([e0]),
                "retention": pd.Series([q0]),
                "capital": pd.Series([i0]),
            },
            {
                "ebit": pd.Series([e1]),
                "retention": pd.Series([q1]),
                "capital": pd.Series([i1]),
            },
            lambda ebit, retention, capital: ebit * retention / capital,
        )

        names = ("ebit", "retention", "capital")
        endpoints = {"ebit": (e0, e1), "retention": (q0, q1), "capital": (i0, i1)}
        totals = dict.fromkeys(names, 0.0)
        for order in permutations(names):
            state = {name: endpoints[name][0] for name in names}
            value = state["ebit"] * state["retention"] / state["capital"]
            for name in order:
                state[name] = endpoints[name][1]
                moved = state["ebit"] * state["retention"] / state["capital"]
                totals[name] += moved - value
                value = moved

        for name in names:
            assert actual[name].iloc[0] == pytest.approx(
                totals[name] / 6, rel=1e-9, abs=1e-12
            )

        # And the six orderings still add up to the total change.
        assert sum(actual[name].iloc[0] for name in names) == pytest.approx(
            e1 * q1 / i1 - e0 * q0 / i0, rel=1e-9, abs=1e-12
        )


def test_every_label_column_stays_inside_its_declared_vocabulary():
    panel = _random_panel()
    vocabularies = {
        "calc_roic_business_classification": BUSINESS_CLASSIFICATIONS,
        "calc_roic_effect_structure": EFFECT_STRUCTURES,
        "calc_ebit_sign_regime": SIGN_REGIMES,
        "calc_nopat_sign_regime": SIGN_REGIMES,
        "calc_nopat_change_direction": {
            "INCREASE", "DECREASE", "STABLE", "UNCLASSIFIED",
        },
        "calc_roic_posttax_change_direction": {
            "INCREASE", "DECREASE", "STABLE", "UNCLASSIFIED",
        },
        "calc_ebit_movement": {"UP", "DOWN", "FLAT", "UNCLASSIFIED"},
        "calc_tax_rate_movement": {"UP", "DOWN", "FLAT", "UNCLASSIFIED"},
        "calc_ic_movement": {"UP", "DOWN", "FLAT", "UNCLASSIFIED"},
        "calc_roic_ebit_effect": {
            "POSITIVE", "NEGATIVE", "NEUTRAL", "UNCLASSIFIED",
        },
        "calc_roic_tax_effect": {
            "POSITIVE", "NEGATIVE", "NEUTRAL", "UNCLASSIFIED",
        },
        "calc_roic_ic_effect": {"POSITIVE", "NEGATIVE", "NEUTRAL", "UNCLASSIFIED"},
        "calc_roic_dominant_driver": {
            "EBIT", "TAX", "IC", "BALANCED", "NONE", "UNCLASSIFIED",
        },
        "calc_tax_rate_quality_flag": {
            "VALID", "NEGATIVE_TAX_RATE", "ABOVE_100_PERCENT", "MISSING",
        },
    }
    for column, allowed in vocabularies.items():
        values = panel[column]
        assert values.notna().all(), column
        assert set(values) <= set(allowed), (column, set(values) - set(allowed))


def test_every_observation_receives_an_explanation():
    panel = _random_panel()
    explanation = panel["calc_roic_explanation"]
    assert explanation.notna().all()
    assert explanation.str.len().gt(0).all()
    assert explanation.str.endswith(".").all()
    # No double spaces or leading space from an empty clause.
    assert not explanation.str.contains("  ").any()
    assert (explanation == explanation.str.strip()).all()


def test_absolute_shares_are_used_rather_than_signed_ratios():
    """With opposing drivers a signed ratio would exceed one or go negative."""
    panel = _random_panel()
    shares = panel[
        [
            "calc_roic_ebit_absolute_share",
            "calc_roic_tax_absolute_share",
            "calc_roic_ic_absolute_share",
        ]
    ].dropna()
    assert not shares.empty
    assert (shares >= 0).all().all()
    assert (shares <= 1).all().all()
    assert shares.sum(axis=1).sub(1).abs().max() < 1e-12


def test_offset_ratio_is_blank_rather_than_zero_when_nothing_moved():
    row = last([100, 100], [0.25, 0.25], [1000, 1000])
    assert row["calc_roic_effect_structure"] == "NO_MATERIAL_CHANGE"
    assert row["calc_roic_dominant_driver"] == "NONE"
    assert row["calc_roic_dominant_driver_effect"] == "NEUTRAL"
    assert pd.isna(row["calc_roic_offset_ratio"])
    assert pd.isna(row["calc_roic_ebit_absolute_share"])
    assert row["calc_roic_business_classification"] == "ROIC_STABLE_NO_CHANGE"
    assert row["calc_roic_effect_combination"] == "EBIT_ZERO__TAX_ZERO__IC_ZERO"


def test_balanced_is_reported_when_no_single_driver_leads():
    # Two contributions of equal size and one negligible.
    row = last([100, 110], [0.25, 0.25], [1000, 1000 * 100 / 110])
    assert row["calc_roic_ebit_absolute_share"] == pytest.approx(
        row["calc_roic_ic_absolute_share"], abs=0.05
    )
    assert row["calc_roic_dominant_driver"] == "BALANCED"
    assert row["calc_roic_dominant_driver_effect"] == "UNCLASSIFIED"


# ---------------------------------------------------------------------------
# Integration with the published columns
# ---------------------------------------------------------------------------
def test_the_change_columns_difference_the_published_columns(calculated):
    """ΔROIC and ΔNOPAT are the shipped columns differenced, not re-derived."""
    for published, change in (
        ("calc_nopat_quarterly", "calc_nopat_change_quarterly"),
        ("calc_roic_posttax_quarterly_ic_raw", "calc_roic_posttax_change_quarterly"),
    ):
        expected = calculated[published].diff()
        both = pd.concat([expected, calculated[change]], axis=1).dropna()
        assert not both.empty
        assert (both.iloc[:, 0] - both.iloc[:, 1]).abs().max() < 1e-12


def test_the_decomposition_reads_the_shipped_ic_average_without_re_averaging(
    calculated,
):
    """I0 and I1 are the existing average column at t-1 and t.

    Reproducing the EBIT contribution from that column alone would be
    impossible if the module had taken an average of the average.
    """
    capital_current = calculated["calc_average_ic_raw_quarterly"]
    capital_previous = capital_current.shift(1)
    ebit_current = calculated["ebit"]
    ebit_previous = ebit_current.shift(1)
    retention_current = 1 - calculated["calc_raw_tax_rate_quarterly"]
    retention_previous = retention_current.shift(1)

    expected = (ebit_current - ebit_previous) * (
        (1 / 3) * retention_previous / capital_previous
        + (1 / 6) * retention_current / capital_previous
        + (1 / 6) * retention_previous / capital_current
        + (1 / 3) * retention_current / capital_current
    )
    both = pd.concat(
        [expected, calculated["calc_roic_ebit_contribution"]], axis=1
    ).dropna()
    assert not both.empty
    assert (both.iloc[:, 0] - both.iloc[:, 1]).abs().max() < 1e-15


def test_a_flat_history_produces_the_all_neutral_classification(calculated):
    settled = calculated.iloc[2:]
    assert (settled["calc_roic_decomposition_status"] == STATUS_VALID).all()
    assert (
        settled["calc_roic_effect_combination"] == "EBIT_ZERO__TAX_ZERO__IC_ZERO"
    ).all()
    assert (settled["calc_roic_combo__ebit_zero__tax_zero__ic_zero"] == 1).all()
    assert (settled["calc_roic_neutral_driver_count"] == 3).all()


def test_tolerance_is_configurable_and_changes_the_classification():
    """A move can be real arithmetic and still be economically immaterial."""
    tiny = ([1000.0, 1000.02], [0.25, 0.25], [10000.0, 10000.0])
    assert last(*tiny)["calc_roic_ebit_effect"] == "NEUTRAL"

    strict = DEFAULT_TOLERANCE.__class__(roic_absolute=1e-12, roic_relative=1e-12)
    assert last(*tiny, tolerance=strict)["calc_roic_ebit_effect"] == "POSITIVE"


def test_an_invalid_tolerance_is_rejected():
    with pytest.raises(ValueError, match="non-negative"):
        DEFAULT_TOLERANCE.__class__(roic_absolute=-1.0).validate()
    with pytest.raises(ValueError, match="share"):
        DEFAULT_TOLERANCE.__class__(dominance=1.5).validate()
