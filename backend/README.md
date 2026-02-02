# Trensition Backend

Implementation details for the Trensition backend. This document explains **how** each module works internally, including parsing strategies, database implementation, search techniques, and RAG pipeline.

For high-level architecture and design decisions, see the [main README](../README.md).

## Module Implementation Details

### Ingestion: Yahoo Finance Scraping

**Files**: `app/ingestion/yahoo_ingestion.py`, `app/ingestion/ingestion_service.py`

#### How Discovery Works

Yahoo Finance lists earnings transcripts at:
```
https://finance.yahoo.com/quote/{TICKER}/earnings/
```

The discovery process (`discover_transcripts`):

1. **Load cookies** from `.yahoo_cookies/storage_state.json` (Playwright storage state - auto-created)
2. **Navigate** to the earnings page
3. **Parse** the HTML looking for transcript links matching pattern:
   ```
   /quote/{TICKER}/earnings/{TICKER}-Q{N}-{YEAR}-earnings_call-{EVENT_ID}.html
   ```
4. **Extract metadata** from URL: `ticker`, `quarter`, `year`, `event_id`
5. **Sort by event_id** (newest first) and **deduplicate**
6. **Check for consent redirect** - if redirected to `consent.yahoo.com`, cookies are invalid

#### How Parsing Works (Three Fallback Paths)

Yahoo Finance serves transcript data in multiple formats. We try three strategies in order:

**PATH 0: Cached Blob** (`_try_parse_transcriptcontent_from_cached_blob`)
- Check if Playwright's browser cache has a response to:
  ```
  https://query.yahoo.com/v1/finance/financials/transcript?eventId={ID}&crumb={CRUMB}
  ```
- Parse the cached JSON response directly
- Fastest method but only works if cache exists

**PATH A: Embedded JSON in `<script>` tags**
- Parse the HTML page looking for `<script>` tags containing `"transcriptContent":`
- Extract the JSON blob embedded in the page source
- Works when Yahoo embeds the data directly in HTML

**PATH B: XHR Endpoint Fallback**
- Parse HTML for a `data-url` attribute like:
  ```html
  <div data-url="/xhr/transcript?eventId=123&amp;crumb=abc"></div>
  ```
- Unescape HTML entities (`&amp;` → `&`)
- Make a separate HTTP request to fetch the JSON
- Most reliable but requires an extra network call

#### How Turn Extraction Works

Transcript JSON contains:
```json
{
  "transcriptContent": {
    "version": "1.0.0",
    "speakers": [...],
    "turns": [...]
  }
}
```

**Speaker Map Construction** (`_build_speaker_map`):
- Each speaker has: `id` (int), `name`, `role`, `company`
- Build a dict mapping `speaker_id → {name, role, company}`

**Turn Extraction** (`_extract_turns`):
- Two possible formats:
  - **Old format**: `{"speaker": 0, "text": "...", "start": 5.0, "end": 10.0}`
  - **New format**: `{"speakerId": 0, "paragraph": "..."}`
- Normalize to: `{"speaker": int, "text": str, "start": float|None, "end": float|None}`

#### Paywall Detection

Paywalled transcripts have:
1. **HTML markers**: `<div class="paywall-message">` or similar
2. **Heuristic**: Total text length < 1000 chars and turn count ≤ 2

If detected, set `paywalled=True` and store minimal metadata.

#### Cookie Management

**Storage Format**: Playwright's `storage_state.json`:
```json
{
  "cookies": [
    {"name": "consent_id", "value": "...", "domain": ".yahoo.com", ...}
  ],
  "origins": [...]
}
```

**Loading into requests.Session** (`load_playwright_cookies_into_session`):
- Convert Playwright cookies to `requests`-compatible format
- Set domain, path, secure, httponly flags correctly

**Validating Cookies** (`check_cookie_validity`):
- Navigate to `https://finance.yahoo.com`
- Check if redirected to `consent.yahoo.com`
- If yes → cookies expired, need refresh

