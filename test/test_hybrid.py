from src.config.settings import Settings
from src.domain.models import DocumentChunk, DocumentMetadata, RetrievedChunk
from src.retrieval.hybrid import HybridRetriever


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
        dense_top_k=2,
        lexical_top_k=2,
        fusion_top_k=3,
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
    metadata = DocumentMetadata(source=f"{chunk_id}.txt")
    chunk = DocumentChunk(chunk_id, "doc", f"content-{chunk_id}", 0, None, "", metadata)
    return RetrievedChunk(chunk=chunk, routes={route})


class FakeDenseStore:
    def search(self, embedding, limit):
        return [_item("shared", "dense"), _item("dense-only", "dense")]


class FakeLexicalStore:
    def lexical_search(self, query, limit):
        return [_item("shared", "lexical"), _item("lexical-only", "lexical")]


class FakeModel:
    def embed_query(self, query):
        return [0.1, 0.2]

    def rerank(self, query, documents, top_n):
        shared_index = documents.index("content-shared")
        other_index = 0 if shared_index != 0 else 1
        return [(shared_index, 0.95), (other_index, 0.4)]


def test_hybrid_retriever_fuses_two_routes_then_reranks(tmp_path):
    retriever = HybridRetriever(
        FakeDenseStore(), FakeLexicalStore(), FakeModel(), _settings(tmp_path)
    )

    results = retriever.retrieve("食品添加剂")

    assert results[0].chunk.chunk_id == "shared"
    assert results[0].routes == {"dense", "lexical"}
    assert results[0].rerank_score == 0.95
