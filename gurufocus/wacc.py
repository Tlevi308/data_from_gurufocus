"""Weighted average cost of capital, as a consistent screening threshold.

Purpose
-------
This is not a valuation model. It is a uniform estimate of the cost of capital,
computed the same way for every company and every quarter, so that the screen

    ROIC > WACC

means the same thing across the whole panel. Consistency matters more here than
per-company precision: a bespoke discount rate per name would be more accurate
and completely useless for ranking.

Formula
-------
::

    WACC = E/(D+E) x Re  +  D/(D+E) x Rd x (1 - T)

    E  = market capitalisation, not book equity
    D  = interest-bearing debt, including finance leases
    Re = Rf + ERP                       (no CAPM, no beta)
    Rd = interest expense TTM / average interest-bearing debt
    T  = tax expense TTM / pretax income TTM, floored at zero

Units
-----
Re, Rd and therefore WACC are **annual** rates. The project's ROIC columns are
single-quarter figures, so comparing them directly would understate returns
roughly fourfold. Both conversions are emitted:

* every quarterly ROIC gets an annualised twin, ``(1 + r)**4 - 1``
* WACC gets a quarterly twin, ``(1 + w)**0.25 - 1``

Because ``x -> (1 + x)**4 - 1`` is strictly increasing wherever it is defined,
the *verdict* is identical in both units — only the size of the gap differs.
:func:`add_wacc` emits both spreads and drives the boolean off the quarterly
pair, which stays defined in quarters where the annualised ROIC does not.

What is deliberately not done
-----------------------------
* The reported interest expense is used as-is. When GuruFocus reports zero
  interest against real debt — 48 of 120 quarters for INTC, the five most
  recent for AAPL — the cost of debt is zero and
  ``calc_wacc_quality_flag`` says why. No substitute rate is invented, but the
  row is marked so it can be filtered.
* The tax rate is floored at zero, never at one. A company with negative
  pretax income gets no tax shield, which raises WACC. That is the
  conservative direction for a screen.
"""

from __future__ import annotations

from dataclasses import dataclass, fields as dataclass_fields

import numpy as np
import pandas as pd

from .quarterly import (
    annualize,
    deannualize,
    divide,
    numeric,
    trailing_four_quarter_sum,
    year_over_year_average,
)


# ---------------------------------------------------------------------------
# Assumptions
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class WaccAssumptions:
    """The two inputs that do not come from the financial statements.

    Cost of equity is ``Rf + ERP`` by instruction: no CAPM and no beta. A beta
    estimated per company would reintroduce exactly the cross-sectional noise
    this threshold exists to avoid.

    Both values are decimals, not percents: 0.0425 is 4.25%.
    """

    risk_free_rate: float = 0.0425
    equity_risk_premium: float = 0.0300

    @property
    def cost_of_equity(self) -> float:
        return self.risk_free_rate + self.equity_risk_premium

    def validate(self) -> None:
        for spec in dataclass_fields(self):
            value = getattr(self, spec.name)
            if not np.isfinite(value):
                raise ValueError(f"wacc {spec.name} must be a finite number")
            # A rate above 1 is almost always a percent written as a whole
            # number. Left alone it would silently produce a 725% WACC.
            if not -1.0 < value <= 1.0:
                raise ValueError(
                    f"wacc {spec.name} must be a decimal fraction in (-1, 1] — "
                    f"got {value!r}. Write 4.25% as 0.0425, not 4.25."
                )


DEFAULT_ASSUMPTIONS = WaccAssumptions()


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------
QUALITY_VALID = "VALID"
QUALITY_NO_DEBT = "NO_DEBT"
QUALITY_DEBT_WITHOUT_INTEREST = "DEBT_WITHOUT_INTEREST"
QUALITY_NEGATIVE_INTEREST = "NEGATIVE_INTEREST_EXPENSE"
QUALITY_MISSING_MARKET_CAP = "MISSING_MARKET_CAP"
QUALITY_MISSING_DEBT = "MISSING_DEBT"
QUALITY_INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"

WACC_QUALITY_FLAGS = frozenset(
    {
        QUALITY_VALID,
        QUALITY_NO_DEBT,
        QUALITY_DEBT_WITHOUT_INTEREST,
        QUALITY_NEGATIVE_INTEREST,
        QUALITY_MISSING_MARKET_CAP,
        QUALITY_MISSING_DEBT,
        QUALITY_INSUFFICIENT_HISTORY,
    }
)


