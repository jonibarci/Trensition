# Trensition

An AI-powered platform for analyzing earnings call transcripts. Trensition automatically ingests transcripts from Yahoo Finance, processes them into searchable chunks, and enables natural language question-answering using retrieval-augmented generation (RAG).

## What is Trensition?

Earnings calls contain critical insights about company performance, strategy, and market conditions. However, manually searching through hundreds of pages of transcripts is time-consuming and inefficient. Trensition solves this by:

1. **Automatically collecting** earnings transcripts from public sources
2. **Processing and indexing** them for both full-text and semantic search
3. **Enabling natural language Q&A** - ask questions in plain English and get answers grounded in actual transcript excerpts
4. **Extracting structured data** like mentioned organizations and key entities

## Architecture & Design

Trensition follows a pipeline architecture that mirrors the data flow from ingestion to question answering:

```
Ingestion → Storage → Processing → Search/QA
   ↓          ↓          ↓           ↓
  Yahoo    PostgreSQL  Embeddings  RAG
 Finance   + pgvector   (OpenAI)   (GPT-4o)
```

### Component Stack

- **Frontend**: Angular 21 with SSR (Node 20) - Modern reactive UI with server-side rendering and lazy loading
- **Backend**: FastAPI (Python 3.12) + uv - High-performance async API with fast package management
- **Database**: PostgreSQL 16 + pgvector - Combines traditional full-text search with vector similarity
- **AI**: OpenAI GPT-4o-mini - Cost-effective model for both embeddings and generation
- **Configuration**: Pydantic Settings - Environment-based configuration with type safety

### Why These Tools?

**PostgreSQL + pgvector**: Chose a single database for both structured data and vector embeddings rather than separate vector stores (Pinecone, Weaviate). This simplifies deployment, reduces latency, and enables powerful hybrid queries that combine full-text search, metadata filtering, and semantic similarity.

**FastAPI + uv**: FastAPI provides automatic OpenAPI docs and async support. uv is 10-100x faster than pip for dependency resolution and installation, crucial for Docker builds and development iteration.

**Angular SSR**: Enables SEO-friendly server-side rendering while maintaining a reactive single-page app experience. Prerendering static routes and server-rendering dynamic transcript pages optimizes performance.

**Playwright**: More reliable than Selenium for scraping dynamic JavaScript-rendered pages like Yahoo Finance. Supports headless mode for Docker deployments.

**OpenAI GPT-4o-mini**: Balances cost and quality. For embeddings, text-embedding-3-small provides 1536 dimensions at low cost. For generation, GPT-4o-mini handles citation-heavy tasks well with consistent formatting.

## Design Decisions

### Data Flow Architecture

The system follows a clear data pipeline that informs both the code structure and test organization:

1. **Ingestion** (Foundation) - Yahoo Finance scraper discovers and parses transcripts
2. **Database** (Storage) - PostgreSQL stores normalized data with full-text search vectors
3. **Processing** (Enrichment) - Extract organizations, generate embeddings, create searchable chunks
4. **QA** (Application) - Retrieve relevant chunks via vector search, generate answers with GPT-4o-mini

Each layer depends only on previous layers, making the system testable and maintainable.

### Search Strategy: Hybrid Approach

Trensition uses **both** full-text search and vector similarity:

- **Full-text search** (PostgreSQL tsvector): Fast keyword matching, exact phrase queries, boolean operators
- **Vector search** (pgvector): Semantic similarity, handles synonyms and paraphrasing

For Q&A, we use vector search to find relevant chunks, then GPT-4o-mini generates answers **grounded in those excerpts** - preventing hallucination while maintaining natural language flexibility.

### Database Schema Design

Normalized schema with 9 tables organized by entity lifecycle:

- **Ingestion tracking**: `companies`, `ingestion_runs`, `ingestion_errors`
- **Transcript data**: `earnings_events`, `transcripts`, `speakers`, `transcript_turns`
- **Processed data**: `mentioned_organizations`, `transcript_chunks` (with embeddings)

Key indexes:
- GIN index on `full_text_tsv` for fast full-text search
- HNSW index on embedding vectors for efficient similarity search
- B-tree indexes on foreign keys and frequently filtered columns

