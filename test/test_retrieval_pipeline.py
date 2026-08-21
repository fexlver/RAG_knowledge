from src.config.settings import Settings
from src.domain.models import DocumentChunk, DocumentMetadata, RetrievedChunk
from src.retrieval.hybrid import HybridRetriever
from src.retrieval.pipeline import RetrievalPipelineConfig
from src.storage.sqlite_store import SQLiteStore


def _settings(tmp_path):
    return Settings(
        project_root=tmp_path,
        dashscope_api_key="test",
        dashscope_base_url="https://example.test",
        qwen_model="qwen",
        embedding_model="embedding",
        embedding_dimension=2,
        rerank_model="rerank",
        milvus_host="localhost",
        milvus_port=19530,
        milvus_collection="test",
        sqlite_path=tmp_path / "test.db",
        upload_dir=tmp_path / "uploads",
        dense_top_k=3,
        lexical_top_k=3,
        fusion_top_k=4,
        rerank_top_k=2,
        rrf_k=60,
        rerank_min_score=0.2,
        chunk_size=100,
        chunk_overlap=20,
        max_agent_steps=4,
        embedding_batch_size=10,
        citation_limit=5,
        history_message_limit=20,
    )


def _item(chunk_id: str, route: str) -> RetrievedChunk:
    chunk = DocumentChunk(
        chunk_id,
        "doc",
        f"content-{chunk_id}",
        0,
        None,
        "",
        DocumentMetadata(source="test.txt"),
    )
    return RetrievedChunk(chunk=chunk, routes={route})


class DenseStore:
    def __init__(self):
        self.calls = 0

    def search(self, _embedding, _limit):
        self.calls += 1
        item = _item("dense", "dense")
        item.dense_score = 0.8
        return [item]


class LexicalStore:
    def __init__(self):
        self.calls = 0

    def lexical_search(self, _query, _limit):
        self.calls += 1
        item = _item("lexical", "lexical")
        item.lexical_score = 0.9
        return [item]


class Model:
    def __init__(self):
        self.rerank_calls = 0

    def embed_query(self, _query):
        return [0.1, 0.2]

    def rerank(self, _query, _documents, _top_n):
        self.rerank_calls += 1
        return [(0, 0.95)]


def test_pipeline_plugins_stream_progress_and_persist_composition(tmp_path):
    store = SQLiteStore(tmp_path / "rag.db")
    dense = DenseStore()
    lexical = LexicalStore()
    model = Model()
    pipeline = HybridRetriever(
        dense,
        lexical,
        model,
        _settings(tmp_path),
        config_loader=lambda: store.get_setting("retrieval_pipeline"),
        config_saver=lambda value: store.set_setting("retrieval_pipeline", value),
    )

    default_events = list(pipeline.retrieve_stream("食品安全"))
    progress = [item.progress for item in default_events if item.progress]
    assert [item.status for item in progress[:2]] == ["running", "completed"]
    assert {item.stage for item in progress} == {"retrieval", "fusion", "rerank"}
    assert pipeline.describe()["config"]["retriever_ids"] == ["dense", "lexical"]

    pipeline.configure(RetrievalPipelineConfig(("lexical",), "rrf", False))
    results = pipeline.retrieve("GB 2760")

    assert store.get_setting("retrieval_pipeline")["retriever_ids"] == ["lexical"]
    assert results[0].chunk.chunk_id == "lexical"
    assert dense.calls == 1
    assert lexical.calls == 2
    assert model.rerank_calls == 1
