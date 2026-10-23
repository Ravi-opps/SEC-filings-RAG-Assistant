import math

import pytest
from qdrant_client import QdrantClient

from embeddings import (
    DeterministicHashEmbedder,
    EmbeddingClient,
    EmbeddingConfigError,
    EmbeddingError,
)
from ingest_pipeline import (
    default_document_id,
    ingest_html,
    meta_from_filing_result,
)
from qdrant_store import QdrantStore, StorageError, qdrant_point_id
from sec_pipeline import FilingResult
from sentence_records import parse_filing_html
from tests.structure_fixtures import META, SEC_STYLE_HTML


@pytest.fixture()
def store():
    instance = QdrantStore(
        client=QdrantClient(location=":memory:"),
        collection_name="test_sec_sentences",
    )
    yield instance
    instance.close()


def _ingest(store, *, recreate=False, embedder=None):
    return ingest_html(
        SEC_STYLE_HTML,
        META,
        store,
        embedder or DeterministicHashEmbedder(32),
        recreate=recreate,
    )


def test_ingest_counts_and_upserts(store):
    summary, parsed = _ingest(store)
    assert summary.document_id == META.document_id
    assert summary.records == len(parsed.records) == store.count(document_id=META.document_id)
    assert summary.points_upserted == summary.records
    assert summary.vector_size == 32
    assert summary.parser_version == "1"
    assert summary.embedding_model == "text-embedding-3-large"
    assert summary.links == len(parsed.links) == 6
    assert summary.links_resolved == 4
    assert summary.paragraphs == parsed.unit_counts["paragraph"]
    assert summary.tables == parsed.unit_counts["table"]
    assert summary.list_items == parsed.unit_counts["list_item"]


def test_vectors_are_separate_from_payload(store):
    summary, parsed = _ingest(store)
    points, _ = store.client.scroll(
        collection_name=store.collection_name,
        limit=100,
        with_payload=True,
        with_vectors=True,
    )
    assert len(points) == summary.records
    point = points[0]
    assert "vector" not in point.payload
    assert "embedding_text" in point.payload
    assert point.payload["embedding_text"].startswith(
        " > ".join(point.payload["hierarchy"]) + "\n\n"
    )
    assert len(point.vector) == 32
    assert point.id == qdrant_point_id(point.payload["point_id"])


def test_ingest_is_idempotent(store):
    first, _ = _ingest(store)
    before, _ = store.client.scroll(
        collection_name=store.collection_name,
        limit=100,
        with_payload=True,
    )
    second, _ = _ingest(store)
    after, _ = store.client.scroll(
        collection_name=store.collection_name,
        limit=100,
        with_payload=True,
    )
    assert first.points_upserted == second.points_upserted
    assert [point.id for point in before] == [point.id for point in after]
    assert store.count() == first.records


def test_recreate_replaces_the_collection(store):
    first, _ = _ingest(store)
    second, _ = _ingest(store, recreate=True)
    assert first.points_upserted == second.points_upserted
    assert store.count() == second.records


def test_point_ids_are_deterministic():
    first = parse_filing_html(SEC_STYLE_HTML, META)
    second = parse_filing_html(SEC_STYLE_HTML, META)
    assert [record.point_id for record in first.records] == [
        record.point_id for record in second.records
    ]
    assert qdrant_point_id(first.records[0].point_id) == qdrant_point_id(
        second.records[0].point_id
    )


def test_search_filters_by_item(store):
    _ingest(store)
    hits = store.search(
        DeterministicHashEmbedder(32).embed("risk factors"),
        limit=10,
        document_id=META.document_id,
        item_id="1A",
    )
    assert hits
    assert all(hit.payload["item_id"] == "1A" for hit in hits)
    scores = [hit.score for hit in hits]
    assert scores == sorted(scores, reverse=True)


def test_search_filters_by_document(store):
    _ingest(store)
    other_meta = META.__class__(
        **{**META.__dict__, "document_id": "other_fixture_2025_10k"}
    )
    ingest_html(
        SEC_STYLE_HTML,
        other_meta,
        store,
        DeterministicHashEmbedder(32),
    )
    hits = store.search(
        DeterministicHashEmbedder(32).embed("iPhone"),
        limit=50,
        document_id=other_meta.document_id,
    )
    assert hits
    assert all(hit.payload["document_id"] == other_meta.document_id for hit in hits)


def test_delete_document(store):
    summary, _ = _ingest(store)
    assert store.count() == summary.records
    store.delete_document(META.document_id)
    assert store.count() == 0
    assert store.count(document_id=META.document_id) == 0


