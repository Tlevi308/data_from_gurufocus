"""Shapley decomposition of quarter-over-quarter NOPAT and post-tax ROIC.

Why Shapley
-----------
A sequential decomposition ("move EBIT, then tax, then invested capital") assigns
every interaction term to whichever factor happens to move last, so the answer
depends on an arbitrary ordering. The Shapley value averages each factor's
marginal contribution over all orderings. It is the unique attribution that is
order-independent, symmetric, and *efficient* — the contributions sum exactly to
the total change, with no unexplained residual.

Definitions
-----------
For the previous (0) and current (1) fiscal quarter::

    E = ebit
    T = calc_raw_tax_rate_quarterly
    Q = 1 - T                                post-tax retention rate
    I = calc_average_ic_raw_quarterly        read as-is, never re-averaged

    NOPAT = E * Q          == calc_nopat_quarterly
    ROIC  = E * Q / I      == calc_roic_posttax_quarterly_ic_raw

Tax enters as ``Q = 1 - T`` rather than ``T`` so that both metrics stay
multiplicatively separable and the decomposition remains well defined when the
reported tax rate is negative or above 100%.

No second averaging
-------------------
``calc_average_ic_raw_quarterly`` is already the average of the opening and
closing quarter-end balances. This module reads that column at two points in
time (``I1 = column``, ``I0 = column.shift(1)``); it never averages again. The
same holds for the changes themselves: ``ΔNOPAT`` and ``ΔROIC`` are the shipped
columns differenced, never recomputed from parts, so the decomposition is
validated against the published numbers rather than against a re-derivation.

A consequence worth knowing: because the shipped IC average at t-1 is itself
blank right after a gap, a fully classified row needs three consecutive
quarters. That is inherited from the existing ROIC column, not added here.

Raw movement is not economic effect
-----------------------------------
``EBIT_UP`` is not the same statement as ``EBIT_EFFECT_POSITIVE``. When the
retention rate Q is negative (tax rate above 100%) a rising EBIT lowers NOPAT.
When NOPAT is negative, a shrinking capital base makes ROIC *more* negative.
Every ``*_effect`` column is therefore read off the sign of the Shapley
contribution; every ``*_movement`` column reports the raw direction. Both are
emitted so the two can be compared.

Contract
--------
Like :mod:`gurufocus.calculations`, this module runs on **one ticker's frame,
already sorted ascending by fiscal_period_end_date**, before the multi-ticker
concat in :mod:`gurufocus.pipeline`. Plain ``.shift(1)`` is therefore safe and
no ``groupby`` is used.
"""

from __future__ import annotations

from dataclasses import dataclass, fields as dataclass_fields
from itertools import combinations, product
from math import factorial
from typing import Callable, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from .quarterly import divide as _divide, numeric as _numeric


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------
UNCLASSIFIED = "UNCLASSIFIED"

STATUS_VALID = "VALID"
STATUS_NONCONSECUTIVE = "UNCLASSIFIED_NONCONSECUTIVE"
STATUS_MISSING_DATA = "UNCLASSIFIED_MISSING_DATA"
STATUS_ZERO_IC = "UNCLASSIFIED_ZERO_IC"

INCREASE, DECREASE, STABLE = "INCREASE", "DECREASE", "STABLE"
UP, DOWN, FLAT = "UP", "DOWN", "FLAT"
POSITIVE, NEGATIVE, NEUTRAL = "POSITIVE", "NEGATIVE", "NEUTRAL"

TAX_RATE_VALID = "VALID"
TAX_RATE_NEGATIVE = "NEGATIVE_TAX_RATE"
TAX_RATE_ABOVE_100 = "ABOVE_100_PERCENT"
TAX_RATE_MISSING = "MISSING"

QUALITY_VALID = "VALID"
QUALITY_NEGATIVE_IC = "NEGATIVE_IC_MECHANICAL_ONLY"
QUALITY_TAX_OUT_OF_RANGE = "TAX_RATE_OUT_OF_RANGE"

# Effect combinations use POS/NEG/ZERO; raw movements use UP/DOWN/FLAT.
_EFFECT_TOKEN = {POSITIVE: "POS", NEGATIVE: "NEG", NEUTRAL: "ZERO"}
_MOVEMENT_TOKEN = {UP: "UP", DOWN: "DOWN", FLAT: "FLAT"}

EBIT, TAX, IC = "EBIT", "TAX", "IC"

# Natural-language names used by calc_roic_explanation. Two maps because
# str.capitalize() would render the acronym EBIT as "Ebit".
_DRIVER_PHRASE = {
    EBIT: "EBIT",
    TAX: "the tax rate",
    IC: "invested capital",
}
_DRIVER_SENTENCE_START = {
    EBIT: "EBIT",
    TAX: "The tax rate",
    IC: "Invested capital",
}


def _combination_labels(names: Sequence[str], tokens: Sequence[str]) -> list[str]:
    """Every combination label for ``names``, via a cartesian product.

    Generating these rather than typing them out is what guarantees that no
    combination is missing: 3**2 = 9 for NOPAT and 3**3 = 27 for ROIC.
    """
    return [
        "__".join(f"{name}_{token}" for name, token in zip(names, choice))
        for choice in product(tokens, repeat=len(names))
    ]


_EFFECT_TOKENS = ("POS", "NEG", "ZERO")
_MOVEMENT_TOKENS = ("UP", "DOWN", "FLAT")

NOPAT_EFFECT_COMBINATIONS = _combination_labels((EBIT, TAX), _EFFECT_TOKENS)
ROIC_EFFECT_COMBINATIONS = _combination_labels((EBIT, TAX, IC), _EFFECT_TOKENS)
RAW_MOVEMENT_COMBINATIONS = _combination_labels((EBIT, TAX, IC), _MOVEMENT_TOKENS)

NOPAT_COMBO_PREFIX = "calc_nopat_combo__"
RAW_COMBO_PREFIX = "calc_raw_combo__"
ROIC_COMBO_PREFIX = "calc_roic_combo__"


def _dummy_names(prefix: str, labels: Iterable[str]) -> list[str]:
    return [prefix + label.lower() for label in labels]


# ---------------------------------------------------------------------------
# Effect structure and business classification vocabularies
# ---------------------------------------------------------------------------
STRUCTURE_ALL_POSITIVE = "ALL_POSITIVE"
STRUCTURE_ALL_NEGATIVE = "ALL_NEGATIVE"
STRUCTURE_MIXED_NET_INCREASE = "MIXED_NET_INCREASE"
STRUCTURE_MIXED_NET_DECREASE = "MIXED_NET_DECREASE"
STRUCTURE_MIXED_FULL_OFFSET = "MIXED_FULL_OFFSET"
STRUCTURE_SINGLE_POSITIVE = "SINGLE_POSITIVE_DRIVER"
STRUCTURE_SINGLE_NEGATIVE = "SINGLE_NEGATIVE_DRIVER"
STRUCTURE_NO_MATERIAL_CHANGE = "NO_MATERIAL_CHANGE"

EFFECT_STRUCTURES = frozenset(
    {
        STRUCTURE_ALL_POSITIVE,
        STRUCTURE_ALL_NEGATIVE,
        STRUCTURE_MIXED_NET_INCREASE,
        STRUCTURE_MIXED_NET_DECREASE,
        STRUCTURE_MIXED_FULL_OFFSET,
        STRUCTURE_SINGLE_POSITIVE,
        STRUCTURE_SINGLE_NEGATIVE,
        STRUCTURE_NO_MATERIAL_CHANGE,
        UNCLASSIFIED,
    }
)

