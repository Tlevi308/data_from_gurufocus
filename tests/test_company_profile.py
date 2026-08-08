"""Tests for GuruFocus Profile classification extraction."""

from __future__ import annotations

import pandas as pd

from gurufocus.client import GuruFocusClient, PATH_PROFILE
from gurufocus.company_profile import (
    attach_profile_classification,
    extract_profile_classification,
)


def test_extracts_documented_general_sector_and_industry():
    payload = {
        "general": {
            "company": "Apple Inc",
            "sector": "Technology",
            "industry": "Hardware",
        }
    }
    assert extract_profile_classification(payload) == {
        "sector": "Technology",
        "industry": "Hardware",
    }


def test_supports_wrapped_profile_and_missing_values():
    assert extract_profile_classification(
        {"data": {"general": {"sector": "Healthcare"}}}
    ) == {"sector": "Healthcare", "industry": None}


def test_attaches_classification_to_every_period_row():
    frame = pd.DataFrame({"symbol": ["AAPL", "AAPL"], "ebit": [1.0, 2.0]})
    result, report = attach_profile_classification(
        frame,
        {"general": {"sector": "Technology", "industry": "Hardware"}},
    )
    assert result["sector"].tolist() == ["Technology", "Technology"]
    assert result["industry"].tolist() == ["Hardware", "Hardware"]
    assert report["fields_found"] == 2
    assert report["fields_missing"] == []


def test_client_profile_uses_the_profile_endpoint(monkeypatch):
    client = GuruFocusClient("secret", cache_dir=None)
    seen = []

    def fake_get(path, params=None):
        seen.append(path)
        return {"general": {"sector": "Technology", "industry": "Hardware"}}

    monkeypatch.setattr(client, "_get", fake_get)
    payload = client.profile("AAPL")
    assert seen == [PATH_PROFILE.format(symbol="AAPL")]
    assert payload["general"]["sector"] == "Technology"
