# SEC Filing RAG

The smallest useful slice is one command:

```powershell
python index_filing.py AAPL --form 10-K --dry-run
```

Run it from `backend/` after installing the Python requirements. It downloads the latest Apple 10-K, parses the HTML in document order, splits prose into sentences, and writes sentence-level records that keep their PART/Item/subsection path. It embeds them with a deterministic local embedder into an in-memory Qdrant collection, then prints a summary and five sample records. No API keys, Docker, or hosted model/vector services are involved.

The full system swaps the dry run's local pieces for Kafka, Redis, and a persistent Qdrant collection. A Next.js client streams questions to FastAPI over SSE; a LangGraph agent loads the signed-in user's long-term memory from Mem0, guards the request, decides whether retrieval is needed, rewrites the query, fetches and reranks filing sentences, and returns an answer plus source links back to the supporting text on SEC EDGAR.

## What you get

- Answers grounded in indexed 10-K, 10-Q, and 8-K filings. Retrieval answers carry numbered sources with direct SEC links.
- Streamed answers. `/query/stream` emits `status`, `sources`, `delta`, and `done` events over SSE, and the chat view renders text as it arrives instead of waiting for the full response.
- A guardrail before the planner. Off-topic prompts, investment advice, and prompt-injection attempts get a fixed refusal (`type: "blocked"`) instead of reaching the retrieval stack.
- Username/password accounts. Signup and login issue a signed JWT, passwords are stored as bcrypt hashes in Postgres, and the chat and ingestion endpoints require a bearer token.
- User-scoped long-term memory. Mem0 stores turns per authenticated user, and each query starts with a memory search whose results are injected as background context. Memory is skipped when `MEM0_KEY` is unset, and a memory failure never fails the request.
- Asynchronous ingestion. `POST /ingest/{ticker}` always queues a worker job for the requested form and year; Redis only avoids re-resolving ticker metadata.
- A pipeline dashboard. The authenticated `/ingest/status` endpoint reports expected versus indexed chunks per filing, and `/dashboard` renders those counts with retry actions.
- Sentence-level citations. Source links prefer an exact HTML anchor, fall back to a `#:~:text=` fragment, and finally to the filing URL.
- Reproducible retrieval evals. Two JSONL sets cover direct, temporal, spanning, comparative, numerical, relationship, and holistic questions, with MRR/Hit@k and keyword-coverage scripts.

## Architecture

The write path and read path are separate. Their shared state is Qdrant for indexed sentences, Mem0 for per-user memory, and Postgres for accounts. The raw HTML in `dump/` lets the index stage retry without downloading the filing again.

```mermaid
flowchart LR
  subgraph Browser["Browser"]
    UI["Filing Room<br/>Next.js 16"]
  end

  subgraph API["FastAPI"]
    AUTH["/auth/*<br/>JWT"]
    QUERY["POST /query<br/>POST /query/stream"]
    INGEST["POST /ingest/{ticker}"]
  end

  subgraph QueryPath["Query path · LangGraph"]
    MEMREAD["Mem0 search<br/>user memories"]
    GUARD["Guardrail<br/>Groq gpt-oss-120b"]
    PLAN["Planner<br/>gpt-5.4-pro<br/>strict JSON"]
    VALIDATE{"Valid planner<br/>JSON schema?"}
    PLANERR["Planner error<br/>HTTP 502"]
    REWRITE["Query rewrite"]
    RETRIEVE["Embed query<br/>Qdrant top 20<br/>rerank top 5"]
    ANSWER["Responder<br/>gpt-6-sol"]
    BLOCK["Fixed refusal"]
    MEMWRITE["Mem0 add turn"]
  end

  subgraph IngestPath["Ingest path · Kafka workers"]
    TOPIC1[["sec-filings"]]
    LOADER["loader_worker<br/>resolve + download"]
    TOPIC2[["sec-filings-index"]]
    CHUNKER["chunk_worker<br/>parse + embed + upsert"]
  end

  subgraph State["State"]
    PG[("Postgres<br/>users")]
    MEM0[("Mem0<br/>user memories")]
    REDIS[("Redis<br/>ticker cache<br/>chunk staging")]
    QDRANT[("Qdrant<br/>sec_sentences")]
    DUMP[("dump/<br/>raw SEC HTML")]
  end

  SEC["SEC EDGAR"]

  UI -->|proxy rewrite| AUTH
  UI -->|proxy rewrite / SSE| QUERY
  UI -->|proxy rewrite| INGEST
  AUTH --> PG
  QUERY --> MEMREAD
  MEMREAD --> MEM0
  MEMREAD --> GUARD
  GUARD -->|allowed| PLAN
  GUARD -->|blocked| BLOCK
  BLOCK --> UI
  PLAN --> VALIDATE
  VALIDATE -->|invalid, retry| PLAN
  VALIDATE -->|valid: conversational| ANSWER
  VALIDATE -->|valid: retrieval| REWRITE
  VALIDATE -->|invalid after retries| PLANERR
  PLANERR --> UI
  REWRITE --> RETRIEVE
  RETRIEVE --> QDRANT
  RETRIEVE --> ANSWER
  ANSWER --> MEMWRITE
  MEMWRITE --> MEM0
  ANSWER --> UI
  INGEST --> REDIS
  INGEST --> TOPIC1
  TOPIC1 --> LOADER
  LOADER --> SEC
  LOADER --> DUMP
  LOADER --> TOPIC2
  TOPIC2 --> CHUNKER
  DUMP --> CHUNKER
  CHUNKER --> REDIS
  CHUNKER --> QDRANT
```