**Refreshing Cookies** (`refresh_consent_cookies` via API):
- Launch Playwright browser (headless or visible)
- Navigate to Yahoo Finance
- Wait for consent dialog
- Auto-accept or manual click
- Save new cookies to `.yahoo_cookies/storage_state.json` (auto-creates directory)

### Configuration Management

**File**: `app/config.py`

Centralized configuration using Pydantic Settings:

```python
class Settings(BaseSettings):
    # Database
    database_url: str

    # OpenAI
    openai_api_key: str

    # Configurable parameters
    embedding_model: str = "text-embedding-3-small"
    embedding_batch_size: int = 100
    max_tokens_per_chunk: int = 500
    rag_top_k: int = 5
    rag_temperature: float = 0.3
    # ... and more
```

**Environment Variables**: Loaded from `.env` file:
```bash
DATABASE_URL=postgresql://user:password@localhost:5432/earnings
OPENAI_API_KEY=sk-...
```

**Usage**: Import settings singleton:
```python
from app.config import settings
client = openai.OpenAI(api_key=settings.openai_api_key)
```

### Database: Schema and Storage

**File**: `app/database/db.py`

#### Schema Design

9 tables organized by entity lifecycle:

```
companies ────┬──────> ingestion_runs ────> ingestion_errors
              │
              └──────> earnings_events ────> transcripts ────┬──> transcript_turns
                                                              ├──> speakers
                                                              ├──> mentioned_organizations
                                                              └──> transcript_chunks (embeddings)
```

**Key Constraints**:
- `earnings_events`: Unique on `(company_id, yahoo_event_id)` and `(company_id, source_url)`
- `transcripts`: One-to-one with `earnings_events` (CASCADE delete)
- `transcript_chunks`: CASCADE delete when transcript deleted

#### Indexing Strategy

1. **GIN Index** on `transcripts.full_text_tsv`:
   ```sql
   CREATE INDEX idx_transcripts_tsv ON transcripts USING GIN(full_text_tsv);
   ```
   Enables fast full-text search with `websearch_to_tsquery()`

2. **HNSW Index** on `transcript_chunks.embedding`:
   ```sql
   CREATE INDEX idx_chunks_embedding ON transcript_chunks
   USING hnsw (embedding vector_cosine_ops);
   ```
   Hierarchical Navigable Small World graph for approximate nearest neighbor search
   - `vector_cosine_ops`: Use cosine distance (1 - cosine similarity)
   - Much faster than brute-force vector comparison

3. **B-tree Indexes** on:
   - Foreign keys: `company_id`, `earnings_event_id`, `transcript_id`
   - Frequently filtered columns: `ticker`, `fiscal_quarter`, `fiscal_year`

#### Storage Pipeline (`store_ingestion`)

**Separation of Concerns**: Database layer only handles storage. Post-processing (embeddings, NLP) is handled by `ingestion_service.py` after database commit.

For each parsed transcript:

1. **Company upsert**:
   ```sql
   INSERT INTO companies (ticker, name) VALUES (...)
   ON CONFLICT (ticker) DO UPDATE SET name = EXCLUDED.name
   RETURNING id;
   ```

2. **Event upsert**:
   ```sql
   INSERT INTO earnings_events (company_id, yahoo_event_id, source_url, ...)
   ON CONFLICT (company_id, yahoo_event_id) DO UPDATE SET ...
   RETURNING id;
   ```

3. **Transcript upsert** (`_store_transcript`):
   - Delete existing transcript for this event (CASCADE deletes chunks, turns, etc.)
   - Generate `full_text` by joining all turn texts
   - Generate `full_text_tsv`:
     ```sql
     to_tsvector('english', full_text)
     ```
   - Insert transcript with JSON `raw_transcriptcontent`
   - Returns: `(transcript_id, formatted_turns, full_text)` for post-processing

