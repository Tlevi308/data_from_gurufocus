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

    _add_decomposition_checks(d, add, n)
    _add_wacc_checks(d, add, n)

    return pd.DataFrame(checks)


def _add_wacc_checks(d: pd.DataFrame, add, n) -> None:
    """Verify the cost-of-capital identities on real output.

    These catch a mis-wired weight or a unit slip, which arithmetic alone would
    not reveal: a WACC of 0.0725 and a WACC of 7.25 are both plausible-looking
    numbers, and only one of them is a rate.
    """
    from .wacc import QUALITY_NO_DEBT, QUALITY_VALID

    weights = {"calc_wacc_equity_weight", "calc_wacc_debt_weight"}
    if weights <= set(d.columns):
        total = n("calc_wacc_equity_weight") + n("calc_wacc_debt_weight")
        add(
            "WACC capital weights sum to one",
            (total - 1).abs() <= 1e-10,
            applicable=total.notna(),
        )

    wacc_inputs = {
        "calc_wacc_equity_weight",
        "calc_wacc_debt_weight",
        "calc_wacc_cost_of_equity",
        "calc_wacc_after_tax_cost_of_debt",
        "calc_wacc_annual",
        "calc_wacc_quality_flag",
    }
    if wacc_inputs <= set(d.columns):
        # A debt-free company contributes no debt term at all, so the identity
        # is only meaningful where the cost of debt actually participates.
        priced = d["calc_wacc_quality_flag"].isin([QUALITY_VALID, QUALITY_NO_DEBT])
        expected = n("calc_wacc_equity_weight") * n("calc_wacc_cost_of_equity") + (
            n("calc_wacc_debt_weight") * n("calc_wacc_after_tax_cost_of_debt")
        ).where(n("calc_wacc_debt_weight") != 0, 0.0)
        actual = n("calc_wacc_annual")
        add(
            "WACC matches its weights and component costs",
            (actual - expected).abs() <= 1e-10,
            "E/(D+E) x Re + D/(D+E) x Rd x (1-T).",
            applicable=priced & expected.notna() & actual.notna(),
        )

    if {"calc_wacc_annual", "calc_wacc_quarterly"} <= set(d.columns):
        annual = n("calc_wacc_annual")
        quarterly = n("calc_wacc_quarterly")
        # Round-trip rather than re-deriving: compounding the quarterly rate
        # back has to land on the annual one.
        recovered = (1 + quarterly) ** 4 - 1
        add(
            "Quarterly WACC compounds back to the annual WACC",
            (recovered - annual).abs() <= 1e-10,
            applicable=annual.notna() & quarterly.notna(),
        )

    verdict_inputs = {
        "calc_roic_posttax_quarterly_ic_raw",
        "calc_roic_posttax_annualized_ic_raw",
        "calc_wacc_annual",
        "calc_wacc_quarterly",
        "calc_creates_value",
    }
    if verdict_inputs <= set(d.columns):
        # Annualisation is strictly increasing, so it cannot reorder a return
        # against a cost. If this ever fails, one of the two conversions is
        # wrong.
        quarterly_verdict = (
            n("calc_roic_posttax_quarterly_ic_raw") > n("calc_wacc_quarterly")
        )
        annual_verdict = (
            n("calc_roic_posttax_annualized_ic_raw") > n("calc_wacc_annual")
        )
        both_defined = (
            n("calc_roic_posttax_annualized_ic_raw").notna()
            & n("calc_roic_posttax_quarterly_ic_raw").notna()
            & n("calc_wacc_annual").notna()
            & n("calc_wacc_quarterly").notna()
        )
        add(
            "Value-creation verdict is the same annually and quarterly",
            quarterly_verdict == annual_verdict,
            "Annualisation is monotonic, so the sign of the spread is invariant.",
            applicable=both_defined,
        )


def _add_decomposition_checks(d: pd.DataFrame, add, n) -> None:
    """Verify the Shapley identities and the one-hot encoding on real output.

    Efficiency holds by construction, so these are not a numerical experiment:
    they are the cheapest possible detector of the decomposition being wired to
    the wrong columns, which arithmetic alone would not reveal.
    """
    from .decomposition import (
        NOPAT_COMBO_COLUMNS,
        RAW_COMBO_COLUMNS,
        ROIC_COMBO_COLUMNS,
        STATUS_VALID,
    )

    for label, change_column, contribution_columns in (
        (
            "NOPAT",
            "calc_nopat_change_quarterly",
            ("calc_nopat_ebit_contribution", "calc_nopat_tax_contribution"),
        ),
        (
            "Post-tax ROIC",
            "calc_roic_posttax_change_quarterly",
            (
                "calc_roic_ebit_contribution",
                "calc_roic_tax_contribution",
                "calc_roic_ic_contribution",
            ),
        ),
    ):
        required = {change_column, *contribution_columns}
        if not required <= set(d.columns):
            continue
        change = n(change_column)
        attributed = sum(n(column) for column in contribution_columns)
        # The tolerance is relative: a ROIC contribution is O(1e-2) but a NOPAT
        # contribution can be O(1e9), and one absolute epsilon cannot serve both.
        scale = pd.concat(
            [change.abs()] + [n(column).abs() for column in contribution_columns],
            axis=1,
        ).max(axis=1).clip(lower=1.0)
        add(
            f"{label} Shapley contributions sum to the total change",
            (change - attributed).abs() <= 1e-9 * scale,
            "Order-independent decomposition is exactly efficient.",
            applicable=change.notna() & attributed.notna(),
        )

    if "calc_roic_decomposition_status" not in d.columns:
        return
    nopat_valid = d.get("calc_nopat_decomposition_status") == STATUS_VALID
    roic_valid = d["calc_roic_decomposition_status"] == STATUS_VALID

    for label, columns, valid in (
        ("NOPAT effect", NOPAT_COMBO_COLUMNS, nopat_valid),
        ("Raw movement", RAW_COMBO_COLUMNS, roic_valid),
        ("ROIC effect", ROIC_COMBO_COLUMNS, roic_valid),
    ):
        if not set(columns) <= set(d.columns):
            continue
        row_total = d.loc[:, columns].apply(pd.to_numeric, errors="coerce").sum(axis=1)
        add(
            f"{label} combination selects exactly one column",
            row_total == valid.astype(int),
            "Classified rows set one indicator; unclassified rows set none.",
        )

    shares = [
        "calc_roic_ebit_absolute_share",
        "calc_roic_tax_absolute_share",
        "calc_roic_ic_absolute_share",
    ]
    if set(shares) <= set(d.columns):
        share_total = sum(n(column) for column in shares)
        add(
            "ROIC absolute contribution shares sum to one",
            (share_total - 1).abs() <= 1e-10,
            applicable=share_total.notna(),
        )

    if "calc_roic_offset_ratio" in d.columns:
        offset = n("calc_roic_offset_ratio")
        add(
            "ROIC offset ratio lies between zero and one",
            (offset >= 0) & (offset <= 1),
            "Guaranteed by the triangle inequality on the contributions.",
            applicable=offset.notna(),
        )


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
