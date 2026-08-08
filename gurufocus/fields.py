"""Fundamentals field contract used by parsing and output generation."""

from __future__ import annotations

from dataclasses import dataclass


SECTION_BALANCE = "balance_sheet"
SECTION_INCOME = "income_statement"
SECTION_CASHFLOW = "cashflow_statement"
SECTION_ROOT = ""
SECTION_META = "basic_information"


@dataclass(frozen=True)
class Field:
    out: str
    label: str
    api_key: str
    section: str = SECTION_ROOT
    aliases: tuple[str, ...] = ()
    numeric: bool = True
    note: str = ""
    flow: bool = False

    @property
    def candidates(self) -> list[str]:
        return [self.api_key, *self.aliases]


_F = Field


FIELDS_KEYS: list[Field] = [
    _F(
        "symbol",
        "Symbol",
        "symbol",
        SECTION_META,
        ("ticker",),
        numeric=False,
        note="Filled from the request when absent from basic_information.",
    ),
    _F("company", "Company", "company", SECTION_META, (), numeric=False),
    _F(
        "fiscal_period_end_date",
        "Fiscal Period End",
        "date",
        SECTION_ROOT,
        ("period_end_date", "fiscal_year"),
        numeric=False,
        note="GuruFocus returns YYYY-MM; parsing converts this to month end.",
    ),
    _F(
        "filing_date",
        "Filing Date",
        "filing_date",
        SECTION_ROOT,
        ("date_filed",),
        numeric=False,
    ),
]


FIELDS_CORE: list[Field] = [
    _F(
        "total_current_assets",
        "Total Current Assets",
        "total_current_assets",
        SECTION_BALANCE,
    ),
    _F(
        "cash_and_cash_equivalents",
        "Cash And Cash Equivalents",
        "cash_and_cash_equivalents",
        SECTION_BALANCE,
        ("cash",),
    ),
    _F(
        "short_term_investments",
        "Short-Term Investments (Marketable Securities)",
        "marke_table_securities",
        SECTION_BALANCE,
        ("marketable_securities",),
    ),
    _F(
        "total_current_liabilities",
        "Total Current Liabilities",
        "total_current_liabilities",
        SECTION_BALANCE,
    ),
    _F(
        "short_term_debt",
        "Short-Term Debt",
        "short_term_debt",
        SECTION_BALANCE,
    ),
    _F(
        "net_ppe",
        "Property, Plant and Equipment (Net)",
        "net_ppe",
        SECTION_BALANCE,
        ("net_property_plant_and_equipment",),
    ),
    _F(
        "goodwill",
        "Goodwill",
        "good_will",
        SECTION_BALANCE,
        ("goodwill",),
    ),
    _F(
        "intangible_assets",
        "Intangible Assets",
        "intangibles",
        SECTION_BALANCE,
        ("intangible_assets",),
    ),
]


FIELDS_SUPPORT: list[Field] = [
    _F(
        "short_term_debt_and_capital_lease",
        "Short-Term Debt & Capital Lease Obligation",
        "short_term_debt_and_capital_lease_obligation",
        SECTION_BALANCE,
    ),
    _F(
        "long_term_debt_and_capital_lease",
        "Long-Term Debt & Capital Lease Obligation",
        "long_term_debt_and_capital_lease_obligation",
        SECTION_BALANCE,
    ),
    _F(
        "total_assets",
        "Total Assets",
        "total_assets",
        SECTION_BALANCE,
    ),
    _F(
        "total_liabilities",
        "Total Liabilities",
        "total_liabilities",
        SECTION_BALANCE,
    ),
    _F(
        "equity",
        "Equity",
        "total_equity",
        SECTION_BALANCE,
    ),
    _F(
        "total_stockholders_equity",
        "Total Stockholders Equity",
        "total_stockholders_equity",
        SECTION_BALANCE,
    ),
    _F("ebit", "EBIT", "ebit", SECTION_INCOME, flow=True),
    _F(
        "pretax_income",
        "Pretax Income",
        "pretax_income",
        SECTION_INCOME,
        ("income_before_tax", "ebt"),
        flow=True,
    ),
    _F(
        "tax_provision",
        "Tax Provision",
        "tax_provision",
        SECTION_INCOME,
        ("tax_expense", "income_tax_expense"),
        flow=True,
        note="GuruFocus records tax expense with a negative sign.",
    ),
    _F(
        "interest_expense",
        "Interest Expense",
        "interest_expense",
        SECTION_INCOME,
        (),
        flow=True,
        note=(
            "GuruFocus records interest expense with a negative sign. Reported "
            "as zero in many quarters even when interest-bearing debt exists, "
            "so the cost of debt carries a quality flag."
        ),
    ),
    _F(
        "free_cash_flow",
        "Free Cash Flow",
        "total_free_cash_flow",
        SECTION_CASHFLOW,
        ("free_cash_flow",),
        flow=True,
    ),
]


FIELDS_RATIOS: list[Field] = []

# Legacy SEC module compatibility; these fields are excluded from the pipeline output.
FILING_FIELDS = ("form_type", "accession_number", "cik", "filing_url")

FIELD_GROUPS: list[tuple[str, list[Field]]] = [
    ("KEYS", FIELDS_KEYS),
    ("CORE", FIELDS_CORE),
    ("SUPPORT", FIELDS_SUPPORT),
    ("RATIOS", FIELDS_RATIOS),
]

ALL_FIELDS: list[Field] = [field for _, group in FIELD_GROUPS for field in group]

_dupes = {
    field.out
    for field in ALL_FIELDS
    if [candidate.out for candidate in ALL_FIELDS].count(field.out) > 1
}
if _dupes:
    raise RuntimeError(f"Duplicate output fields: {sorted(_dupes)}")

TEXT_FIELDS = frozenset(
    field.out for field in ALL_FIELDS if not field.numeric
) | frozenset(FILING_FIELDS)


def field_by_out(name: str) -> Field | None:
    return next((field for field in ALL_FIELDS if field.out == name), None)


def output_columns() -> list[str]:
    return [field.out for field in ALL_FIELDS]
