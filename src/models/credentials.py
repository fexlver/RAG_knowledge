"""模型凭据安全存储。"""

from __future__ import annotations

from typing import Protocol

import keyring


class CredentialStore(Protocol):
    def get(self, provider_id: str) -> str | None: ...

    def set(self, provider_id: str, api_key: str) -> None: ...

    def delete(self, provider_id: str) -> None: ...


class SystemCredentialStore:
    """通过操作系统凭据库保存 API Key，数据库不接触密钥明文。"""

    service_name = "food-safety-agentic-rag"

    def get(self, provider_id: str) -> str | None:
        return keyring.get_password(self.service_name, provider_id)

    def set(self, provider_id: str, api_key: str) -> None:
        value = api_key.strip()
        if not value:
            raise ValueError("API Key 不能为空。")
        keyring.set_password(self.service_name, provider_id, value)

    def delete(self, provider_id: str) -> None:
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
