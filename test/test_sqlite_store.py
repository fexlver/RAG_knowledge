from src.domain.models import DocumentChunk, DocumentMetadata
from src.storage.sqlite_store import SQLiteStore


def test_content_hash_dedup_and_chinese_lexical_search(tmp_path):
    store = SQLiteStore(tmp_path / "rag.db")
    metadata = DocumentMetadata(
        source="GB2760.txt",
        title="食品安全国家标准 食品添加剂使用标准",
        standard_code="GB2760-2024",
        content_hash="abc123",
    )
    chunk = DocumentChunk(
        chunk_id="chunk-1",
        doc_id="doc-1",
        content="食品添加剂应当按照允许使用的品种、使用范围以及最大使用量使用。",
        chunk_index=0,
        page_number=3,
        section="3 使用原则",
        metadata=metadata,
    )

    store.save_document("doc-1", metadata, [chunk])
    result = store.lexical_search("食品添加剂最大使用量是什么？", limit=5)

    assert store.find_document_by_hash("abc123") is not None
    assert result[0].chunk.chunk_id == "chunk-1"
    assert result[0].chunk.page_number == 3


def test_session_lifecycle(tmp_path):
    store = SQLiteStore(tmp_path / "sessions.db")
    store.create_session("session-1")
    store.save_message("session-1", "user", "食品添加剂怎么使用？")
    store.save_message("session-1", "assistant", "请按标准使用。")

    sessions = store.list_sessions()
    messages = store.get_messages("session-1")

    assert sessions[0]["title"] == "食品添加剂怎么使用？"
    assert [item["role"] for item in messages] == ["user", "assistant"]

    store.delete_session("session-1")
    assert store.list_sessions() == []
