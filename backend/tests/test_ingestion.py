"""
Essential tests for ingestion module.

Tests the core ingestion flow: discovery → parsing → storage.
"""

import json
from unittest.mock import Mock

import pytest

from app.ingestion.yahoo_ingestion import (
    TranscriptMeta,
    TranscriptParsed,
    YahooConsentError,
    YahooTranscriptParseError,
    fetch_and_parse_transcript,
)
from app.ingestion.ingestion_service import ingest_ticker, ConsentRequiredError


class DummyResponse:
    """Mock HTTP response."""
    def __init__(self, url, text="", json_obj=None):
        self.url = url
        self.text = text
        self._json = json_obj

    def json(self):
        if self._json is None:
            raise ValueError("No JSON")
        return self._json


class TestIngestion:
    """Core ingestion tests."""

    def test_fetch_transcript_success(self, monkeypatch, tmp_storage_state, sample_transcriptcontent_json):
        """Test successful transcript fetching via XHR."""
        def fake_get(self, url, *args, **kwargs):
            if url.endswith(".html"):
                return DummyResponse(url, text='<div data-url="/xhr/transcript?eventId=1&amp;crumb=x"></div>')
            return DummyResponse(url, json_obj={"status": 200, "body": json.dumps({"transcriptContent": sample_transcriptcontent_json})})

        monkeypatch.setattr("requests.Session.get", fake_get)

        result = fetch_and_parse_transcript("https://finance.yahoo.com/x.html", "AAPL", tmp_storage_state)

        assert result.event_id == 369370
        assert len(result.turns) == 2

    def test_fetch_transcript_consent_error(self, monkeypatch, tmp_storage_state):
        """Test consent error handling."""
        def fake_get(self, url, *args, **kwargs):
            return DummyResponse("https://consent.yahoo.com", text="")

        monkeypatch.setattr("requests.Session.get", fake_get)

        with pytest.raises(YahooConsentError):
            fetch_and_parse_transcript("https://finance.yahoo.com/x.html", "AAPL", tmp_storage_state)

    def test_fetch_transcript_parse_error(self, monkeypatch, tmp_storage_state):
        """Test parse error when no transcript found."""
        def fake_get(self, url, *args, **kwargs):
            return DummyResponse(url, text="<html>no transcript</html>")

        monkeypatch.setattr("requests.Session.get", fake_get)

        with pytest.raises(YahooTranscriptParseError):
            fetch_and_parse_transcript("https://finance.yahoo.com/x.html", "AAPL", tmp_storage_state)

    def test_paywall_detection(self, monkeypatch, tmp_storage_state):
        """Test paywall detection (many speakers, few turns)."""
        transcript_tc = {
            "event_id": 1,
            "company_id": 2,
            "version": "1.0.0",
            "speaker_mapping": [
                {"speaker": i, "speaker_data": {"name": f"Speaker{i}", "role": "Role", "company": "Co"}}
                for i in range(5)
            ],
            "transcript": [{"speaker": 0, "text": "Stub"}],
        }

        def fake_get(self, url, *args, **kwargs):
            if url.endswith(".html"):
                return DummyResponse(url, text='<div data-url="/xhr/transcript?eventId=1&amp;crumb=x"></div>')
            return DummyResponse(url, json_obj={"status": 200, "body": json.dumps({"transcriptContent": transcript_tc})})

        monkeypatch.setattr("requests.Session.get", fake_get)

        result = fetch_and_parse_transcript("https://finance.yahoo.com/x.html", "AAPL", tmp_storage_state)
        assert result.paywalled is True

    def test_ingest_ticker_success(self, monkeypatch):
        """Test full ingestion pipeline."""
        def mock_discover(ticker, storage_path):
            return [TranscriptMeta(ticker=ticker, url="https://example.com", quarter="Q4", year=2025, event_id=1)]

        def mock_fetch(url, ticker, storage_path):
            return TranscriptParsed(
                ticker=ticker, url=url, event_id=1, company_id=1, version="1.0.0",
                speaker_map={0: {"name": "CEO", "role": "CEO", "company": "Co"}},
                turns=[{"speaker": 0, "text": "Hello"}], paywalled=False
            )

        monkeypatch.setattr("app.ingestion.ingestion_service.discover_transcripts", mock_discover)
        monkeypatch.setattr("app.ingestion.ingestion_service.fetch_and_parse_transcript", mock_fetch)
        monkeypatch.setattr("app.ingestion.ingestion_service.db.store_ingestion", lambda x: None)

        result = ingest_ticker("AAPL", delay_seconds=0)

        assert result["ticker"] == "AAPL"
        assert result["discovered"] == 1
        assert result["ingested"] == 1

    def test_ingest_ticker_with_limit(self, monkeypatch):
        """Test ingestion respects limit parameter."""
        def mock_discover(ticker, storage_path):
            return [TranscriptMeta(ticker=ticker, url=f"https://example.com/{i}", quarter="Q1", year=2025, event_id=i) for i in range(5)]

        def mock_fetch(url, ticker, storage_path):
            return TranscriptParsed(ticker=ticker, url=url, event_id=1, company_id=1, version="1.0.0",
                                  speaker_map={}, turns=[{"speaker": 0, "text": "Hello"}], paywalled=False)

        monkeypatch.setattr("app.ingestion.ingestion_service.discover_transcripts", mock_discover)
        monkeypatch.setattr("app.ingestion.ingestion_service.fetch_and_parse_transcript", mock_fetch)
        monkeypatch.setattr("app.ingestion.ingestion_service.db.store_ingestion", lambda x: None)

        result = ingest_ticker("AAPL", limit=2, delay_seconds=0)

        assert result["discovered"] == 5
        assert result["ingested"] == 2

    def test_ingest_ticker_skips_paywalled_no_content(self, monkeypatch):
        """Test that paywalled transcripts with no content are skipped."""
        def mock_discover(ticker, storage_path):
            return [TranscriptMeta(ticker=ticker, url="https://example.com", quarter="Q1", year=2025, event_id=1)]

        def mock_fetch(url, ticker, storage_path):
            return TranscriptParsed(ticker=ticker, url=url, event_id=1, company_id=1, version="1.0.0",
                                  speaker_map={}, turns=[], paywalled=True)

        monkeypatch.setattr("app.ingestion.ingestion_service.discover_transcripts", mock_discover)
        monkeypatch.setattr("app.ingestion.ingestion_service.fetch_and_parse_transcript", mock_fetch)
        monkeypatch.setattr("app.ingestion.ingestion_service.db.store_ingestion", lambda x: None)

        result = ingest_ticker("AAPL", delay_seconds=0)

        assert result["ingested"] == 0
        assert len(result["errors"]) == 1
        assert result["errors"][0]["stage"] == "paywall"

    def test_ingest_ticker_consent_error_raises(self, monkeypatch):
        """Test that consent errors during discovery raise ConsentRequiredError."""
        def mock_discover(ticker, storage_path):
            raise YahooConsentError("Consent required")

        monkeypatch.setattr("app.ingestion.ingestion_service.discover_transcripts", mock_discover)

        with pytest.raises(ConsentRequiredError):
            ingest_ticker("AAPL", delay_seconds=0)

    def test_ingest_ticker_parse_error_continues(self, monkeypatch):
        """Test that parse errors are logged but processing continues."""
        def mock_discover(ticker, storage_path):
            return [TranscriptMeta(ticker=ticker, url=f"https://example.com/{i}", quarter="Q1", year=2025, event_id=i) for i in range(3)]

        call_count = 0
        def mock_fetch(url, ticker, storage_path):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise YahooTranscriptParseError("Parse failed")
            return TranscriptParsed(ticker=ticker, url=url, event_id=1, company_id=1, version="1.0.0",
                                  speaker_map={}, turns=[{"speaker": 0, "text": "Hello"}], paywalled=False)

        monkeypatch.setattr("app.ingestion.ingestion_service.discover_transcripts", mock_discover)
        monkeypatch.setattr("app.ingestion.ingestion_service.fetch_and_parse_transcript", mock_fetch)
        monkeypatch.setattr("app.ingestion.ingestion_service.db.store_ingestion", lambda x: None)

        result = ingest_ticker("AAPL", delay_seconds=0)

        assert result["discovered"] == 3
        assert result["ingested"] == 2
        assert len(result["errors"]) == 1
        assert result["errors"][0]["stage"] == "parse"

    def test_dataclass_immutability(self, sample_transcript_parsed):
        """Test that dataclasses are frozen (immutable)."""
        with pytest.raises(AttributeError):
            sample_transcript_parsed.ticker = "MSFT"
