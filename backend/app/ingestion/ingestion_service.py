from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ..database import db
from .yahoo_ingestion import (
    YahooConsentError,
    YahooTranscriptParseError,
    discover_transcripts,
    fetch_and_parse_transcript,
)


# Storage state path for Yahoo cookies (now handled by settings config)
BASE_DIR = Path(__file__).resolve().parents[2]  # Go up to backend/ directory
STORAGE_STATE_PATH = BASE_DIR / ".yahoo_cookies" / "storage_state.json"


class IngestionError(Exception):
    """Base exception for ingestion errors."""
    pass


class ConsentRequiredError(IngestionError):
    """Raised when Yahoo consent needs to be refreshed."""
    pass




def ingest_ticker(ticker: str, limit: int | None = None, delay_seconds: float = 1.0) -> dict[str, Any]:
    """
    Ingest earnings transcripts for a given ticker from Yahoo Finance.

    Args:
        ticker: Stock ticker symbol (e.g., "AAPL", "MSFT")
        limit: Maximum number of transcripts to ingest (None = ingest all discovered)
        delay_seconds: Delay between requests to avoid rate limiting

    Returns:
        Dict with ingestion results including count and any errors

    Raises:
        ConsentRequiredError: If Yahoo consent needs to be refreshed
        IngestionError: For other ingestion failures
    """
    results = {
        "ticker": ticker,
        "discovered": 0,
        "ingested": 0,
        "errors": [],
        "transcripts": [],  # Collect full transcript data for storage
    }

    # Discover transcripts
    try:
        metas = discover_transcripts(ticker, STORAGE_STATE_PATH)
        results["discovered"] = len(metas)

        if limit:
            metas = metas[:limit]

    except YahooConsentError as e:
        raise ConsentRequiredError(
            "Yahoo consent cookies expired. Please run the consent setup process."
        ) from e
    except Exception as e:
        raise IngestionError(f"Failed to discover transcripts: {type(e).__name__}: {e}") from e

    # Fetch each transcript
    for meta in metas:
        try:
            # Fetch and parse transcript
            tp = fetch_and_parse_transcript(meta.url, ticker, STORAGE_STATE_PATH)

            # Skip paywalled transcripts with no content
            if tp.paywalled and len(tp.turns) == 0:
                results["errors"].append({
                    "url": meta.url,
                    "stage": "paywall",
                    "detail": "Transcript is paywalled with no content available"
                })
                continue

            # Collect transcript data for storage
            transcript_data = {
                "url": meta.url,
                "meta": {
                    "quarter": meta.quarter,
                    "year": meta.year,
                    "event_id": meta.event_id,
                },
                "parsed": {
                    "event_id": tp.event_id,
                    "company_id": tp.company_id,
                    "version": tp.version,
                    "speaker_map": tp.speaker_map,
                    "turns": tp.turns,
                    "paywalled": tp.paywalled,
                }
            }
            results["transcripts"].append(transcript_data)
            results["ingested"] += 1

        except YahooConsentError as e:
            results["errors"].append({
                "url": meta.url,
                "stage": "consent",
                "detail": str(e)
            })
            # Stop processing if consent fails
            break

        except YahooTranscriptParseError as e:
            results["errors"].append({
                "url": meta.url,
                "stage": "parse",
                "detail": str(e)
            })

        except Exception as e:
            results["errors"].append({
                "url": meta.url,
                "stage": "other",
                "detail": f"{type(e).__name__}: {e}"
            })

        # Rate limiting delay
        if delay_seconds > 0:
            time.sleep(delay_seconds)

    # Store all results in PostgreSQL
    try:
        storage_result = db.store_ingestion(results)

        # Post-process transcripts: generate embeddings and extract organizations
        transcripts_to_process = storage_result.get("transcripts_to_process", [])

        for item in transcripts_to_process:
            transcript_id = item["transcript_id"]
            formatted_turns = item["formatted_turns"]
            full_text = item["full_text"]

            # Generate embeddings
            try:
                from ..qa import embedding_service
                stats = embedding_service.embed_transcript(transcript_id, formatted_turns)
                print(f"✓ Generated {stats['chunks_created']} chunks with embeddings for transcript {transcript_id}")
            except Exception as e:
                print(f"Warning: Failed to generate embeddings for transcript {transcript_id}: {e}")
                import traceback
                traceback.print_exc()

            # Extract organizations using NLP
            try:
                from ..nlp import nlp_service
                detected_orgs = nlp_service.extract_organizations(full_text)

                # Store detected organizations
                conn = db.connect()
                cursor = conn.cursor()
                try:
                    for org in detected_orgs:
                        cursor.execute("""
                            INSERT INTO detected_organizations (transcript_id, organization, source)
                            VALUES (%s, %s, 'nlp')
                            ON CONFLICT (transcript_id, organization) DO NOTHING
                        """, (transcript_id, org))
                    conn.commit()
                    print(f"✓ Detected {len(detected_orgs)} organizations via NLP for transcript {transcript_id}")
                finally:
                    cursor.close()
                    conn.close()
            except Exception as e:
                print(f"Warning: Failed to extract organizations for transcript {transcript_id}: {e}")
                import traceback
                traceback.print_exc()

    except Exception as e:
        # Log error but don't fail the entire ingestion
        import traceback
        print(f"Warning: Failed to store ingestion results in database: {e}")
        traceback.print_exc()

    return results
