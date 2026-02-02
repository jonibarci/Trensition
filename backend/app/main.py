from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# Load environment variables from .env file
env_path = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(dotenv_path=env_path)

from .config import settings
from .database import db
from .ingestion import ingestion_service
from .qa import rag_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    # Startup
    db.init_schema()
    yield
    # Shutdown (cleanup if needed)


app = FastAPI(title="Earnings Transcript API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=settings.cors_allow_credentials,
    allow_methods=settings.cors_allow_methods,
    allow_headers=settings.cors_allow_headers,
)


class IngestRequest(BaseModel):
    company: str = Field(..., min_length=1)
    limit: Optional[int] = Field(default=None, ge=1, le=100, description="Max transcripts to ingest (default: all discovered)")


class IngestResponse(BaseModel):
    ok: bool
    company: str
    ingested: int
    message: str


class ConversationTurn(BaseModel):
    question: str
    answer: str


class QaRequest(BaseModel):
    question: str = Field(..., min_length=1)
    company: Optional[str] = None
    transcriptId: Optional[str] = None
    conversation_history: Optional[List[ConversationTurn]] = []


class SourceDetail(BaseModel):
    transcript_id: str
    turn_indices: List[int]


class QaResponse(BaseModel):
    answer: str
    sources: List[str]
    detailed_sources: Optional[List[SourceDetail]] = []


class OrgExtractResponse(BaseModel):
    ok: bool
    transcriptId: str
    organizations: List[str]
    message: str


@app.get("/health")
def health() -> Dict[str, bool]:
    return {"ok": True}


@app.get("/yahoo/cookies/status")
def yahoo_cookie_status():
    """Check if Yahoo Finance cookies are valid."""
    from .ingestion.yahoo_ingestion import check_cookie_validity

    status = check_cookie_validity(settings.yahoo_storage_state_path)

    return {
        "valid": status["valid"],
        "message": status["message"],
        "cookie_file_exists": settings.yahoo_storage_state_path.exists()
    }


@app.post("/yahoo/cookies/refresh")
def yahoo_cookie_refresh():
    """Trigger Yahoo Finance cookie refresh (opens browser)."""
    from .ingestion.yahoo_ingestion import refresh_consent_cookies

    try:
        refresh_consent_cookies(settings.yahoo_storage_state_path)
        return {
            "ok": True,
            "message": "Cookies refreshed successfully"
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to refresh cookies: {type(e).__name__}: {e}"
        )


@app.delete("/yahoo/cookies")
def yahoo_cookie_delete():
    """Delete Yahoo Finance cookies for testing purposes."""
    try:
        # Write empty cookie structure instead of deleting (Docker volume mounted)
        empty_cookies = {"cookies": [], "origins": []}
        with open(settings.yahoo_storage_state_path, 'w') as f:
            json.dump(empty_cookies, f)

        return {
            "ok": True,
            "message": "Cookies deleted successfully"
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete cookies: {type(e).__name__}: {e}"
        )