DOMINANT_BALANCED = "BALANCED"
DOMINANT_NONE = "NONE"

BUSINESS_CLASSIFICATIONS = frozenset(
    {
        # Rows that cannot be compared at all.
        STATUS_NONCONSECUTIVE,
        STATUS_MISSING_DATA,
        STATUS_ZERO_IC,
        # Regimes where the ratio is arithmetically valid but not an
        # efficiency statement.
        "ROIC_MECHANICAL_NEGATIVE_IC",
        "OPERATING_TURNAROUND",
        "PROFIT_TO_LOSS_DETERIORATION",
        "MECHANICAL_IMPROVEMENT_WITH_NEGATIVE_NOPAT",
        "MECHANICAL_DETERIORATION_WITH_NEGATIVE_NOPAT",
        # Stable.
        "ROIC_STABLE_NO_CHANGE",
        "ROIC_STABLE_OFFSETTING_EFFECTS",
        # Increase.
        "ROIC_INCREASE_ALL_DRIVERS_POSITIVE",
        "ROIC_INCREASE_EBIT_DOMINANT_WITH_TAX_AND_IC_SUPPORT",
        "ROIC_INCREASE_EBIT_DOMINANT_WITH_TAX_DRAG",
        "ROIC_INCREASE_EBIT_DOMINANT_WITH_IC_DRAG",
        "ROIC_INCREASE_EBIT_DOMINANT_WITH_TAX_AND_IC_DRAG",
        "ROIC_INCREASE_TAX_DOMINANT_WITH_EBIT_DECLINE",
        "ROIC_INCREASE_TAX_DOMINANT_WITH_IC_DRAG",
        "ROIC_INCREASE_IC_DOMINANT_WITH_EBIT_DECLINE",
        "ROIC_INCREASE_IC_DOMINANT_WITH_TAX_DRAG",
        # ROIC rose even though the largest single contribution pulled the
        # other way — the two smaller drivers more than covered it.
        "ROIC_INCREASE_DESPITE_DOMINANT_EBIT_DRAG",
        "ROIC_INCREASE_DESPITE_DOMINANT_TAX_DRAG",
        "ROIC_INCREASE_DESPITE_DOMINANT_IC_DRAG",
        "ROIC_INCREASE_MIXED_EFFECTS",
        # Decrease.
        "ROIC_DECREASE_ALL_DRIVERS_NEGATIVE",
        "ROIC_DECREASE_EBIT_DOMINANT_DESPITE_TAX_SUPPORT",
        "ROIC_DECREASE_EBIT_DOMINANT_DESPITE_IC_SUPPORT",
        "ROIC_DECREASE_TAX_DOMINANT_DESPITE_EBIT_IMPROVEMENT",
        "ROIC_DECREASE_TAX_DOMINANT_DESPITE_IC_SUPPORT",
        "ROIC_DECREASE_IC_DOMINANT_DESPITE_EBIT_IMPROVEMENT",
        "ROIC_DECREASE_IC_DOMINANT_DESPITE_TAX_SUPPORT",
        # ROIC fell even though the largest single contribution was positive.
        "ROIC_DECREASE_DESPITE_DOMINANT_EBIT_SUPPORT",
        "ROIC_DECREASE_DESPITE_DOMINANT_TAX_SUPPORT",
        "ROIC_DECREASE_DESPITE_DOMINANT_IC_SUPPORT",
        "ROIC_DECREASE_MIXED_EFFECTS",
        UNCLASSIFIED,
    }
)

# PROFIT / LOSS / ZERO transitions, generated so that no pairing is missed.
_SIGN_TOKENS = ("PROFIT", "LOSS", "ZERO")
SIGN_REGIMES = frozenset(
    [f"{before}_TO_{after}" for before, after in product(_SIGN_TOKENS, repeat=2)]
    + [UNCLASSIFIED]
)


# ---------------------------------------------------------------------------
# Tolerance
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DecompositionTolerance:
    """Materiality bands used by every direction, sign, and dominance test.

    A contribution counts as NEUTRAL, and a change as STABLE, when its absolute
    value falls at or below the band. Comparing to a literal zero is never
    correct here: floating-point noise would classify pure rounding error as a
    real economic effect.

    The bands are deliberately *economic* rather than merely numerical. With a
    float-noise threshold alone, real data essentially never produces NEUTRAL,
    which would leave the ZERO/STABLE classes and roughly forty of the sixty
    three one-hot columns permanently empty.

    Two scale rules are used, because the quantities have different units:

    ``ratio`` bands (ROIC, tax rate)
        ``tol = max(absolute, relative * max(|x0|, |x1|))``. No floor of 1.0 —
        ROIC is a ratio of order 0.05, so flooring the scale at 1.0 would make
        the relative term swamp the absolute one and declare a 90 bp move
        immaterial.

    ``amount`` bands (NOPAT, EBIT, invested capital)
        ``tol = max(absolute, relative * max(|x0|, |x1|, 1.0))``, i.e. the
        spec's scale rule. Currency amounts span many orders of magnitude, so
        the band has to be proportional.
    """

    # Post-tax ROIC: ΔROIC and the three ROIC contributions.
    roic_absolute: float = 1e-4      # 10 bp of ROIC
    roic_relative: float = 1e-3      # 0.1% of the ROIC level

    # NOPAT: ΔNOPAT and the two NOPAT contributions.
    nopat_absolute: float = 1e-9
    nopat_relative: float = 5e-3     # 0.5% of the larger period's NOPAT

    # Raw movement of currency variables (EBIT, invested capital).
    amount_absolute: float = 1e-9
    amount_relative: float = 5e-3

    # Raw movement of the tax rate.
    rate_absolute: float = 1e-4      # 1 bp of tax rate
    rate_relative: float = 1e-3

    # PROFIT / LOSS / ZERO sign regime. Near-exact by design: "zero EBIT" is a
    # statement about the reported value, not about materiality.
    level_absolute: float = 1e-9
    level_relative: float = 1e-9

    # Two drivers count as BALANCED when their absolute-contribution shares are
    # within this many share points (0.05 = five percentage points).
    dominance: float = 0.05

    def validate(self) -> None:
        for spec in dataclass_fields(self):
            value = getattr(self, spec.name)
            if not np.isfinite(value) or value < 0:
                raise ValueError(
                    f"decomposition tolerance {spec.name} must be finite and "
                    f"non-negative — got {value!r}"
                )
        if self.dominance > 1:
            raise ValueError("dominance tolerance is a share, so it cannot exceed 1")


DEFAULT_TOLERANCE = DecompositionTolerance()


# ---------------------------------------------------------------------------
# Small numeric helpers
# ---------------------------------------------------------------------------
def _band(
    previous: pd.Series,
    current: pd.Series,
    absolute: float,
    relative: float,
    *,
    floor: float = 0.0,
) -> pd.Series:
    """Return the per-row materiality band for a metric measured at 0 and 1."""
    scale = pd.Series(
        np.fmax(previous.abs().to_numpy(), current.abs().to_numpy()),
        index=previous.index,
        dtype=float,
    ).fillna(0.0)
    if floor:
        scale = scale.clip(lower=floor)
    return pd.Series(
        np.maximum(absolute, relative * scale.to_numpy()),
        index=previous.index,
        dtype=float,
    )


def _labelled(
    conditions: Sequence[pd.Series],
    choices: Sequence[str],
    default: str,
    index: pd.Index,
) -> pd.Series:
    """``np.select`` over string labels, returned as an object Series."""
    if len(index) == 0:
        return pd.Series([], index=index, dtype=object)
    masks = [condition.to_numpy(dtype=bool, na_value=False) for condition in conditions]
    return pd.Series(np.select(masks, list(choices), default=default), index=index, dtype=object)


