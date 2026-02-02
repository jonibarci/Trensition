"""
Shared pytest fixtures for TrendTracker tests.
"""

import json
from pathlib import Path

import pytest

from app.ingestion.yahoo_ingestion import TranscriptMeta, TranscriptParsed


@pytest.fixture
def sample_transcript_meta() -> TranscriptMeta:
    """Sample TranscriptMeta for testing."""
    return TranscriptMeta(
        ticker="AAPL",
        url="https://finance.yahoo.com/quote/AAPL/earnings/AAPL-Q4-2025-earnings_call-369370.html",
        quarter="Q4",
        year=2025,
        event_id=369370
    )


@pytest.fixture
def sample_transcript_parsed() -> TranscriptParsed:
    """Sample TranscriptParsed with complete data."""
    return TranscriptParsed(
        ticker="AAPL",
        url="https://finance.yahoo.com/quote/AAPL/earnings/AAPL-Q4-2025-earnings_call-369370.html",
        event_id=369370,
        company_id=4742,
        version="1.0.0",
        speaker_map={
            0: {"name": "Tim Cook", "role": "CEO", "company": "Apple Inc."},
            1: {"name": "Luca Maestri", "role": "CFO", "company": "Apple Inc."},
            2: {"name": "Operator", "role": None, "company": None}
        },
        turns=[
            {"speaker": 2, "text": "Welcome to Apple's Q4 2025 earnings call.", "start": 0.0, "end": 5.0},
            {"speaker": 0, "text": "Revenue was $95 billion, up 8% year-over-year.", "start": 5.1, "end": 45.0}
        ],
        paywalled=False
    )


@pytest.fixture
def sample_transcriptcontent_json() -> dict:
    """Sample transcriptContent JSON from Yahoo Finance."""
    return {
        "event_id": 369370,
        "company_id": 4742,
        "version": "1.0.0",
        "speaker_mapping": [
            {"speaker": 0, "speaker_data": {"name": "Tim Cook", "role": "CEO", "company": "Apple Inc."}},
            {"speaker": 1, "speaker_data": {"name": "Luca Maestri", "role": "CFO", "company": "Apple Inc."}}
        ],
        "transcript": [
            {"speaker": 0, "text": "Welcome to the call.", "start": 0.0, "end": 5.0},
            {"speaker": 1, "text": "Revenue was strong.", "start": 5.1, "end": 10.0}
        ]
    }


@pytest.fixture
def tmp_storage_state(tmp_path) -> Path:
    """Temporary Playwright storage_state.json with sample cookies."""
    storage_state = {
        "cookies": [{
            "name": "test_cookie",
            "value": "test_value",
            "domain": ".yahoo.com",
            "path": "/",
            "expires": 2000000000,
            "httpOnly": True,
            "secure": True
        }]
    }

    state_path = tmp_path / "storage_state.json"
    state_path.write_text(json.dumps(storage_state))
    return state_path