### Ingest path

1. `POST /ingest/{ticker}` normalizes the ticker and form, then checks Redis for a cached CIK, company name, and year. On a miss it resolves the company against SEC and caches the result. It then publishes a job to the `sec-filings` topic and returns `202` with `status: "queued"`. A cache hit sets `cached: true` but still publishes the job, so the same ticker can be re-ingested for a different form or year.
2. `loader_worker.py` (consumer group `loader`) resolves the filing's accession number and primary document, downloads the HTML, writes it under `dump/`, and publishes an index job to `sec-filings-index`.
3. `chunk_worker.py` (consumer group `step2`) parses each document in a process pool and writes every sentence record to Redis in document order. Saving a new parse clears the document's previous index marker.
4. The worker embeds the staged records in batches and upserts them into Qdrant. A staged batch is deleted after its Qdrant upsert succeeds.
5. Each document is marked indexed after its points are upserted. Kafka offsets are committed only after every document in the poll batch succeeds. Replays skip a document when its indexed point count is at least the stored count and the embedding model, parser version, and collection name still match.

`scripts/kafka_migrate.py` creates or reconciles both topics. It is idempotent, only raises partition counts, and applies retention settings only at topic creation, so later operator tuning is not overwritten. The compose broker auto-creates topics, which makes the script optional locally but keeps partition and retention settings explicit.

The split is deliberate: the API does the fast part, and the slow, failure-prone parts run in workers with retries. A failed batch leaves Kafka offsets uncommitted, so the work is retried rather than lost.

### Query path

1. The browser sends `{"user prompt": "..."}` to `/api/backend/query/stream` and renders `status`, `sources`, and `delta` events as they arrive. The non-streaming `/query` endpoint accepts the same payload and returns one JSON object. Next.js rewrites both paths to FastAPI and forwards `X-Conversation-Id` and the bearer token.
2. FastAPI scopes the conversation to the authenticated user as `<user_id>:<conversation_id>` and searches Mem0 for that user's memories. Hits become a system message that labels them as background data, not instructions and not filing evidence.
3. `run_agent` loads the scoped conversation history and starts the LangGraph flow. The guardrail calls Groq `openai/gpt-oss-120b`. A blocked prompt ends the run with a fixed refusal; the planner, retrieval service, and answer model are never called.
4. The planner calls the classifier endpoint and returns `conversational` or `retrieval`. The prompt requires exactly one JSON object with exactly one key, `type`. The app parses the entire response, rejects prose, markdown, extra keys, and any other route, and retries invalid output up to `PLANNER_MAX_ATTEMPTS` (default 2). If every attempt fails, the request returns HTTP 502. The planner sees the conversation history and any memory context, so a follow-up can be answered without another search.
5. For retrieval, the rewriter turns the latest message plus history into a self-contained query. The query is embedded, Qdrant returns the top 20 candidates, and the reranker cuts them to the top 5 that are actually sent to the answer model.
6. The responder receives the numbered chunks and is instructed to cite them as `[1]`, `[2]`. On the streaming route it emits `delta` events and finishes with a `done` payload; the non-streaming route returns the same route, answer, and sources as one JSON object.
7. After a non-blocked answer, FastAPI writes the turn back to Mem0. A memory write failure is logged and ignored.

