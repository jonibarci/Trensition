"""Embedding service for RAG using OpenAI text-embedding-3-small."""
from __future__ import annotations

import json
import os
from typing import List, Dict, Any

import openai
from ..config import settings
from ..database import db


def chunk_transcript_turns(turns: List[Dict[str, Any]], max_tokens: int | None = None) -> List[Dict[str, Any]]:
    """
    Chunk transcript turns for embedding.

    Strategy: Keep turns intact when possible, but split long turns into smaller chunks.
    Each chunk preserves speaker name and turn metadata for citation.

    Args:
        turns: List of turn dictionaries with keys: index, speaker_id, speaker_name, text, start, end
        max_tokens: Maximum tokens per chunk (approximate, using char count / 4 as estimate)

    Returns:
        List of chunk dictionaries with keys: turn_index, speaker_name, text, metadata
    """
    if max_tokens is None:
        max_tokens = settings.max_tokens_per_chunk
    chunks = []
    max_chars = max_tokens * 4  # Rough approximation: 1 token ≈ 4 chars

    for turn in turns:
        turn_text = turn.get("text", "").strip()
        if not turn_text:
            continue

        speaker_name = turn.get("speaker_name", "Unknown")
        turn_index = turn.get("index", 0)
        start_time = turn.get("start")
        end_time = turn.get("end")

        # If turn is short enough, keep it as one chunk
        if len(turn_text) <= max_chars:
            chunks.append({
                "turn_index": turn_index,
                "speaker_name": speaker_name,
                "text": turn_text,
                "metadata": {
                    "start_time": start_time,
                    "end_time": end_time,
                    "is_partial": False
                }
            })
        else:
            # Split long turn into sentences
            sentences = _split_into_sentences(turn_text)
            current_chunk = []
            current_length = 0

            for sentence in sentences:
                sentence_len = len(sentence)

                if current_length + sentence_len > max_chars and current_chunk:
                    # Save current chunk
                    chunk_text = " ".join(current_chunk)
                    chunks.append({
                        "turn_index": turn_index,
                        "speaker_name": speaker_name,
                        "text": chunk_text,
                        "metadata": {
                            "start_time": start_time,
                            "end_time": end_time,
                            "is_partial": True
                        }
                    })
                    current_chunk = [sentence]
                    current_length = sentence_len
                else:
                    current_chunk.append(sentence)
                    current_length += sentence_len + 1  # +1 for space

            # Save remaining chunk
            if current_chunk:
                chunk_text = " ".join(current_chunk)
                chunks.append({
                    "turn_index": turn_index,
                    "speaker_name": speaker_name,
                    "text": chunk_text,
                    "metadata": {
                        "start_time": start_time,
                        "end_time": end_time,
                        "is_partial": True
                    }
                })

    return chunks


def _split_into_sentences(text: str) -> List[str]:
    """Simple sentence splitter (naive but effective for earnings calls)."""
    import re
    # Split on period, exclamation, question mark followed by space/newline
    sentences = re.split(r'(?<=[.!?])\s+', text)
    return [s.strip() for s in sentences if s.strip()]


def generate_embeddings(texts: List[str], model: str | None = None) -> List[List[float]]:
    """
    Generate embeddings using OpenAI API.

    Args:
        texts: List of text strings to embed
        model: OpenAI embedding model name

    Returns:
        List of embedding vectors (each is a list of floats)
    """
    if not texts:
        return []

    if model is None:
        model = settings.embedding_model

    client = openai.OpenAI(api_key=settings.openai_api_key)

    # OpenAI API supports batch embedding (up to 2048 texts per request)
    # For safety, batch in configurable groups
    all_embeddings = []
    batch_size = settings.embedding_batch_size

    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        response = client.embeddings.create(
            input=batch,
            model=model
        )
        batch_embeddings = [item.embedding for item in response.data]
        all_embeddings.extend(batch_embeddings)

    return all_embeddings


def store_chunks_with_embeddings(transcript_id: int, chunks: List[Dict[str, Any]], embeddings: List[List[float]]) -> int:
    """
    Store chunks with their embeddings in the database.

    Args:
        transcript_id: ID of the transcript these chunks belong to
        chunks: List of chunk dictionaries
        embeddings: List of embedding vectors (same length as chunks)

    Returns:
        Number of chunks stored
    """
    if len(chunks) != len(embeddings):
        raise ValueError(f"Mismatch: {len(chunks)} chunks but {len(embeddings)} embeddings")

    conn = db.connect()
    cursor = conn.cursor()

    try:
        # Delete existing chunks for this transcript (for re-ingestion)
        cursor.execute("DELETE FROM transcript_chunks WHERE transcript_id = %s", (transcript_id,))

        # Insert new chunks
        for chunk, embedding in zip(chunks, embeddings):
            cursor.execute("""
                INSERT INTO transcript_chunks
                (transcript_id, turn_index, speaker_name, text, embedding, metadata)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb)
            """, (
                transcript_id,
                chunk["turn_index"],
                chunk["speaker_name"],
                chunk["text"],
                embedding,  # psycopg3 handles list -> vector conversion
                json.dumps(chunk["metadata"])
            ))

        conn.commit()
        return len(chunks)

    except Exception as e:
        conn.rollback()
        raise

    finally:
        cursor.close()
        conn.close()


def embed_transcript(transcript_id: int, turns: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Complete pipeline: chunk transcript, generate embeddings, store in database.

    Args:
        transcript_id: Database ID of the transcript
        turns: List of turn dictionaries

    Returns:
        Dictionary with stats: chunks_created, embeddings_generated
    """
    # Step 1: Chunk the turns
    chunks = chunk_transcript_turns(turns)

    if not chunks:
        return {"chunks_created": 0, "embeddings_generated": 0}

    # Step 2: Generate embeddings
    chunk_texts = [chunk["text"] for chunk in chunks]
    embeddings = generate_embeddings(chunk_texts)

    # Step 3: Store in database
    stored = store_chunks_with_embeddings(transcript_id, chunks, embeddings)

    return {
        "chunks_created": len(chunks),
        "embeddings_generated": len(embeddings),
        "stored": stored
    }