4. **Speaker upsert**:
   - For each speaker in `speaker_map`:
     ```sql
     INSERT INTO speakers (earnings_event_id, speaker_id, name, role, organization, source)
     ON CONFLICT (earnings_event_id, speaker_id) DO NOTHING;
     ```

5. **Turn insertion**:
   - Batch insert all turns:
     ```sql
     INSERT INTO transcript_turns (transcript_id, turn_index, speaker_id, text, start_time, end_time)
     VALUES (?, 0, ?, ?, ?, ?), (?, 1, ?, ?, ?, ?), ...
     ```

6. **Error logging**:
   - Insert rows into `ingestion_errors` with type, URL, error message

**Post-processing** (handled by `ingestion_service.py` after commit):
- **Generate embeddings**: `embedding_service.embed_transcript()` creates chunks and vectors
- **Extract organizations**: `nlp_service.extract_organizations()` identifies mentioned entities
- Both operations are non-blocking - failures are logged but don't fail ingestion

#### Full-Text Search Implementation

**Query Construction**:
```sql
SELECT
  t.id,
  t.full_text,
  ts_rank(t.full_text_tsv, query) AS rank,
  ts_headline('english', t.full_text, query) AS snippet
FROM transcripts t
WHERE t.full_text_tsv @@ websearch_to_tsquery('english', ?)
ORDER BY rank DESC;
```

**`websearch_to_tsquery`** supports:
- Keywords: `revenue` → `'revenue'`
- Phrases: `"revenue growth"` → `'revenue' <-> 'growth'`
- Boolean: `revenue AND growth` → `'revenue' & 'growth'`
- Negation: `revenue -decline` → `'revenue' & !'decline'`

**`ts_headline`** generates snippets:
- Highlights matching terms
- Shows surrounding context (default 35 words on each side)

### NLP: Organization Extraction

**File**: `app/nlp/nlp_service.py`

#### How It Works

Uses GPT-4o-mini to extract mentioned organizations from transcript text.

**System Prompt**:
```
Extract all companies, organizations, and entities mentioned in the transcript.
Include: competitors, partners, subsidiaries, government agencies, etc.
Return ONLY a JSON list of strings.
Exclude generic terms like "the company", "management", "we".
```

**Configuration** (from `app/config.py`):
- **Model**: `settings.nlp_model` (default: "gpt-4o-mini")
- **Temperature**: `settings.nlp_temperature` (default: 0.1 for deterministic results)
- **Max tokens**: `settings.nlp_max_tokens` (default: 500)
- **Max input length**: `settings.nlp_max_text_length` (default: 8000 chars)

**Input truncation**:
- Limit to first N chars (configurable) to stay within token limits
- Typically covers opening remarks and Q&A intro

**Post-processing** (`_post_process_orgs`):
1. Remove bullets/numbers: `1. Apple Inc.` → `Apple Inc.`
2. Strip whitespace
3. Filter single-character entries
4. Deduplicate (case-insensitive)
5. Return sorted list

**Error handling**:
- If API call fails, return empty list (non-blocking)
- Log error for debugging

### Embeddings: Chunking and Vector Generation

**File**: `app/qa/embedding_service.py`

#### Chunking Strategy (`chunk_transcript_turns`)

Goal: Create chunks that preserve speaker context and semantic coherence.

**Configuration** (from `app/config.py`):
- **Max tokens per chunk**: `settings.max_tokens_per_chunk` (default: 500)
- Converts to ~2000 chars using 1 token ≈ 4 chars approximation

**Algorithm**:
1. For each turn:
   - If `len(text) ≤ max_chars`: Keep as single chunk
   - Else: Split at sentence boundaries (`. `, `! `, `? `)
2. Accumulate sentences until reaching char limit
3. Flush accumulated text as a chunk
4. Preserve metadata: `turn_index`, `speaker_name`, `start_time`, `end_time`
5. Mark split chunks with `is_partial: true`

