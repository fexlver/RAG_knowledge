"""HTTP 接口的数据结构。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SessionUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=80)
    model_profile_id: str | None = None


class RunRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20000)
    model_profile_id: str | None = None


class ProviderInput(BaseModel):
    provider_id: str | None = None
    name: str = Field(min_length=1, max_length=80)
    provider_type: Literal["dashscope", "openai_compatible"]
    base_url: str = Field(min_length=4, max_length=500)
    api_key: str | None = Field(default=None, max_length=1000)
    enabled: bool = True


class ModelProfileInput(BaseModel):
    profile_id: str | None = None
    provider_id: str
    model_id: str = Field(min_length=1, max_length=200)
    display_name: str = Field(min_length=1, max_length=100)
    enabled: bool = True


class RetrievalConfigInput(BaseModel):
    """可组合检索流水线配置。"""

    retriever_ids: list[str] = Field(min_length=1, max_length=12)
    fusion_id: str = Field(default="rrf", min_length=1, max_length=80)
    rerank_enabled: bool = True
