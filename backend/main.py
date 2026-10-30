import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from dotenv import dotenv_values
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from async_sec import AsyncSecClient, resolve_company_async
from auth import CurrentUser, get_current_user, init_db, router as auth_router
from cache import DEFAULT_REDIS_URL, RedisCache, ticker_cache_key
from document_extractor import EmptyExtractionError, ExtractionError
from filing_resolver import (
    FilingNotFoundError,
    MalformedMetadataError,
    TickerNotFoundError,
    normalize_form,
    normalize_ticker,
    parse_cached_company,
)
from input_guardrail import GuardrailConfigError, GuardrailError
from job_queue import QueueUnavailableError, close_producer, publish_filing_job
from retrieval_agent import (
    PlannerConfigError,
    PlannerError,
    ResponderError,
    close_agent_runtime,
    run_agent,
    stream_agent,
)
from retrieval_service import RetrievalConfigError, RetrievalError
from sec_client import (
    SecConnectionError,
    SecError,
    SecHTTPError,
    SecInvalidJSONError,
    SecTimeoutError,
)
from sec_pipeline import fetch_filing

ENV_PATH = Path(__file__).resolve().parent / ".env"

LOGGER = logging.getLogger("main")

_loaded_keys: set[str] = set()

@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        init_db()
    except Exception as exc:
        LOGGER.warning("database schema is not ready: %s", exc)
    yield
    try:
        await close_producer()
    finally:
        await close_agent_runtime()


app = FastAPI(title="RAG Backend", lifespan=lifespan)
app.include_router(auth_router)


class QueryRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    user_prompt: str = Field(..., alias="user prompt", min_length=1)


class SourceInfo(BaseModel):
    citation: int
    url: str
    document_url: str
    link_type: Literal["anchor", "text_fragment", "document"]
    title: str
    ticker: str | None = None
    cik: str | None = None
    form_type: str | None = None
    accession_number: str | None = None
    document_id: str | None = None
    year: int | None = None
    part: str | None = None
    item_id: str | None = None
    item_title: str | None = None
    section: list[str] = Field(default_factory=list)
    anchor_id: str | None = None
    source_dom_id: str | None = None
    point_id: str | None = None
    snippet: str | None = None
    relevance_score: float | None = None


class QueryResponse(BaseModel):
    type: Literal["conversational", "retrieval", "blocked"]
    answer: str | None = None
    sources: list[SourceInfo] = Field(default_factory=list)


def mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def reload_env() -> dict[str, str]:
    global _loaded_keys
    raw = dotenv_values(ENV_PATH) if ENV_PATH.exists() else {}
    current = {key: (value if value is not None else "") for key, value in raw.items()}

    for key in _loaded_keys - set(current):
        os.environ.pop(key, None)
    for key, value in current.items():
        os.environ[key] = value
    _loaded_keys = set(current)
    return current


reload_env()

_cache: RedisCache | None = None
_cache_url: str | None = None


def get_cache() -> RedisCache:
    """Return a process-wide Redis cache, rebuilt if REDIS_URL changes."""
    global _cache, _cache_url
    url = os.getenv("REDIS_URL") or DEFAULT_REDIS_URL
    if _cache is None or _cache_url != url:
        _cache = RedisCache(url)
        _cache_url = url
    return _cache


@app.get("/")
def root():
    return {"service": "rag-backend", "docs": "/docs"}


@app.get("/health")
def health():
    values = reload_env()

    if not ENV_PATH.exists():
        return JSONResponse(
            status_code=500,
            content={"all_loaded": False, "error": f"{ENV_PATH} not found"},
        )

    envs = {
        key: {
            "loaded": key in os.environ,
            "has_value": bool(value),
            "preview": mask(value),
        }
        for key, value in values.items()
    }

    return {
        "all_loaded": all(item["loaded"] for item in envs.values()),
        "keys_loaded": sum(1 for item in envs.values() if item["loaded"]),
        "keys_with_values": sum(1 for item in envs.values() if item["has_value"]),
        "total_keys": len(envs),
        "env_file": str(ENV_PATH),
        "envs": envs,
    }