# ---------------------------------------------------------------------------
# Column inventory
# ---------------------------------------------------------------------------
# Annualised twins sit next to their quarterly sources in the workbook, because
# that is the only placement where the conversion can be checked by eye.
ANNUALIZED_ROIC_COLUMNS = [
    "calc_roic_pretax_annualized_ic_raw",
    "calc_roic_posttax_annualized_ic_raw",
]

WACC_CORE_COLUMNS = [
    # Assumptions, written per row so every observation records the rate it
    # was priced with.
    "calc_wacc_risk_free_rate",
    "calc_wacc_equity_risk_premium",
    "calc_wacc_cost_of_equity",
    # Capital structure.
    "calc_wacc_equity_value",
    "calc_wacc_average_debt",
    "calc_wacc_total_capital",
    "calc_wacc_equity_weight",
    "calc_wacc_debt_weight",
    # Cost of debt.
    "calc_interest_expense_ttm",
    "calc_wacc_cost_of_debt",
    "calc_wacc_tax_rate",
    "calc_wacc_after_tax_cost_of_debt",
    # Result.
    "calc_wacc_annual",
    "calc_wacc_quarterly",
    "calc_wacc_quality_flag",
    "calc_wacc_inputs_complete",
    # The screen.
    "calc_roic_minus_wacc_annualized",
    "calc_roic_minus_wacc_quarterly",
    "calc_creates_value",
]

TEXT_WACC_COLUMNS = frozenset(
    {
        "calc_wacc_quality_flag",
        "calc_wacc_inputs_complete",
        "calc_creates_value",
    }
)

# Rates and weights are small ratios; the shared "0.00" display would show a
# 2.7% cost of debt as 0.03 and a 12 bp spread as 0.00.
HIGH_PRECISION_WACC_COLUMNS = frozenset(
    {
        "calc_roic_pretax_annualized_ic_raw",
        "calc_roic_posttax_annualized_ic_raw",
        "calc_wacc_risk_free_rate",
        "calc_wacc_equity_risk_premium",
        "calc_wacc_cost_of_equity",
        "calc_wacc_equity_weight",
        "calc_wacc_debt_weight",
        "calc_wacc_cost_of_debt",
        "calc_wacc_tax_rate",
        "calc_wacc_after_tax_cost_of_debt",
        "calc_wacc_annual",
        "calc_wacc_quarterly",
        "calc_roic_minus_wacc_annualized",
        "calc_roic_minus_wacc_quarterly",
    }
)


def wacc_columns() -> list[str]:
    """Return the WACC columns in their canonical order."""
    return list(ANNUALIZED_ROIC_COLUMNS) + list(WACC_CORE_COLUMNS)


def wacc_dependencies() -> dict[str, tuple[str, ...]]:
    """Return direct source columns for each WACC column."""
    # The weights are built from period-end balances; the average debt belongs
    # to the cost of debt alone.
    capital = ("calc_wacc_equity_value", "calc_debt_value_quarterly")
    dependencies: dict[str, tuple[str, ...]] = {
        "calc_roic_pretax_annualized_ic_raw": (
            "calc_roic_pretax_quarterly_ic_raw",
        ),
        "calc_roic_posttax_annualized_ic_raw": (
            "calc_roic_posttax_quarterly_ic_raw",
        ),
        "calc_wacc_risk_free_rate": (),
        "calc_wacc_equity_risk_premium": (),
        "calc_wacc_cost_of_equity": (
            "calc_wacc_risk_free_rate",
            "calc_wacc_equity_risk_premium",
        ),
        "calc_wacc_equity_value": ("market_cap",),
        "calc_wacc_average_debt": ("calc_debt_value_quarterly",),
        "calc_wacc_total_capital": capital,
        "calc_wacc_equity_weight": capital + ("calc_wacc_total_capital",),
        "calc_wacc_debt_weight": capital + ("calc_wacc_total_capital",),
        "calc_interest_expense_ttm": ("interest_expense",),
        "calc_wacc_cost_of_debt": (
            "calc_interest_expense_ttm",
            "calc_wacc_average_debt",
        ),
        "calc_wacc_tax_rate": (
            "calc_tax_expense_quarterly",
            "pretax_income",
        ),
        "calc_wacc_after_tax_cost_of_debt": (
            "calc_wacc_cost_of_debt",
            "calc_wacc_tax_rate",
        ),
        "calc_wacc_annual": (
            "calc_wacc_equity_weight",
            "calc_wacc_cost_of_equity",
            "calc_wacc_debt_weight",
            "calc_wacc_after_tax_cost_of_debt",
        ),
        "calc_wacc_quarterly": ("calc_wacc_annual",),
        "calc_wacc_quality_flag": (
            "calc_wacc_equity_value",
            "calc_wacc_average_debt",
            "calc_interest_expense_ttm",
        ),
        "calc_wacc_inputs_complete": ("calc_wacc_quality_flag",),
        "calc_roic_minus_wacc_annualized": (
            "calc_roic_posttax_annualized_ic_raw",
            "calc_wacc_annual",
        ),
        "calc_roic_minus_wacc_quarterly": (
            "calc_roic_posttax_quarterly_ic_raw",
            "calc_wacc_quarterly",
        ),
        "calc_creates_value": (
            "calc_roic_posttax_quarterly_ic_raw",
            "calc_wacc_quarterly",
        ),
    }
    missing = [column for column in wacc_columns() if column not in dependencies]
    if missing:
        raise RuntimeError(
            "Missing dependencies for WACC columns: " + ", ".join(missing)
        )
    return dependencies


