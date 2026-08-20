"""SQLite 元数据、会话与中文关键词索引。"""

from __future__ import annotations

import json
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
        """幂等创建本地数据结构。"""

        schema = """
        CREATE TABLE IF NOT EXISTS documents (
            doc_id TEXT PRIMARY KEY,
            file_name TEXT NOT NULL,
            content_hash TEXT NOT NULL UNIQUE,
            metadata_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS chunks (
            chunk_id TEXT PRIMARY KEY,
            doc_id TEXT NOT NULL,
            content TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            page_number INTEGER,
            section TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
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
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            trace_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS operation_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            target TEXT NOT NULL,
            detail TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
        with closing(self._connect()) as connection:
            connection.executescript(schema)
            connection.commit()

    def find_document_by_hash(self, content_hash: str) -> dict | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM documents WHERE content_hash = ?", (content_hash,)
            ).fetchone()
        return dict(row) if row else None

    def find_document_by_name(self, file_name: str) -> dict | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM documents WHERE file_name = ? ORDER BY created_at DESC LIMIT 1",
                (file_name,),
            ).fetchone()
        return dict(row) if row else None

    def save_document(
        self, doc_id: str, metadata: DocumentMetadata, chunks: Iterable[DocumentChunk]
    ) -> None:
        """在一个事务中保存文档及其关键词索引。"""

        chunk_list = list(chunks)
        now = datetime.now(UTC).isoformat()
        metadata_json = json.dumps(metadata.to_dict(), ensure_ascii=False)
        with closing(self._connect()) as connection:
            connection.execute(
                "INSERT INTO documents VALUES (?, ?, ?, ?, ?)",
                (doc_id, metadata.source, metadata.content_hash, metadata_json, now),
            )
            for chunk in chunk_list:
                connection.execute(
                    "INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        chunk.chunk_id,
                        chunk.doc_id,
                        chunk.content,
                        chunk.chunk_index,
                        chunk.page_number,
                        chunk.section,
                        json.dumps(chunk.metadata.to_dict(), ensure_ascii=False),
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

    def list_documents(self) -> list[dict]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT doc_id, file_name, content_hash, metadata_json, created_at "
                "FROM documents ORDER BY created_at DESC"
            ).fetchall()
        documents: list[dict] = []
        for row in rows:
            item = dict(row)
            item.update(json.loads(item.pop("metadata_json")))
            documents.append(item)
        return documents

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
        self, session_id: str, role: str, content: str, trace: list[str] | None = None
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with closing(self._connect()) as connection:
            connection.execute(
                "INSERT OR IGNORE INTO sessions VALUES (?, ?, ?, ?)",
                (session_id, "新对话", now, now),
            )
            connection.execute(
                "INSERT INTO messages(session_id, role, content, trace_json, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    session_id,
                    role,
                    content,
                    json.dumps(trace or [], ensure_ascii=False),
                    now,
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

    def create_session(self, session_id: str, title: str = "新对话") -> None:
        now = datetime.now(UTC).isoformat()
        with closing(self._connect()) as connection:
            connection.execute(
                "INSERT OR IGNORE INTO sessions VALUES (?, ?, ?, ?)",
                (session_id, title, now, now),
            )
            connection.commit()

    def list_sessions(self) -> list[dict]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT session_id, title, updated_at FROM sessions ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_session(self, session_id: str) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                "DELETE FROM sessions WHERE session_id = ?", (session_id,)
            )
            connection.commit()

    def get_messages(self, session_id: str, limit: int = 20) -> list[dict]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT role, content, trace_json, created_at FROM messages "
                "WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def log(self, action: str, target: str, detail: str = "") -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                "INSERT INTO operation_logs(action, target, detail, created_at) VALUES (?, ?, ?, ?)",
                (action, target, detail, datetime.now(UTC).isoformat()),
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
        )
