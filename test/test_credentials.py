"""凭据库在无 keyring 后端环境（容器）下的降级行为测试。"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import keyring

from src.models.credentials import SystemCredentialStore


def test_falls_back_to_env_when_keyring_unavailable(monkeypatch):
    def raise_no_backend(*args, **kwargs):
        raise keyring.errors.NoKeyringError("No recommended backend")

    monkeypatch.setattr(keyring, "get_password", raise_no_backend)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-env-fallback")

    store = SystemCredentialStore()
    assert store.get("dashscope") == "sk-env-fallback"

    store.set("dashscope", "sk-memory")
    assert store.get("dashscope") == "sk-memory"
    store.delete("dashscope")
    assert store.get("dashscope") == "sk-env-fallback"


def test_provider_id_to_env_name():
    assert SystemCredentialStore._env_name("dashscope") == "DASHSCOPE_API_KEY"
    assert SystemCredentialStore._env_name("my-provider") == "MY_PROVIDER_API_KEY"
