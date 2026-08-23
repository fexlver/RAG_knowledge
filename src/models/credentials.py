"""模型凭据安全存储。"""

from __future__ import annotations

import os
import re
from typing import Protocol

import keyring


class CredentialStore(Protocol):
    def get(self, provider_id: str) -> str | None: ...

    def set(self, provider_id: str, api_key: str) -> None: ...

    def delete(self, provider_id: str) -> None: ...


class SystemCredentialStore:
    """通过操作系统凭据库保存 API Key，数据库不接触密钥明文。

    无系统凭据库的环境（精简 Linux 容器没有 keyring 后端）自动降级：
    读取 ``{PROVIDER}_API_KEY`` 环境变量，写入仅进内存。容器部署靠
    .env 里的 DASHSCOPE_API_KEY 即可跑通全链路。
    """

    service_name = "food-safety-agentic-rag"

    def __init__(self) -> None:
        self._memory: dict[str, str] = {}
        self._keyring_available = self._probe_keyring()

    def _probe_keyring(self) -> bool:
        try:
            keyring.get_password(self.service_name, "__backend_probe__")
            return True
        except Exception:
            return False

    @staticmethod
    def _env_name(provider_id: str) -> str:
        slug = re.sub(r"[^A-Za-z0-9]+", "_", provider_id).strip("_").upper()
        return f"{slug}_API_KEY"

    def get(self, provider_id: str) -> str | None:
        if self._keyring_available:
            return keyring.get_password(self.service_name, provider_id)
        return self._memory.get(provider_id) or os.getenv(self._env_name(provider_id))

    def set(self, provider_id: str, api_key: str) -> None:
        value = api_key.strip()
        if not value:
            raise ValueError("API Key 不能为空。")
        if self._keyring_available:
            keyring.set_password(self.service_name, provider_id, value)
        else:
            self._memory[provider_id] = value

    def delete(self, provider_id: str) -> None:
        if not self._keyring_available:
            self._memory.pop(provider_id, None)
            return
        try:
            keyring.delete_password(self.service_name, provider_id)
        except keyring.errors.PasswordDeleteError:
            pass


class MemoryCredentialStore:
    """测试使用的内存凭据库。"""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, provider_id: str) -> str | None:
        return self.values.get(provider_id)

    def set(self, provider_id: str, api_key: str) -> None:
        self.values[provider_id] = api_key

    def delete(self, provider_id: str) -> None:
        self.values.pop(provider_id, None)
