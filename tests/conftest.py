"""
תשתית לבדיקות.

כל הבדיקות רצות על נתונים סינתטיים או על JSON שמור — אף בדיקה לא פונה ל-API
ולא צורכת מהמכסה.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# JSON אמיתי שנשמר במטמון, אם קיים — מאפשר בדיקות אינטגרציה בלי רשת
CACHED_AAPL = ROOT / "data" / "raw" / "AAPL__fundamentals.json"


def make_row(
    date: str,
    *,
    total_assets: float = 1000.0,
    total_current_assets: float = 400.0,
    total_current_liabilities: float = 300.0,
    total_liabilities: float = 600.0,
    total_equity: float = 400.0,
    cash: float = 100.0,
    marketable: float = 50.0,
    short_term_debt: float = 20.0,
    long_term_debt: float = 80.0,
    net_ppe: float = 200.0,
    gross_ppe: float = 300.0,
    accumulated_depreciation: float = -100.0,
    goodwill: float = 30.0,
    intangibles: float = 80.0,
    ebit: float = 100.0,
    pretax: float = 90.0,
    tax: float = -20.0,          # מוסכמת GuruFocus: מס שלילי
    interest_expense: float = -2.0,
    revenue: float = 500.0,
    filing_date: str = "2020-01-15",
) -> dict:
    """בונה שורת תקופה במבנה המדויק של ה-API."""
    net_income = pretax + tax
    return {
        "date": date,
        "filing_date": filing_date,
        "balance_sheet": {
            "total_assets": total_assets,
            "total_current_assets": total_current_assets,
            "total_current_liabilities": total_current_liabilities,
            "total_liabilities": total_liabilities,
            "total_equity": total_equity,
            "total_stockholders_equity": total_equity,
            "cash_and_cash_equivalents": cash,
            "marke_table_securities": marketable,
            "cash_equivalents_marketable_securities": cash + marketable,
            "short_term_debt": short_term_debt,
            "short_term_debt_and_capital_lease_obligation": short_term_debt,
            "long_term_debt": long_term_debt,
            "long_term_debt_and_capital_lease_obligation": long_term_debt,
            "net_ppe": net_ppe,
            "gross_ppe": gross_ppe,
            "accumulated_depreciation": accumulated_depreciation,
            "good_will": goodwill,
            "intangibles": intangibles,
            "investments_and_advances": 0,
        },
        "income_statement": {
            "ebit": ebit,
            "operating_income": ebit,
            "pretax_income": pretax,
            "tax_provision": tax,
            "tax_rate": round(abs(tax) / pretax * 100, 2) if pretax > 0 else 0,
            "net_income": net_income,
            "revenue": revenue,
            "interest_expense": interest_expense,
        },
        "cashflow_statement": {
            "purchase_of_ppe": -30.0,
            "total_free_cash_flow": 70.0,
            "purchase_of_business": 0,
        },
    }


@pytest.fixture
def quarterly_payload() -> dict:
    """payload סינתטי: 12 רבעונים רצופים, 2020Q1 עד 2022Q4 ביישור."""
    months = ["03", "06", "09", "12"]
    rows = [
        make_row(f"{year}-{month}", filing_date=f"{year}-{month}-15")
        for year in (2020, 2021, 2022)
        for month in months
    ]
    return {
        "basic_information": {
            "company": "Test Corp", "currency": "USD",
            "exchange": "NAS", "symbol": "TEST",
        },
        "quarterly": rows,
        "annually": rows[::4],
        "stockid": "TEST01",
    }


@pytest.fixture
def real_payload() -> dict:
    """JSON אמיתי של AAPL מהמטמון. מדלג אם לא הורד עדיין."""
    if not CACHED_AAPL.exists():
        pytest.skip("אין JSON שמור — הריצו קודם: python run.py --tickers AAPL")
    return json.loads(CACHED_AAPL.read_text(encoding="utf-8"))
