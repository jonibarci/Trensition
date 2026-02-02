"""RAG service for Q&A over earnings transcripts using vector similarity search."""
from __future__ import annotations

import os
from typing import List, Dict, Any, Optional

import openai
from ..config import settings
from ..database import db
from . import embedding_service


def retrieve_relevant_chunks(
    question: str,
    company_ticker: Optional[str] = None,
    transcript_id: Optional[int] = None,
    top_k: int = 5
) -> List[Dict[str, Any]]:
    """
    Retrieve the most relevant transcript chunks using vector similarity search.

    Args:
        question: The user's question
        company_ticker: Optional filter by company ticker
        transcript_id: Optional filter by specific transcript
        top_k: Number of chunks to retrieve

    Returns:
        List of chunk dictionaries with keys: id, transcript_id, turn_index, speaker_name, text, metadata, similarity
    """
    # Generate embedding for the question
    question_embedding = embedding_service.generate_embeddings([question])[0]

    conn = db.connect()
    cursor = conn.cursor()

    try:
        # Build query with optional filters
        where_clauses = []
        params = [question_embedding]

        if transcript_id:
            where_clauses.append("tc.transcript_id = %s")
            params.append(transcript_id)

        if company_ticker:
            where_clauses.append("UPPER(c.ticker) = UPPER(%s)")
            params.append(company_ticker)

        where_sql = " AND " + " AND ".join(where_clauses) if where_clauses else ""

        # Vector similarity search using cosine distance
        # Lower distance = more similar
        cursor.execute(f"""
            SELECT
                tc.id,
                tc.transcript_id,
                tc.turn_index,
                tc.speaker_name,
                tc.text,
                tc.metadata,
                1 - (tc.embedding <=> %s::vector) as similarity,
                CONCAT('yahoo_', c.ticker, '_', e.yahoo_event_id) as transcript_display_id,
                c.ticker,
                c.name as company_name,
                e.fiscal_quarter,
                e.fiscal_year
            FROM transcript_chunks tc
            JOIN transcripts t ON t.id = tc.transcript_id
            JOIN earnings_events e ON e.id = t.event_id
            JOIN companies c ON c.id = e.company_id
            WHERE tc.embedding IS NOT NULL{where_sql}
            ORDER BY tc.embedding <=> %s::vector
            LIMIT %s
        """, params + [question_embedding, top_k])

        chunks = cursor.fetchall()
        return chunks

    finally:
        cursor.close()
        conn.close()


def generate_rag_answer(
    question: str,
    chunks: List[Dict[str, Any]],
    conversation_history: Optional[List[Dict[str, str]]] = None,
    model: str | None = None
) -> Dict[str, Any]:
    """
    Generate an answer using retrieved chunks and GPT-4o-mini.

    Args:
        question: The user's question
        chunks: Retrieved chunks from vector search
        conversation_history: Previous Q&A turns for context
        model: OpenAI model to use for generation

    Returns:
        Dictionary with keys: answer, sources, context_used
    """
    if not chunks:
        return {
            "answer": "I don't have enough information to answer this question based on the available transcripts.",
            "sources": [],
            "context_used": False
        }

    # Format context from chunks
    context_parts = []
    sources = []

    for i, chunk in enumerate(chunks, 1):
        speaker = chunk.get("speaker_name", "Unknown")
        text = chunk.get("text", "")
        ticker = chunk.get("ticker", "")
        quarter = chunk.get("fiscal_quarter")
        year = chunk.get("fiscal_year")
        transcript_id = chunk.get("transcript_display_id", "")
        turn_index = chunk.get("turn_index")
        similarity = chunk.get("similarity", 0.0)

        # Format source citation
        quarter_str = f"Q{quarter}" if quarter else "Unknown"
        source = f"{ticker} {quarter_str} {year} Earnings Call"

        context_parts.append(
            f"[Source {i}: {source}]\n"
            f"Speaker: {speaker}\n"
            f"{text}\n"
        )

        sources.append({
            "transcript_id": transcript_id,
            "ticker": ticker,
            "quarter": quarter,
            "fiscal_year": year,
            "speaker": speaker,
            "turn_index": turn_index,  # Include turn index for highlighting
            "similarity": round(similarity, 3)
        })

    context = "\n---\n\n".join(context_parts)

    # Build prompt for GPT-4o-mini
    system_prompt = """You are an AI assistant helping investors analyze earnings call transcripts.

You will be provided with excerpts from earnings call transcripts along with speaker names and source information.

Your task is to answer the user's question based ONLY on the provided transcript excerpts.

Guidelines:
- Be precise and factual
- Quote specific numbers, metrics, and statements when relevant
- Cite which speaker said what (e.g., "According to [Speaker Name], ...")
- If the excerpts don't contain enough information to answer confidently, say so
- Do not make up or infer information not present in the transcripts
- Keep your answer concise but complete
- For follow-up questions, consider the previous conversation context"""

    # Build messages array with conversation history
    messages = [{"role": "system", "content": system_prompt}]

    # Add conversation history if provided
    if conversation_history:
        for turn in conversation_history:
            messages.append({"role": "user", "content": turn["question"]})
            messages.append({"role": "assistant", "content": turn["answer"]})

    # Add current question with context
    user_prompt = f"""Question: {question}

Transcript Excerpts:
{context}

Please answer the question based on the transcript excerpts above."""

    messages.append({"role": "user", "content": user_prompt})

    # Call OpenAI API
    if model is None:
        model = settings.rag_model

    client = openai.OpenAI(api_key=settings.openai_api_key)

    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=settings.rag_temperature,
        max_tokens=settings.rag_max_tokens
    )

    answer = response.choices[0].message.content

    return {
        "answer": answer,
        "sources": sources,
        "context_used": True
    }


