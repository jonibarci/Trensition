"""Configuration settings for the application."""
from __future__ import annotations

import os
from pathlib import Path
from typing import List

from pydantic import ConfigDict
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = ConfigDict(
        env_file=".env",
        env_file_encoding="utf-8"
    )

    # Database
    database_url: str

    # OpenAI
    openai_api_key: str

    # Embedding configuration
    embedding_model: str = "text-embedding-3-small"
    embedding_batch_size: int = 100
    max_tokens_per_chunk: int = 500

    # RAG configuration
    rag_model: str = "gpt-4o-mini"
    rag_top_k: int = 5
    rag_temperature: float = 0.3
    rag_max_tokens: int = 800

    # NLP configuration
    nlp_model: str = "gpt-4o-mini"
    nlp_max_text_length: int = 8000
    nlp_temperature: float = 0.1
    nlp_max_tokens: int = 500

    # Ingestion configuration
    ingestion_delay_seconds: float = 1.0
    ingestion_default_limit: int | None = None

    # Yahoo Finance
    yahoo_storage_state_path: Path = Path(__file__).resolve().parents[1] / ".yahoo_cookies" / "storage_state.json"
    headless: bool = True

    # CORS configuration
    cors_origins: List[str] = [
        "http://localhost:4000",  # Docker frontend
        "http://localhost:4200",  # Local Angular dev
        "http://127.0.0.1:4000",
        "http://127.0.0.1:4200",
    ]
    cors_allow_credentials: bool = True
    cors_allow_methods: List[str] = ["GET", "POST", "PUT", "DELETE", "OPTIONS"]
    cors_allow_headers: List[str] = ["Content-Type", "Authorization", "Accept"]


# Global settings instance
settings = Settings()
