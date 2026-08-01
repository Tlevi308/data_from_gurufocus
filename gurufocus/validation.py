"""Read-only validation checks for the selected quarterly dataset."""

from __future__ import annotations

import pandas as pd

from .fields import FIELD_GROUPS


JUMP_RELATIVE = 0.50
JUMP_ABSOLUTE = 1000.0
GAP_RANGES = {
    "quarterly": (60, 130),
    "annually": (300, 430),
}
DEFAULT_GAP_RANGE = GAP_RANGES["quarterly"]


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    if column in frame.columns:
        return pd.to_numeric(frame[column], errors="coerce")
    return pd.Series(dtype=float, index=frame.index)


def infer_period(frame: pd.DataFrame) -> str:
    dates = pd.to_datetime(
        frame.get("fiscal_period_end_date"), errors="coerce"
    ).dropna().sort_values()
    if len(dates) < 2:
        return "quarterly"
    return "annually" if dates.diff().dt.days.median() > 200 else "quarterly"


def quality_checks(frame: pd.DataFrame, period: str | None = None) -> pd.DataFrame:
    """Run checks that reference only fields retained in the current output."""
    d = frame
    period = period or infer_period(frame)
    n = lambda column: _numeric(d, column)  # noqa: E731
    checks: list[dict] = []
    symbols = (
        d["symbol"]
        if "symbol" in d.columns
        else pd.Series("__all__", index=d.index)
    )

    def add(
        name: str,
        passed: pd.Series,
        note: str = "",
        applicable: pd.Series | None = None,
    ) -> None:
        series = passed.astype(float)
        if applicable is not None:
            series = series.where(applicable.fillna(False))
        series = series.dropna()
        n_tested = len(series)
        checks.append(
            {
                "check": name,
                "n_tested": int(n_tested),
                "n_failed": int((series == 0).sum()),
                "pct_ok": round(100 * series.mean(), 1) if n_tested else None,
                "n_skipped": int(len(d) - n_tested),
                "note": note,
            }
        )

    current_assets = n("total_current_assets")
    cash_and_investments = n("cash_and_cash_equivalents").fillna(0) + n(
        "short_term_investments"
    ).fillna(0)
    add(
        "Current assets cover cash and short-term investments",
        current_assets >= cash_and_investments,
        applicable=current_assets.notna(),
    )

    for column in (
        "goodwill",
        "intangible_assets",
        "net_ppe",
        "short_term_investments",
    ):
        if column not in d.columns:
            continue
        current = n(column)
        previous = current.groupby(symbols).shift(1)
        delta = (current - previous).abs()
        jump = (delta > JUMP_RELATIVE * previous.abs()) & (
            delta > JUMP_ABSOLUTE
        )
        add(
            f"No unexplained jump in {column}",
            ~jump,
            f"Flags changes above {JUMP_RELATIVE:.0%} and {JUMP_ABSOLUTE:,.0f}.",
            applicable=current.notna() & previous.notna(),
        )

    dates = pd.to_datetime(d["fiscal_period_end_date"], errors="coerce")
    gaps = dates.groupby(symbols).diff().dt.days
    gap_min, gap_max = GAP_RANGES.get(period, DEFAULT_GAP_RANGE)
    add(
        "Consecutive reporting periods",
        (gaps >= gap_min) & (gaps <= gap_max),
        f"Expected a gap of {gap_min}-{gap_max} days.",
        applicable=gaps.notna(),
    )

    if "period_key" in d.columns:
        if "symbol" in d.columns:
            duplicated = d.duplicated(
                subset=["symbol", "period_key"], keep=False
            )
        else:
            duplicated = d["period_key"].duplicated(keep=False)
        add("period_key is unique within ticker", ~duplicated)

    if "market_cap" in d.columns:
        market_cap = n("market_cap")
        add(
            "Market capitalization is positive",
            market_cap > 0,
            applicable=market_cap.notna(),
        )

    equity_inputs = {"total_assets", "total_liabilities", "equity"}
    if equity_inputs <= set(d.columns):
        expected_equity = n("total_assets") - n("total_liabilities")
        actual_equity = n("equity")
        add(
            "Equity matches total assets less total liabilities",
            (actual_equity - expected_equity).abs() <= 1e-10,
            applicable=expected_equity.notna() & actual_equity.notna(),
        )

    debt_inputs = {
        "short_term_debt_and_capital_lease",
        "long_term_debt_and_capital_lease",
        "calc_debt_value_quarterly",
    }
    if debt_inputs <= set(d.columns):
        expected_debt = n("short_term_debt_and_capital_lease") + n(
            "long_term_debt_and_capital_lease"
        )
        actual_debt = n("calc_debt_value_quarterly")
        applicable = expected_debt.notna() & actual_debt.notna()
        add(
            "Calculated debt matches short-term plus long-term debt",
            (actual_debt - expected_debt).abs() <= 1e-10,
            applicable=applicable,
        )
        add(
            "Calculated debt is non-negative",
            actual_debt >= 0,
            applicable=actual_debt.notna(),
        )

    debt_ratio_inputs = {
        "calc_debt_value_quarterly",
        "total_stockholders_equity",
        "calc_debt_to_equity_quarterly",
    }
    if debt_ratio_inputs <= set(d.columns):
        equity = n("total_stockholders_equity")
        expected_ratio = n("calc_debt_value_quarterly") / equity.where(
            equity != 0
        )
        actual_ratio = n("calc_debt_to_equity_quarterly")
        add(
            "Debt-to-equity ratio matches debt and stockholders equity",
            (actual_ratio - expected_ratio).abs() <= 1e-10,
            applicable=expected_ratio.notna() & actual_ratio.notna(),
        )

    ev_fcf_inputs = {
        "market_cap",
        "calc_debt_value_quarterly",
        "cash_and_cash_equivalents",
        "short_term_investments",
        "free_cash_flow",
        "calc_enterprise_value_quarterly",
        "calc_free_cash_flow_ttm",
        "calc_ev_to_fcf_quarterly",
    }
    if ev_fcf_inputs <= set(d.columns):
        expected_ev = (
            n("market_cap")
            + n("calc_debt_value_quarterly")
            - n("cash_and_cash_equivalents")
            - n("short_term_investments")
        )
        actual_ev = n("calc_enterprise_value_quarterly")
        add(
            "Enterprise value matches its displayed inputs",
            (actual_ev - expected_ev).abs() <= 1e-10,
            "EV includes market capitalization plus debt, less cash and short-term investments.",
            applicable=expected_ev.notna() & actual_ev.notna(),
        )

        quarter_num = (
            d["period_quarter"].astype(str).str.extract(r"(\d)")[0].astype(float)
        )
        quarter_index = d["period_year"].astype(float) * 4 + quarter_num - 1
        consecutive = quarter_index.groupby(symbols).diff().eq(1)
        complete_window = (
            consecutive
            & consecutive.groupby(symbols).shift(1).eq(True)
            & consecutive.groupby(symbols).shift(2).eq(True)
        )
        expected_fcf_ttm = n("free_cash_flow").groupby(symbols).transform(
            lambda series: series.rolling(4, min_periods=4).sum()
        ).where(complete_window)
        actual_fcf_ttm = n("calc_free_cash_flow_ttm")
        add(
            "Trailing-four-quarter FCF matches four consecutive quarters",
            (actual_fcf_ttm - expected_fcf_ttm).abs() <= 1e-10,
            applicable=expected_fcf_ttm.notna() & actual_fcf_ttm.notna(),
        )

        expected_multiple = expected_ev / expected_fcf_ttm.where(
            expected_fcf_ttm != 0
        )
        actual_multiple = n("calc_ev_to_fcf_quarterly")
        add(
            "Quarter-end EV/FCF matches EV divided by trailing FCF",
            (actual_multiple - expected_multiple).abs() <= 1e-10,
            applicable=expected_multiple.notna() & actual_multiple.notna(),
        )

    return pd.DataFrame(checks)


def null_report(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for group_name, fields in FIELD_GROUPS:
        for field in fields:
            if field.out not in frame.columns:
                continue
            series = frame[field.out]
            last = series.dropna().iloc[-1] if series.notna().any() else ""
            rows.append(
                {
                    "group": group_name,
                    "column": field.out,
                    "pct_null": round(100 * series.isna().mean(), 1),
                    "n_present": int(series.notna().sum()),
                    "last_value": last,
                }
            )

    from .valuations import VALUATION_COLUMNS

    for column in VALUATION_COLUMNS:
        if column not in frame.columns:
            continue
        series = frame[column]
        last = series.dropna().iloc[-1] if series.notna().any() else ""
        rows.append(
            {
                "group": "VALUATIONS",
                "column": column,
                "pct_null": round(100 * series.isna().mean(), 1),
                "n_present": int(series.notna().sum()),
                "last_value": last,
            }
        )
    return pd.DataFrame(rows)


def failed_checks(checks: pd.DataFrame) -> pd.DataFrame:
    if checks.empty:
        return checks
    return checks[checks["n_failed"] > 0].sort_values(
        "n_failed", ascending=False
    )
