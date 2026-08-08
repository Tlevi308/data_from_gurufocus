"""Primitives for working with one ticker's quarterly series.

Every consumer of these helpers runs on **one ticker's frame, already sorted
ascending by fiscal_period_end_date**, before the multi-ticker concat in
:mod:`gurufocus.pipeline`. Plain ``.shift()`` is therefore safe here and no
``groupby`` is used. Moving this module downstream of the concat would silently
bleed values across tickers.

Two rules run through all of them:

* A missing or zero denominator yields ``NaN``, never zero. "We could not
  compute this" and "this is zero" are different statements and the output has
  to keep them apart.
* A window that reaches back past a gap in the fiscal calendar yields ``NaN``
  rather than quietly comparing non-adjacent quarters.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    """Return a numeric column, or an all-NaN series when it is absent.

    Absent columns must not raise: a field the API stopped returning should
    blank the calculations that depend on it, not abort the run.
    """
    if column in frame.columns:
        return pd.to_numeric(frame[column], errors="coerce")
    return pd.Series(np.nan, index=frame.index, dtype=float)


def divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    """Divide while returning NaN for a missing or zero denominator."""
    return numerator / denominator.where(denominator != 0)


def consecutive_quarters(frame: pd.DataFrame) -> pd.Series:
    """Identify rows whose preceding row is the immediately prior quarter."""
    quarter_num = (
        frame["period_quarter"].astype(str).str.extract(r"(\d)")[0].astype(float)
    )
    quarter_index = frame["period_year"].astype(float) * 4 + (quarter_num - 1)
    return ((quarter_index - quarter_index.shift(1)) == 1).fillna(False)


def consecutive_window(consecutive: pd.Series, periods: int) -> pd.Series:
    """Rows where the last ``periods`` observations are an unbroken run.

    ``periods`` observations are joined by ``periods - 1`` links, so a
    four-quarter window needs the consecutive flag at t, t-1 and t-2.
    """
    if periods < 2:
        raise ValueError("a window needs at least two observations")
    window = consecutive.astype(bool)
    for lag in range(1, periods - 1):
        window = window & consecutive.shift(lag, fill_value=False).astype(bool)
    return window


def quarter_average(
    ending_balance: pd.Series,
    consecutive: pd.Series,
) -> pd.Series:
    """Average the opening and closing quarter-end balances."""
    result = (ending_balance.shift(1) + ending_balance) / 2
    return result.where(consecutive_window(consecutive, 2))


def year_over_year_average(
    ending_balance: pd.Series,
    consecutive: pd.Series,
) -> pd.Series:
    """Average the balance four quarters back with the current balance.

    The opening balance of a trailing-twelve-month period is the one four
    quarters back. Pairing a TTM flow with a single-quarter average would only
    be right when the balance is flat.
    """
    result = (ending_balance.shift(4) + ending_balance) / 2
    return result.where(consecutive_window(consecutive, 5))


def trailing_four_quarter_sum(
    flow: pd.Series,
    consecutive: pd.Series,
) -> pd.Series:
    """Sum four quarters only when all four fiscal observations are consecutive."""
    return (
        flow.rolling(window=4, min_periods=4)
        .sum()
        .where(consecutive_window(consecutive, 4))
    )


def annualize(quarterly_rate: pd.Series) -> pd.Series:
    """Compound a quarterly rate into an annual one: ``(1 + r)**4 - 1``.

    Undefined at or below a total loss of the base. For ``r <= -1`` the even
    power flips the sign back to positive and the answer inverts: a quarterly
    -200% would come out as 0%, and -150% would read as *better* than -100%.
    Those rows return NaN instead of a number that looks plausible and says the
    opposite of what happened.
    """
    base = 1 + quarterly_rate
    return base.where(base > 0) ** 4 - 1


def deannualize(annual_rate: pd.Series) -> pd.Series:
    """Convert an annual rate into its quarterly equivalent.

    ``(1 + r)**0.25 - 1``, the exact inverse of :func:`annualize`. A fourth root
    of a negative number is not real, so rates at or below -100% return NaN.
    """
    base = 1 + annual_rate
    return base.where(base > 0) ** 0.25 - 1