**Why sentence boundaries?**
- Preserves semantic coherence (don't split mid-sentence)
- Improves embedding quality (complete thoughts)
- Better for citation (can reference specific statements)

**Metadata per chunk**:
```json
{
  "turn_index": 5,
  "speaker_name": "Tim Cook",
  "start_time": 120.5,
  "end_time": 185.0,
  "is_partial": false
}
```

#### Embedding Generation (`generate_embeddings`)

**Configuration** (from `app/config.py`):
- **Model**: `settings.embedding_model` (default: "text-embedding-3-small")
- **Batch size**: `settings.embedding_batch_size` (default: 100)
- **Dimensions**: 1536 (determined by model)

**Process**:
1. Batch chunks into configurable groups
2. For each batch:
   ```python
   client = openai.OpenAI(api_key=settings.openai_api_key)
   response = client.embeddings.create(
       model=settings.embedding_model,
       input=batch_texts
   )
   embeddings = [item.embedding for item in response.data]
   ```
3. Collect all embeddings (each is a list of 1536 floats)

**Cost**: ~$0.02 per 1M tokens (~$0.001 for 5 transcripts)

#### Storage (`store_chunks_with_embeddings`)

**Idempotency**:
```sql
DELETE FROM transcript_chunks WHERE transcript_id = ?;
```
Clear existing chunks before inserting new ones.

**Insertion**:
```sql
INSERT INTO transcript_chunks (
  transcript_id, chunk_index, text, embedding, metadata, turn_index
) VALUES
  (?, 0, ?, ?, ?::jsonb, ?),
  (?, 1, ?, ?, ?::jsonb, ?),
  ...
```

**Vector format**: PostgreSQL expects `vector` type:
```python
embedding_str = '[' + ','.join(map(str, embedding)) + ']'
```

### RAG: Retrieval-Augmented Generation

**File**: `app/qa/rag_service.py`

#### Retrieval (`retrieve_relevant_chunks`)

Vector similarity search using pgvector:

```sql
SELECT
  tc.text,
  tc.metadata,
  tc.turn_index,
  e.fiscal_quarter,
  e.fiscal_year,
  c.ticker,
  1 - (tc.embedding <=> ?) AS similarity  -- Cosine similarity
FROM transcript_chunks tc
JOIN transcripts t ON tc.transcript_id = t.id
JOIN earnings_events e ON t.earnings_event_id = e.id
JOIN companies c ON e.company_id = c.id
WHERE 1=1
  AND (? IS NULL OR c.ticker = ?)  -- Optional company filter
  AND (? IS NULL OR tc.transcript_id = ?)  -- Optional transcript filter
ORDER BY tc.embedding <=> ?  -- Cosine distance (ascending)
LIMIT ?;
```

**Distance operator** `<=>`: Cosine distance (1 - cosine similarity)
**HNSW index**: Makes this query fast even with 10k+ chunks

**Top-k selection**: `settings.rag_top_k` (default: 5)

#### Answer Generation (`generate_rag_answer`)

**System Prompt**:
```
You are a financial analyst assistant. Answer questions using ONLY the provided transcript excerpts.

Rules:
1. Use ONLY the information in the excerpts
2. Cite speakers by name and role when referencing information
3. If the excerpts don't contain the answer, say "I don't have enough information"
4. Be precise with numbers and financial data
5. Don't infer or make up information
```

**Message format**:
```python
messages = [
    {"role": "system", "content": system_prompt},
    {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {question}"}
]
```

**Context formatting**:
```
[Apple Q4 2025 - Tim Cook, CEO]
Our revenue for Q4 was $95 billion, up 6% year-over-year...

[Apple Q4 2025 - Luca Maestri, CFO]
Operating margins improved to 28%, driven by...
```

**Model config** (from `app/config.py`):
- **Model**: `settings.rag_model` (default: "gpt-4o-mini")
- **Temperature**: `settings.rag_temperature` (default: 0.3 for slightly creative phrasing)
- **Max tokens**: `settings.rag_max_tokens` (default: 800 for detailed answers)

#### Source Tracking

**Parsing citations**:
- Extract turn indices from retrieved chunks
- Group by transcript_id
- Return `detailed_sources` with:
  ```json
  [
    {
      "transcript_display_id": "yahoo_AAPL_369370",
      "ticker": "AAPL",
      "fiscal_quarter": 4,
      "fiscal_year": 2025,
      "turn_indices": [5, 12, 18],
      "similarity": 0.87
    }
  ]
  ```

**Frontend use**: Highlight specific turns in transcript view

## Local Development Setup

For Docker setup, see the [main README](../README.md). This section covers local development without containers.

### Prerequisites

- Python 3.12+
- uv (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- PostgreSQL 16+ with pgvector extension
- OpenAI API key

### Installation

```bash
cd backend

# Install dependencies
uv sync

# Install Playwright browsers (required for Yahoo scraping)
uv run playwright install chromium
```

### Database Setup

```bash
# Create database
createdb earnings

# Set environment variables
export DATABASE_URL="postgresql://user:password@localhost:5432/earnings"
export OPENAI_API_KEY="sk-..."

# Initialize schema
uv run python -m app.database.db --init
```

### Running the API

```bash
# Development mode with auto-reload
uv run uvicorn app.main:app --reload --port 8000

# API available at http://localhost:8000
# Interactive docs at http://localhost:8000/docs
```

## CLI Commands

### Database Management

```bash
# Initialize schema (creates all 9 tables with indexes)
uv run python -m app.database.db --init

# Show ingestion statistics for a ticker
uv run python -m app.database.db --show AAPL
```

**Example output** (`--show AAPL`):
```
Ticker: AAPL | Company: Apple Inc.
Events: 5 | Transcripts: 5 | Speakers: 75 | Turns: 139

Last 5 Events:
  Q1 2026 | 139 turns | Not paywalled
  Q4 2025 | 1 turn    | Paywalled
  Q3 2025 | 1 turn    | Paywalled
```

### Cookie Management

Cookies can be managed via API endpoints or CLI:

```bash
# Check cookie status
curl http://localhost:8000/yahoo/cookies

# Refresh cookies (opens browser if not headless)
curl -X POST http://localhost:8000/yahoo/cookies/refresh

# Delete cookies (for testing)
curl -X DELETE http://localhost:8000/yahoo/cookies
```

## Project Structure

```
backend/
├── app/
│   ├── main.py                 # FastAPI app, routes, CORS config
│   ├── config.py               # Centralized settings and configuration
│   ├── database/
│   │   └── db.py              # Schema, storage, queries
│   ├── ingestion/
│   │   ├── yahoo_ingestion.py # Parsing logic
│   │   └── ingestion_service.py # Orchestration + post-processing
│   ├── nlp/
│   │   └── nlp_service.py     # Organization extraction
│   └── qa/
│       ├── embedding_service.py # Chunking + embeddings
│       └── rag_service.py      # Retrieval + generation
├── tests/
│   ├── conftest.py            # Shared fixtures
│   ├── test_ingestion.py      # 10 tests
│   ├── test_embeddings.py     # 10 tests
│   └── test_rag.py            # 11 tests
└── pyproject.toml             # uv dependencies
```

## Troubleshooting

### Ingestion Issues

**Consent Error (503 Service Unavailable)**
```
YahooConsentError: Detected redirect to consent.yahoo.com
```
- **Cause**: Yahoo Finance cookies expired
- **Fix**: Refresh cookies via API or relaunch browser:
  ```bash
  curl -X POST http://localhost:8000/yahoo/cookies/refresh
  ```
- **Docker**: Set `HEADLESS=true` in environment to use headless mode

**Paywall Detection False Positives**
- Some recent transcripts may be incorrectly marked as paywalled
- Check `transcripts.turn_count` - if > 2, likely not paywalled
- Override by re-ingesting with updated heuristic in `_detect_paywall()`

**Parsing Failures (All 3 Paths Fail)**
- Yahoo Finance may have changed HTML structure
- Check `ingestion_errors` table for details:
  ```sql
  SELECT * FROM ingestion_errors ORDER BY created_at DESC LIMIT 10;
  ```
- Update selectors in `yahoo_ingestion.py` if needed

### Database Issues

**pgvector Extension Not Found**
```
ERROR: type "vector" does not exist
```
- **Cause**: pgvector extension not installed
- **Fix**: Install extension:
  ```sql
  CREATE EXTENSION vector;
  ```
- **Docker**: Use `pgvector/pgvector:pg16` image (already configured)

**HNSW Index Build Slow**
- Building HNSW index on large datasets can take minutes
- Consider using IVFFlat index for faster builds (less accurate):
  ```sql
  CREATE INDEX idx_chunks_embedding ON transcript_chunks
  USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
  ```

**Full-Text Search Returns No Results**
- Check if `full_text_tsv` is populated:
  ```sql
  SELECT id, full_text_tsv IS NOT NULL FROM transcripts LIMIT 5;
  ```
- Re-generate tsvector if needed:
  ```sql
  UPDATE transcripts SET full_text_tsv = to_tsvector('english', full_text);
  ```

### OpenAI API Issues

**Rate Limiting**
```
openai.RateLimitError: Rate limit exceeded
```
- **Embeddings**: Reduce batch size in `generate_embeddings()` (default: 100)
- **Q&A**: Add retry logic with exponential backoff
- **Cost management**: Check usage at https://platform.openai.com/usage

**Timeout Errors**
- Increase timeout in OpenAI client initialization:
  ```python
  client = openai.OpenAI(timeout=60.0)  # Default is 30s
  ```

**Invalid API Key**
- Verify `OPENAI_API_KEY` is set correctly
- Test with:
  ```bash
  curl https://api.openai.com/v1/models \
    -H "Authorization: Bearer $OPENAI_API_KEY"
  ```

### Performance Optimization

**Slow Ingestion**
- Default rate limit: 1 second per transcript
- Adjust in `ingestion_service.py`:
  ```python
  await asyncio.sleep(0.5)  # Reduce to 0.5s if Yahoo allows
  ```

**Slow Vector Search**
- Check if HNSW index exists:
  ```sql
  SELECT indexname FROM pg_indexes WHERE tablename = 'transcript_chunks';
  ```
- Adjust `top_k` in RAG retrieval (lower = faster):
  ```python
  chunks = retrieve_relevant_chunks(question_embedding, top_k=3)
  ```

**Large Database Size**
- `raw_transcriptcontent` JSONB can be large (1-10 MB per transcript)
- Consider archiving old transcripts or removing raw JSON after processing

## Development Tips

### Running Tests

```bash
# All tests
uv run pytest tests/ -v

# Specific module
uv run pytest tests/test_ingestion.py -v

# With coverage
uv run pytest tests/ --cov=app --cov-report=html
```

### Key Dependencies

- **psycopg**: PostgreSQL adapter with async support
- **FastAPI**: ASGI web framework with OpenAPI generation
- **Playwright**: Browser automation (headless Chrome)
- **OpenAI Python SDK**: Embeddings + chat completions
- **BeautifulSoup**: HTML parsing (fallback for Yahoo structure changes)
- **uv**: Fast package manager (10-100x faster than pip)

### Schema Migrations

To add new fields:

1. Update `init_schema()` in `app/database/db.py`
2. Add migration SQL (or use Alembic for production)
3. Update `_store_transcript()` to populate new fields
4. Update API response models in `main.py`

### Debugging Tips

**Enable SQL logging**:
```python
# In db.py
conn.execute("SET log_statement = 'all';")
```

**Check Playwright browser state**:
```python
# In yahoo_ingestion.py
browser = p.chromium.launch(headless=False, slow_mo=1000)  # Slow down actions
```

**Inspect embeddings**:
```sql
SELECT
  text,
  embedding <=> '[0.1, 0.2, ...]'::vector AS distance
FROM transcript_chunks
ORDER BY distance
LIMIT 5;
```
