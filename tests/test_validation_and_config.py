"""Tests for validation, settings, and the legacy filings helper."""

from __future__ import annotations

import os

import pandas as pd
import pytest

from gurufocus.calculations import add_calculated
from gurufocus.config import Settings, _load_dotenv
from gurufocus.extract import build_frame
from gurufocus.filings import attach_filings
from gurufocus.validation import infer_period, null_report, quality_checks


@pytest.fixture
def panel(quarterly_payload):
    frame, _ = build_frame(quarterly_payload, "TEST")
    frame["market_cap"] = 1000.0
    return add_calculated(frame)


class TestQualityChecks:
    def test_clean_data_passes(self, panel):
        checks = quality_checks(panel)
        assert not checks.empty
        assert (checks["n_failed"] == 0).all(), checks[
            checks["n_failed"] > 0
        ].to_string()

    def test_detects_current_assets_inconsistency(self, quarterly_payload):
        frame, _ = build_frame(quarterly_payload, "TEST")
        frame.loc[0, "total_current_assets"] = 1.0
        checks = quality_checks(frame)
        assert (checks["n_failed"] > 0).any()

    def test_detects_bad_debt_ratio(self, panel):
        panel.loc[1, "calc_debt_to_equity_quarterly"] = 99.0
        checks = quality_checks(panel)
        row = checks[checks["check"].str.contains("Debt-to-equity")]
        assert int(row["n_failed"].iloc[0]) == 1

    def test_multi_ticker_panel_no_false_failures(self, quarterly_payload):
        a, _ = build_frame(quarterly_payload, "AAA")
        b, _ = build_frame(quarterly_payload, "BBB")
        combined = pd.concat([a, b], ignore_index=True)
        checks = quality_checks(combined)
        for pattern in ("Consecutive", "unique", "jump"):
            selected = checks[checks["check"].str.contains(pattern)]
            assert not selected.empty
            assert (selected["n_failed"] == 0).all()

    def test_annual_gaps_not_flagged_as_failures(self, quarterly_payload):
        frame, _ = build_frame(quarterly_payload, "TEST", period="annually")
        checks = quality_checks(frame, period="annually")
        gaps = checks[checks["check"].str.contains("Consecutive")]
        assert int(gaps["n_failed"].iloc[0]) == 0

    def test_period_inferred_when_not_given(self, quarterly_payload):
        annual, _ = build_frame(quarterly_payload, "TEST", period="annually")
        quarterly, _ = build_frame(quarterly_payload, "TEST")
        assert infer_period(annual) == "annually"
        assert infer_period(quarterly) == "quarterly"


class TestNullReport:
    def test_removed_columns_are_not_reported(self, panel):
        report = null_report(panel)
        assert not {
            "currency",
            "exchange",
            "reporting_unit",
            "ebitda",
        } & set(report["column"])

    def test_reports_requested_equity_inputs(self, panel):
        report = null_report(panel)
        assert {
            "total_assets",
            "total_liabilities",
            "equity",
            "total_stockholders_equity",
        } <= set(report["column"])

    def test_reports_present_column(self, panel):
        report = null_report(panel)
        row = report[report["column"] == "ebit"]
        assert float(row["pct_null"].iloc[0]) == 0.0


class TestFilings:
    def test_exact_match_on_filing_date(self, quarterly_payload):
        frame, _ = build_frame(quarterly_payload, "TEST")
        records = [
            {
                "filing_date": "2020-03-15",
                "form_type": "10-Q",
                "accession_number": "0001",
                "cik": "0000320193",
                "filing_url": "https://example.test/1",
            }
        ]
        result, report = attach_filings(frame, records)
        assert report["matched_exact"] == 1
        matched = result[result["filing_match"] == "exact"]
        assert matched["form_type"].iloc[0] == "10-Q"

    def test_non_periodic_forms_ignored(self, quarterly_payload):
        frame, _ = build_frame(quarterly_payload, "TEST")
        records = [
            {
                "filing_date": "2020-03-15",
                "form_type": "8-K",
                "accession_number": "0002",
            }
        ]
        result, report = attach_filings(frame, records)
        assert report["filings_available"] == 0
        assert (result["filing_match"] == "none").all()

    def test_empty_filings_is_safe(self, quarterly_payload):
        frame, _ = build_frame(quarterly_payload, "TEST")
        result, report = attach_filings(frame, [])
        assert report["matched_exact"] == 0
        assert "form_type" in result.columns
        assert len(result) == len(frame)


class TestSettings:
    def test_rejects_annuals_typo(self):
        settings = Settings(api_key="x" * 20, period="annuals")
        with pytest.raises(ValueError, match="annually"):
            settings.validate()

    def test_accepts_annually(self):
        Settings(api_key="x" * 20, period="annually").validate()

    def test_rejects_placeholder_key(self):
        with pytest.raises(ValueError, match="placeholder"):
            Settings(api_key="YOUR_API_KEY").validate()

    def test_rejects_empty_key(self):
        with pytest.raises(ValueError):
            Settings(api_key="").validate()

    def test_masked_key_never_leaks(self):
        secret = "abcdef123456:7890fedcba"
        masked = Settings(api_key=secret).masked_key()
        assert secret not in masked
        assert masked.startswith("abcd")

    def test_dotenv_strips_bom(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_bytes(b"\xef\xbb\xbfGURUFOCUS_API_KEY=secret123\n")
        monkeypatch.delenv("GURUFOCUS_API_KEY", raising=False)
        _load_dotenv(env_file)
        assert os.environ.get("GURUFOCUS_API_KEY") == "secret123"

    def test_rejects_inverted_date_range(self):
        settings = Settings(
            api_key="x" * 20,
            start_date="2024-01-01",
            end_date="2020-01-01",
        )
        with pytest.raises(ValueError, match="end_date"):
            settings.validate()

    def test_accepts_open_ended_range(self):
        Settings(
            api_key="x" * 20, start_date="2020-01-01", end_date=""
        ).validate()