def answer_question(
    question: str,
    company_ticker: Optional[str] = None,
    transcript_id: Optional[str] = None,
    conversation_history: Optional[List[Dict[str, str]]] = None,
    top_k: int = 5
) -> Dict[str, Any]:
    """
    Complete RAG pipeline: retrieve relevant chunks and generate answer.

    Args:
        question: The user's question
        company_ticker: Optional filter by company ticker
        transcript_id: Optional transcript ID in format "yahoo_TICKER_EVENTID"
        conversation_history: Previous Q&A turns for context
        top_k: Number of chunks to retrieve

    Returns:
        Dictionary with keys: answer, sources (list of transcript IDs)
    """
    # Extract numeric transcript ID from display ID if provided
    numeric_transcript_id = None
    if transcript_id:
        parts = transcript_id.split('_')
        if len(parts) >= 3:
            try:
                yahoo_event_id = int(parts[-1])
                # Look up transcript_id from yahoo_event_id
                conn = db.connect()
                cursor = conn.cursor()
                try:
                    cursor.execute("""
                        SELECT t.id FROM transcripts t
                        JOIN earnings_events e ON e.id = t.event_id
                        WHERE e.yahoo_event_id = %s
                    """, (yahoo_event_id,))
                    result = cursor.fetchone()
                    if result:
                        numeric_transcript_id = result["id"]
                finally:
                    cursor.close()
                    conn.close()
            except ValueError:
                pass

    # Step 1: Retrieve relevant chunks
    chunks = retrieve_relevant_chunks(
        question=question,
        company_ticker=company_ticker,
        transcript_id=numeric_transcript_id,
        top_k=top_k
    )

    # Step 2: Generate answer with LLM
    result = generate_rag_answer(question, chunks, conversation_history)

    # Step 3: Format sources for API response with chunk details
    # Group by transcript with turn indices for highlighting
    sources_by_transcript = {}

    for source in result.get("sources", []):
        transcript_id = source.get("transcript_id")
        turn_index = source.get("turn_index")

        if transcript_id:
            if transcript_id not in sources_by_transcript:
                sources_by_transcript[transcript_id] = {
                    "transcript_id": transcript_id,
                    "turn_indices": []
                }
            if turn_index is not None:
                sources_by_transcript[transcript_id]["turn_indices"].append(turn_index)

    # Return both simple source IDs and detailed sources
    source_ids = list(sources_by_transcript.keys())
    detailed_sources = list(sources_by_transcript.values())

    return {
        "answer": result["answer"],
        "sources": source_ids,
        "detailed_sources": detailed_sources  # For frontend highlighting
    }
