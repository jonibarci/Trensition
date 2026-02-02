"""
Essential tests for embedding service.

Tests chunking and embedding generation for RAG.
"""

from unittest.mock import Mock, patch

import pytest

from app.qa import embedding_service


class TestEmbeddings:
    """Core embedding tests."""

    def test_chunk_single_short_turn(self):
        """Test that a short turn stays as one chunk."""
        turns = [{"index": 0, "speaker_name": "CEO", "text": "Welcome to our call.", "start": 0.0, "end": 5.0}]

        chunks = embedding_service.chunk_transcript_turns(turns)

        assert len(chunks) == 1
        assert chunks[0]["text"] == "Welcome to our call."
        assert chunks[0]["metadata"]["is_partial"] is False

    def test_chunk_multiple_short_turns(self):
        """Test that multiple short turns each become separate chunks."""
        turns = [
            {"index": 0, "speaker_name": "CEO", "text": "First turn", "start": 0.0, "end": 5.0},
            {"index": 1, "speaker_name": "CFO", "text": "Second turn", "start": 5.1, "end": 10.0}
        ]

        chunks = embedding_service.chunk_transcript_turns(turns)

        assert len(chunks) == 2
        assert chunks[0]["text"] == "First turn"
        assert chunks[1]["text"] == "Second turn"

    def test_chunk_long_turn_splits(self):
        """Test that long turns are split at sentence boundaries."""
        long_text = ". ".join([f"Sentence {i}" for i in range(100)])
        turns = [{"index": 0, "speaker_name": "CEO", "text": long_text, "start": 0.0, "end": 60.0}]

        chunks = embedding_service.chunk_transcript_turns(turns, max_tokens=50)

        assert len(chunks) > 1
        for chunk in chunks:
            assert chunk["metadata"]["is_partial"] is True
            assert chunk["speaker_name"] == "CEO"

    def test_chunk_preserves_metadata(self):
        """Test that speaker names and timestamps are preserved."""
        turns = [{"index": 5, "speaker_name": "Tim Cook", "text": "Hello", "start": 10.5, "end": 15.3}]

        chunks = embedding_service.chunk_transcript_turns(turns)

        assert chunks[0]["turn_index"] == 5
        assert chunks[0]["speaker_name"] == "Tim Cook"
        assert chunks[0]["metadata"]["start_time"] == 10.5
        assert chunks[0]["metadata"]["end_time"] == 15.3

    def test_chunk_empty_turns(self):
        """Test handling of empty turns list."""
        chunks = embedding_service.chunk_transcript_turns([])
        assert chunks == []

    def test_chunk_skips_empty_text(self):
        """Test that turns with empty text are skipped."""
        turns = [
            {"index": 0, "speaker_name": "CEO", "text": "Valid"},
            {"index": 1, "speaker_name": "CFO", "text": ""},
            {"index": 2, "speaker_name": "CEO", "text": "Also valid"}
        ]

        chunks = embedding_service.chunk_transcript_turns(turns)

        assert len(chunks) == 2

    def test_generate_embeddings_success(self):
        """Test successful embedding generation."""
        mock_client = Mock()
        mock_response = Mock()
        mock_response.data = [Mock(embedding=[0.1] * 1536), Mock(embedding=[0.2] * 1536)]
        mock_client.embeddings.create.return_value = mock_response

        with patch('app.qa.embedding_service.openai.OpenAI') as mock_openai:
            mock_openai.return_value = mock_client
            embeddings = embedding_service.generate_embeddings(["Text 1", "Text 2"])

        assert len(embeddings) == 2
        assert len(embeddings[0]) == 1536

    def test_generate_embeddings_empty_list(self):
        """Test handling of empty text list."""
        embeddings = embedding_service.generate_embeddings([])
        assert embeddings == []

    def test_embed_transcript_empty_turns(self):
        """Test pipeline with empty turns."""
        result = embedding_service.embed_transcript(1, [])

        assert result["chunks_created"] == 0
        assert result["embeddings_generated"] == 0

    def test_chunk_defaults_missing_speaker_to_unknown(self):
        """Test that missing speaker name defaults to 'Unknown'."""
        turns = [{"index": 0, "text": "No speaker provided"}]

        chunks = embedding_service.chunk_transcript_turns(turns)

        assert chunks[0]["speaker_name"] == "Unknown"