def _direction(
    change: pd.Series,
    band: pd.Series,
    valid: pd.Series,
    labels: tuple[str, str, str],
) -> pd.Series:
    """Classify a signed change as up / down / flat within the band."""
    up, down, flat = labels
    unknown = ~valid | change.isna()
    return _labelled(
        [unknown, change > band, change < -band],
        [UNCLASSIFIED, up, down],
        flat,
        change.index,
    )


# ---------------------------------------------------------------------------
# Shapley
# ---------------------------------------------------------------------------
def _coalition_values(
    previous: Mapping[str, pd.Series],
    current: Mapping[str, pd.Series],
    metric: Callable[..., pd.Series],
) -> dict[frozenset[str], pd.Series]:
    """Evaluate the metric for all 2**n mixes of previous and current inputs."""
    players = tuple(previous)
    values: dict[frozenset[str], pd.Series] = {}
    for size in range(len(players) + 1):
        for subset in combinations(players, size):
            member = frozenset(subset)
            values[member] = metric(
                **{
                    name: (current[name] if name in member else previous[name])
                    for name in players
                }
            )
    return values


def shapley_contributions(
    previous: Mapping[str, pd.Series],
    current: Mapping[str, pd.Series],
    metric: Callable[..., pd.Series],
) -> dict[str, pd.Series]:
    """Exact Shapley attribution of ``metric(current) - metric(previous)``.

    Each factor's contribution is the average of its marginal effect over every
    ordering in which the factors could be switched from their previous to
    their current value. Rather than enumerating the n! orderings row by row,
    this evaluates the 2**n coalition values once and applies the equivalent
    combinatorial weights ``|S|!(n-|S|-1)!/n!`` — the same number, computed with
    vectorised Series arithmetic and no Python loop over rows.

    Efficiency (``sum of contributions == total change``) holds exactly by
    construction: the weights on each coalition value telescope so that only
    ``v(all current) - v(all previous)`` survives. The caller still emits a
    residual column, because an exact identity is the cheapest possible runtime
    check that the inputs were wired up correctly.
    """
    players = tuple(previous)
    if set(players) != set(current):
        raise ValueError("previous and current must describe the same factors")
    count = len(players)
    values = _coalition_values(previous, current, metric)

    contributions: dict[str, pd.Series] = {}
    for player in players:
        others = [name for name in players if name != player]
        total: pd.Series | None = None
        for size in range(count):
            weight = factorial(size) * factorial(count - size - 1) / factorial(count)
            for subset in combinations(others, size):
                member = frozenset(subset)
                marginal = (values[member | {player}] - values[member]) * weight
                total = marginal if total is None else total + marginal
        contributions[player] = total
    return contributions


def _nopat(ebit: pd.Series, retention: pd.Series) -> pd.Series:
    return ebit * retention


def _roic(ebit: pd.Series, retention: pd.Series, capital: pd.Series) -> pd.Series:
    return _divide(ebit * retention, capital)


# ---------------------------------------------------------------------------
# Column inventory
# ---------------------------------------------------------------------------
NOPAT_BRIDGE_COLUMNS = [
    "calc_nopat_decomposition_status",
    "calc_nopat_change_quarterly",
    "calc_nopat_change_direction",
    "calc_nopat_ebit_contribution",
    "calc_nopat_tax_contribution",
    "calc_nopat_decomposition_residual",
    "calc_nopat_ebit_effect",
    "calc_nopat_tax_effect",
    "calc_nopat_effect_combination",
]

ROIC_BRIDGE_COLUMNS = [
    "calc_roic_decomposition_status",
    "calc_roic_posttax_change_quarterly",
    "calc_roic_posttax_change_direction",
    "calc_roic_ebit_contribution",
    "calc_roic_tax_contribution",
    "calc_roic_ic_contribution",
    "calc_roic_decomposition_residual",
    "calc_roic_ebit_effect",
    "calc_roic_tax_effect",
    "calc_roic_ic_effect",
    "calc_roic_effect_combination",
    "calc_ebit_movement",
    "calc_tax_rate_movement",
    "calc_ic_movement",
    "calc_raw_movement_combination",
    "calc_roic_has_opposing_effects",
    "calc_roic_positive_driver_count",
    "calc_roic_negative_driver_count",
    "calc_roic_neutral_driver_count",
    "calc_roic_active_driver_count",
    "calc_roic_effect_structure",
    "calc_roic_total_absolute_contribution",
    "calc_roic_ebit_absolute_share",
    "calc_roic_tax_absolute_share",
    "calc_roic_ic_absolute_share",
    "calc_roic_dominant_driver",
    "calc_roic_dominant_driver_effect",
    "calc_roic_offset_ratio",
    "calc_ebit_sign_regime",
    "calc_nopat_sign_regime",
    "calc_tax_rate_quality_flag",
    "calc_roic_quality_flag",
    "calc_roic_economic_interpretation_valid",
    "calc_roic_business_classification",
    "calc_roic_explanation",
]

NAMED_DECOMPOSITION_COLUMNS = NOPAT_BRIDGE_COLUMNS + ROIC_BRIDGE_COLUMNS

NOPAT_COMBO_COLUMNS = _dummy_names(NOPAT_COMBO_PREFIX, NOPAT_EFFECT_COMBINATIONS)
RAW_COMBO_COLUMNS = _dummy_names(RAW_COMBO_PREFIX, RAW_MOVEMENT_COMBINATIONS)
ROIC_COMBO_COLUMNS = _dummy_names(ROIC_COMBO_PREFIX, ROIC_EFFECT_COMBINATIONS)

DECOMPOSITION_DUMMY_COLUMNS = (
    NOPAT_COMBO_COLUMNS + RAW_COMBO_COLUMNS + ROIC_COMBO_COLUMNS
)

# Columns that hold labels or booleans. The exporter must not coerce these to
# float, or every classification silently becomes NaN on the way to Excel.
TEXT_DECOMPOSITION_COLUMNS = frozenset(
    {
        "calc_nopat_decomposition_status",
        "calc_nopat_change_direction",
        "calc_nopat_ebit_effect",
        "calc_nopat_tax_effect",
        "calc_nopat_effect_combination",
        "calc_roic_decomposition_status",
        "calc_roic_posttax_change_direction",
        "calc_roic_ebit_effect",
        "calc_roic_tax_effect",
        "calc_roic_ic_effect",
        "calc_roic_effect_combination",
        "calc_ebit_movement",
        "calc_tax_rate_movement",
        "calc_ic_movement",
        "calc_raw_movement_combination",
        "calc_roic_has_opposing_effects",
        "calc_roic_effect_structure",
        "calc_roic_dominant_driver",
        "calc_roic_dominant_driver_effect",
        "calc_ebit_sign_regime",
        "calc_nopat_sign_regime",
        "calc_tax_rate_quality_flag",
        "calc_roic_quality_flag",
        "calc_roic_economic_interpretation_valid",
        "calc_roic_business_classification",
        "calc_roic_explanation",
    }
)

# Contributions and shares are small ratios. Excel's shared "0.00" format would
# display a genuine 31 bp contribution as 0.00.
HIGH_PRECISION_DECOMPOSITION_COLUMNS = frozenset(
    {
        "calc_roic_posttax_change_quarterly",
        "calc_roic_ebit_contribution",
        "calc_roic_tax_contribution",
        "calc_roic_ic_contribution",
        "calc_roic_decomposition_residual",
        "calc_roic_total_absolute_contribution",
        "calc_roic_ebit_absolute_share",
        "calc_roic_tax_absolute_share",
        "calc_roic_ic_absolute_share",
        "calc_roic_offset_ratio",
        "calc_nopat_decomposition_residual",
    }
)


