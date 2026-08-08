"""Extraction of company classification fields from GuruFocus Profile."""

from __future__ import annotations

from typing import Any

import pandas as pd


PROFILE_COLUMNS = ("sector", "industry")


def _nonempty_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def extract_profile_classification(payload: dict) -> dict[str, str | None]:
    """Return sector and industry from a GuruFocus Profile response.

    GuruFocus currently returns both fields below ``general``.  The additional
    ``data``/``result`` unwrapping and ``basic_information`` fallback keep the
    parser safe if the API wraps the documented response or relocates identity
    fields without making us scan unrelated keys such as price-sector metrics.
    """
    roots = [payload]
    for wrapper in ("data", "result"):
        wrapped = payload.get(wrapper)
        if isinstance(wrapped, dict):
            roots.append(wrapped)

    sections: list[dict] = []
    for root in roots:
        for section_name in ("general", "basic_information"):
            section = root.get(section_name)
            if isinstance(section, dict):
                sections.append(section)

    return {
        column: next(
            (
                value
                for section in sections
                if (value := _nonempty_text(section.get(column))) is not None
            ),
            None,
        )
        for column in PROFILE_COLUMNS
    }


def attach_profile_classification(
    frame: pd.DataFrame,
    payload: dict,
) -> tuple[pd.DataFrame, dict]:
    """Attach the ticker-level classification to every financial-period row."""
    out = frame.copy()
    classification = extract_profile_classification(payload)
    for column in PROFILE_COLUMNS:
        out[column] = pd.Series(
            classification[column],
            index=out.index,
            dtype="string",
        )

    missing = [
        column for column, value in classification.items() if value is None
    ]
    report = {
        **classification,
        "fields_found": len(PROFILE_COLUMNS) - len(missing),
        "fields_total": len(PROFILE_COLUMNS),
        "fields_missing": missing,
    }
    return out, report