### Configuration Management

All configuration is centralized in `backend/app/config.py` using Pydantic Settings:

- **Environment-based**: Load from `.env` file or environment variables
- **Type-safe**: Pydantic validates all settings at startup
- **Configurable defaults**: Easy to adjust AI parameters, batch sizes, rate limits
- **No magic numbers**: All constants are named and documented

Examples of configurable parameters:
- `embedding_batch_size` (default: 100)
- `rag_top_k` (default: 5)
- `max_tokens_per_chunk` (default: 500)
- `nlp_max_text_length` (default: 8000)

Frontend uses Angular environment files for API URL configuration.

### Error Handling Philosophy

- **Graceful degradation**: Paywalled transcripts are stored with metadata even if content is unavailable
- **Comprehensive logging**: All ingestion errors categorized by type (consent, paywall, parse, network)
- **Idempotent operations**: Re-running ingestion updates existing data rather than duplicating
- **Transaction safety**: Database operations with proper rollback on errors
- **Separation of concerns**: Storage layer never calls business logic - prevents circular dependencies

## Quick Start

### Using Docker (Recommended)

```bash
# 1. Set up environment variables
cp .env.example .env
# Edit .env and add your OPENAI_API_KEY

# 2. Start all services (PostgreSQL, backend, frontend)
docker compose up -d

# 3. Database schema is initialized automatically on backend startup

# 4. Access the application (Yahoo cookies can be initialized from the frontend)
# Frontend: http://localhost:4000
# Backend API: http://localhost:8000/docs
```

### Local Development

For local development without Docker, see:
- Backend setup: [backend/README.md](backend/README.md)
- Frontend setup: [frontend/ui/README.md](frontend/ui/README.md)
- Deployment guide: [DEPLOYMENT.md](DEPLOYMENT.md)

## Testing

### Test Philosophy

Tests are organized to mirror the data flow architecture - each module's tests validate its core responsibilities before testing dependent layers:

```
test_ingestion.py (10 tests)      ← Foundation: Data acquisition
test_embeddings.py (10 tests)     ← Processing: Chunking and vectors
test_rag.py (11 tests)            ← Application: Question answering
```

This ordering ensures:
1. **Isolation**: Each test suite validates one module's contract
2. **Dependencies**: Tests build on verified lower layers
3. **Clarity**: Test structure reflects system architecture

### Test Coverage

**Ingestion Tests** (`test_ingestion.py`)
- Discovery phase: URL parsing, sorting, deduplication
- Parsing phase: Three fallback paths (cached blob, embedded JSON, XHR endpoint)
- Cookie management: Loading, validation, expiration handling
- Error handling: Paywall detection, consent errors, malformed responses

**Embedding Tests** (`test_embeddings.py`)
- Chunking: Turn preservation, sentence boundary splitting, token limits
- Embedding generation: Batch processing, dimensionality validation
- Storage: Idempotent chunk insertion, metadata preservation
- Edge cases: Very long turns, missing speakers, empty transcripts

**RAG Tests** (`test_rag.py`)
- Retrieval: Vector similarity search, filtering by company/transcript
- Generation: System prompt correctness, speaker citation format
- Context handling: Relevance scoring, source grouping
- Error cases: No relevant chunks, empty questions, API failures

### Running Tests

```bash
cd backend
uv run pytest tests/ -v

# With coverage report
uv run pytest tests/ -v --cov=app --cov-report=html
```

**30 total tests** covering critical paths through ingestion, processing, and QA layers.

## Project Structure

```
Trensition/
├── backend/                 # FastAPI backend
│   ├── app/
│   │   ├── main.py         # API routes and CORS config
│   │   ├── config.py       # Centralized settings (NEW)
│   │   ├── database/       # Database layer (schema, storage, queries)
│   │   ├── ingestion/      # Yahoo Finance scraper (discovery, parsing, post-processing)
│   │   ├── nlp/            # Organization extraction (GPT-4o)
│   │   └── qa/             # RAG system (chunking, embeddings, retrieval)
│   ├── tests/              # Unit tests (30 tests total)
│   └── pyproject.toml      # uv dependencies
│
├── frontend/ui/            # Angular 21 SSR frontend
│   ├── src/
│   │   ├── app/           # Pages, services (lazy-loaded components)
│   │   └── environments/  # API URL configuration (NEW)
│   └── Dockerfile         # Multi-stage Node build
│
├── docker-compose.yml      # PostgreSQL + backend + frontend
└── DEPLOYMENT.md          # Production deployment guide
```