def decomposition_columns() -> list[str]:
    """Return the decomposition columns in their canonical order."""
    return list(NAMED_DECOMPOSITION_COLUMNS) + list(DECOMPOSITION_DUMMY_COLUMNS)


def decomposition_dependencies() -> dict[str, tuple[str, ...]]:
    """Return direct source columns for each decomposition column."""
    pair_inputs = (
        "ebit",
        "calc_raw_tax_rate_quarterly",
        "calc_average_ic_raw_quarterly",
    )
    nopat_status = ("calc_nopat_decomposition_status",)
    roic_status = ("calc_roic_decomposition_status",)
    nopat_contributions = (
        "calc_nopat_ebit_contribution",
        "calc_nopat_tax_contribution",
    )
    roic_contributions = (
        "calc_roic_ebit_contribution",
        "calc_roic_tax_contribution",
        "calc_roic_ic_contribution",
    )
    roic_effects = (
        "calc_roic_ebit_effect",
        "calc_roic_tax_effect",
        "calc_roic_ic_effect",
    )
    driver_counts = (
        "calc_roic_positive_driver_count",
        "calc_roic_negative_driver_count",
        "calc_roic_neutral_driver_count",
        "calc_roic_active_driver_count",
    )
    shares = (
        "calc_roic_ebit_absolute_share",
        "calc_roic_tax_absolute_share",
        "calc_roic_ic_absolute_share",
    )

    dependencies: dict[str, tuple[str, ...]] = {
        "calc_nopat_decomposition_status": ("ebit", "calc_raw_tax_rate_quarterly"),
        "calc_nopat_change_quarterly": ("calc_nopat_quarterly",),
        "calc_nopat_change_direction": ("calc_nopat_change_quarterly",) + nopat_status,
        "calc_nopat_ebit_contribution": ("ebit", "calc_raw_tax_rate_quarterly"),
        "calc_nopat_tax_contribution": ("ebit", "calc_raw_tax_rate_quarterly"),
        "calc_nopat_decomposition_residual": ("calc_nopat_change_quarterly",)
        + nopat_contributions,
        "calc_nopat_ebit_effect": ("calc_nopat_ebit_contribution",) + nopat_status,
        "calc_nopat_tax_effect": ("calc_nopat_tax_contribution",) + nopat_status,
        "calc_nopat_effect_combination": (
            "calc_nopat_ebit_effect",
            "calc_nopat_tax_effect",
        ),
        "calc_roic_decomposition_status": pair_inputs,
        "calc_roic_posttax_change_quarterly": (
            "calc_roic_posttax_quarterly_ic_raw",
        ),
        "calc_roic_posttax_change_direction": (
            "calc_roic_posttax_change_quarterly",
        )
        + roic_status,
        "calc_roic_ebit_contribution": pair_inputs,
        "calc_roic_tax_contribution": pair_inputs,
        "calc_roic_ic_contribution": pair_inputs,
        "calc_roic_decomposition_residual": ("calc_roic_posttax_change_quarterly",)
        + roic_contributions,
        "calc_roic_ebit_effect": ("calc_roic_ebit_contribution",) + roic_status,
        "calc_roic_tax_effect": ("calc_roic_tax_contribution",) + roic_status,
        "calc_roic_ic_effect": ("calc_roic_ic_contribution",) + roic_status,
        "calc_roic_effect_combination": roic_effects,
        "calc_ebit_movement": ("ebit",) + nopat_status,
        "calc_tax_rate_movement": ("calc_raw_tax_rate_quarterly",) + nopat_status,
        "calc_ic_movement": ("calc_average_ic_raw_quarterly",) + roic_status,
        "calc_raw_movement_combination": (
            "calc_ebit_movement",
            "calc_tax_rate_movement",
            "calc_ic_movement",
        ),
        "calc_roic_has_opposing_effects": roic_effects,
        "calc_roic_positive_driver_count": roic_effects,
        "calc_roic_negative_driver_count": roic_effects,
        "calc_roic_neutral_driver_count": roic_effects,
        "calc_roic_active_driver_count": roic_effects,
        "calc_roic_effect_structure": roic_effects
        + driver_counts
        + ("calc_roic_posttax_change_direction",),
        "calc_roic_total_absolute_contribution": roic_contributions,
        "calc_roic_ebit_absolute_share": (
            "calc_roic_ebit_contribution",
            "calc_roic_total_absolute_contribution",
        ),
        "calc_roic_tax_absolute_share": (
            "calc_roic_tax_contribution",
            "calc_roic_total_absolute_contribution",
        ),
        "calc_roic_ic_absolute_share": (
            "calc_roic_ic_contribution",
            "calc_roic_total_absolute_contribution",
        ),
        "calc_roic_dominant_driver": shares + ("calc_roic_active_driver_count",),
        "calc_roic_dominant_driver_effect": ("calc_roic_dominant_driver",)
        + roic_effects,
        "calc_roic_offset_ratio": (
            "calc_roic_posttax_change_quarterly",
            "calc_roic_total_absolute_contribution",
            "calc_roic_active_driver_count",
        ),
        "calc_ebit_sign_regime": ("ebit",) + nopat_status,
        "calc_nopat_sign_regime": ("calc_nopat_quarterly",) + nopat_status,
        "calc_tax_rate_quality_flag": ("calc_raw_tax_rate_quarterly",),
        "calc_roic_quality_flag": (
            "calc_average_ic_raw_quarterly",
            "calc_raw_tax_rate_quarterly",
        )
        + roic_status,
        "calc_roic_economic_interpretation_valid": ("calc_roic_quality_flag",),
        "calc_roic_business_classification": (
            "calc_roic_posttax_change_direction",
            "calc_roic_effect_structure",
            "calc_roic_dominant_driver",
            "calc_nopat_sign_regime",
            "calc_roic_quality_flag",
        )
        + roic_effects,
        "calc_roic_explanation": (
            "calc_roic_posttax_change_direction",
            "calc_roic_dominant_driver",
            "calc_roic_business_classification",
        )
        + roic_effects,
    }

    for column in NOPAT_COMBO_COLUMNS:
        dependencies[column] = ("calc_nopat_effect_combination",)
    for column in RAW_COMBO_COLUMNS:
        dependencies[column] = ("calc_raw_movement_combination",)
    for column in ROIC_COMBO_COLUMNS:
        dependencies[column] = ("calc_roic_effect_combination",)

    missing = [
        column for column in decomposition_columns() if column not in dependencies
    ]
    if missing:
        raise RuntimeError(
            "Missing dependencies for decomposition columns: " + ", ".join(missing)
        )
    return dependencies


# ---------------------------------------------------------------------------
# Classification building blocks
# ---------------------------------------------------------------------------
def _effect_sign(
    contribution: pd.Series, band: pd.Series, valid: pd.Series
) -> pd.Series:
    """Classify a Shapley contribution as POSITIVE / NEGATIVE / NEUTRAL.

    Read off the contribution, never off the raw movement of the factor: a
    rising EBIT contributes negatively when the retention rate is negative, and
    a shrinking capital base contributes negatively when NOPAT is negative.
    """
    return _direction(contribution, band, valid, (POSITIVE, NEGATIVE, NEUTRAL))


