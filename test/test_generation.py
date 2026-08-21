import json
from types import SimpleNamespace

import httpx

from src.generation.composer import AnswerComposer
from src.models.credentials import MemoryCredentialStore
from src.models.generation import (
    GenerationModelRegistry,
    OpenAICompatibleGenerationAdapter,
)
from src.storage.sqlite_store import SQLiteStore


class FakeStreamResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def raise_for_status(self):
        return None

    def iter_lines(self):
        chunks = [
            {"choices": [{"delta": {"content": "食品"}}]},
            {"choices": [{"delta": {"content": "安全"}}]},
            {
                "choices": [],
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 3,
                    "total_tokens": 15,
                },
            },
        ]
        yield from [f"data: {json.dumps(item)}" for item in chunks]
        yield "data: [DONE]"


def test_openai_compatible_adapter_streams_text_and_usage(monkeypatch):
    monkeypatch.setattr(httpx, "stream", lambda *_args, **_kwargs: FakeStreamResponse())
    adapter = OpenAICompatibleGenerationAdapter("secret", "https://example.com/v1")
    deltas = list(adapter.stream([{"role": "user", "content": "测试"}], "model"))

    assert "".join(item.text for item in deltas) == "食品安全"
    assert deltas[-1].usage.total_tokens == 15


def test_provider_database_never_contains_plaintext_key(tmp_path):
    store = SQLiteStore(tmp_path / "rag.db")
    credentials = MemoryCredentialStore()
    credentials.set("p1", "super-secret-key")
    store.upsert_provider(
        {
            "provider_id": "p1",
            "name": "测试提供方",
            "provider_type": "openai_compatible",
            "base_url": "https://example.com/v1",
            "has_api_key": True,
        }
    )

    assert "super-secret-key" not in (tmp_path / "rag.db").read_bytes().decode(
        "utf-8", errors="ignore"
    )
    assert store.get_provider("p1")["has_api_key"] == 1


def test_invalid_citation_markers_are_removed():
    answer = "基本原则见[1]，第十六条规定见[16]。"

    sanitized = AnswerComposer._sanitize_citation_markers(answer, 3)

    assert sanitized == "基本原则见[1]，第十六条规定见。"


def test_default_dashscope_key_is_imported_to_credential_store(tmp_path):
    store = SQLiteStore(tmp_path / "rag.db")
    credentials = MemoryCredentialStore()
    settings = SimpleNamespace(
        dashscope_api_key="initial-secret",
        dashscope_base_url="https://dashscope.aliyuncs.com/api/v1",
        qwen_model="qwen-plus",
    )

    GenerationModelRegistry(store, credentials, settings)

    assert credentials.get("dashscope-default") == "initial-secret"
    assert "initial-secret" not in (tmp_path / "rag.db").read_bytes().decode(
        "utf-8", errors="ignore"
    )


def test_existing_credential_metadata_avoids_reimport_on_background_start(tmp_path):
    store = SQLiteStore(tmp_path / "rag.db")
    store.upsert_provider(
        {
            "provider_id": "dashscope-default",
            "name": "阿里云百炼",
            "provider_type": "dashscope",
            "base_url": "https://dashscope.aliyuncs.com/api/v1",
            "has_api_key": True,
        }
    )

    class BackgroundCredentialStore:
        def get(self, _provider_id):
            return None

        def set(self, _provider_id, _api_key):
            raise AssertionError("后台启动不应重复写入系统凭据库")

        def delete(self, _provider_id):
            return None

    settings = SimpleNamespace(
        dashscope_api_key="environment-secret",
        dashscope_base_url="https://dashscope.aliyuncs.com/api/v1",
        qwen_model="qwen-plus",
    )

    GenerationModelRegistry(store, BackgroundCredentialStore(), settings)
