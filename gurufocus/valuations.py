"""Attach selected historical values from the GuruFocus valuations endpoint."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .parsing import find_period_block, to_number, to_period_end


MARKET_CAP_COLUMN = "market_cap"
API_EV_FCF_COLUMN = (
    "valuations__valuation_ratios__enterprise_value_to_fcf"
)
API_SHARES_COLUMN = "valuations__per_share_data__shares_outstanding"
API_STOCK_PRICE_COLUMN = "valuations__per_share_data__month_end_stock_price"
API_DEBT_EQUITY_COLUMN = "valuations__ratios__debt_to_equity"

VALUATION_COLUMNS = (
    MARKET_CAP_COLUMN,
    API_EV_FCF_COLUMN,
    API_SHARES_COLUMN,
    API_STOCK_PRICE_COLUMN,
    API_DEBT_EQUITY_COLUMN,
)

_SOURCE_URL = "https://www.gurufocus.com/data-api/stocks/valuations"

_VALUATION_SPECS = (
    (
        MARKET_CAP_COLUMN,
        "Market Capitalization",
        "valuationand_quality",
        "mktcap",
        "Market capitalization in the API reporting units.",
    ),
    (
        API_EV_FCF_COLUMN,
        "Enterprise Value / Free Cash Flow (GuruFocus)",
        "valuation_ratios",
        "enterprise_value_to_fcf",
        "GuruFocus's reported cash-flow multiple.",
    ),
    (
        API_SHARES_COLUMN,
        "Shares Outstanding (GuruFocus valuations)",
        "per_share_data",
        "shares_outstanding",
        "Shares outstanding reported by the valuations endpoint.",
    ),
    (
        API_STOCK_PRICE_COLUMN,
        "Month-End Stock Price (GuruFocus valuations)",
        "per_share_data",
        "month_end_stock_price",
        "Month-end stock price reported by the valuations endpoint.",
    ),
    (
        API_DEBT_EQUITY_COLUMN,
        "Debt to Equity (GuruFocus valuations)",
        "ratios",
        "debt_to_equity",
        "Debt-to-equity ratio reported by the valuations endpoint.",
    ),
)


def _nested_number(row: dict[str, Any], section: str, key: str) -> float | None:
    block = row.get(section)
    if not isinstance(block, dict):
        return None
    return to_number(block.get(key))


def _coverage(found: dict[str, bool]) -> pd.DataFrame:
    rows = []
    for output, label, section, key, note in _VALUATION_SPECS:
        is_found = found.get(output, False)
        rows.append(
            {
                "group": "VALUATIONS",
                "requested_label": label,
                "output_column": output,
                "expected_section": section,
                "api_key_found": f"{section}::{key}" if is_found else "",
                "matched_via": key if is_found else "",
                "status": "OK" if is_found else "MISSING",
                "note": f"{note} Source: {_SOURCE_URL}",
            }
        )
    return pd.DataFrame(rows)


def attach_valuations(
    frame: pd.DataFrame,
    payload: dict[str, Any],
    *,
    period: str = "quarterly",
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Join the selected valuation fields by fiscal period-end date."""
    out = frame.copy()
    for column in VALUATION_COLUMNS:
        out[column] = np.nan

    block = find_period_block(payload, period) if payload else None
    rows = (
        block
        if isinstance(block, list)
        else ([block] if isinstance(block, dict) else [])
    )

    records: list[dict[str, Any]] = []
    raw_keys: set[str] = set()
    found = {column: False for column in VALUATION_COLUMNS}

    for row in rows:
        if not isinstance(row, dict):
            continue
        date = to_period_end(row.get("date"))
        if pd.isna(date):
            continue

        record: dict[str, Any] = {
            "fiscal_period_end_date": date.strftime("%Y-%m-%d")
        }
        for output, _label, section, key, _note in _VALUATION_SPECS:
            value = _nested_number(row, section, key)
            record[output] = value if value is not None else np.nan
            if value is not None:
                found[output] = True
                raw_keys.add(f"valuations::{section}::{key}")
        records.append(record)

    coverage = _coverage(found)
    if not records:
        return out, {
            "rows_available": 0,
            "rows_matched": 0,
            "coverage": coverage,
            "available_keys": sorted(raw_keys),
        }

    values = pd.DataFrame(records).drop_duplicates(
        subset=["fiscal_period_end_date"],
        keep="last",
    )
    out = out.drop(columns=list(VALUATION_COLUMNS)).merge(
        values,
        how="left",
        on="fiscal_period_end_date",
        validate="one_to_one",
        sort=False,
    )
    matched = int(out[MARKET_CAP_COLUMN].notna().sum())
    return out, {
        "rows_available": len(values),
        "rows_matched": matched,
        "coverage": coverage,
        "available_keys": sorted(raw_keys),
    }
