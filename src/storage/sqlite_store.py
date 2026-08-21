"""SQLite 元数据、会话与中文关键词索引。"""

from __future__ import annotations

import json
import mimetypes
import sqlite3
from collections.abc import Iterable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import jieba

from src.domain.models import DocumentChunk, DocumentMetadata, RetrievedChunk


def tokenize_for_search(text: str) -> str:
    """把中文文本切成 FTS5 可稳定匹配的空格分隔词元。"""

    tokens = (token.strip().lower() for token in jieba.cut(text))
    # FTS5 会自行忽略标点；查询侧也过滤纯标点，避免生成空短语或语法错误。
    return " ".join(token for token in tokens if any(char.isalnum() for char in token))


class SQLiteStore:
    """管理文档账本、FTS5 关键词索引、会话与操作日志。"""

    def __init__(self, database_path: Path):
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        """幂等创建并增量迁移本地数据结构。"""

        schema = """
        CREATE TABLE IF NOT EXISTS documents (
            doc_id TEXT PRIMARY KEY,
            file_name TEXT NOT NULL,
            content_hash TEXT NOT NULL UNIQUE,
            metadata_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            storage_path TEXT,
            mime_type TEXT,
            series_id TEXT,
            version_number INTEGER NOT NULL DEFAULT 1,
            is_current INTEGER NOT NULL DEFAULT 1,
            file_size INTEGER NOT NULL DEFAULT 0,
            parser_name TEXT,
            parser_version TEXT,
            canonical_path TEXT,
            layout_path TEXT
        );
        CREATE TABLE IF NOT EXISTS chunks (
            chunk_id TEXT PRIMARY KEY,
            doc_id TEXT NOT NULL,
            content TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            page_number INTEGER,
            section TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            locator_json TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY(doc_id) REFERENCES documents(doc_id) ON DELETE CASCADE
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            chunk_id UNINDEXED,
            search_text,
            tokenize='unicode61'
        );
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            model_profile_id TEXT
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            trace_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            citations_json TEXT NOT NULL DEFAULT '[]',
            model_profile_id TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            total_tokens INTEGER,
            refused INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS model_providers (
            provider_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            provider_type TEXT NOT NULL,
            base_url TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            has_api_key INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS model_profiles (
            profile_id TEXT PRIMARY KEY,
            provider_id TEXT NOT NULL,
            model_id TEXT NOT NULL,
            display_name TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(provider_id) REFERENCES model_providers(provider_id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS operation_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            target TEXT NOT NULL,
            detail TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS application_settings (
            setting_key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
        with closing(self._connect()) as connection:
            connection.executescript(schema)
            self._ensure_column(connection, "documents", "storage_path", "TEXT")
            self._ensure_column(connection, "documents", "mime_type", "TEXT")
            self._ensure_column(connection, "documents", "series_id", "TEXT")
            self._ensure_column(
                connection, "documents", "version_number", "INTEGER NOT NULL DEFAULT 1"
            )
            self._ensure_column(
                connection, "documents", "is_current", "INTEGER NOT NULL DEFAULT 1"
            )
            self._ensure_column(
                connection, "documents", "file_size", "INTEGER NOT NULL DEFAULT 0"
            )
            self._ensure_column(connection, "documents", "parser_name", "TEXT")
            self._ensure_column(connection, "documents", "parser_version", "TEXT")
            self._ensure_column(connection, "documents", "canonical_path", "TEXT")
            self._ensure_column(connection, "documents", "layout_path", "TEXT")
            # 旧数据在迁移后各自形成一条独立版本链，不影响既有索引。
            connection.execute(
                "UPDATE documents SET series_id = doc_id WHERE series_id IS NULL OR series_id = ''"
            )
            self._ensure_column(
                connection, "chunks", "locator_json", "TEXT NOT NULL DEFAULT '{}'"
            )
            self._ensure_column(connection, "sessions", "model_profile_id", "TEXT")
            self._ensure_column(
                connection,
                "messages",
                "citations_json",
                "TEXT NOT NULL DEFAULT '[]'",
            )
            self._ensure_column(connection, "messages", "model_profile_id", "TEXT")
            self._ensure_column(connection, "messages", "input_tokens", "INTEGER")
            self._ensure_column(connection, "messages", "output_tokens", "INTEGER")
            self._ensure_column(connection, "messages", "total_tokens", "INTEGER")
            self._ensure_column(
                connection, "messages", "refused", "INTEGER NOT NULL DEFAULT 0"
            )
            connection.commit()

    @staticmethod
    def _ensure_column(
        connection: sqlite3.Connection, table: str, column: str, definition: str
    ) -> None:
        columns = {
            row[1] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def find_document_by_hash(self, content_hash: str) -> dict | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM documents WHERE content_hash = ?", (content_hash,)
            ).fetchone()
        return dict(row) if row else None

    def find_document_by_name(self, file_name: str) -> dict | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM documents WHERE file_name = ? "
                "ORDER BY is_current DESC, version_number DESC, created_at DESC LIMIT 1",
                (file_name,),
            ).fetchone()
        return dict(row) if row else None

    def save_document(
        self,
        doc_id: str,
        metadata: DocumentMetadata,
        chunks: Iterable[DocumentChunk],
        storage_path: str | None = None,
        mime_type: str | None = None,
        series_id: str | None = None,
        version_number: int = 1,
        is_current: bool = True,
        file_size: int = 0,
        parser_name: str | None = None,
        parser_version: str | None = None,
        canonical_path: str | None = None,
        layout_path: str | None = None,
    ) -> None:
        """在一个事务中保存文档及其关键词索引。"""

        chunk_list = list(chunks)
        now = datetime.now(UTC).isoformat()
        metadata_json = json.dumps(metadata.to_dict(), ensure_ascii=False)
        with closing(self._connect()) as connection:
            connection.execute(
                "INSERT INTO documents "
                "(doc_id, file_name, content_hash, metadata_json, created_at, storage_path, "
                "mime_type, series_id, version_number, is_current, file_size, parser_name, "
                "parser_version, canonical_path, layout_path) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    doc_id,
                    metadata.source,
                    metadata.content_hash,
                    metadata_json,
                    now,
                    storage_path,
                    mime_type or mimetypes.guess_type(metadata.source)[0],
                    series_id or doc_id,
                    version_number,
                    int(is_current),
                    max(0, file_size),
                    parser_name,
                    parser_version,
                    canonical_path,
                    layout_path,
                ),
            )
            for chunk in chunk_list:
                connection.execute(
                    "INSERT INTO chunks "
                    "(chunk_id, doc_id, content, chunk_index, page_number, section, metadata_json, locator_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        chunk.chunk_id,
                        chunk.doc_id,
                        chunk.content,
                        chunk.chunk_index,
                        chunk.page_number,
                        chunk.section,
                        json.dumps(chunk.metadata.to_dict(), ensure_ascii=False),
                        json.dumps(chunk.locator, ensure_ascii=False),
                    ),
                )
                searchable = " ".join(
                    filter(
                        None,
                        [
                            chunk.content,
                            chunk.section,
                            chunk.metadata.title,
                            chunk.metadata.standard_code,
                            chunk.metadata.document_type,
                        ],
                    )
                )
                connection.execute(
                    "INSERT INTO chunks_fts(chunk_id, search_text) VALUES (?, ?)",
                    (chunk.chunk_id, tokenize_for_search(searchable)),
                )
            connection.commit()

    def delete_document(self, doc_id: str) -> None:
        with closing(self._connect()) as connection:
            chunk_ids = [
                row[0]
                for row in connection.execute(
                    "SELECT chunk_id FROM chunks WHERE doc_id = ?", (doc_id,)
                ).fetchall()
            ]
            connection.executemany(
                "DELETE FROM chunks_fts WHERE chunk_id = ?",
                ((item,) for item in chunk_ids),
            )
            connection.execute("DELETE FROM documents WHERE doc_id = ?", (doc_id,))
            connection.commit()

    def list_documents(self, current_only: bool = True) -> list[dict]:
        where = "WHERE d.is_current = 1" if current_only else ""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT d.doc_id, d.file_name, d.content_hash, d.metadata_json, d.created_at, "
                "d.storage_path, d.mime_type, d.series_id, d.version_number, d.is_current, "
                "d.file_size, d.parser_name, d.parser_version, d.canonical_path, d.layout_path, "
                "COUNT(c.chunk_id) AS chunk_count, "
                "(SELECT COUNT(*) FROM documents v WHERE v.series_id = d.series_id) AS version_count "
                "FROM documents d LEFT JOIN chunks c ON c.doc_id = d.doc_id "
                f"{where} GROUP BY d.doc_id ORDER BY d.created_at DESC"
            ).fetchall()
        documents: list[dict] = []
        for row in rows:
            item = dict(row)
            item.update(json.loads(item.pop("metadata_json")))
            documents.append(item)
        return documents

    def list_document_versions(self, doc_id: str) -> list[dict]:
        """按版本号倒序返回同一文件的完整版本链。"""

        document = self.get_document(doc_id)
        if not document:
            return []
        series_id = document.get("series_id") or doc_id
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT d.*, COUNT(c.chunk_id) AS chunk_count "
                "FROM documents d LEFT JOIN chunks c ON c.doc_id = d.doc_id "
                "WHERE d.series_id = ? GROUP BY d.doc_id "
                "ORDER BY d.version_number DESC, d.created_at DESC",
                (series_id,),
            ).fetchall()
        versions: list[dict] = []
        for row in rows:
            item = dict(row)
            item.update(json.loads(item.pop("metadata_json")))
            versions.append(item)
        return versions

    def get_document_chunks(self, doc_id: str) -> list[DocumentChunk]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM chunks WHERE doc_id = ? ORDER BY chunk_index", (doc_id,)
            ).fetchall()
        return [self._row_to_chunk(row) for row in rows]

    def set_current_document(self, doc_id: str) -> None:
        """在单个事务中切换版本链的当前版本。"""

        document = self.get_document(doc_id)
        if not document:
            raise KeyError(doc_id)
        series_id = document.get("series_id") or doc_id
        with closing(self._connect()) as connection:
            connection.execute(
                "UPDATE documents SET is_current = CASE WHEN doc_id = ? THEN 1 ELSE 0 END "
                "WHERE series_id = ?",
                (doc_id, series_id),
            )
            connection.commit()

    def next_document_version(self, series_id: str) -> int:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(version_number), 0) + 1 AS next_version "
                "FROM documents WHERE series_id = ?",
                (series_id,),
            ).fetchone()
        return int(row["next_version"])

    def get_document(self, doc_id: str) -> dict | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM documents WHERE doc_id = ?", (doc_id,)
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        item.update(json.loads(item.pop("metadata_json")))
        return item

    def get_chunk(self, chunk_id: str, doc_id: str | None = None) -> dict | None:
        sql = "SELECT * FROM chunks WHERE chunk_id = ?"
        params: tuple = (chunk_id,)
        if doc_id:
            sql += " AND doc_id = ?"
            params = (chunk_id, doc_id)
        with closing(self._connect()) as connection:
            row = connection.execute(sql, params).fetchone()
        if not row:
            return None
        item = dict(row)
        item["locator"] = json.loads(item.pop("locator_json") or "{}")
        item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
        return item

    def lexical_search(self, query: str, limit: int) -> list[RetrievedChunk]:
        """使用中文分词后的 FTS5 BM25 召回关键词结果。"""

        tokens = tokenize_for_search(query).split()
        if not tokens:
            return []
        match_query = " OR ".join(f'"{token.replace(chr(34), "")}"' for token in tokens)
        sql = """
            SELECT c.*, bm25(chunks_fts) AS rank
            FROM chunks_fts
            JOIN chunks c ON c.chunk_id = chunks_fts.chunk_id
            WHERE chunks_fts MATCH ?
            ORDER BY rank
            LIMIT ?
        """
        with closing(self._connect()) as connection:
            rows = connection.execute(sql, (match_query, limit)).fetchall()
        results: list[RetrievedChunk] = []
        for row in rows:
            rank = abs(float(row["rank"]))
            results.append(
                RetrievedChunk(
                    chunk=self._row_to_chunk(row),
                    lexical_score=1.0 / (1.0 + rank),
                    routes={"lexical"},
                )
            )
        return results

    def save_message(
        self,
        session_id: str,
        role: str,
        content: str,
        trace: list | None = None,
        citations: list[dict] | None = None,
        model_profile_id: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        total_tokens: int | None = None,
        refused: bool = False,
    ) -> int:
        now = datetime.now(UTC).isoformat()
        with closing(self._connect()) as connection:
            connection.execute(
                "INSERT OR IGNORE INTO sessions "
                "(session_id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (session_id, "新对话", now, now),
            )
            cursor = connection.execute(
                "INSERT INTO messages "
                "(session_id, role, content, trace_json, created_at, citations_json, "
                "model_profile_id, input_tokens, output_tokens, total_tokens, refused) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    role,
                    content,
                    json.dumps(trace or [], ensure_ascii=False),
                    now,
                    json.dumps(citations or [], ensure_ascii=False),
                    model_profile_id,
                    input_tokens,
                    output_tokens,
                    total_tokens,
                    int(refused),
                ),
            )
            if role == "user":
                title = content.strip().replace("\n", " ")[:30] or "新对话"
                connection.execute(
                    "UPDATE sessions SET title = CASE WHEN title = '新对话' THEN ? ELSE title END, "
                    "updated_at = ? WHERE session_id = ?",
                    (title, now, session_id),
                )
            connection.commit()
            return int(cursor.lastrowid)

    def create_session(self, session_id: str, title: str = "新对话") -> None:
        now = datetime.now(UTC).isoformat()
        with closing(self._connect()) as connection:
            connection.execute(
                "INSERT OR IGNORE INTO sessions "
                "(session_id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (session_id, title, now, now),
            )
            connection.commit()

    def list_sessions(self) -> list[dict]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT session_id, title, updated_at, model_profile_id "
                "FROM sessions ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_session(self, session_id: str) -> dict | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        return dict(row) if row else None

    def rename_session(self, session_id: str, title: str) -> None:
        now = datetime.now(UTC).isoformat()
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                "UPDATE sessions SET title = ?, updated_at = ? WHERE session_id = ?",
                (title, now, session_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(session_id)
            connection.commit()

    def set_session_model(self, session_id: str, model_profile_id: str | None) -> None:
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                "UPDATE sessions SET model_profile_id = ? WHERE session_id = ?",
                (model_profile_id, session_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(session_id)
            connection.commit()

    def delete_session(self, session_id: str) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                "DELETE FROM sessions WHERE session_id = ?", (session_id,)
            )
            connection.commit()

    def get_messages(self, session_id: str, limit: int = 20) -> list[dict]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT id, role, content, trace_json, created_at, citations_json, "
                "model_profile_id, input_tokens, output_tokens, total_tokens, refused "
                "FROM messages "
                "WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def session_token_total(self, session_id: str) -> int | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT SUM(total_tokens) AS total FROM messages "
                "WHERE session_id = ? AND role = 'assistant'",
                (session_id,),
            ).fetchone()
        return int(row["total"]) if row and row["total"] is not None else None

    def upsert_provider(self, provider: dict) -> None:
        now = datetime.now(UTC).isoformat()
        with closing(self._connect()) as connection:
            connection.execute(
                "INSERT INTO model_providers "
                "(provider_id, name, provider_type, base_url, enabled, has_api_key, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(provider_id) DO UPDATE SET name=excluded.name, "
                "provider_type=excluded.provider_type, base_url=excluded.base_url, "
                "enabled=excluded.enabled, has_api_key=excluded.has_api_key, updated_at=excluded.updated_at",
                (
                    provider["provider_id"],
                    provider["name"],
                    provider["provider_type"],
                    provider["base_url"].rstrip("/"),
                    int(provider.get("enabled", True)),
                    int(provider.get("has_api_key", False)),
                    now,
                    now,
                ),
            )
            connection.commit()

    def list_providers(self) -> list[dict]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM model_providers ORDER BY created_at"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_provider(self, provider_id: str) -> dict | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM model_providers WHERE provider_id = ?", (provider_id,)
            ).fetchone()
        return dict(row) if row else None

    def upsert_model_profile(self, profile: dict) -> None:
        now = datetime.now(UTC).isoformat()
        with closing(self._connect()) as connection:
            connection.execute(
                "INSERT INTO model_profiles "
                "(profile_id, provider_id, model_id, display_name, enabled, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(profile_id) DO UPDATE SET provider_id=excluded.provider_id, "
                "model_id=excluded.model_id, display_name=excluded.display_name, "
                "enabled=excluded.enabled, updated_at=excluded.updated_at",
                (
                    profile["profile_id"],
                    profile["provider_id"],
                    profile["model_id"],
                    profile["display_name"],
                    int(profile.get("enabled", True)),
                    now,
                    now,
                ),
            )
            connection.commit()

    def list_model_profiles(self, enabled_only: bool = False) -> list[dict]:
        sql = (
            "SELECT m.*, p.name AS provider_name, p.provider_type, p.base_url, "
            "p.has_api_key FROM model_profiles m "
            "JOIN model_providers p ON p.provider_id = m.provider_id"
        )
        if enabled_only:
            sql += " WHERE m.enabled = 1 AND p.enabled = 1"
        sql += " ORDER BY m.created_at"
        with closing(self._connect()) as connection:
            rows = connection.execute(sql).fetchall()
        return [dict(row) for row in rows]

    def get_model_profile(self, profile_id: str) -> dict | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT m.*, p.name AS provider_name, p.provider_type, p.base_url, "
                "p.has_api_key FROM model_profiles m "
                "JOIN model_providers p ON p.provider_id = m.provider_id "
                "WHERE m.profile_id = ?",
                (profile_id,),
            ).fetchone()
        return dict(row) if row else None

    def log(self, action: str, target: str, detail: str = "") -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                "INSERT INTO operation_logs(action, target, detail, created_at) VALUES (?, ?, ?, ?)",
                (action, target, detail, datetime.now(UTC).isoformat()),
            )
            connection.commit()

    def list_logs(self, limit: int = 100) -> list[dict]:
        """返回最近的知识库操作记录，限制上限避免界面一次加载过多数据。"""

        safe_limit = max(1, min(limit, 500))
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT id, action, target, detail, created_at FROM operation_logs "
                "ORDER BY id DESC LIMIT ?",
                (safe_limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_setting(self, key: str) -> dict | None:
        """读取一项应用级 JSON 配置。"""

        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT value_json FROM application_settings WHERE setting_key = ?",
                (key,),
            ).fetchone()
        return json.loads(row["value_json"]) if row else None

    def set_setting(self, key: str, value: dict) -> None:
        """幂等保存应用级配置，避免检索策略散落在业务代码中。"""

        now = datetime.now(UTC).isoformat()
        with closing(self._connect()) as connection:
            connection.execute(
                "INSERT INTO application_settings(setting_key, value_json, updated_at) "
                "VALUES (?, ?, ?) ON CONFLICT(setting_key) DO UPDATE SET "
                "value_json=excluded.value_json, updated_at=excluded.updated_at",
                (key, json.dumps(value, ensure_ascii=False), now),
            )
            connection.commit()

    @staticmethod
    def _row_to_chunk(row: sqlite3.Row) -> DocumentChunk:
        metadata = DocumentMetadata(**json.loads(row["metadata_json"]))
        return DocumentChunk(
            chunk_id=row["chunk_id"],
            doc_id=row["doc_id"],
            content=row["content"],
            chunk_index=row["chunk_index"],
            page_number=row["page_number"],
            section=row["section"],
            metadata=metadata,
            locator=json.loads(row["locator_json"] or "{}")
            if "locator_json" in row
            else {},
        )