def _combination(
    parts: Sequence[tuple[str, pd.Series]],
    token_map: Mapping[str, str],
    index: pd.Index,
) -> pd.Series:
    """Join per-factor labels into a single combination label."""
    if len(index) == 0:
        return pd.Series([], index=index, dtype=object)
    joined: pd.Series | None = None
    unusable: pd.Series | None = None
    for name, labels in parts:
        token = labels.map(token_map)
        bad = token.isna()
        unusable = bad if unusable is None else (unusable | bad)
        piece = name + "_" + token.fillna("")
        joined = piece if joined is None else joined + "__" + piece
    return joined.where(~unusable, UNCLASSIFIED).astype(object)


def _combination_dummies(
    combination: pd.Series,
    labels: Sequence[str],
    prefix: str,
) -> pd.DataFrame:
    """One-hot encode a combination against its complete label set.

    Reindexing onto the generated cartesian product is what makes the encoding
    exhaustive and mutually exclusive: every declared combination gets a column
    whether or not it occurs, and an UNCLASSIFIED row simply has no column to
    land in, so its whole family is zero.
    """
    dummies = pd.get_dummies(combination, dtype="int8")
    dummies = dummies.reindex(columns=list(labels), fill_value=0).astype("int8")
    dummies.columns = _dummy_names(prefix, labels)
    dummies.index = combination.index
    return dummies


def _sign_regime(
    previous: pd.Series,
    current: pd.Series,
    band: pd.Series,
    valid: pd.Series,
) -> pd.Series:
    """Classify the profit/loss transition of a level between two quarters.

    Emitted because a change in invested capital means the opposite thing when
    the numerator is negative: growing the capital base makes a negative ROIC
    less negative, which is arithmetic, not improvement.
    """
    tokens = []
    for series in (previous, current):
        tokens.append(
            _labelled(
                [series.isna(), series > band, series < -band],
                ["", "PROFIT", "LOSS"],
                "ZERO",
                series.index,
            )
        )
    before, after = tokens
    unusable = ~valid | (before == "") | (after == "")
    regime = before + "_TO_" + after
    return regime.where(~unusable, UNCLASSIFIED).astype(object)


def _effect_structure(
    positive: pd.Series,
    negative: pd.Series,
    active: pd.Series,
    direction: pd.Series,
    valid: pd.Series,
) -> pd.Series:
    """Summarise how the three ROIC contributions relate to one another.

    First match wins. SINGLE_POSITIVE_DRIVER is tested before ALL_POSITIVE
    because the spec defines both for a lone positive contribution and the more
    specific label is the useful one.
    """
    return _labelled(
        [
            ~valid,
            active == 0,
            (negative == 0) & (active == 1),
            negative == 0,
            (positive == 0) & (active == 1),
            positive == 0,
            direction == INCREASE,
            direction == DECREASE,
        ],
        [
            UNCLASSIFIED,
            STRUCTURE_NO_MATERIAL_CHANGE,
            STRUCTURE_SINGLE_POSITIVE,
            STRUCTURE_ALL_POSITIVE,
            STRUCTURE_SINGLE_NEGATIVE,
            STRUCTURE_ALL_NEGATIVE,
            STRUCTURE_MIXED_NET_INCREASE,
            STRUCTURE_MIXED_NET_DECREASE,
        ],
        STRUCTURE_MIXED_FULL_OFFSET,
        direction.index,
    )