def test_upsert_before_ensure_fails(store):
    with pytest.raises(StorageError):
        store.upsert([], [])


def test_empty_document_does_not_create_a_collection(store):
    summary, parsed = ingest_html("   ", META, store, DeterministicHashEmbedder(8))
    assert parsed.records == []
    assert summary.records == 0
    assert summary.points_upserted == 0
    assert not store.client.collection_exists(store.collection_name)


def test_default_document_id():
    assert default_document_id("AAPL", "10-K", "2025-09-27") == "aapl_2025_10k"
    assert default_document_id("msft", "10-Q", None) == "msft_10q"


def test_meta_from_filing_result():
    result = FilingResult(
        ticker="AAPL",
        cik=320193,
        company="Apple Inc.",
        form_type="10-K",
        accession_number="0000320193-25-000079",
        filing_date="2025-10-31",
        report_date="2025-09-27",
        primary_document="aapl-20250927.htm",
        source_url="https://www.sec.gov/example.htm",
        content_type="text/html",
        title="Apple 2025 Form 10-K",
        extracted_text="",
        dump_path="",
        raw_content=b"<html></html>",
    )
    meta = meta_from_filing_result(result)
    assert meta.document_id == "aapl_2025_10k"
    assert meta.ticker == "AAPL"
    assert meta.cik == "320193"
    assert meta.form_type == "10-K"
    assert meta.report_date == "2025-09-27"
    assert meta.source_url == result.source_url


def test_hash_embedder_is_deterministic_and_normalized():
    embedder = DeterministicHashEmbedder(dimension=16)
    first = [embedder.embed(text) for text in ("Apple", "Microsoft")]
    second = [embedder.embed(text) for text in ("Apple", "Microsoft")]
    assert first == second
    assert len(first) == 2
    for vector in first:
        assert len(vector) == 16
        assert math.isclose(math.sqrt(sum(value * value for value in vector)), 1.0)


class _FakeResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return {"data": self._data}


class _FakeSession:
    def __init__(self):
        self.batches = []
        self.urls = []
        self.headers = []

    def post(self, url, headers, json, timeout):
        self.batches.append(list(json["input"]))
        self.urls.append(url)
        self.headers.append(headers)
        data = [
            {"index": index, "embedding": [float(len(text)), 1.0]}
            for index, text in enumerate(json["input"])
        ]
        data.reverse()
        return _FakeResponse(data)


def test_embedding_client_batches_and_orders_results():
    session = _FakeSession()
    client = EmbeddingClient(
        url="http://embedding.test",
        api_key="test-key",
        batch_size=2,
        session=session,
    )
    vectors = client.embed_documents(["a", "bb", "ccc", "dddd", "eeeee"])
    assert [len(batch) for batch in session.batches] == [2, 2, 1]
    assert session.urls[0] == "http://embedding.test/openai/v1/embeddings"
    assert session.headers[0]["api-key"] == "test-key"
    assert vectors == [[1.0, 1.0], [2.0, 1.0], [3.0, 1.0], [4.0, 1.0], [5.0, 1.0]]


def test_embedding_client_rejects_wrong_dimensions():
    class _BadSession(_FakeSession):
        def post(self, url, headers, json, timeout):
            data = [
                {
                    "index": index,
                    "embedding": [1.0] if index == 0 else [1.0, 2.0],
                }
                for index, _text in enumerate(json["input"])
            ]
            return _FakeResponse(data)

    client = EmbeddingClient(
        url="http://embedding.test",
        api_key="test-key",
        batch_size=10,
        session=_BadSession(),
    )
    with pytest.raises(EmbeddingError):
        client.embed_documents(["a", "b"])


def test_embedding_client_requires_configuration(monkeypatch):
    monkeypatch.delenv("EMBED_URL", raising=False)
    monkeypatch.delenv("OPENAI_EMBEDDINGS", raising=False)
    with pytest.raises(EmbeddingConfigError):
        EmbeddingClient(url=None, api_key=None)


def test_embedding_url_variants():
    assert (
        EmbeddingClient(
            url="http://embedding.test/openai/v1",
            api_key="key",
            session=_FakeSession(),
        ).url
        == "http://embedding.test/openai/v1/embeddings"
    )
    assert (
        EmbeddingClient(
            url="http://embedding.test/embeddings/",
            api_key="key",
            session=_FakeSession(),
        ).url
        == "http://embedding.test/embeddings"
    )