## API Overview

Trensition exposes a REST API for transcript management and Q&A:

- **Ingestion**: `POST /ingest` - Fetch transcripts from Yahoo Finance
- **Search**: `GET /transcripts?q=<query>` - Full-text search across transcripts
- **Details**: `GET /transcripts/{id}` - Retrieve full transcript with turns
- **Q&A**: `POST /qa` - Ask natural language questions with RAG
- **Organizations**: `POST /transcripts/{id}/organizations/extract` - Extract mentioned entities
- **Cookies**: `GET/POST/DELETE /yahoo/cookies` - Manage Yahoo Finance authentication

Full interactive documentation: http://localhost:8000/docs

For implementation details, see [backend/README.md](backend/README.md)

## Key Technologies

| Component | Technology | Why? |
|-----------|-----------|------|
| **Web Framework** | FastAPI | Async support, automatic OpenAPI docs, high performance |
| **Database** | PostgreSQL 16 + pgvector | Single store for structured + vector data, proven reliability |
| **Package Manager** | uv | 10-100x faster than pip, lockfile support, unified tooling |
| **Embeddings** | text-embedding-3-small | 1536 dimensions, low cost, good quality |
| **LLM** | GPT-4o-mini | Balances cost and quality for citation-heavy tasks |
| **Scraping** | Playwright | Reliable browser automation, headless support |
| **Frontend** | Angular 21 + SSR | SEO-friendly, reactive, strong typing |

## Requirements

- Python 3.12+
- Node.js 20 LTS
- PostgreSQL 16+ with pgvector extension
- OpenAI API key
- Docker (optional but recommended)

## Environment Variables

```bash
DATABASE_URL=postgresql://user:password@host:5432/earnings
OPENAI_API_KEY=sk-...
HEADLESS=true  # For Playwright in Docker
```

## Data Flow Pipeline

```
┌─────────────┐     ┌──────────┐     ┌─────────────┐     ┌──────────┐
│   Yahoo     │────▶│ Database │────▶│  Chunking   │────▶│   RAG    │
│  Ingestion  │     │ Storage  │     │ Embeddings  │     │   Q&A    │
└─────────────┘     └──────────┘     └─────────────┘     └──────────┘
      │                   │                  │                  │
   Discover            Store             Process           Retrieve
   Parse            Normalize            Embed             Generate
```

**Step-by-step:**

1. **Ingestion**: Playwright navigates Yahoo Finance, parses HTML/JSON, extracts transcript data
2. **Storage**: Normalized schema stores events, transcripts, speakers, turns with full-text search vectors
3. **Processing**: Transcripts chunked at sentence boundaries, embedded via OpenAI, stored as 1536-dim vectors
4. **Q&A**: User question embedded → vector similarity search → top-k chunks → GPT-4o-mini generates grounded answer

## Limitations & Trade-offs

**Yahoo Finance Paywalls**: Historical transcripts (older than ~1 quarter) require premium subscription. Recent transcripts are freely available.

**Rate Limiting**: 1-second delay between ingestion requests to avoid blocking. Adjust in `ingestion_service.py` if needed.

**OpenAI Costs**: Embeddings cost ~$0.02 per 1M tokens. 5 transcripts ≈ 50k tokens ≈ $0.001. Q&A cost depends on chunk count and answer length.

**Single Language**: Full-text search optimized for English. Non-English transcripts may have degraded search quality.

## Learn More

- **Implementation details**: [backend/README.md](backend/README.md)
- **Deployment guide**: [DEPLOYMENT.md](DEPLOYMENT.md)
- **API reference**: http://localhost:8000/docs

## License

MIT (or specify your license)