def _english_list(items: Sequence[str]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def _join_drivers(
    names: Sequence[str],
    masks: Sequence[pd.Series],
    *,
    sentence_start: bool = False,
) -> pd.Series:
    """Build an English list of the drivers whose mask is set, vectorised.

    With three drivers there are only eight patterns, so enumerating them keeps
    the sentence assembly free of a per-row Python loop.

    ``sentence_start`` capitalises the leading character only. ``str.capitalize``
    would lower-case the rest and turn the acronym EBIT into "Ebit".
    """
    index = masks[0].index
    if len(index) == 0:
        return pd.Series([], index=index, dtype=object)
    conditions: list[pd.Series] = []
    choices: list[str] = []
    for pattern in product((True, False), repeat=len(names)):
        condition: pd.Series | None = None
        for wanted, mask in zip(pattern, masks):
            part = mask if wanted else ~mask
            condition = part if condition is None else (condition & part)
        conditions.append(condition)
        text = _english_list(
            [_DRIVER_PHRASE[name] for name, wanted in zip(names, pattern) if wanted]
        )
        if sentence_start and text:
            text = text[0].upper() + text[1:]
        choices.append(text)
    return _labelled(conditions, choices, "", index)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def add_decomposition(
    frame: pd.DataFrame,
    *,
    consecutive_quarters: pd.Series,
    tolerance: DecompositionTolerance = DEFAULT_TOLERANCE,
) -> pd.DataFrame:
    """Add the NOPAT and post-tax ROIC driver decomposition to one ticker.

    Every observation is compared only with the immediately preceding fiscal
    quarter of the same company. When that quarter is absent, or a required
    input is missing, nothing is imputed: the numeric columns are NaN, the
    label columns say why, and all sixty three one-hot columns are zero.
    """
    tolerance.validate()
    d = frame
    index = d.index
    n = lambda column: _numeric(d, column)  # noqa: E731
    # Columns are collected here and attached in one concat. Assigning ninety
    # of them one at a time would fragment the block manager.
    out: dict[str, pd.Series] = {}

    # -- inputs ------------------------------------------------------------
    # I is read straight from the shipped average; it is never re-averaged.
    ebit_current = n("ebit")
    ebit_previous = ebit_current.shift(1)
    tax_current = n("calc_raw_tax_rate_quarterly")
    tax_previous = tax_current.shift(1)
    retention_current = 1 - tax_current
    retention_previous = 1 - tax_previous
    capital_current = n("calc_average_ic_raw_quarterly")
    capital_previous = capital_current.shift(1)

    consecutive = consecutive_quarters.reindex(index).fillna(False).astype(bool)

    nopat_current = n("calc_nopat_quarterly")
    nopat_previous = nopat_current.shift(1)
    roic_current = n("calc_roic_posttax_quarterly_ic_raw")
    roic_previous = roic_current.shift(1)

    # -- status ------------------------------------------------------------
    # NOPAT depends only on EBIT and the tax rate, so a zero or missing capital
    # base must not invalidate the NOPAT bridge.
    nopat_inputs_present = (
        ebit_previous.notna()
        & ebit_current.notna()
        & tax_previous.notna()
        & tax_current.notna()
    )
    roic_inputs_present = (
        nopat_inputs_present & capital_previous.notna() & capital_current.notna()
    )
    zero_capital = (capital_previous == 0) | (capital_current == 0)

    nopat_status = _labelled(
        [~consecutive, ~nopat_inputs_present],
        [STATUS_NONCONSECUTIVE, STATUS_MISSING_DATA],
        STATUS_VALID,
        index,
    )
    roic_status = _labelled(
        [~consecutive, ~roic_inputs_present, zero_capital],
        [STATUS_NONCONSECUTIVE, STATUS_MISSING_DATA, STATUS_ZERO_IC],
        STATUS_VALID,
        index,
    )
    nopat_valid = nopat_status == STATUS_VALID
    roic_valid = roic_status == STATUS_VALID

    out["calc_nopat_decomposition_status"] = nopat_status

    # -- part A: NOPAT bridge ---------------------------------------------
    nopat_change = (nopat_current - nopat_previous).where(nopat_valid)
    nopat_band = _band(
        nopat_previous,
        nopat_current,
        tolerance.nopat_absolute,
        tolerance.nopat_relative,
        floor=1.0,
    )
    nopat_parts = shapley_contributions(
        {"ebit": ebit_previous, "retention": retention_previous},
        {"ebit": ebit_current, "retention": retention_current},
        _nopat,
    )
    nopat_ebit = nopat_parts["ebit"].where(nopat_valid)
    nopat_tax = nopat_parts["retention"].where(nopat_valid)

    out["calc_nopat_change_quarterly"] = nopat_change
    out["calc_nopat_change_direction"] = _direction(
        nopat_change, nopat_band, nopat_valid, (INCREASE, DECREASE, STABLE)
    )
    out["calc_nopat_ebit_contribution"] = nopat_ebit
    out["calc_nopat_tax_contribution"] = nopat_tax
    out["calc_nopat_decomposition_residual"] = nopat_change - nopat_ebit - nopat_tax

    nopat_ebit_effect = _effect_sign(nopat_ebit, nopat_band, nopat_valid)
    nopat_tax_effect = _effect_sign(nopat_tax, nopat_band, nopat_valid)
    out["calc_nopat_ebit_effect"] = nopat_ebit_effect
    out["calc_nopat_tax_effect"] = nopat_tax_effect

    nopat_combination = _combination(
        [(EBIT, nopat_ebit_effect), (TAX, nopat_tax_effect)], _EFFECT_TOKEN, index
    )
    out["calc_nopat_effect_combination"] = nopat_combination

    # -- parts B and C: ROIC change and its Shapley split ------------------
    out["calc_roic_decomposition_status"] = roic_status

    roic_change = (roic_current - roic_previous).where(roic_valid)
    roic_band = _band(
        roic_previous,
        roic_current,
        tolerance.roic_absolute,
        tolerance.roic_relative,
    )
    roic_parts = shapley_contributions(
        {
            "ebit": ebit_previous,
            "retention": retention_previous,
            "capital": capital_previous,
        },
        {
            "ebit": ebit_current,
            "retention": retention_current,
            "capital": capital_current,
        },
        _roic,
    )
    roic_ebit = roic_parts["ebit"].where(roic_valid)
    roic_tax = roic_parts["retention"].where(roic_valid)
    roic_ic = roic_parts["capital"].where(roic_valid)

    out["calc_roic_posttax_change_quarterly"] = roic_change
    roic_direction = _direction(
        roic_change, roic_band, roic_valid, (INCREASE, DECREASE, STABLE)
    )
    out["calc_roic_posttax_change_direction"] = roic_direction
    out["calc_roic_ebit_contribution"] = roic_ebit
    out["calc_roic_tax_contribution"] = roic_tax
    out["calc_roic_ic_contribution"] = roic_ic
    out["calc_roic_decomposition_residual"] = (
        roic_change - roic_ebit - roic_tax - roic_ic
    )

    # -- part E: effect signs ---------------------------------------------
    ebit_effect = _effect_sign(roic_ebit, roic_band, roic_valid)
    tax_effect = _effect_sign(roic_tax, roic_band, roic_valid)
    ic_effect = _effect_sign(roic_ic, roic_band, roic_valid)
    out["calc_roic_ebit_effect"] = ebit_effect
    out["calc_roic_tax_effect"] = tax_effect
    out["calc_roic_ic_effect"] = ic_effect

    roic_combination = _combination(
        [(EBIT, ebit_effect), (TAX, tax_effect), (IC, ic_effect)],
        _EFFECT_TOKEN,
        index,
    )
    out["calc_roic_effect_combination"] = roic_combination

    # -- part D: raw movement of the underlying variables ------------------
    # calc_tax_rate_movement describes the tax RATE, so a falling rate is DOWN
    # even though it usually raises NOPAT.
    #
    # EBIT and the tax rate are gated on the NOPAT pair, not the ROIC pair: a
    # zero or missing capital base says nothing about whether EBIT rose. Only
    # the combination column, which needs all three, goes UNCLASSIFIED there.
    ebit_movement = _direction(
        ebit_current - ebit_previous,
        _band(
            ebit_previous,
            ebit_current,
            tolerance.amount_absolute,
            tolerance.amount_relative,
            floor=1.0,
        ),
        nopat_valid,
        (UP, DOWN, FLAT),
    )
    tax_movement = _direction(
        tax_current - tax_previous,
        _band(
            tax_previous,
            tax_current,
            tolerance.rate_absolute,
            tolerance.rate_relative,
        ),
        nopat_valid,
        (UP, DOWN, FLAT),
    )
    ic_movement = _direction(
        capital_current - capital_previous,
        _band(
            capital_previous,
            capital_current,
            tolerance.amount_absolute,
            tolerance.amount_relative,
            floor=1.0,
        ),
        roic_valid,
        (UP, DOWN, FLAT),
    )
    out["calc_ebit_movement"] = ebit_movement
    out["calc_tax_rate_movement"] = tax_movement
    out["calc_ic_movement"] = ic_movement
    raw_combination = _combination(
        [(EBIT, ebit_movement), (TAX, tax_movement), (IC, ic_movement)],
        _MOVEMENT_TOKEN,
        index,
    )
    out["calc_raw_movement_combination"] = raw_combination

    # -- part G: opposing effects and driver counts ------------------------
    effects = (ebit_effect, tax_effect, ic_effect)
    positive_count = sum((effect == POSITIVE).astype(float) for effect in effects)
    negative_count = sum((effect == NEGATIVE).astype(float) for effect in effects)
    neutral_count = sum((effect == NEUTRAL).astype(float) for effect in effects)
    active_count = positive_count + negative_count

    out["calc_roic_has_opposing_effects"] = (
        ((positive_count > 0) & (negative_count > 0)).astype("boolean").where(roic_valid)
    )
    out["calc_roic_positive_driver_count"] = positive_count.where(roic_valid)
    out["calc_roic_negative_driver_count"] = negative_count.where(roic_valid)
    out["calc_roic_neutral_driver_count"] = neutral_count.where(roic_valid)
    out["calc_roic_active_driver_count"] = active_count.where(roic_valid)

    structure = _effect_structure(
        positive_count, negative_count, active_count, roic_direction, roic_valid
    )
    out["calc_roic_effect_structure"] = structure

    # -- part H: dominance -------------------------------------------------
    # Absolute shares, not C_i / ΔROIC: with opposing effects the signed ratio
    # can be negative or exceed 100%, which makes it useless for ranking.
    absolute = pd.concat(
        [roic_ebit.abs(), roic_tax.abs(), roic_ic.abs()], axis=1, keys=[EBIT, TAX, IC]
    )
    total_absolute = absolute.sum(axis=1, min_count=3).where(roic_valid)
    out["calc_roic_total_absolute_contribution"] = total_absolute

    # Shares are meaningless when every contribution is inside the band: the
    # split would just describe noise.
    has_signal = roic_valid & (active_count > 0)
    shares = {
        name: _divide(absolute[name], total_absolute).where(has_signal)
        for name in (EBIT, TAX, IC)
    }
    out["calc_roic_ebit_absolute_share"] = shares[EBIT]
    out["calc_roic_tax_absolute_share"] = shares[TAX]
    out["calc_roic_ic_absolute_share"] = shares[IC]

    share_frame = pd.concat(
        [shares[EBIT], shares[TAX], shares[IC]], axis=1, keys=[EBIT, TAX, IC]
    )
    ranked = np.sort(share_frame.fillna(0.0).to_numpy(), axis=1)
    top_share = pd.Series(ranked[:, -1], index=index, dtype=float)
    runner_up_share = pd.Series(ranked[:, -2], index=index, dtype=float)
    leader = pd.Series(
        share_frame.fillna(-1.0).to_numpy().argmax(axis=1), index=index
    ).map({0: EBIT, 1: TAX, 2: IC})

    dominant = _labelled(
        [
            ~roic_valid,
            ~has_signal,
            (top_share - runner_up_share) <= tolerance.dominance,
        ],
        [UNCLASSIFIED, DOMINANT_NONE, DOMINANT_BALANCED],
        "",
        index,
    )
    dominant = dominant.where(dominant != "", leader).astype(object)
    out["calc_roic_dominant_driver"] = dominant

    # NONE means every contribution was inside the band, so NEUTRAL is the
    # honest answer. BALANCED means there is no single driver to attribute the
    # move to, which is a different thing from a neutral effect.
    dominant_effect = _labelled(
        [
            ~roic_valid,
            dominant == DOMINANT_NONE,
            dominant == DOMINANT_BALANCED,
            dominant == EBIT,
            dominant == TAX,
            dominant == IC,
        ],
        [UNCLASSIFIED, NEUTRAL, UNCLASSIFIED, ebit_effect, tax_effect, ic_effect],
        UNCLASSIFIED,
        index,
    )
    out["calc_roic_dominant_driver_effect"] = dominant_effect

    # -- part I: offsetting -------------------------------------------------
    # |ΔROIC| <= sum |C_i| by the triangle inequality, so the ratio lies in
    # [0, 1]; the clip only absorbs float noise. NaN — never 0 — when there is
    # no material contribution to offset, matching the pipeline's rule that
    # missing information is blank.
    offset = (
        1 - _divide(roic_change.abs(), total_absolute)
    ).where(has_signal).clip(lower=0.0, upper=1.0)
    out["calc_roic_offset_ratio"] = offset

    # -- part J: sign regimes ----------------------------------------------
    ebit_level_band = _band(
        ebit_previous,
        ebit_current,
        tolerance.level_absolute,
        tolerance.level_relative,
        floor=1.0,
    )
    nopat_level_band = _band(
        nopat_previous,
        nopat_current,
        tolerance.level_absolute,
        tolerance.level_relative,
        floor=1.0,
    )
    # Both regimes need only the income statement, so they survive a zero or
    # missing capital base.
    out["calc_ebit_sign_regime"] = _sign_regime(
        ebit_previous, ebit_current, ebit_level_band, nopat_valid
    )
    nopat_regime = _sign_regime(
        nopat_previous, nopat_current, nopat_level_band, nopat_valid
    )
    out["calc_nopat_sign_regime"] = nopat_regime

    # -- part M: data-quality flags ----------------------------------------
    # The reported tax rate is never clipped into [0, 1]; it is flagged and
    # used as reported.
    out["calc_tax_rate_quality_flag"] = _labelled(
        [tax_current.isna(), tax_current < 0, tax_current > 1],
        [TAX_RATE_MISSING, TAX_RATE_NEGATIVE, TAX_RATE_ABOVE_100],
        TAX_RATE_VALID,
        index,
    )

    negative_capital = (capital_previous < 0) | (capital_current < 0)
    tax_out_of_range = (
        (tax_previous < 0) | (tax_previous > 1) | (tax_current < 0) | (tax_current > 1)
    )
    quality = _labelled(
        [~roic_valid, negative_capital, tax_out_of_range],
        [UNCLASSIFIED, QUALITY_NEGATIVE_IC, QUALITY_TAX_OUT_OF_RANGE],
        QUALITY_VALID,
        index,
    )
    out["calc_roic_quality_flag"] = quality
    out["calc_roic_economic_interpretation_valid"] = (
        (quality == QUALITY_VALID).astype("boolean").where(roic_valid)
    )

    # -- parts K and L: business label and sentence ------------------------
    out["calc_roic_business_classification"] = _business_classification(
        status=roic_status,
        direction=roic_direction,
        structure=structure,
        dominant=dominant,
        dominant_effect=dominant_effect,
        effects={EBIT: ebit_effect, TAX: tax_effect, IC: ic_effect},
        nopat_regime=nopat_regime,
        nopat_current=nopat_current,
        quality=quality,
        valid=roic_valid,
    )
    out["calc_roic_explanation"] = _explanation(
        status=roic_status,
        direction=roic_direction,
        dominant=dominant,
        effects={EBIT: ebit_effect, TAX: tax_effect, IC: ic_effect},
        nopat_current=nopat_current,
        nopat_regime=nopat_regime,
        quality=quality,
        valid=roic_valid,
    )

    # -- one-hot families ---------------------------------------------------
    families = [
        _combination_dummies(combination, labels, prefix)
        for combination, labels, prefix in (
            (nopat_combination, NOPAT_EFFECT_COMBINATIONS, NOPAT_COMBO_PREFIX),
            (raw_combination, RAW_MOVEMENT_COMBINATIONS, RAW_COMBO_PREFIX),
            (roic_combination, ROIC_EFFECT_COMBINATIONS, ROIC_COMBO_PREFIX),
        )
    ]

    # Emitting in the declared order keeps the module's public column list and
    # the physical frame in step; the check turns a silent mismatch into an
    # error at the point it is introduced.
    if list(out) != NAMED_DECOMPOSITION_COLUMNS:
        raise RuntimeError(
            "decomposition emitted columns in an unexpected order — "
            f"expected {NAMED_DECOMPOSITION_COLUMNS}, got {list(out)}"
        )
    named = pd.DataFrame(out, index=index)
    return pd.concat([d, named, *families], axis=1)


def _business_classification(
    *,
    status: pd.Series,
    direction: pd.Series,
    structure: pd.Series,
    dominant: pd.Series,
    dominant_effect: pd.Series,
    effects: Mapping[str, pd.Series],
    nopat_regime: pd.Series,
    nopat_current: pd.Series,
    quality: pd.Series,
    valid: pd.Series,
) -> pd.Series:
    """Collapse the decomposition into one business label per observation.

    A strict priority ladder, most specific first. Regime overrides come before
    driver arithmetic because "ROIC rose" is a misleading headline when NOPAT
    is still negative or the capital base is negative — the ratio moved, the
    economics did not. Both the increase and the decrease branches end in a
    ``…_MIXED_EFFECTS`` fallback, so the ladder is total: no observation can
    fall through it, and the result is always a member of
    :data:`BUSINESS_CLASSIFICATIONS`.

    Within each direction the dominant driver is only allowed to name the label
    when its own effect agrees with the direction. The largest absolute
    contribution can point the other way — ROIC can rise while EBIT is the
    biggest single mover and it is negative — and calling that quarter
    "EBIT dominant with support" would invert the story. Those observations get
    an explicit ``…_DESPITE_DOMINANT_…`` label instead.
    """
    index = direction.index
    ebit_effect, tax_effect, ic_effect = effects[EBIT], effects[TAX], effects[IC]
    tax_drag = tax_effect == NEGATIVE
    ic_drag = ic_effect == NEGATIVE
    tax_support = tax_effect == POSITIVE
    ic_support = ic_effect == POSITIVE
    ebit_up = ebit_effect == POSITIVE
    ebit_down = ebit_effect == NEGATIVE
    increase = direction == INCREASE
    decrease = direction == DECREASE
    loss_making = nopat_current < 0

    return _labelled(
        [
            # 0 — nothing to compare.
            ~valid,
            # 1 — the ratio is arithmetically defined but not an efficiency.
            quality == QUALITY_NEGATIVE_IC,
            # 2-3 — sign transitions dominate any driver story.
            nopat_regime == "LOSS_TO_PROFIT",
            nopat_regime == "PROFIT_TO_LOSS",
            # 4-5 — still loss-making: the ratio moved mechanically.
            loss_making & increase,
            loss_making & decrease,
            # 6 — stable.
            (direction == STABLE) & (structure == STRUCTURE_NO_MATERIAL_CHANGE),
            direction == STABLE,
            # 7 — increase.
            increase
            & structure.isin([STRUCTURE_ALL_POSITIVE, STRUCTURE_SINGLE_POSITIVE]),
            increase & (dominant_effect == NEGATIVE) & (dominant == EBIT),
            increase & (dominant_effect == NEGATIVE) & (dominant == TAX),
            increase & (dominant_effect == NEGATIVE) & (dominant == IC),
            increase & (dominant == EBIT) & tax_drag & ic_drag,
            increase & (dominant == EBIT) & tax_drag,
            increase & (dominant == EBIT) & ic_drag,
            increase & (dominant == EBIT),
            increase & (dominant == TAX) & ebit_down,
            increase & (dominant == TAX) & ic_drag,
            increase & (dominant == IC) & ebit_down,
            increase & (dominant == IC) & tax_drag,
            increase,
            # 8 — decrease.
            decrease
            & structure.isin([STRUCTURE_ALL_NEGATIVE, STRUCTURE_SINGLE_NEGATIVE]),
            decrease & (dominant_effect == POSITIVE) & (dominant == EBIT),
            decrease & (dominant_effect == POSITIVE) & (dominant == TAX),
            decrease & (dominant_effect == POSITIVE) & (dominant == IC),
            decrease & (dominant == EBIT) & tax_support,
            decrease & (dominant == EBIT) & ic_support,
            decrease & (dominant == TAX) & ebit_up,
            decrease & (dominant == TAX) & ic_support,
            decrease & (dominant == IC) & ebit_up,
            decrease & (dominant == IC) & tax_support,
            decrease,
        ],
        [
            status,
            "ROIC_MECHANICAL_NEGATIVE_IC",
            "OPERATING_TURNAROUND",
            "PROFIT_TO_LOSS_DETERIORATION",
            "MECHANICAL_IMPROVEMENT_WITH_NEGATIVE_NOPAT",
            "MECHANICAL_DETERIORATION_WITH_NEGATIVE_NOPAT",
            "ROIC_STABLE_NO_CHANGE",
            "ROIC_STABLE_OFFSETTING_EFFECTS",
            "ROIC_INCREASE_ALL_DRIVERS_POSITIVE",
            "ROIC_INCREASE_DESPITE_DOMINANT_EBIT_DRAG",
            "ROIC_INCREASE_DESPITE_DOMINANT_TAX_DRAG",
            "ROIC_INCREASE_DESPITE_DOMINANT_IC_DRAG",
            "ROIC_INCREASE_EBIT_DOMINANT_WITH_TAX_AND_IC_DRAG",
            "ROIC_INCREASE_EBIT_DOMINANT_WITH_TAX_DRAG",
            "ROIC_INCREASE_EBIT_DOMINANT_WITH_IC_DRAG",
            "ROIC_INCREASE_EBIT_DOMINANT_WITH_TAX_AND_IC_SUPPORT",
            "ROIC_INCREASE_TAX_DOMINANT_WITH_EBIT_DECLINE",
            "ROIC_INCREASE_TAX_DOMINANT_WITH_IC_DRAG",
            "ROIC_INCREASE_IC_DOMINANT_WITH_EBIT_DECLINE",
            "ROIC_INCREASE_IC_DOMINANT_WITH_TAX_DRAG",
            "ROIC_INCREASE_MIXED_EFFECTS",
            "ROIC_DECREASE_ALL_DRIVERS_NEGATIVE",
            "ROIC_DECREASE_DESPITE_DOMINANT_EBIT_SUPPORT",
            "ROIC_DECREASE_DESPITE_DOMINANT_TAX_SUPPORT",
            "ROIC_DECREASE_DESPITE_DOMINANT_IC_SUPPORT",
            "ROIC_DECREASE_EBIT_DOMINANT_DESPITE_TAX_SUPPORT",
            "ROIC_DECREASE_EBIT_DOMINANT_DESPITE_IC_SUPPORT",
            "ROIC_DECREASE_TAX_DOMINANT_DESPITE_EBIT_IMPROVEMENT",
            "ROIC_DECREASE_TAX_DOMINANT_DESPITE_IC_SUPPORT",
            "ROIC_DECREASE_IC_DOMINANT_DESPITE_EBIT_IMPROVEMENT",
            "ROIC_DECREASE_IC_DOMINANT_DESPITE_TAX_SUPPORT",
            "ROIC_DECREASE_MIXED_EFFECTS",
        ],
        UNCLASSIFIED,
        index,
    )


def _explanation(
    *,
    status: pd.Series,
    direction: pd.Series,
    dominant: pd.Series,
    effects: Mapping[str, pd.Series],
    nopat_current: pd.Series,
    nopat_regime: pd.Series,
    quality: pd.Series,
    valid: pd.Series,
) -> pd.Series:
    """One plain-English sentence per observation, driven by the contributions.

    The wording follows the Shapley signs rather than the raw movements, so a
    quarter where EBIT rose but the capital base rose faster reads as a ROIC
    decline caused by invested capital — not as an EBIT improvement.
    """
    index = direction.index
    names = (EBIT, TAX, IC)
    positive_masks = [effects[name] == POSITIVE for name in names]
    negative_masks = [effects[name] == NEGATIVE for name in names]
    # Two renderings of the negative list: one that opens a sentence and one
    # that follows "while" in the middle of a clause.
    positives = _join_drivers(names, positive_masks, sentence_start=True)
    negatives_leading = _join_drivers(names, negative_masks, sentence_start=True)
    negatives_inline = _join_drivers(names, negative_masks)
    any_positive = positive_masks[0] | positive_masks[1] | positive_masks[2]
    any_negative = negative_masks[0] | negative_masks[1] | negative_masks[2]

    headline = _labelled(
        [
            ~valid,
            quality == QUALITY_NEGATIVE_IC,
            nopat_regime == "LOSS_TO_PROFIT",
            nopat_regime == "PROFIT_TO_LOSS",
            (nopat_current < 0) & (direction == INCREASE),
            (nopat_current < 0) & (direction == DECREASE),
            direction == INCREASE,
            direction == DECREASE,
        ],
        [
            "",
            "Invested capital was negative, so the ratio is mechanical and "
            "carries no efficiency reading.",
            "NOPAT turned positive this quarter.",
            "NOPAT turned negative this quarter.",
            "ROIC became less negative, but the change was mechanical because "
            "NOPAT stayed negative.",
            "ROIC became more negative.",
            "ROIC increased.",
            "ROIC decreased.",
        ],
        "ROIC was essentially unchanged.",
        index,
    )

    drivers = _labelled(
        [
            ~valid,
            any_positive & any_negative,
            any_positive,
            any_negative,
        ],
        [
            "",
            positives + " contributed positively, while " + negatives_inline
            + " worked against the change.",
            positives + " contributed positively.",
            negatives_leading + " contributed negatively.",
        ],
        "No driver moved materially.",
        index,
    )

    dominance = _labelled(
        [
            ~valid | dominant.isin([DOMINANT_NONE, UNCLASSIFIED]),
            dominant == DOMINANT_BALANCED,
        ],
        ["", "No single driver dominated."],
        dominant.map(_DRIVER_SENTENCE_START).fillna("")
        + " was the dominant driver.",
        index,
    )

    unavailable = "No comparison is available (" + status + ")."
    sentence = (headline + " " + drivers + " " + dominance).str.replace(
        r"\s+", " ", regex=True
    ).str.strip()
    return sentence.where(valid, unavailable).astype(object)