### Why the pieces look like this

**Sentence records, not fixed-size chunks.** `sentence_records.py` walks the DOM in order and tracks PART, Item, and subsection headings. Every record stores its `hierarchy`, `part`, `item_id`, `item_title`, `source_url`, `anchor_id`, and position in the document. The vector input is `hierarchy breadcrumb + unit text` (a sentence for prose, markdown for tables), not a raw token window. A sentence about pricing pressure is therefore embedded with the section that gives it meaning, and the citation can point at the exact sentence rather than the top of a 200-page filing.

**Tables stay tables.** Real tables become markdown records and large tables are split into row groups (40 rows per group, above 60 rows total). Layout tables and tables with one cell per row are walked as normal content instead of being flattened into meaningless pipes.

**Deterministic IDs.** `make_point_id` hashes the accession number, document ID, DOM key, sentence index, and sentence text, then wraps the hash in a UUIDv5. Qdrant stores the resulting ID. Reprocessing the same sentence produces the same point ID, so retries overwrite instead of duplicating.

**Redis is both a cache and a handoff buffer.** The ticker cache is fail-open: if Redis is unavailable, resolution still works, just uncached. Chunk staging is not optional for the worker path; it is how parsing is separated from embedding and how interrupted batches are resumed.

**The guardrail runs first.** It rejects prompts that are unrelated to SEC filings, ask for investment advice, or try to override the system. That keeps the expensive path focused and makes the refusal deterministic.

**Planner output is validated, not trusted.** The planner prompt is deterministic and ordered, but the app does not rely on the model to obey it. `parse_planner_response` requires a single JSON object with exactly one key, `type`, and only the values `conversational` or `retrieval`; `planner_node` retries invalid output up to `PLANNER_MAX_ATTEMPTS` (default 2), then fails with HTTP 502. This prevents a malformed classifier response from being routed or silently treated as a default.

**Memory is background, not evidence.** `memory_service.py` searches Mem0 with `filters={"user_id": ...}` built from `MEM0_USER_PREFIX` plus the JWT user id, so one account cannot read another account's memories. The injected system message tells the guardrail, planner, and responder to treat memories as untrusted background data, never as instructions and never as filing evidence. Turns are saved only when the route is not `blocked`, so a refusal cannot poison memory. An empty `MEM0_KEY` or `MEMORY_ENABLED=false` turns the feature off, and search or write failures are logged and swallowed.

**Streaming is SSE, not WebSockets.** `/query/stream` emits `status` events for `accepted`, guardrail, planning, retrieving, and answering, `sources` when retrieval finishes, `delta` for answer text, and a final `done` payload. FastAPI sets `Cache-Control: no-cache, no-transform` and `X-Accel-Buffering: no` so proxies do not buffer the stream. The Next rewrite allows 240 seconds for it.

## Quickstart

### Prerequisites

- Python 3.12
- Node.js 20.9 or newer
- Docker, for local Postgres, Redis, and Kafka (or an RDS Postgres instance)
- A Qdrant instance (Qdrant Cloud or a local server)
- Service endpoints and keys for embeddings, reranking, and the Responses-style classifier/answer models
- A Groq API key for the guardrail
- A Mem0 API key for long-term memory (optional; leave `MEM0_KEY` empty to disable it)

### 1. Start Postgres, Redis, and Kafka

```powershell
cd backend
docker compose up -d
```

This starts Postgres on `5433` (container port `5432`), Redis on `6379`, and Kafka on `9092`. The Postgres host port is `5433` so it does not collide with a local PostgreSQL install. Qdrant is not in the compose file; point `QDRANT_URL` at your instance. The `users` table is created on API startup.