# ---------------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------------
def _effective_tax_rate(
    tax_expense_ttm: pd.Series,
    pretax_income_ttm: pd.Series,
) -> pd.Series:
    """Trailing-twelve-month effective tax rate, floored at zero.

    TTM rather than single-quarter: the numerator of the cost of debt is a TTM
    flow, and a one-quarter effective rate swings wildly on discrete tax
    settlements — rates above 100% and below zero both occur in single quarters.

    Zero when pretax income is not positive, or when the ratio cannot be formed.
    A loss-making company gets no interest tax shield, which raises WACC; that
    is the conservative direction for a threshold used to admit investments.
    """
    rate = divide(tax_expense_ttm, pretax_income_ttm.where(pretax_income_ttm > 0))
    return rate.fillna(0.0)


def _quality_flag(
    equity: pd.Series,
    debt: pd.Series,
    average_debt: pd.Series,
    interest_ttm: pd.Series,
) -> pd.Series:
    """Explain, per row, how far the estimate can be trusted.

    The flag never changes a number. Its job is to separate three situations
    that all look identical in ``calc_wacc_cost_of_debt``: a company with no
    debt, a company whose interest GuruFocus did not report, and a company that
    genuinely pays nothing.

    First match wins, cheapest failure first: without the weights there is no
    estimate at all, so a missing market cap outranks anything about interest.
    """
    conditions = [
        equity.isna(),
        debt.isna(),
        debt == 0,
        average_debt.isna() | interest_ttm.isna(),
        interest_ttm < 0,
        interest_ttm == 0,
    ]
    choices = [
        QUALITY_MISSING_MARKET_CAP,
        QUALITY_MISSING_DEBT,
        QUALITY_NO_DEBT,
        QUALITY_INSUFFICIENT_HISTORY,
        QUALITY_NEGATIVE_INTEREST,
        QUALITY_DEBT_WITHOUT_INTEREST,
    ]
    masks = [condition.to_numpy(dtype=bool, na_value=False) for condition in conditions]
    return pd.Series(
        np.select(masks, choices, default=QUALITY_VALID),
        index=equity.index,
        dtype=object,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def add_wacc(
    frame: pd.DataFrame,
    *,
    consecutive_quarters: pd.Series,
    assumptions: WaccAssumptions = DEFAULT_ASSUMPTIONS,
) -> pd.DataFrame:
    """Add the cost-of-capital estimate and the ROIC > WACC screen.

    Runs on one ticker's sorted frame, after the base calculations. Reads the
    published debt, ROIC and tax-expense columns; recomputes none of them.
    """
    assumptions.validate()
    d = frame
    index = d.index
    n = lambda column: numeric(d, column)  # noqa: E731
    out: dict[str, pd.Series] = {}

    def constant(value: float) -> pd.Series:
        return pd.Series(value, index=index, dtype=float)

    # -- annualised ROIC ---------------------------------------------------
    # Emitted for both ROIC variants so the workbook never mixes a quarterly
    # return with an annual cost of capital.
    for quarterly_column, annual_column in zip(
        ("calc_roic_pretax_quarterly_ic_raw", "calc_roic_posttax_quarterly_ic_raw"),
        ANNUALIZED_ROIC_COLUMNS,
    ):
        out[annual_column] = annualize(n(quarterly_column))

    # -- cost of equity ----------------------------------------------------
    out["calc_wacc_risk_free_rate"] = constant(assumptions.risk_free_rate)
    out["calc_wacc_equity_risk_premium"] = constant(assumptions.equity_risk_premium)
    out["calc_wacc_cost_of_equity"] = constant(assumptions.cost_of_equity)

    # -- capital structure -------------------------------------------------
    # E is market capitalisation. Book equity would make the weights a
    # statement about accounting history rather than about how the market
    # currently funds the business.
    equity = n("market_cap")
    out["calc_wacc_equity_value"] = equity

    # D is the existing debt column: short-term plus long-term, both including
    # capital lease obligations. Verified identical to summing the four
    # component fields, so there is deliberately no second definition of debt.
    #
    # The weights use the period-end balance, matching market capitalisation,
    # which is also a point-in-time figure. Only the cost of debt averages, and
    # it averages for a different reason: its numerator is a full-year flow.
    debt = n("calc_debt_value_quarterly")
    average_debt = year_over_year_average(debt, consecutive_quarters)
    out["calc_wacc_average_debt"] = average_debt

    total_capital = equity + debt
    out["calc_wacc_total_capital"] = total_capital
    equity_weight = divide(equity, total_capital)
    debt_weight = divide(debt, total_capital)
    out["calc_wacc_equity_weight"] = equity_weight
    out["calc_wacc_debt_weight"] = debt_weight

    # -- cost of debt ------------------------------------------------------
    # Sign flip mirrors calc_tax_expense_quarterly: GuruFocus reports the
    # expense as negative, so a positive reported value is net interest income
    # and produces a negative cost of debt, which the quality flag calls out.
    interest_ttm = trailing_four_quarter_sum(-n("interest_expense"), consecutive_quarters)
    out["calc_interest_expense_ttm"] = interest_ttm

    cost_of_debt = divide(interest_ttm, average_debt)
    out["calc_wacc_cost_of_debt"] = cost_of_debt

    tax_rate = _effective_tax_rate(
        trailing_four_quarter_sum(n("calc_tax_expense_quarterly"), consecutive_quarters),
        trailing_four_quarter_sum(n("pretax_income"), consecutive_quarters),
    )
    out["calc_wacc_tax_rate"] = tax_rate
    after_tax_cost_of_debt = cost_of_debt * (1 - tax_rate)
    out["calc_wacc_after_tax_cost_of_debt"] = after_tax_cost_of_debt

    # -- the estimate ------------------------------------------------------
    # With no debt the debt term is dropped rather than multiplied by a NaN
    # cost of debt, so a debt-free company gets WACC = Re from its very first
    # quarter instead of waiting a year for a cost of debt it does not need.
    debt_term = (debt_weight * after_tax_cost_of_debt).where(debt != 0, 0.0)
    wacc_annual = equity_weight * assumptions.cost_of_equity + debt_term
    out["calc_wacc_annual"] = wacc_annual
    wacc_quarterly = deannualize(wacc_annual)
    out["calc_wacc_quarterly"] = wacc_quarterly

    quality = _quality_flag(equity, debt, average_debt, interest_ttm)
    out["calc_wacc_quality_flag"] = quality
    out["calc_wacc_inputs_complete"] = (
        quality.isin([QUALITY_VALID, QUALITY_NO_DEBT]).astype("boolean")
    )

    # -- the screen --------------------------------------------------------
    # Both spreads are reported because their magnitudes carry different
    # meaning, but the sign is provably the same: annualisation is strictly
    # increasing, so it cannot reorder a return against a cost.
    roic_quarterly = n("calc_roic_posttax_quarterly_ic_raw")
    out["calc_roic_minus_wacc_annualized"] = (
        out["calc_roic_posttax_annualized_ic_raw"] - wacc_annual
    )
    out["calc_roic_minus_wacc_quarterly"] = roic_quarterly - wacc_quarterly

    # Driven off the quarterly pair: it survives quarters where the loss
    # exceeds the capital base and the annualised return is undefined.
    comparable = roic_quarterly.notna() & wacc_quarterly.notna()
    out["calc_creates_value"] = (
        (roic_quarterly > wacc_quarterly).astype("boolean").where(comparable)
    )

    if list(out) != wacc_columns():
        raise RuntimeError(
            "WACC emitted columns in an unexpected order — "
            f"expected {wacc_columns()}, got {list(out)}"
        )
    return pd.concat([d, pd.DataFrame(out, index=index)], axis=1)
