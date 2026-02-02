from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Optional

# DATABASE_URL env var required: postgres://user:pass@localhost:5432/earnings
try:
    import psycopg
    from psycopg.rows import dict_row
    PSYCOPG_VERSION = 3
except ImportError:
    try:
        import psycopg2
        import psycopg2.extras
        PSYCOPG_VERSION = 2
    except ImportError:
        raise ImportError(
            "Neither psycopg (v3) nor psycopg2 (v2) found. "
            "Install with: pip install psycopg[binary] or pip install psycopg2-binary"
        )


def get_connection_string() -> str:
    """Get DATABASE_URL from environment."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise ValueError("DATABASE_URL environment variable is required")
    return url


def connect():
    """Connect to PostgreSQL database."""
    conn_str = get_connection_string()
    if PSYCOPG_VERSION == 3:
        conn = psycopg.connect(conn_str, row_factory=dict_row)
    else:
        conn = psycopg2.connect(conn_str)
        conn.cursor_factory = psycopg2.extras.RealDictCursor
    return conn


def init_schema(conn=None) -> None:
    """Create all database tables if they don't exist."""
    owns_conn = conn is None
    if conn is None:
        conn = connect()

    cursor = conn.cursor()

    # Enable pgvector extension for embeddings
    cursor.execute("""
        CREATE EXTENSION IF NOT EXISTS vector
    """)

    # Create tables in dependency order
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS companies (
            id BIGSERIAL PRIMARY KEY,
            ticker TEXT UNIQUE NOT NULL,
            name TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ingestion_runs (
            id BIGSERIAL PRIMARY KEY,
            company_id BIGINT REFERENCES companies(id),
            retrieved_at_utc TIMESTAMPTZ NOT NULL,
            discovered INTEGER NOT NULL,
            fetched_count INTEGER NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS earnings_events (
            id BIGSERIAL PRIMARY KEY,
            company_id BIGINT NOT NULL REFERENCES companies(id),
            fiscal_year INTEGER,
            fiscal_quarter SMALLINT,
            yahoo_event_id BIGINT,
            yahoo_company_id BIGINT,
            source_url TEXT NOT NULL,
            paywalled BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    # Add unique constraints for earnings_events
    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_events_yahoo_event_id
        ON earnings_events(company_id, yahoo_event_id)
        WHERE yahoo_event_id IS NOT NULL
    """)

    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_events_source_url
        ON earnings_events(company_id, source_url)
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transcripts (
            id BIGSERIAL PRIMARY KEY,
            event_id BIGINT UNIQUE NOT NULL REFERENCES earnings_events(id) ON DELETE CASCADE,
            parser_version TEXT,
            turn_count INTEGER NOT NULL,
            char_count INTEGER NOT NULL,
            full_text TEXT,
            full_text_tsv TSVECTOR,
            raw_transcriptcontent JSONB,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    # GIN index for full-text search
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_transcripts_tsv
        ON transcripts USING GIN(full_text_tsv)
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS speakers (
            id BIGSERIAL PRIMARY KEY,
            event_id BIGINT NOT NULL REFERENCES earnings_events(id) ON DELETE CASCADE,
            yahoo_speaker_id INTEGER NOT NULL,
            name TEXT,
            role TEXT,
            org TEXT,
            source TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(event_id, yahoo_speaker_id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transcript_turns (
            id BIGSERIAL PRIMARY KEY,
            transcript_id BIGINT NOT NULL REFERENCES transcripts(id) ON DELETE CASCADE,
            turn_index INTEGER NOT NULL,
            yahoo_speaker_id INTEGER,
            text TEXT NOT NULL,
            start_sec DOUBLE PRECISION,
            end_sec DOUBLE PRECISION,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(transcript_id, turn_index)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ingestion_errors (
            id BIGSERIAL PRIMARY KEY,
            run_id BIGINT REFERENCES ingestion_runs(id) ON DELETE CASCADE,
            url TEXT NOT NULL,
            stage TEXT NOT NULL,
            error TEXT NOT NULL,
            detail TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    # Transcript chunks for RAG
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transcript_chunks (
            id BIGSERIAL PRIMARY KEY,
            transcript_id BIGINT NOT NULL REFERENCES transcripts(id) ON DELETE CASCADE,
            turn_index INTEGER NOT NULL,
            speaker_name TEXT,
            text TEXT NOT NULL,
            embedding vector(1536),
            metadata JSONB,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    # HNSW index for fast vector similarity search
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_chunks_embedding
        ON transcript_chunks USING hnsw (embedding vector_cosine_ops)
    """)

    # Index for filtering by transcript
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_chunks_transcript
        ON transcript_chunks(transcript_id)
    """)

    # Detected organizations from NLP
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS detected_organizations (
            id BIGSERIAL PRIMARY KEY,
            transcript_id BIGINT NOT NULL REFERENCES transcripts(id) ON DELETE CASCADE,
            organization TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'nlp',
            created_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(transcript_id, organization)
        )
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_detected_orgs_transcript
        ON detected_organizations(transcript_id)
    """)

    conn.commit()
    cursor.close()

    if owns_conn:
        conn.close()


def store_ingestion(result: dict) -> dict[str, Any]:
    """
    Store ingestion results into PostgreSQL.

    Expected result format from ingest_ticker():
    {
        "ticker": str,
        "discovered": int,
        "ingested": int,
        "errors": [{"url": str, "stage": str, "detail": str}],
        "transcripts": [  # Added by ingestion service
            {
                "url": str,
                "meta": {"quarter": str|None, "year": int|None, "event_id": int|None},
                "parsed": {
                    "event_id": int|None,
                    "company_id": int|None,
                    "version": str|None,
                    "speaker_map": {int: {"name": str, "role": str, "company": str}},
                    "turns": [{"speaker": int, "text": str, "start": float, "end": float}],
                    "paywalled": bool
                }
            }
        ]
    }

    Returns:
        Dict with transcript processing info: {"transcripts_to_process": [(transcript_id, formatted_turns), ...]}
    """
    conn = connect()
    cursor = conn.cursor()

    try:
        # Start transaction
        ticker = result["ticker"]
        discovered = result["discovered"]
        fetched_count = result.get("ingested", 0)
        errors = result.get("errors", [])
        transcripts = result.get("transcripts", [])
        retrieved_at = datetime.now(timezone.utc)

        # 1. Upsert company
        cursor.execute("""
            INSERT INTO companies (ticker, name)
            VALUES (%s, %s)
            ON CONFLICT (ticker) DO UPDATE SET name = COALESCE(EXCLUDED.name, companies.name)
            RETURNING id
        """, (ticker, None))
        company_id = cursor.fetchone()["id"]

        # 2. Insert ingestion run
        cursor.execute("""
            INSERT INTO ingestion_runs (company_id, retrieved_at_utc, discovered, fetched_count)
            VALUES (%s, %s, %s, %s)
            RETURNING id
        """, (company_id, retrieved_at, discovered, fetched_count))
        run_id = cursor.fetchone()["id"]

        # 3. Store transcripts and collect for post-processing
        transcripts_to_process = []
        for transcript_data in transcripts:
            transcript_id, formatted_turns, full_text = _store_transcript(cursor, company_id, transcript_data)
            if transcript_id and formatted_turns:
                transcripts_to_process.append({
                    "transcript_id": transcript_id,
                    "formatted_turns": formatted_turns,
                    "full_text": full_text
                })

        # 4. Store errors
        for error in errors:
            cursor.execute("""
                INSERT INTO ingestion_errors (run_id, url, stage, error, detail)
                VALUES (%s, %s, %s, %s, %s)
            """, (
                run_id,
                error.get("url", ""),
                error.get("stage", "unknown"),
                error.get("error", "unknown"),  # Fixed: use 'error' key instead of 'stage'
                error.get("detail", "")
            ))

        conn.commit()

        return {"transcripts_to_process": transcripts_to_process}

    except Exception as e:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _store_transcript(cursor, company_id: int, transcript_data: dict) -> tuple[int | None, list[dict] | None, str | None]:
    """Store a single transcript with all related data."""
    meta = transcript_data.get("meta", {})
    parsed = transcript_data.get("parsed", {})
    url = transcript_data.get("url", "")

    # Extract metadata
    quarter_str = meta.get("quarter")  # "Q1", "Q2", etc.
    fiscal_quarter = None
    if quarter_str and quarter_str.startswith("Q"):
        try:
            fiscal_quarter = int(quarter_str[1])
        except (ValueError, IndexError):
            pass

    fiscal_year = meta.get("year")
    yahoo_event_id = meta.get("event_id") or parsed.get("event_id")
    yahoo_company_id = parsed.get("company_id")
    paywalled = parsed.get("paywalled", False)
    version = parsed.get("version")
    speaker_map = parsed.get("speaker_map", {})
    turns = parsed.get("turns", [])

    # Calculate derived fields
    turn_count = len(turns)

    # Format full text with speaker names and paywalled warning
    lines = []

    # Add paywalled warning if applicable
    if paywalled:
        lines.append("⚠️ Note: This transcript may be paywalled or incomplete.\n")

    # Add speaker list
    if speaker_map:
        lines.append("Speakers:")
        for speaker_id_str, speaker_data in speaker_map.items():
            name = speaker_data.get("name", "Unknown")
            role = speaker_data.get("role", "")
            company = speaker_data.get("company", "")

            speaker_info = f"{speaker_id_str}: {name}"
            if role:
                speaker_info += f", {role}"
            if company:
                speaker_info += f" at {company}"
            lines.append(speaker_info)
        lines.append("")

    # Add transcript turns with speaker names
    lines.append("Transcript:")
    lines.append("")

    for turn in turns:
        speaker_id = turn.get("speaker")
        text = turn.get("text", "")

        if speaker_id is not None and speaker_id in speaker_map:
            speaker_name = speaker_map[speaker_id].get("name", f"Speaker {speaker_id}")
        else:
            speaker_name = f"Speaker {speaker_id}" if speaker_id is not None else "Unknown"

        lines.append(f"{speaker_name}: {text}")
        lines.append("")

    full_text = "\n".join(lines)
    char_count = sum(len(turn.get("text", "")) for turn in turns)

    # Upsert earnings_event
    if yahoo_event_id:
        cursor.execute("""
            INSERT INTO earnings_events (
                company_id, fiscal_year, fiscal_quarter, yahoo_event_id,
                yahoo_company_id, source_url, paywalled, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (company_id, yahoo_event_id)
            WHERE yahoo_event_id IS NOT NULL
            DO UPDATE SET
                fiscal_year = EXCLUDED.fiscal_year,
                fiscal_quarter = EXCLUDED.fiscal_quarter,
                yahoo_company_id = EXCLUDED.yahoo_company_id,
                source_url = EXCLUDED.source_url,
                paywalled = EXCLUDED.paywalled,
                updated_at = NOW()
            RETURNING id
        """, (company_id, fiscal_year, fiscal_quarter, yahoo_event_id,
              yahoo_company_id, url, paywalled))
    else:
        cursor.execute("""
            INSERT INTO earnings_events (
                company_id, fiscal_year, fiscal_quarter, yahoo_event_id,
                yahoo_company_id, source_url, paywalled, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (company_id, source_url)
            DO UPDATE SET
                fiscal_year = EXCLUDED.fiscal_year,
                fiscal_quarter = EXCLUDED.fiscal_quarter,
                yahoo_company_id = EXCLUDED.yahoo_company_id,
                paywalled = EXCLUDED.paywalled,
                updated_at = NOW()
            RETURNING id
        """, (company_id, fiscal_year, fiscal_quarter, yahoo_event_id,
              yahoo_company_id, url, paywalled))

    event_id = cursor.fetchone()["id"]

    # Upsert transcript
    cursor.execute("""
        INSERT INTO transcripts (
            event_id, parser_version, turn_count, char_count,
            full_text, full_text_tsv, raw_transcriptcontent, updated_at
        )
        VALUES (
            %s, %s, %s, %s, %s,
            to_tsvector('english', COALESCE(%s, '')),
            %s::jsonb, NOW()
        )
        ON CONFLICT (event_id) DO UPDATE SET
            parser_version = EXCLUDED.parser_version,
            turn_count = EXCLUDED.turn_count,
            char_count = EXCLUDED.char_count,
            full_text = EXCLUDED.full_text,
            full_text_tsv = EXCLUDED.full_text_tsv,
            raw_transcriptcontent = EXCLUDED.raw_transcriptcontent,
            updated_at = NOW()
        RETURNING id
    """, (event_id, version, turn_count, char_count, full_text, full_text,
          json.dumps(parsed) if parsed else None))

    transcript_id = cursor.fetchone()["id"]

    # Upsert speakers (even if paywalled, store speaker_map)
    for yahoo_speaker_id_str, speaker_data in speaker_map.items():
        yahoo_speaker_id = int(yahoo_speaker_id_str)
        cursor.execute("""
            INSERT INTO speakers (
                event_id, yahoo_speaker_id, name, role, org, source
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (event_id, yahoo_speaker_id) DO UPDATE SET
                name = EXCLUDED.name,
                role = EXCLUDED.role,
                org = EXCLUDED.org,
                source = EXCLUDED.source
        """, (event_id, yahoo_speaker_id, speaker_data.get("name"),
              speaker_data.get("role"), speaker_data.get("company"), "speaker_map"))

    # Store transcript turns (delete old ones first for idempotency)
    cursor.execute("DELETE FROM transcript_turns WHERE transcript_id = %s", (transcript_id,))

    for turn_index, turn in enumerate(turns):
        cursor.execute("""
            INSERT INTO transcript_turns (
                transcript_id, turn_index, yahoo_speaker_id, text, start_sec, end_sec
            )
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (transcript_id, turn_index, turn.get("speaker"),
              turn.get("text", ""), turn.get("start"), turn.get("end")))

    # Format turns for post-processing (embeddings and NLP)
    formatted_turns = []
    for turn_index, turn in enumerate(turns):
        speaker_id = turn.get("speaker")
        if speaker_id is not None and speaker_id in speaker_map:
            speaker_name = speaker_map[speaker_id].get("name", f"Speaker {speaker_id}")
        else:
            speaker_name = f"Speaker {speaker_id}" if speaker_id is not None else "Unknown"

        formatted_turns.append({
            "index": turn_index,
            "speaker_id": speaker_id,
            "speaker_name": speaker_name,
            "text": turn.get("text", ""),
            "start": turn.get("start"),
            "end": turn.get("end")
        })

    # Return transcript_id, formatted turns, and full_text for post-processing
    return transcript_id, formatted_turns, full_text


def show_ticker_stats(ticker: str) -> None:
    """Display statistics for a ticker (CLI command)."""
    conn = connect()
    cursor = conn.cursor()

    try:
        # Get company
        cursor.execute("SELECT id, ticker, name FROM companies WHERE UPPER(ticker) = UPPER(%s)", (ticker,))
        company = cursor.fetchone()

        if not company:
            print(f"No data found for ticker: {ticker}")
            return

        company_id = company["id"]
        print(f"\n{'='*60}")
        print(f"Ticker: {company['ticker']}")
        print(f"Company: {company['name'] or 'N/A'}")
        print(f"{'='*60}\n")

        # Table counts
        cursor.execute("""
            SELECT COUNT(*) as count FROM earnings_events WHERE company_id = %s
        """, (company_id,))
        events_count = cursor.fetchone()["count"]

        cursor.execute("""
            SELECT COUNT(*) as count FROM transcripts t
            JOIN earnings_events e ON e.id = t.event_id
            WHERE e.company_id = %s
        """, (company_id,))
        transcripts_count = cursor.fetchone()["count"]

        cursor.execute("""
            SELECT COUNT(*) as count FROM speakers s
            JOIN earnings_events e ON e.id = s.event_id
            WHERE e.company_id = %s
        """, (company_id,))
        speakers_count = cursor.fetchone()["count"]

        cursor.execute("""
            SELECT COUNT(*) as count FROM transcript_turns tt
            JOIN transcripts t ON t.id = tt.transcript_id
            JOIN earnings_events e ON e.id = t.event_id
            WHERE e.company_id = %s
        """, (company_id,))
        turns_count = cursor.fetchone()["count"]

        print("Table Counts:")
        print(f"  Events:       {events_count}")
        print(f"  Transcripts:  {transcripts_count}")
        print(f"  Speakers:     {speakers_count}")
        print(f"  Turns:        {turns_count}")
        print()

        # Last 5 events
        cursor.execute("""
            SELECT
                e.fiscal_quarter,
                e.fiscal_year,
                e.paywalled,
                t.turn_count
            FROM earnings_events e
            LEFT JOIN transcripts t ON t.event_id = e.id
            WHERE e.company_id = %s
            ORDER BY e.fiscal_year DESC, e.fiscal_quarter DESC
            LIMIT 5
        """, (company_id,))

        events = cursor.fetchall()

        if events:
            print("Last 5 Events:")
            print(f"  {'Quarter':<10} {'Year':<6} {'Paywalled':<12} {'Turns':<8}")
            print(f"  {'-'*10} {'-'*6} {'-'*12} {'-'*8}")
            for event in events:
                quarter = f"Q{event['fiscal_quarter']}" if event['fiscal_quarter'] else "N/A"
                year = str(event['fiscal_year']) if event['fiscal_year'] else "N/A"
                paywalled = "Yes" if event['paywalled'] else "No"
                turns = str(event['turn_count']) if event['turn_count'] is not None else "0"
                print(f"  {quarter:<10} {year:<6} {paywalled:<12} {turns:<8}")
        print()

    finally:
        cursor.close()
        conn.close()


# CLI interface
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python -m app.db --init              # Initialize database schema")
        print("  python -m app.db --show TICKER       # Show stats for a ticker")
        sys.exit(1)

    command = sys.argv[1]

    if command == "--init":
        print("Initializing database schema...")
        init_schema()
        print("✓ Database schema initialized successfully")

    elif command == "--show":
        if len(sys.argv) < 3:
            print("Error: --show requires a ticker argument")
            print("Usage: python -m app.db --show AAPL")
            sys.exit(1)
        ticker = sys.argv[2]
        show_ticker_stats(ticker)

    else:
        print(f"Unknown command: {command}")
        print("Valid commands: --init, --show")
        sys.exit(1)