To use RDS instead of the compose Postgres, set `RDS_IAM_AUTH=true` and the `RDS_*` keys in `backend/.env`. The API then generates a fresh IAM token for each new pooled connection. See [Using RDS](#using-rds) below.

### 2. Install the backend

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` and fill in the Qdrant, model gateway, and Groq values. Set `JWT_SECRET` to your own random value (at least 32 bytes); login and signup stay disabled with a `503` until it is set. Set `MEM0_KEY` to enable per-user long-term memory, or leave it empty or set `MEMORY_ENABLED=false` to run without it. `.env.example` lists the runtime keys. Never commit `.env`.

### 3. Verify the parser without external services

```powershell
.venv\Scripts\python.exe index_filing.py AAPL --form 10-K --dry-run
```

This uses an in-memory Qdrant collection and a deterministic hash embedder. It still downloads the filing from SEC, so internet access is required. The summary shows record counts, vector size, embedding model, and parser version. The five sample records show the unit type and hierarchy for each record.

### 4. Run the API

```powershell
.venv\Scripts\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload --env-file .env
```

### 5. Create the Kafka topics

Compose enables broker auto-create, so this is optional locally. Run it once to set partitions, retention, and replication explicitly:

```powershell
.venv\Scripts\python.exe scripts\kafka_migrate.py --dry-run
.venv\Scripts\python.exe scripts\kafka_migrate.py
```

The script is idempotent: it creates missing topics, raises partition counts when the configured value is higher, and leaves existing topic config alone. From the repository root, run it inside the compose network with `docker compose run --rm backend python scripts/kafka_migrate.py`.

### 6. Run both workers

In two more terminals, from `backend/`:

```powershell
.venv\Scripts\python.exe loader_worker.py
```

```powershell
.venv\Scripts\python.exe chunk_worker.py
```

The loader consumes `sec-filings`; the chunk worker consumes `sec-filings-index`. Keep both running while you ingest.

### 7. Run the frontend

Create `frontend/.env` with the API origin before the first build:

```text
BACKEND_URL=http://127.0.0.1:8000
```

Then:

```powershell
cd ..\frontend
npm install
npm run dev
```

`next.config.ts` throws if `BACKEND_URL` is missing, so `npm run dev` and `npm run build` both fail fast without it. Open `http://localhost:3000`. In Docker the build arg sets the in-network URL, `http://backend:8000`.

### 8. Index a filing

Use the **Index filing** button in the UI, or call the endpoint:

```powershell
curl.exe -X POST "http://localhost:8000/ingest/AAPL?form_type=10-K&report_year=2025" -H "Authorization: Bearer <token>"
```

You can also bypass Kafka and write directly to Qdrant with the CLI, from `backend/`:

```powershell
.venv\Scripts\python.exe index_filing.py AAPL --form 10-K --report-year 2025
```

The CLI path is useful for a single controlled ingest. The API path is the one that exercises the background pipeline.

### Run the full stack with Docker Compose

From the repository root:

```powershell
docker compose up --build
```

This builds and runs the frontend, API, both Kafka workers, Postgres, Redis, and Kafka:

| Service | URL |
| --- | --- |
| Frontend | `http://localhost:3000` |
| API and docs | `http://localhost:8001` and `http://localhost:8001/docs` |
| Postgres | `localhost:5433` |
| Redis | `localhost:6379` |
| Kafka | `localhost:9092` |

`backend/.env` is loaded as an optional env file, so `JWT_SECRET`, `MEM0_KEY`, and the provider keys reach the API and workers. Compose overrides `DATABASE_URL`, `REDIS_URL`, and `KAFKA_BOOTSTRAP_SERVERS` with the in-network service names, and builds the frontend with `BACKEND_URL=http://backend:8000`. When `RDS_IAM_AUTH=true`, the API uses the `RDS_*` keys and ignores the compose `DATABASE_URL`; the API container still needs AWS credentials for token generation. Set `JWT_SECRET` in that file before starting. If Qdrant runs on the host rather than in the cloud, use `host.docker.internal` in `QDRANT_URL` instead of `localhost`. Override host ports with `FRONTEND_PORT`, `BACKEND_PORT`, `POSTGRES_PORT`, `REDIS_PORT`, and `KAFKA_PORT`. Stop with `docker compose down`; add `-v` to delete the volumes.

## API

| Endpoint | What it does |
| --- | --- |
| `GET /` | Returns the service name and a link to `/docs`. |
| `POST /auth/signup` | Creates an account and returns `{access_token, token_type, expires_in, user}`. Usernames are lowercase, `3–32` chars of `a-z`, `0-9`, `_`, `.`, `-`; passwords are `8–72` chars. |
| `POST /auth/login` | Verifies credentials and returns the same token payload. Invalid credentials return a generic `401`. |
| `GET /auth/me` | Returns the current user. Requires `Authorization: Bearer <token>`. |
| `POST /query` | Routes a prompt through the agent and returns `{type, answer, sources}`. Loads and saves user memory when Mem0 is configured. Requires `Authorization: Bearer <token>`. |
| `POST /query/stream` | Same request and auth as `/query`, streamed as SSE: `status`, `sources`, `delta`, `done`, and `error`. Requires `Authorization: Bearer <token>`. |
| `POST /ingest/{ticker}` | Resolves a ticker and queues a download job. Every request publishes a job so a ticker can be loaded for different forms or years. Query parameters: `form_type`, `report_year`, `filing_year`. Requires `Authorization: Bearer <token>`. |
| `GET /ingest/status` | Reports expected and indexed chunks per filing along with per-document index state. Filters: `ticker`, `form_type`, `report_year`, `document_id`. Requires `Authorization: Bearer <token>`. |
| `GET /sec/filing` | Resolves, downloads, extracts, and dumps one filing synchronously. Useful for inspection, not for indexing. |
| `GET /health` | Reports which `.env` keys loaded and whether each has a value. No secret previews. Treat it as an internal diagnostic. |

`/query` and `/query/stream` accept an optional `X-Conversation-Id` header. Use the same ID across turns to keep server-side context; the server namespaces it with the authenticated user id.

The response `type` is one of:

- `conversational`: answered from history or general conversation; `sources` is empty.
- `retrieval`: answered from filing context; `sources` lists the retrieved chunks with citation numbers.
- `blocked`: the guardrail refused the prompt; the answer is the fixed refusal.

Each source has a `citation` number that matches the numbered context given to the answer model. `link_type` is one of:

| `link_type` | Where the link goes |
| --- | --- |
| `anchor` | An exact HTML anchor in the SEC filing, when the record has an `anchor_id` or `source_dom_id`. |
| `text_fragment` | A `#:~:text=` URL built from the retrieved text or the last section heading, used when no anchor exists. |
| `document` | The filing URL without a fragment. |

## Evaluation

Two JSONL sets live in `backend/`. `evalset.jsonl` has 28 questions (four per question type); `eval2.jsonl` has 49 questions (seven per type). Both cover Direct Fact, Temporal, Spanning, Comparative, Numerical, Relationship, and Holistic questions against Apple's 2025 Form 10-K. Each row carries a question, reference answer, answer chunks, and source document metadata.

The answer chunks are synthesized evidence summaries, not gold chunk IDs. Relevance is therefore measured by covering the evidence text, not by looking for an exact point ID. The scripts use the production path: embed the question, fetch 20 candidates from Qdrant for `aapl_2025_10k`, rerank them, and inspect only the top 5 chunks. They do not call the answer model.

Run both evals from `backend/`:

```powershell
.venv\Scripts\python.exe eval_retrieval.py --evalset eval2.jsonl --json-out eval2_results.json
.venv\Scripts\python.exe eval_keyword_coverage.py --evalset eval2.jsonl --json-out eval2_keyword_coverage.json
```

`eval_retrieval.py` reports MRR, Hit@1/3/5, mean best evidence coverage, per-type summaries, and `no_match` / `failed` counts. `eval_keyword_coverage.py` reports macro coverage, micro coverage (matched keywords / expected keywords), top-1 coverage, and the share of questions with every expected keyword present. Its default `grounded-reference` keyword source keeps reference-answer terms that also occur in the answer chunks, so derived figures absent from the filing do not count against retrieval. Use `--keyword-source reference` for full reference-answer coverage or `--keyword-source answer-chunks` for source-only coverage.

Latest reference run against 1,242 AAPL chunks:

| Question type | n | MRR | Hit@1 | Hit@5 | Keyword cov | Micro cov |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Direct Fact | 7 | 1.0000 | 1.000 | 1.000 | 1.000 | 1.000 |
| Temporal | 7 | 1.0000 | 1.000 | 1.000 | 0.871 | 0.889 |
| Spanning | 7 | 1.0000 | 1.000 | 1.000 | 0.768 | 0.735 |
| Comparative | 7 | 0.9286 | 0.857 | 1.000 | 0.903 | 0.902 |
| Numerical | 7 | 0.8571 | 0.857 | 0.857 | 0.643 | 0.700 |
| Relationship | 7 | 0.8571 | 0.857 | 0.857 | 0.783 | 0.755 |
| Holistic | 7 | 0.8857 | 0.857 | 1.000 | 0.514 | 0.538 |
| **Overall** | **49** | **0.9327** | **0.918** | **0.959** | **0.783** | **0.728** |

The two MRR misses were `NQ07` and `RQ05`: both returned a related MD&A chunk, but no evidence span cleared the default `0.35` relevance threshold. Keyword coverage falls most on holistic and spanning questions, where a correct top-5 set can still omit individual evidence terms. Thresholds, numeric weighting, top-k/final-k, and per-type filtering are configurable; see `--help` on either script. Generated JSON reports are ignored by git.

## Configuration

`.env.example` lists the runtime keys. The topic migration script also reads the `KAFKA_*` tuning keys below. The groups that matter most:

| Variables | Purpose |
| --- | --- |
| `QDRANT_URL`, `QDRANT_API_KEY`, `QDRANT_API`, `QDRANT_COLLECTION` | Vector store connection and collection. Default collection: `sec_sentences`. |
| `EMBED_URL`, `OPENAI_EMBEDDINGS`, `EMBED_MODEL` | Embedding endpoint and model. Default: `text-embedding-3-large`. |
| `CLASSIFIER_URL`, `CLASSIFIER_NAME`, `CLASSIFIER_KEY` | Planner and query-rewrite model. Default model: `gpt-5.4-pro`. |
| `PLANNER_MAX_ATTEMPTS` | Planner attempts when the classifier returns invalid JSON or a route outside the allowed schema. Default: `2`. |
| `SOL_URL`, `OPENAI_SOL` | Answer model endpoint and key. Default model: `gpt-6-sol`. |
| `RERANK_URL`, `RERANK_KEY`, `RERANK_MODEL` | Cohere-style rerank endpoint. |
| `GROQ` or `GROQ_API_KEY`, `GROQ_URL`, `GROQ_GUARDRAIL_MODEL` | Guardrail model. Default: `openai/gpt-oss-120b`. |
| `REDIS_URL`, `REDIS_TTL_SECONDS` | Ticker cache and chunk staging. |
| `DATABASE_URL` | SQLAlchemy URL for the auth database. Default: `postgresql+psycopg://rag:rag@localhost:5433/rag`. Ignored when `RDS_IAM_AUTH=true`. |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | Credentials used by the compose Postgres service. Defaults: `rag`/`rag`/`rag`. |
| `POSTGRES_PORT` | Host port for the compose Postgres service. Default: `5433`. Update `DATABASE_URL` if you change it. |
| `RDS_IAM_AUTH`, `RDS_HOST`, `RDS_PORT`, `RDS_DATABASE`, `RDS_USER`, `RDS_REGION` | RDS IAM auth. When `RDS_IAM_AUTH=true`, these replace `DATABASE_URL`. The AWS principal needs `rds-db:connect`, and the database user needs `GRANT rds_iam`. |
| `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_POOL_RECYCLE` | SQLAlchemy pool sizing and recycle age in seconds. Defaults: `5`, `10`, `600`. The recycle age keeps pooled connections below the 15-minute IAM token lifetime. |
| `JWT_SECRET` | Signing key for access tokens. Required for signup, login, and protected routes; unset means `503`. |
| `JWT_ALGORITHM`, `JWT_EXPIRE_MINUTES` | Token algorithm (`HS256`, `HS384`, or `HS512`) and lifetime. Defaults: `HS256`, `1440` minutes. |
| `MEM0_KEY`, `MEMORY_ENABLED`, `MEM0_USER_PREFIX` | Per-user long-term memory. Memory is off without `MEM0_KEY` or with `MEMORY_ENABLED=false`; the user-id prefix defaults to `secrag:user:`. |
| `KAFKA_BOOTSTRAP_SERVERS`, `KAFKA_TOPIC`, `KAFKA_STEP2_TOPIC` | Kafka connection and the two topics. |
| `KAFKA_TOPIC_PARTITIONS`, `KAFKA_STEP2_TOPIC_PARTITIONS`, `KAFKA_TOPIC_REPLICATION_FACTOR`, `KAFKA_TOPIC_RETENTION_MS` | Read by `scripts/kafka_migrate.py`. Defaults: 3, 3, 1, and 604800000 (7 days). |
| `CHUNK_PROCESSES`, `CHUNK_EMBED_BATCH_SIZE`, `CHUNK_UPLOAD_BATCH_SIZE` | Parser concurrency and embedding/upsert batch sizes. |
| `BACKEND_URL` | Frontend-only rewrite target. Required at build time; `next.config.ts` throws if unset. Not read by FastAPI. |

### Using RDS

1. Enable IAM database authentication on the RDS instance.
2. Create a dedicated app user and grant it `rds_iam` plus the table privileges it needs. Do not run the API as the `postgres` master user.

```sql
CREATE ROLE rag_app LOGIN;
GRANT rds_iam TO rag_app;
GRANT CONNECT ON DATABASE postgres TO rag_app;
GRANT USAGE ON SCHEMA public TO rag_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO rag_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO rag_app;
```

3. Give the AWS principal `rds-db:connect` for the database user ARN.
4. Set the RDS keys in `backend/.env`:

```text
RDS_IAM_AUTH=true
RDS_HOST=your-instance.region.rds.amazonaws.com
RDS_PORT=5432
RDS_DATABASE=postgres
RDS_USER=rag_app
RDS_REGION=ap-south-2
DB_POOL_RECYCLE=600
```

5. Start the API with `--env-file .env` so the keys reach the process.

The engine calls `generate_db_auth_token` for each new pooled connection. Existing connections stay usable after the token expires; `pool_pre_ping` plus `DB_POOL_RECYCLE=600` replaces them before the 15-minute token lifetime ends. For multiple API replicas or heavier pooling, use RDS Proxy or the AWS Advanced Python Wrapper.

The ticker cache is fail-open. The chunk worker's Redis dependency is not; it needs Redis for staging and index markers.

## Project layout

```text
.
├── backend/
│   ├── main.py                 FastAPI routes, request/response models, and memory wiring
│   ├── auth.py                 user model, bcrypt hashing, JWT, auth routes and dependency
│   ├── memory_service.py       Mem0 client, per-user scoping, memory context formatting
│   ├── retrieval_agent.py      LangGraph guardrail, planner, rewrite, retrieval, responder
│   ├── retrieval_service.py    query embedding, Qdrant search, reranking, source formatting
│   ├── input_guardrail.py      SEC-domain gate and fixed refusal
│   ├── loader_worker.py        Kafka stage 1: resolve, download, dump
│   ├── chunk_worker.py         Kafka stage 2: parse, embed, upsert
│   ├── sentence_records.py     structure-aware sentence parser
│   ├── sentence_splitter.py    abbreviation-aware sentence segmentation
│   ├── ingest_pipeline.py      parse -> embed -> Qdrant, plus ingest summary
│   ├── qdrant_store.py         collection management, point IDs, search, upsert
│   ├── embeddings.py           sync and async embedding clients
│   ├── chunk_store.py          Redis chunk staging and index markers
│   ├── job_queue.py            Kafka producer and topic names
│   ├── sec_pipeline.py         SEC resolve, download, extract, dump
│   ├── document_extractor.py   HTML/PDF text extraction
│   ├── sec_client.py           SEC HTTP client and URL helpers
│   ├── filing_resolver.py      filing selection and ticker normalization
│   ├── async_sec.py            async SEC client and resolution
│   ├── terminal_ui.py          loader download progress
│   ├── cache.py                fail-open Redis JSON cache
│   ├── qdrant_load_status.py   expected/indexed chunk reporting for the dashboard
│   ├── eval_retrieval.py       MRR and Hit@k evaluation on reranked chunks
│   ├── eval_keyword_coverage.py keyword-coverage evaluation on reranked chunks
│   ├── index_filing.py         single-filing CLI
│   ├── processor.py            directory ingestion CLI (parse -> embed -> Qdrant)
│   ├── inspect_filing.py       interactive filing inspection CLI
│   ├── check_agent_memory.py   experimental Azure AI Foundry memory-store probe
│   ├── llm_memory_demo.py      experimental Foundry memory + LLM demo
│   ├── scripts/
│   │   └── kafka_migrate.py    idempotent topic creation and partition migration
│   ├── evalset.jsonl          28-question evaluation set
│   ├── eval2.jsonl            49-question evaluation set
│   ├── docker-compose.yml      Postgres, Redis, and Kafka
│   ├── .env.example            configuration template
│   └── tests/                  parser, resolver, worker, storage, retrieval, eval, auth, memory, and streaming tests
└── frontend/
    ├── next.config.ts          required BACKEND_URL, backend rewrite, proxy timeout
    └── src/
        ├── app/page.tsx        auth gate, chat shell, and streaming request lifecycle
        ├── app/dashboard/      ingestion status dashboard
        ├── components/         auth screen, sidebar, chat view, message list, source cards, composer, ingest dialog, brand mark
        └── lib/                API client (JSON + SSE), auth hook, types, chat store, backend status
```

## Tests

Backend tests run from the repository root:

```powershell
backend\.venv\Scripts\python.exe -m pytest
```

`pytest.ini` points at `backend/tests`. The suite covers sentence splitting, structure records, SEC downloads and document extraction, ticker and filing resolution, the loader, the Qdrant pipeline, cache behavior, terminal output, retrieval source links, ingest-status reporting, SSE query streaming, Mem0 memory scoping, and both evaluation scripts.

Frontend checks:

```powershell
cd frontend
npm run lint
npx tsc --noEmit
npm run build
```

## Operational notes and limits

- Ingestion is eventually consistent. `/ingest` returns before the workers finish. Use the authenticated `/ingest/status` endpoint or `/dashboard` to compare expected and indexed chunk counts; worker logs remain the source of truth for download and parse errors.
- Every `/ingest` publishes a job, so the same ticker can be loaded for a different form or year. Redis only caches ticker resolution metadata; it no longer short-circuits the request. For a controlled single-file ingest, use `index_filing.py`.
- In local testing, a retrieval query took roughly 30–50 seconds because it makes an embedding call, a Qdrant search, a rerank call, and an answer call. The frontend aborts non-streaming queries at 180 seconds and streaming queries at 240 seconds; the Next rewrite proxy allows 240 seconds. The `experimental.proxyTimeout` setting in `frontend/next.config.ts` exists for that reason. A shorter proxy timeout produces a misleading Next.js `500` even when FastAPI is still working.
- Conversation history is held in the FastAPI process, keyed by `<user_id>:<X-Conversation-Id>`. Restarting the API clears that history. The browser transcript stays in localStorage, but the agent's server-side context does not. Run one API process, or move history to shared storage before scaling out.
- Mem0 memory is external state. It is user-scoped and fail-open: a search or write failure is logged and the query continues. Blocked prompts are not saved. There is no admin endpoint for deleting a user's memories; use the Mem0 API. `MEMORY_ENABLED=false` disables both search and writes.
- Redis is optional for ticker resolution and required for the worker's chunk staging. Postgres, Kafka, Redis, Qdrant, Mem0, and the model endpoints are all external dependencies; the compose file provides the first three.
- The compose broker auto-creates topics. Run `scripts/kafka_migrate.py` when you need explicit partitions, retention, or replication; it never lowers partition counts or rewrites existing topic config.
- `/query`, `/query/stream`, `/ingest/{ticker}`, and `/ingest/status` require a bearer token; `/`, `/health`, and `/sec/filing` stay public. Rate limiting is still absent. Add it before exposing the API outside a trusted network. `.env` values stay server-side; the browser only talks to the Next rewrite.
- `dump/` holds raw SEC filings and is ignored by git. Delete entries to reclaim disk space; reprocessing requires the file or a fresh download.

## Security

Provider keys and `MEM0_KEY` live in `backend/.env`. They are not bundled into the frontend and are not exposed through `/query`. Do not commit `.env`, and do not put real keys in `.env.example`.

Generate a signing key with `python -c "import secrets; print(secrets.token_urlsafe(48))"` and put it in `JWT_SECRET`. Passwords are bcrypt-hashed; tokens are stateless HS256 JWTs with a configurable expiry. The browser keeps the token in `localStorage`, so any XSS on the frontend can read it; keep dependencies patched and avoid injecting untrusted HTML. Logging out clears the token, cached user, and the local conversation list.

Memories are scoped by the JWT user id and are injected as untrusted background context, so a stored prompt injection cannot become an instruction. Treat Mem0 as another external data store: it sees the user's prompts and answers. Review its retention and data-handling settings before pointing it at real users.

`GET /health` reports key names and whether values are set, but no previews. Restrict or remove it if the API is reachable by untrusted clients.
