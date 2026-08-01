"""Quarterly tax, NOPAT, IC_RAW, ROIC, EV/FCF, and debt calculations.

NOPAT and ROIC use the current quarter. Balance-sheet ROIC denominators use the
average of consecutive opening and closing quarter-end balances. EV/FCF is a
quarter-end valuation observation divided by trailing-four-quarter FCF, which
matches the GuruFocus valuation-ratio convention.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


_RESULT_COLUMNS = [
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
    }
    missing = [column for column in calc_columns() if column not in dependencies]
    if missing:
        raise RuntimeError(
            "Missing dependencies for calculated columns: " + ", ".join(missing)
        )
    return dependencies


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    """Return a numeric column, or an all-NaN series when it is absent."""
    if column in frame.columns:
        return pd.to_numeric(frame[column], errors="coerce")
    return pd.Series(np.nan, index=frame.index, dtype=float)


def _consecutive_quarters(frame: pd.DataFrame) -> pd.Series:
    """Identify rows whose preceding row is the immediately prior quarter."""
    quarter_num = (
        frame["period_quarter"].astype(str).str.extract(r"(\d)")[0].astype(float)
    )
    quarter_index = frame["period_year"].astype(float) * 4 + (quarter_num - 1)
    return ((quarter_index - quarter_index.shift(1)) == 1).fillna(False)


def _divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    """Divide while returning NaN for a missing or zero denominator."""
    return numerator / denominator.where(denominator != 0)


def _quarter_average(
    ending_balance: pd.Series,
    consecutive_quarters: pd.Series,
) -> pd.Series:
    """Average the opening and closing quarter-end balances."""
    result = (ending_balance.shift(1) + ending_balance) / 2
    return result.where(consecutive_quarters)


def _trailing_four_quarter_sum(
    flow: pd.Series,
    consecutive_quarters: pd.Series,
) -> pd.Series:
    """Sum four quarters only when all four fiscal observations are consecutive."""
    complete_window = (
        consecutive_quarters
        & consecutive_quarters.shift(1, fill_value=False)
        & consecutive_quarters.shift(2, fill_value=False)
    )
    return flow.rolling(window=4, min_periods=4).sum().where(complete_window)


def add_calculated(frame: pd.DataFrame) -> pd.DataFrame:
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

    return d
