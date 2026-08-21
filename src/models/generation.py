"""可切换生成模型的流式适配器。"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import dashscope
import httpx

from src.config.settings import Settings
from src.models.credentials import CredentialStore
from src.storage.sqlite_store import SQLiteStore


@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class GenerationDelta:
    text: str = ""
    usage: TokenUsage | None = None


class GenerationAdapter(Protocol):
    def stream(
        self, messages: Sequence[dict], model_id: str
    ) -> Iterator[GenerationDelta]: ...


def _usage_from(value: Any) -> TokenUsage | None:
    if value is None:
        return None
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    elif not isinstance(value, dict):
        try:
            value = dict(value)
        except (TypeError, ValueError):
            return None
    input_tokens = value.get("input_tokens", value.get("prompt_tokens"))
    output_tokens = value.get("output_tokens", value.get("completion_tokens"))
    total_tokens = value.get("total_tokens")
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = int(input_tokens) + int(output_tokens)
    if input_tokens is None and output_tokens is None and total_tokens is None:
        return None
    return TokenUsage(
        int(input_tokens) if input_tokens is not None else None,
        int(output_tokens) if output_tokens is not None else None,
        int(total_tokens) if total_tokens is not None else None,
    )


class DashScopeGenerationAdapter:
    def __init__(self, api_key: str, base_url: str):
        self.api_key = api_key
        self.base_url = base_url

    def stream(
        self, messages: Sequence[dict], model_id: str
    ) -> Iterator[GenerationDelta]:
        dashscope.api_key = self.api_key
        dashscope.base_http_api_url = self.base_url
        responses = dashscope.Generation.call(
            model=model_id,
            messages=list(messages),
            result_format="message",
            stream=True,
            incremental_output=True,
        )
        for response in responses:
            status_code = getattr(response, "status_code", 200)
            if status_code != 200:
                raise RuntimeError(
                    f"DashScope 生成失败：{getattr(response, 'message', '未知错误')}"
                )
            choices = response.output.choices if response.output else []
            text = choices[0].message.content if choices else ""
            usage = _usage_from(getattr(response, "usage", None))
            if text or usage:
                yield GenerationDelta(text=text or "", usage=usage)


class OpenAICompatibleGenerationAdapter:
    def __init__(self, api_key: str | None, base_url: str):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    @property
    def endpoint(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"

    def stream(
        self, messages: Sequence[dict], model_id: str
    ) -> Iterator[GenerationDelta]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {
            "model": model_id,
            "messages": list(messages),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        with httpx.stream(
            "POST", self.endpoint, headers=headers, json=payload, timeout=120.0
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if not raw or raw == "[DONE]":
                    continue
                data = json.loads(raw)
                choices = data.get("choices") or []
                delta = choices[0].get("delta", {}) if choices else {}
                text = delta.get("content") or ""
                usage = _usage_from(data.get("usage"))
                if text or usage:
                    yield GenerationDelta(text=text, usage=usage)


class GenerationModelRegistry:
    """管理模型配置并按会话选择对应生成适配器。"""

    def __init__(
        self,
        store: SQLiteStore,
        credentials: CredentialStore,
        settings: Settings,
    ):
        self.store = store
        self.credentials = credentials
        self.settings = settings
        self.ensure_default_profile()

    def ensure_default_profile(self) -> None:
        provider_id = "dashscope-default"
        profile_id = "qwen-default"
        existing_provider = self.store.get_provider(provider_id)
        # 已导入过的凭据不在后台服务启动时重复写入。Windows Credential
        # Manager 可能在非交互登录会话中拒绝 CredWrite，但 DashScope 仍可使用
        # 当前进程的环境变量完成调用。
        should_import_key = (
            bool(self.settings.dashscope_api_key)
            and not bool(existing_provider and existing_provider.get("has_api_key"))
            and not self.credentials.get(provider_id)
        )
        if should_import_key:
            self.credentials.set(provider_id, self.settings.dashscope_api_key)
        if not existing_provider:
            self.store.upsert_provider(
                {
                    "provider_id": provider_id,
                    "name": "阿里云百炼",
                    "provider_type": "dashscope",
                    "base_url": self.settings.dashscope_base_url,
                    "enabled": True,
                    "has_api_key": bool(self.settings.dashscope_api_key),
                }
            )
        if not self.store.get_model_profile(profile_id):
            self.store.upsert_model_profile(
                {
                    "profile_id": profile_id,
                    "provider_id": provider_id,
                    "model_id": self.settings.qwen_model,
                    "display_name": self.settings.qwen_model,
                    "enabled": True,
                }
            )

    def default_profile_id(self) -> str:
        profiles = self.store.list_model_profiles(enabled_only=True)
        if not profiles:
            raise RuntimeError("没有可用的生成模型，请先在模型设置中配置。")
        return str(profiles[0]["profile_id"])

    def _api_key(self, profile: dict) -> str | None:
        key = self.credentials.get(str(profile["provider_id"]))
        if key:
            return key
        if profile["provider_type"] == "dashscope":
            return self.settings.dashscope_api_key or None
        return None

    def adapter_for(self, profile_id: str) -> tuple[dict, GenerationAdapter]:
        profile = self.store.get_model_profile(profile_id)
        if not profile or not profile.get("enabled"):
            raise ValueError("所选生成模型不存在或已停用。")
        api_key = self._api_key(profile)
        if profile["provider_type"] == "dashscope":
            if not api_key:
                raise RuntimeError("DashScope 提供方尚未配置 API Key。")
            adapter: GenerationAdapter = DashScopeGenerationAdapter(
                api_key, str(profile["base_url"])
            )
        elif profile["provider_type"] == "openai_compatible":
            adapter = OpenAICompatibleGenerationAdapter(
                api_key, str(profile["base_url"])
            )
        else:
            raise ValueError(f"不支持的模型提供方类型：{profile['provider_type']}")
        return profile, adapter

    def stream_answer(
        self,
        profile_id: str,
        question: str,
        contexts: Sequence[str],
    ) -> Iterator[GenerationDelta]:
        profile, adapter = self.adapter_for(profile_id)
        context_text = "\n\n".join(contexts)
        available_labels = "、".join(
            f"[证据{index}]" for index in range(1, len(contexts) + 1)
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "你是食品安全知识库助手。只能依据给定资料回答；每项关键结论必须使用"
                    "[证据1]、[证据2]形式引用。资料不足时明确说无法从当前知识库确认，不得编造"
                    "标准条款、数值或日期。涉及处罚、健康或合规决策时提醒用户核对现行原文。"
                    f"本轮仅允许使用这些证据标签：{available_labels}。引用前必须确认结论"
                    "确实出现在对应证据块中。不得直接输出[1]形式，也不得把法律条款序号"
                    "当作证据标签；系统会在展示时将[证据1]转换为[1]。"
                ),
            },
            {"role": "user", "content": f"资料：\n{context_text}\n\n问题：{question}"},
        ]
        yield from adapter.stream(messages, str(profile["model_id"]))
