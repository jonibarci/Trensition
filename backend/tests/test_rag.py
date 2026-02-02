"""
Essential tests for RAG service.

Tests question answering with retrieval-augmented generation.
"""

from unittest.mock import Mock, patch

import pytest

from app.qa import rag_service


class TestRAG:
    """Core RAG/QA tests."""

    @patch('app.qa.rag_service.openai.OpenAI')
    def test_generate_answer_success(self, mock_openai):
        """Test successful answer generation with chunks."""
        mock_client = Mock()
        mock_response = Mock()
        mock_response.choices = [Mock(message=Mock(content="Revenue was $95 billion."))]
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai.return_value = mock_client

        chunks = [{
            "speaker_name": "CEO",
            "text": "Revenue was $95 billion.",
            "ticker": "AAPL",
            "fiscal_quarter": 4,
            "fiscal_year": 2025,
            "transcript_display_id": "yahoo_AAPL_369370",
            "turn_index": 0,
            "similarity": 0.95
        }]

        result = rag_service.generate_rag_answer("What was revenue?", chunks)

        assert "answer" in result
        assert result["context_used"] is True
        assert len(result["sources"]) == 1

    @patch('app.qa.rag_service.openai.OpenAI')
    def test_generate_answer_no_chunks(self, mock_openai):
        """Test answer generation when no chunks are available."""
        result = rag_service.generate_rag_answer("What was revenue?", [])

        assert "don't have enough information" in result["answer"].lower()
        assert result["context_used"] is False
        assert result["sources"] == []

    @patch('app.qa.rag_service.openai.OpenAI')
    def test_uses_gpt_4o_mini(self, mock_openai):
        """Test that gpt-4o-mini model is used."""
        mock_client = Mock()
        mock_response = Mock()
        mock_response.choices = [Mock(message=Mock(content="Answer"))]
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai.return_value = mock_client

        chunks = [{"speaker_name": "CEO", "text": "Text", "ticker": "AAPL", "fiscal_quarter": 4,
                   "fiscal_year": 2025, "transcript_display_id": "yahoo_AAPL_1", "turn_index": 0, "similarity": 0.9}]

        rag_service.generate_rag_answer("Question", chunks)

        call_args = mock_client.chat.completions.create.call_args
        assert call_args[1]["model"] == "gpt-4o-mini"

    @patch('app.qa.rag_service.openai.OpenAI')
    def test_uses_low_temperature(self, mock_openai):
        """Test that temperature is low for factual responses."""
        mock_client = Mock()
        mock_response = Mock()
        mock_response.choices = [Mock(message=Mock(content="Answer"))]
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai.return_value = mock_client

        chunks = [{"speaker_name": "CEO", "text": "Text", "ticker": "AAPL", "fiscal_quarter": 4,
                   "fiscal_year": 2025, "transcript_display_id": "yahoo_AAPL_1", "turn_index": 0, "similarity": 0.9}]

        rag_service.generate_rag_answer("Question", chunks)

        call_args = mock_client.chat.completions.create.call_args
        assert call_args[1]["temperature"] == 0.3

    @patch('app.qa.rag_service.openai.OpenAI')
    def test_includes_conversation_history(self, mock_openai):
        """Test that conversation history is included in prompt."""
        mock_client = Mock()
        mock_response = Mock()
        mock_response.choices = [Mock(message=Mock(content="Follow-up answer"))]
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai.return_value = mock_client

        chunks = [{"speaker_name": "CEO", "text": "Text", "ticker": "AAPL", "fiscal_quarter": 4,
                   "fiscal_year": 2025, "transcript_display_id": "yahoo_AAPL_1", "turn_index": 0, "similarity": 0.9}]

        history = [
            {"question": "What was revenue?", "answer": "$95 billion"},
            {"question": "What about margins?", "answer": "28%"}
        ]

        rag_service.generate_rag_answer("Tell me more", chunks, conversation_history=history)

        call_args = mock_client.chat.completions.create.call_args
        messages = call_args[1]["messages"]
        assert len(messages) >= 5  # system + 2 history pairs + current

    @patch('app.qa.rag_service.openai.OpenAI')
    def test_sources_include_metadata(self, mock_openai):
        """Test that sources include all required metadata."""
        mock_client = Mock()
        mock_response = Mock()
        mock_response.choices = [Mock(message=Mock(content="Answer"))]
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai.return_value = mock_client

        chunks = [{
            "speaker_name": "Tim Cook",
            "text": "Revenue was strong",
            "ticker": "AAPL",
            "fiscal_quarter": 4,
            "fiscal_year": 2025,
            "transcript_display_id": "yahoo_AAPL_369370",
            "turn_index": 5,
            "similarity": 0.95
        }]

        result = rag_service.generate_rag_answer("Question", chunks)

        source = result["sources"][0]
        assert source["transcript_id"] == "yahoo_AAPL_369370"
        assert source["ticker"] == "AAPL"
        assert source["turn_index"] == 5
        assert source["similarity"] == 0.95

    def test_answer_question_groups_sources(self):
        """Test that answer_question groups sources by transcript."""
        with patch('app.qa.rag_service.retrieve_relevant_chunks') as mock_retrieve:
            mock_retrieve.return_value = []

            with patch('app.qa.rag_service.generate_rag_answer') as mock_generate:
                mock_generate.return_value = {
                    "answer": "Answer",
                    "sources": [
                        {"transcript_id": "yahoo_AAPL_1", "turn_index": 0, "ticker": "AAPL"},
                        {"transcript_id": "yahoo_AAPL_1", "turn_index": 5, "ticker": "AAPL"},
                        {"transcript_id": "yahoo_MSFT_2", "turn_index": 3, "ticker": "MSFT"}
                    ]
                }

                result = rag_service.answer_question("Question")

                assert len(result["sources"]) == 2
                assert "yahoo_AAPL_1" in result["sources"]
                assert "yahoo_MSFT_2" in result["sources"]

                # Check turn indices are grouped
                detailed = result["detailed_sources"]
                aapl_source = next(s for s in detailed if s["transcript_id"] == "yahoo_AAPL_1")
                assert 0 in aapl_source["turn_indices"]
                assert 5 in aapl_source["turn_indices"]

    @patch('app.qa.rag_service.openai.OpenAI')
    def test_system_prompt_emphasizes_transcript_only(self, mock_openai):
        """Test that system prompt instructs to use ONLY transcript excerpts."""
        mock_client = Mock()
        mock_response = Mock()
        mock_response.choices = [Mock(message=Mock(content="Answer"))]
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai.return_value = mock_client

        chunks = [{"speaker_name": "CEO", "text": "Text", "ticker": "AAPL", "fiscal_quarter": 4,
                   "fiscal_year": 2025, "transcript_display_id": "yahoo_AAPL_1", "turn_index": 0, "similarity": 0.9}]

        rag_service.generate_rag_answer("Question", chunks)

        call_args = mock_client.chat.completions.create.call_args
        system_message = call_args[1]["messages"][0]["content"]

        assert "ONLY" in system_message
        assert "transcript" in system_message.lower()

    @patch('app.qa.rag_service.openai.OpenAI')
    def test_system_prompt_forbids_inference(self, mock_openai):
        """Test that system prompt forbids making up information."""
        mock_client = Mock()
        mock_response = Mock()
        mock_response.choices = [Mock(message=Mock(content="Answer"))]
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai.return_value = mock_client

        chunks = [{"speaker_name": "CEO", "text": "Text", "ticker": "AAPL", "fiscal_quarter": 4,
                   "fiscal_year": 2025, "transcript_display_id": "yahoo_AAPL_1", "turn_index": 0, "similarity": 0.9}]

        rag_service.generate_rag_answer("Question", chunks)

        call_args = mock_client.chat.completions.create.call_args
        system_message = call_args[1]["messages"][0]["content"]

        assert "not" in system_message.lower() and ("infer" in system_message.lower() or "make up" in system_message.lower())

    @patch('app.qa.rag_service.openai.OpenAI')
    def test_handles_missing_source_fields(self, mock_openai):
        """Test graceful handling of chunks with missing fields."""
        mock_client = Mock()
        mock_response = Mock()
        mock_response.choices = [Mock(message=Mock(content="Answer"))]
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai.return_value = mock_client

        chunks = [{
            "speaker_name": None,
            "text": "Some text",
            "ticker": None,
            "fiscal_quarter": None,
            "fiscal_year": None,
            "transcript_display_id": "",
            "turn_index": None,
            "similarity": 0.5
        }]

        result = rag_service.generate_rag_answer("Question", chunks)

        assert "answer" in result
        assert len(result["sources"]) == 1