@app.get("/companies")
def companies() -> List[Dict[str, str]]:
    """Get list of companies from PostgreSQL."""
    conn = db.connect()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT DISTINCT ticker, name
            FROM companies
            ORDER BY ticker
        """)
        rows = cursor.fetchall()
        return [{"company": row["name"] or row["ticker"], "ticker": row["ticker"]} for row in rows]
    finally:
        cursor.close()
        conn.close()


@app.post("/ingest", response_model=IngestResponse)
def ingest(req: IngestRequest) -> IngestResponse:
    company = req.company.strip().upper()
    if not company:
        raise HTTPException(status_code=400, detail="company must be non-empty")

    try:
        # Attempt to ingest transcripts for the ticker
        # Use limit from request if provided, otherwise ingest all discovered (limit=None means no limit)
        results = ingestion_service.ingest_ticker(
            ticker=company,
            limit=req.limit,
            delay_seconds=settings.ingestion_delay_seconds
        )

        # Build response message
        message_parts = [f"Successfully ingested {results['ingested']} of {results['discovered']} discovered transcripts"]
        if results["errors"]:
            error_count = len(results["errors"])
            message_parts.append(f"{error_count} error(s) occurred during ingestion")
            # Add detailed error information for debugging
            for i, err in enumerate(results["errors"][:3], 1):  # Show first 3 errors
                stage = err.get("stage", "unknown")
                detail = err.get("detail", "no details")
                message_parts.append(f"Error {i} ({stage}): {detail}")

        return IngestResponse(
            ok=True,
            company=company,
            ingested=results["ingested"],
            message=". ".join(message_parts),
        )

    except ingestion_service.ConsentRequiredError as e:
        raise HTTPException(
            status_code=503,
            detail="Yahoo Finance consent required. Please contact administrator to refresh cookies."
        )

    except ingestion_service.IngestionError as e:
        raise HTTPException(status_code=500, detail=str(e))

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error during ingestion: {type(e).__name__}: {e}"
        )


@app.get("/transcripts")
def list_transcripts(
    company: Optional[str] = Query(default=None),
    q: Optional[str] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    """List transcripts from PostgreSQL with optional filtering.

    Search supports:
    - Simple terms: revenue growth
    - Phrases with quotes: "revenue was $11.5 billion"
    - AND/OR operators: revenue AND growth
    - Negation: revenue -decline
    """
    conn = db.connect()
    cursor = conn.cursor()
    try:
        # Build query
        where_clauses = []
        params = []
        snippet_select = "LEFT(t.full_text, 200) as snippet"

        if company:
            where_clauses.append("(UPPER(c.ticker) = UPPER(%s))")
            params.append(company)

        if q:
            # Use websearch_to_tsquery which supports quoted phrases and operators
            where_clauses.append("t.full_text_tsv @@ websearch_to_tsquery('english', %s)")
            params.append(q)

            # Generate contextual snippet using ts_headline to show WHERE the match occurred
            snippet_select = f"""
                ts_headline(
                    'english',
                    t.full_text,
                    websearch_to_tsquery('english', %s),
                    'MaxWords=50, MinWords=25, MaxFragments=1'
                ) as snippet
            """
            params.insert(0 if not company else 1, q)

        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

        # Build ORDER BY clause conditionally
        if q:
            order_by = """
                ORDER BY
                    ts_rank(t.full_text_tsv, websearch_to_tsquery('english', %s)) DESC,
                    e.fiscal_year DESC,
                    e.fiscal_quarter DESC
            """
            order_params = [q]
        else:
            order_by = """
                ORDER BY
                    e.fiscal_year DESC,
                    e.fiscal_quarter DESC
            """
            order_params = []

        cursor.execute(f"""
            SELECT
                CONCAT('yahoo_', c.ticker, '_', e.yahoo_event_id) as id,
                c.name as company,
                c.ticker,
                CONCAT(c.ticker, ' Q', e.fiscal_quarter, ' ', e.fiscal_year, ' Earnings Call') as title,
                TO_CHAR(e.created_at, 'YYYY-MM-DD') as date,
                {snippet_select}
            FROM transcripts t
            JOIN earnings_events e ON e.id = t.event_id
            JOIN companies c ON c.id = e.company_id
            WHERE {where_sql}
            {order_by}
            LIMIT %s OFFSET %s
        """, params + order_params + [limit, offset])

        rows = cursor.fetchall()
        return rows
    finally:
        cursor.close()
        conn.close()


@app.get("/transcripts/{transcript_id}")
def get_transcript(transcript_id: str):
    """Get transcript detail from PostgreSQL."""
    conn = db.connect()
    cursor = conn.cursor()
    try:
        # Extract event_id from transcript_id format: yahoo_TICKER_EVENTID
        parts = transcript_id.split('_')
        if len(parts) >= 3:
            try:
                event_id = int(parts[-1])
            except ValueError:
                raise HTTPException(status_code=404, detail="Invalid transcript ID format")
        else:
            raise HTTPException(status_code=404, detail="Invalid transcript ID format")

        cursor.execute("""
            SELECT
                CONCAT('yahoo_', c.ticker, '_', e.yahoo_event_id) as id,
                c.name as company,
                c.ticker,
                CONCAT(c.ticker, ' Q', e.fiscal_quarter, ' ', e.fiscal_year, ' Earnings Call') as title,
                TO_CHAR(e.created_at, 'YYYY-MM-DD') as date,
                e.fiscal_quarter as quarter,
                e.fiscal_year,
                t.full_text as text,
                t.raw_transcriptcontent,
                ARRAY(
                    SELECT DISTINCT org_name
                    FROM (
                        SELECT s.org as org_name
                        FROM speakers s
                        WHERE s.event_id = e.id AND s.org IS NOT NULL
                        UNION
                        SELECT d.organization as org_name
                        FROM detected_organizations d
                        WHERE d.transcript_id = t.id
                    ) combined_orgs
                    ORDER BY org_name
                ) as organizations,
                ARRAY(
                    SELECT json_build_object(
                        'id', s.yahoo_speaker_id,
                        'name', s.name,
                        'role', s.role,
                        'org', s.org
                    )
                    FROM speakers s
                    WHERE s.event_id = e.id
                    ORDER BY s.yahoo_speaker_id
                ) as speakers,
                ARRAY(
                    SELECT json_build_object(
                        'index', tt.turn_index,
                        'speaker_id', tt.yahoo_speaker_id,
                        'speaker_name', s.name,
                        'text', tt.text,
                        'start', tt.start_sec,
                        'end', tt.end_sec
                    )
                    FROM transcript_turns tt
                    LEFT JOIN speakers s ON s.event_id = e.id AND s.yahoo_speaker_id = tt.yahoo_speaker_id
                    WHERE tt.transcript_id = t.id
                    ORDER BY tt.turn_index
                ) as turns
            FROM transcripts t
            JOIN earnings_events e ON e.id = t.event_id
            JOIN companies c ON c.id = e.company_id
            WHERE e.yahoo_event_id = %s
        """, (event_id,))

        detail = cursor.fetchone()
        if not detail:
            raise HTTPException(status_code=404, detail="Transcript not found")

        return detail
    finally:
        cursor.close()
        conn.close()


@app.post("/transcripts/{transcript_id}/organizations/extract", response_model=OrgExtractResponse)
def extract_organizations(transcript_id: str) -> OrgExtractResponse:
    """Extract organizations from transcript (placeholder for future NLP)."""
    conn = db.connect()
    cursor = conn.cursor()
    try:
        # Extract event_id from transcript_id
        parts = transcript_id.split('_')
        if len(parts) >= 3:
            try:
                event_id = int(parts[-1])
            except ValueError:
                raise HTTPException(status_code=404, detail="Invalid transcript ID format")
        else:
            raise HTTPException(status_code=404, detail="Invalid transcript ID format")

        # Get existing organizations from speakers
        cursor.execute("""
            SELECT DISTINCT org
            FROM speakers s
            JOIN earnings_events e ON e.id = s.event_id
            WHERE e.yahoo_event_id = %s AND s.org IS NOT NULL
        """, (event_id,))

        orgs = [row["org"] for row in cursor.fetchall()]

        return OrgExtractResponse(
            ok=True,
            transcriptId=transcript_id,
            organizations=orgs,
            message="ORG extraction from speakers (NLP placeholder)",
        )
    finally:
        cursor.close()
        conn.close()


@app.post("/qa", response_model=QaResponse)
def qa(req: QaRequest) -> QaResponse:
    """Q&A over transcripts using RAG with vector similarity search."""
    question = req.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="question must be non-empty")

    try:
        # Convert conversation history to dict format
        conversation_history = None
        if req.conversation_history:
            conversation_history = [
                {"question": turn.question, "answer": turn.answer}
                for turn in req.conversation_history
            ]

        result = rag_service.answer_question(
            question=question,
            company_ticker=req.company,
            transcript_id=req.transcriptId,
            conversation_history=conversation_history,
            top_k=settings.rag_top_k
        )

        return QaResponse(
            answer=result["answer"],
            sources=result["sources"],
            detailed_sources=result.get("detailed_sources", [])
        )

    except Exception as e:
        # Log error and return a friendly message
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Error generating answer: {type(e).__name__}: {str(e)}"
        )