@app.get("/sec/filing")
def sec_filing(
    ticker: str = Query(..., description="Stock ticker, e.g. AAPL"),
    form_type: str = Query("10-K", description="SEC form, e.g. 10-K, 10-Q, 8-K"),
    report_year: int | None = Query(
        None, description="Fiscal/report period year, matched against reportDate"
    ),
    filing_year: int | None = Query(
        None, description="Calendar filing year, matched against filingDate"
    ),
):
    """Resolve a ticker, fetch the SEC filing document, and save it to dump/.

    ``report_year`` and ``filing_year`` are different criteria: the first uses
    the filing's reportDate (fiscal period end), the second uses its filingDate
    (when it was submitted). If both are supplied, both must match.
    """
    try:
        result = fetch_filing(
            ticker,
            form_type,
            report_year=report_year,
            filing_year=filing_year,
            cache=get_cache(),
        )
    except (TickerNotFoundError, FilingNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (MalformedMetadataError, EmptyExtractionError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ExtractionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except (SecTimeoutError, SecConnectionError) as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except (SecHTTPError, SecInvalidJSONError, SecError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {
        "ticker": result.ticker,
        "cik": result.cik,
        "company": result.company,
        "form_type": result.form_type,
        "accession_number": result.accession_number,
        "filing_date": result.filing_date,
        "report_date": result.report_date,
        "primary_document": result.primary_document,
        "source_url": result.source_url,
        "content_type": result.content_type,
        "title": result.title,
        "text_length": len(result.extracted_text),
        "extracted_text": result.extracted_text,
        "raw_content_length": len(result.raw_content),
        "dump_path": result.dump_path,
    }


@app.post("/ingest/{ticker}", status_code=202)
async def ingest_ticker(
    ticker: str,
    form_type: str = Query("10-K", description="SEC form, e.g. 10-K, 10-Q"),
    report_year: int | None = Query(
        None, description="Fiscal/report period year, matched against reportDate"
    ),
    filing_year: int | None = Query(
        None, description="Calendar filing year, matched against filingDate"
    ),
    _user: CurrentUser = Depends(get_current_user),
):
    """Resolve a ticker and enqueue an asynchronous filing download.

    Redis caches only the company name, CIK, and year. On a cache hit the
    stored resolution is returned immediately. On a miss the ticker is
    resolved over async HTTP, the job is published to Kafka, and the
    resolution is cached. The ``loader`` consumer group downloads the HTML.
    """
    reload_env()
    wanted = normalize_ticker(ticker)
    form = normalize_form(form_type)
    year = report_year if report_year is not None else filing_year
    cache = get_cache()

    cached = parse_cached_company(cache.get_json(ticker_cache_key(wanted)))
    if cached is not None:
        cik, company, cached_year = cached
        return JSONResponse(
            status_code=200,
            content={
                "ticker": wanted,
                "cik": cik,
                "company": company,
                "year": cached_year if cached_year is not None else year,
                "form_type": form,
                "cached": True,
                "status": "cached",
            },
        )

    client = AsyncSecClient()
    try:
        cik, company, _ = await resolve_company_async(
            wanted, client, cache=None, year=year
        )
    except (TickerNotFoundError, FilingNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except MalformedMetadataError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (SecTimeoutError, SecConnectionError) as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except (SecHTTPError, SecInvalidJSONError, SecError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        await client.aclose()

    job = {
        "ticker": wanted,
        "cik": cik,
        "company": company,
        "form_type": form,
        "report_year": report_year,
        "filing_year": filing_year,
        "requested_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        await publish_filing_job(job)
    except QueueUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    cache.set_json(
        ticker_cache_key(wanted),
        {"cik": cik, "company": company, "year": year},
    )
    return JSONResponse(
        status_code=202,
        content={
            "ticker": wanted,
            "cik": cik,
            "company": company,
            "year": year,
            "form_type": form,
            "cached": False,
            "status": "queued",
        },
    )


@app.post("/query", response_model=QueryResponse)
async def query(
    request: QueryRequest,
    conversation_id: str | None = Header(
        default=None,
        alias="X-Conversation-Id",
    ),
    _user: CurrentUser = Depends(get_current_user),
):
    """Route a user prompt and answer, retrieving filing context when needed."""
    reload_env()
    prompt = request.user_prompt.strip()
    if not prompt:
        raise HTTPException(status_code=422, detail="user prompt cannot be empty")
    conversation_id = (conversation_id or "default").strip()[:128] or "default"

    try:
        state = await run_agent(prompt, conversation_id=conversation_id)
    except (PlannerConfigError, RetrievalConfigError, GuardrailConfigError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (
        PlannerError,
        ResponderError,
        RetrievalError,
        GuardrailError,
    ) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    route = state.get("plan", {}).get("type")
    if route not in ("conversational", "retrieval", "blocked"):
        raise HTTPException(
            status_code=502,
            detail="planner did not return a valid route",
        )
    answer = state.get("final_ans") or None
    sources = [
        SourceInfo.model_validate(source)
        for source in state.get("sources") or []
    ]
    return QueryResponse(type=route, answer=answer, sources=sources)


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/query/stream")
async def query_stream(
    request: QueryRequest,
    conversation_id: str | None = Header(
        default=None,
        alias="X-Conversation-Id",
    ),
    _user: CurrentUser = Depends(get_current_user),
):
    """Stream a routed answer as Server-Sent Events."""
    reload_env()
    prompt = request.user_prompt.strip()
    if not prompt:
        raise HTTPException(status_code=422, detail="user prompt cannot be empty")
    conversation_id = (conversation_id or "default").strip()[:128] or "default"

    async def events():
        yield _sse("status", {"stage": "accepted"})
        try:
            async for item in stream_agent(
                prompt,
                conversation_id=conversation_id,
            ):
                event = str(item.get("event") or "message")
                yield _sse(event, item.get("data"))
        except (
            PlannerConfigError,
            RetrievalConfigError,
            GuardrailConfigError,
        ) as exc:
            yield _sse("error", {"detail": str(exc), "status": 503})
        except (
            PlannerError,
            ResponderError,
            RetrievalError,
            GuardrailError,
        ) as exc:
            yield _sse("error", {"detail": str(exc), "status": 502})
        except Exception:
            LOGGER.exception("streaming query failed")
            yield _sse(
                "error",
                {"detail": "The retrieval request failed.", "status": 500},
            )

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
