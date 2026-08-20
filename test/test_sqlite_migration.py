import sqlite3

from src.storage.sqlite_store import SQLiteStore


def test_legacy_database_is_migrated_without_losing_messages(tmp_path):
    database = tmp_path / "legacy.db"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE documents (
            doc_id TEXT PRIMARY KEY, file_name TEXT NOT NULL,
            content_hash TEXT NOT NULL UNIQUE, metadata_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE chunks (
            chunk_id TEXT PRIMARY KEY, doc_id TEXT NOT NULL, content TEXT NOT NULL,
            chunk_index INTEGER NOT NULL, page_number INTEGER, section TEXT NOT NULL,
            metadata_json TEXT NOT NULL
        );
        CREATE TABLE sessions (
            session_id TEXT PRIMARY KEY, title TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
            role TEXT NOT NULL, content TEXT NOT NULL,
            trace_json TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL
        );
        INSERT INTO sessions VALUES ('s1', '旧会话', '2025-01-01', '2025-01-01');
        INSERT INTO messages(session_id, role, content, trace_json, created_at)
        VALUES ('s1', 'assistant', '旧回答', '["旧轨迹"]', '2025-01-01');
        """
    )
    connection.commit()
    connection.close()

    store = SQLiteStore(database)
    message = store.get_messages("s1")[0]

    assert message["content"] == "旧回答"
    assert message["citations_json"] == "[]"
    assert message["total_tokens"] is None
    assert store.get_session("s1")["model_profile_id"] is None


def test_session_model_rename_and_token_usage_are_persisted(tmp_path):
    store = SQLiteStore(tmp_path / "rag.db")
    store.create_session("s1")
    store.rename_session("s1", "食品添加剂查询")
    store.set_session_model("s1", "model-1")
    store.save_message("s1", "user", "问题")
    store.save_message(
        "s1",
        "assistant",
        "回答",
        citations=[{"label": 1, "doc_id": "d1"}],
        model_profile_id="model-1",
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
    )

    assert store.get_session("s1")["title"] == "食品添加剂查询"
    assert store.get_session("s1")["model_profile_id"] == "model-1"
    assert store.session_token_total("s1") == 15
    assert '"doc_id": "d1"' in store.get_messages("s1")[-1]["citations_json"]
