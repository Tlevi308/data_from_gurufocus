"""Quarterly tax, NOPAT, IC_RAW, ROIC, EV/FCF, and debt calculations.

NOPAT and ROIC use the current quarter. Balance-sheet ROIC denominators use the
average of consecutive opening and closing quarter-end balances. EV/FCF is a
quarter-end valuation observation divided by trailing-four-quarter FCF, which
matches the GuruFocus valuation-ratio convention.

The quarter-over-quarter driver attribution for NOPAT and post-tax ROIC lives in
:mod:`gurufocus.decomposition` and is appended by :func:`add_calculated`. It
reads the columns computed here; it does not recompute or re-average any of
them.
"""

from __future__ import annotations

import pandas as pd

from .decomposition import (
    DEFAULT_TOLERANCE,
    DecompositionTolerance,
    add_decomposition,
    decomposition_columns,
    decomposition_dependencies,
)
from .quarterly import (
    consecutive_quarters as _consecutive_quarters,
    divide as _divide,
    numeric as _numeric,
    quarter_average as _quarter_average,
    trailing_four_quarter_sum as _trailing_four_quarter_sum,
)
from .wacc import (
    DEFAULT_ASSUMPTIONS,
    WaccAssumptions,
    add_wacc,
    wacc_columns,
    wacc_dependencies,
)


_BASE_RESULT_COLUMNS = [
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

_RESULT_COLUMNS = _BASE_RESULT_COLUMNS + decomposition_columns() + wacc_columns()


def calc_columns() -> list[str]:
    """Return calculated columns in their canonical order."""
    return list(_RESULT_COLUMNS)


CALC_COLUMNS = calc_columns()


def calculation_dependencies() -> dict[str, tuple[str, ...]]:
    """Return direct source columns for each calculated column."""
    dependencies = {
        "calc_tax_expense_quarterly": ("tax_provision",),
        "calc_raw_tax_rate_quarterly": (
            "calc_tax_expense_quarterly",
            "pretax_income",
        ),
        "calc_nopat_quarterly": (
            "ebit",
            "calc_raw_tax_rate_quarterly",
        ),
        "calc_ic_raw": (
            "total_current_assets",
            "total_current_liabilities",
            "net_ppe",
            "goodwill",
        ),
        "calc_average_ic_raw_quarterly": ("calc_ic_raw",),
        "calc_roic_pretax_quarterly_ic_raw": (
            "ebit",
            "calc_average_ic_raw_quarterly",
        ),
        "calc_roic_posttax_quarterly_ic_raw": (
            "calc_nopat_quarterly",
            "calc_average_ic_raw_quarterly",
        ),
        "calc_debt_value_quarterly": (
            "short_term_debt_and_capital_lease",
            "long_term_debt_and_capital_lease",
        ),
        "calc_debt_to_equity_quarterly": (
            "calc_debt_value_quarterly",
            "total_stockholders_equity",
        ),
        "calc_enterprise_value_quarterly": (
            "market_cap",
            "calc_debt_value_quarterly",
            "cash_and_cash_equivalents",
            "short_term_investments",
        ),
        "calc_free_cash_flow_ttm": ("free_cash_flow",),
        "calc_ev_to_fcf_quarterly": (
            "calc_enterprise_value_quarterly",
            "calc_free_cash_flow_ttm",
        ),
        **decomposition_dependencies(),
        **wacc_dependencies(),
    }
    missing = [column for column in calc_columns() if column not in dependencies]
    if missing:
        raise RuntimeError(
            "Missing dependencies for calculated columns: " + ", ".join(missing)
        )
    return dependencies


def add_calculated(
    frame: pd.DataFrame,
    *,
    tolerance: DecompositionTolerance = DEFAULT_TOLERANCE,
    wacc_assumptions: WaccAssumptions = DEFAULT_ASSUMPTIONS,
) -> pd.DataFrame:
    """Add the selected quarterly calculations to one ticker's sorted data."""
    d = frame.copy()
    n = lambda column: _numeric(d, column)  # noqa: E731
    consecutive_quarters = _consecutive_quarters(d)

    # GuruFocus reports a tax expense as a negative tax_provision value.
    d["calc_tax_expense_quarterly"] = -n("tax_provision")
    d["calc_raw_tax_rate_quarterly"] = _divide(
        d["calc_tax_expense_quarterly"],
        n("pretax_income"),
    )
    d["calc_nopat_quarterly"] = n("ebit") * (
        1 - d["calc_raw_tax_rate_quarterly"]
    )

    # IC_RAW = TCA - TCL + Net PPE + Goodwill. Net PPE excludes goodwill.
    d["calc_ic_raw"] = (
        n("total_current_assets")
        - n("total_current_liabilities")
        + n("net_ppe")
        + n("goodwill")
    )
    d["calc_average_ic_raw_quarterly"] = _quarter_average(
        d["calc_ic_raw"],
        consecutive_quarters,
    )
    d["calc_roic_pretax_quarterly_ic_raw"] = _divide(
        n("ebit"),
        d["calc_average_ic_raw_quarterly"],
    )
    d["calc_roic_posttax_quarterly_ic_raw"] = _divide(
        d["calc_nopat_quarterly"],
        d["calc_average_ic_raw_quarterly"],
    )

    # Debt is the book-value proxy available in the quarterly fundamentals.
    d["calc_debt_value_quarterly"] = (
        n("short_term_debt_and_capital_lease")
        + n("long_term_debt_and_capital_lease")
    )

    d["calc_debt_to_equity_quarterly"] = _divide(
        d["calc_debt_value_quarterly"],
        n("total_stockholders_equity"),
    )

    # Requested EV proxy: market cap + debt - cash - short-term investments.
    d["calc_enterprise_value_quarterly"] = (
        n("market_cap")
        + d["calc_debt_value_quarterly"]
        - n("cash_and_cash_equivalents")
        - n("short_term_investments")
    )
    d["calc_free_cash_flow_ttm"] = _trailing_four_quarter_sum(
        n("free_cash_flow"),
        consecutive_quarters,
    )
    d["calc_ev_to_fcf_quarterly"] = _divide(
        d["calc_enterprise_value_quarterly"],
        d["calc_free_cash_flow_ttm"],
    )

    # Reads the published NOPAT, IC average, and ROIC columns above and
    # attributes their quarter-over-quarter change to EBIT, tax, and invested
    # capital. The same consecutive-quarter mask is reused throughout so the
    # stages can never disagree about which rows are comparable.
    d = add_decomposition(
        d,
        consecutive_quarters=consecutive_quarters,
        tolerance=tolerance,
    )

    # Runs last: it needs the debt, ROIC and tax-expense columns above, and it
    # annualises the ROIC columns so the ROIC > WACC screen compares two rates
    # measured over the same horizon.
    return add_wacc(
        d,
        consecutive_quarters=consecutive_quarters,
        assumptions=wacc_assumptions,
    )
