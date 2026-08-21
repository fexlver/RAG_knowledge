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
    settings = SimpleNamespace(
        chunk_size=50, chunk_overlap=10, upload_dir=tmp_path / "uploads"
    )
    service = DocumentIngestionService(store, vector_store, model, settings)

    first = service.ingest(document)
    second = service.ingest(document)

    assert first.status == "success"
    assert second.status == "skipped"
    assert model.calls == 1
    doc_id = next(iter(vector_store.documents))
    stored = store.get_document(doc_id)
    assert stored is not None
    assert stored["storage_path"]
    assert (settings.upload_dir / stored["storage_path"]).is_file()
    assert stored["parser_name"] == "plain_text"
    assert stored["canonical_path"]
    assert stored["layout_path"]
    assert (settings.upload_dir / stored["canonical_path"]).is_file()
    assert (settings.upload_dir / stored["layout_path"]).is_file()


def test_same_name_can_keep_version_history_and_restore_old_version(tmp_path):
    document = tmp_path / "标准.txt"
    document.write_text("食品安全标准第一版，规定添加剂使用范围。", encoding="utf-8")
    store = SQLiteStore(tmp_path / "rag.db")
    model = FakeModel()
    vector_store = FakeVectorStore()
    settings = SimpleNamespace(
        chunk_size=50, chunk_overlap=10, upload_dir=tmp_path / "uploads"
    )
    service = DocumentIngestionService(store, vector_store, model, settings)

    service.ingest(document)
    first = store.list_documents()[0]
    document.write_text("食品安全标准第二版，扩大添加剂适用范围。", encoding="utf-8")
    service.ingest(document, duplicate_mode="overwrite")

    current = store.list_documents()[0]
    versions = store.list_document_versions(current["doc_id"])
    assert current["version_number"] == 2
    assert current["version_count"] == 2
    assert len(versions) == 2
    assert first["doc_id"] not in vector_store.documents

    service.activate(first["doc_id"])
    restored = store.get_document(first["doc_id"])
    assert restored is not None and restored["is_current"] == 1
    assert first["doc_id"] in vector_store.documents
    assert current["doc_id"] not in vector_store.documents


def test_operation_logs_are_returned_newest_first(tmp_path):
    store = SQLiteStore(tmp_path / "rag.db")
    store.log("文档入库", "a.pdf", "第一条")
    store.log("删除文档版本", "a.pdf", "第二条")

    logs = store.list_logs()

    assert [item["detail"] for item in logs] == ["第二条", "第一条"]
