from types import SimpleNamespace

from src.ingestion.service import DocumentIngestionService
from src.storage.sqlite_store import SQLiteStore


class FakeModel:
    def __init__(self):
        self.calls = 0

    def embed_documents(self, texts):
        self.calls += 1
        return [[0.1, 0.2] for _ in texts]


class FakeVectorStore:
    def __init__(self):
        self.documents = {}

    def add(self, chunks, embeddings):
        self.documents[chunks[0].doc_id] = list(zip(chunks, embeddings, strict=True))

    def delete_document(self, doc_id):
        self.documents.pop(doc_id, None)


def test_ingestion_uses_hash_to_avoid_duplicate_embedding(tmp_path):
    document = tmp_path / "标准.txt"
    document.write_text(
        "食品安全国家标准\n第一章 范围\n本标准规定食品添加剂使用要求。",
        encoding="utf-8",
    )
    store = SQLiteStore(tmp_path / "rag.db")
    model = FakeModel()
    vector_store = FakeVectorStore()
    settings = SimpleNamespace(chunk_size=50, chunk_overlap=10)
    service = DocumentIngestionService(store, vector_store, model, settings)

    first = service.ingest(document)
    second = service.ingest(document)

    assert first.status == "success"
    assert second.status == "skipped"
    assert model.calls == 1
